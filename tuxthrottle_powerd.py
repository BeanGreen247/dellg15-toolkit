#!/usr/bin/env python3
"""TuxThrottle power daemon — stdlib only, no GUI deps.

Two jobs, one poll loop:

  * Closed-loop **fan curve** — map CPU / GPU temperature through a
    piecewise-linear curve to an *additive* alienware_wmi fan boost
    (`fanN_boost`, the safe lever — it can only add airflow on top of the
    firmware curve, never slow a fan). Hysteresis stops it hunting at a
    breakpoint. On exit it restores the automatic fan control.

  * **AC / battery auto-switch** — when the charger is plugged or pulled,
    apply a saved profile bundle (platform_profile + a ryzenadj TDP preset).

Config: ~/.config/tuxthrottle/powerd.json (written by the GUI's Fans /
Power & Limits tabs), re-read live when its mtime changes:

  {
    "poll_s": 2,
    "fan_curve": {
      "enabled": false,
      "sensor": "max",            # "cpu" | "gpu" | "max"
      "hysteresis_c": 3,
      "points": [[45,0],[60,25],[72,55],[82,85],[90,100]]   # [temp_c, boost_%]
    },
    "autoswitch": {
      "enabled": false,
      "on_ac": "Balanced",        # Quiet | Balanced | Performance
      "on_battery": "Quiet"
    }
  }

Runs as root (systemd unit installed by the FanCurveDaemon tweak). Also
usable by hand:  sudo python3 tuxthrottle_powerd.py once --user bean
"""
import argparse
import json
import os
import pwd
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sensors  # noqa: E402  (sibling module, stdlib-only)

DEFAULTS = {
    "poll_s": 2,
    "fan_curve": {
        "enabled": False,
        "sensor": "max",
        "hysteresis_c": 3,
        "points": [[45, 0], [60, 25], [72, 55], [82, 85], [90, 100]],
    },
    "autoswitch": {"enabled": False, "on_ac": "Balanced", "on_battery": "Quiet"},
}

# (STAPM, fast, slow) Watts — mirrors tuxthrottle.py's _TDP_PRESETS (STAPM >= slow
# so the SMU doesn't clamp it). Kept here so the daemon has no GUI dependency.
TDP_PRESETS = {
    "Quiet": (25, 35, 25),
    "Balanced": (42, 54, 42),
    "Performance": (65, 80, 54),
}
PROFILE_FOR_BUNDLE = {"Quiet": "balanced", "Balanced": "balanced",
                      "Performance": "performance"}

_STOP = False


def log(msg: str) -> None:
    print(f"[powerd] {msg}", flush=True)


def _config_path(user: str | None) -> Path:
    if user:
        try:
            home = Path(pwd.getpwnam(user).pw_dir)
        except KeyError:
            home = Path.home()
    else:
        home = Path.home()
    return home / ".config" / "tuxthrottle" / "powerd.json"


def _merge(base: dict, over: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def load_config(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError("not an object")
        return _merge(DEFAULTS, raw)
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULTS))


def interp(points: list, temp: float) -> int:
    """Piecewise-linear temp -> boost%. Clamps outside the point range."""
    pts = sorted((float(t), float(b)) for t, b in points)
    if not pts:
        return 0
    if temp <= pts[0][0]:
        return int(round(pts[0][1]))
    if temp >= pts[-1][0]:
        return int(round(pts[-1][1]))
    for (t0, b0), (t1, b1) in zip(pts, pts[1:]):
        if t0 <= temp <= t1:
            frac = (temp - t0) / (t1 - t0) if t1 != t0 else 0.0
            return int(round(b0 + frac * (b1 - b0)))
    return int(round(pts[-1][1]))


def read_temp(sensor: str) -> float | None:
    cpu = sensors.read_cpu_temp_c_value()
    gpu = None
    _clk, gtemp, _u, _p = sensors.read_dgpu_values()
    if gtemp is not None:
        gpu = float(gtemp)
    if sensor == "cpu":
        return cpu
    if sensor == "gpu":
        return gpu
    vals = [v for v in (cpu, gpu) if v is not None]
    return max(vals) if vals else None


def _ac_online() -> bool | None:
    import glob
    for p in glob.glob("/sys/class/power_supply/A[CD]*/online"):
        try:
            return bool(int(open(p).read().strip()))
        except (OSError, ValueError):
            continue
    return None


def apply_bundle(name: str) -> None:
    preset = TDP_PRESETS.get(name)
    prof = PROFILE_FOR_BUNDLE.get(name)
    if prof and prof in sensors.platform_profile_choices():
        ok, err = sensors.set_platform_profile(prof)
        log(f"bundle {name}: platform_profile -> {prof}" + ("" if ok else f" FAILED {err}"))
    if preset and sensors.ryzenadj_available():
        s, f, sl = preset
        ok, err = sensors.set_ryzenadj_limits(fast_w=f, slow_w=sl, stapm_w=s)
        log(f"bundle {name}: TDP -> {s}/{f}/{sl} W" + ("" if ok else f" FAILED {err}"))


class FanController:
    def __init__(self):
        self._applied = None      # last boost% we wrote
        self._active = False      # are we currently overriding the fans?

    def tick(self, cfg: dict) -> None:
        fc = cfg["fan_curve"]
        if not fc.get("enabled"):
            if self._active:
                sensors.restore_fan_auto()
                log("fan curve disabled -> restored automatic control")
                self._active, self._applied = False, None
            return
        temp = read_temp(fc.get("sensor", "max"))
        if temp is None:
            return
        pts = fc.get("points") or DEFAULTS["fan_curve"]["points"]
        hys = float(fc.get("hysteresis_c", 3))
        target = interp(pts, temp)
        if self._applied is None:
            self._commit(target, temp)
            return
        if target >= self._applied:
            if target != self._applied:
                self._commit(target, temp)                 # ramp up immediately
        elif interp(pts, temp + hys) < self._applied:
            self._commit(target, temp)                     # cool down past hysteresis

    def _commit(self, boost_pct: int, temp: float) -> None:
        raw = max(0, min(255, round(boost_pct * 255 / 100)))
        for i in (1, 2):
            sensors.set_fan_boost(i, raw)
        self._applied, self._active = boost_pct, True
        log(f"temp {temp:.0f}C -> fan boost {boost_pct}%")

    def restore(self) -> None:
        if self._active:
            sensors.restore_fan_auto()
            log("exiting -> restored automatic fan control")


def _handle_stop(signum, _frame):
    global _STOP
    _STOP = True
    log(f"signal {signum} -> stopping")


def run(cfg_path: Path, once: bool = False) -> int:
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, _handle_stop)
    fans = FanController()
    last_mtime = -1.0
    cfg = load_config(cfg_path)
    last_ac = None
    log(f"start; config {cfg_path} ({'exists' if cfg_path.exists() else 'defaults'})")
    try:
        while not _STOP:
            try:
                m = cfg_path.stat().st_mtime
            except OSError:
                m = 0.0
            if m != last_mtime:
                cfg = load_config(cfg_path)
                last_mtime = m
                log(f"config loaded: fan_curve={cfg['fan_curve']['enabled']} "
                    f"autoswitch={cfg['autoswitch']['enabled']}")

            fans.tick(cfg)

            if cfg["autoswitch"].get("enabled"):
                ac = _ac_online()
                if ac is not None and ac != last_ac:
                    bundle = cfg["autoswitch"]["on_ac" if ac else "on_battery"]
                    log(f"power source -> {'AC' if ac else 'battery'}; apply '{bundle}'")
                    apply_bundle(bundle)
                    last_ac = ac

            if once:
                break
            time.sleep(max(1, int(cfg.get("poll_s", 2))))
    finally:
        fans.restore()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TuxThrottle fan-curve + AC-switch daemon")
    ap.add_argument("mode", choices=["run", "once"], nargs="?", default="run")
    ap.add_argument("--user", help="resolve the default config path in this user's home")
    ap.add_argument("--config", type=Path, help="explicit config file path")
    args = ap.parse_args()
    if os.geteuid() != 0:
        log("warning: not root — fan/profile writes will fail")
    cfg_path = args.config or _config_path(args.user or os.environ.get("SUDO_USER"))
    return run(cfg_path, once=(args.mode == "once"))


if __name__ == "__main__":
    raise SystemExit(main())
