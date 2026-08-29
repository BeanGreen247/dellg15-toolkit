#!/usr/bin/env python3
"""Keyboard-backlight control for the Dell G15 5515 (Alienware AW-ELC, USB
187c:0550) — a thin wrapper around the `openrgb` CLI.

Background: this BIOS has no SMBIOS keyboard tokens, `dell-laptop` makes no
LED, and hand-rolled HID writes (feature *and* output reports) are ACK'd but
never light up. What *does* work is **OpenRGB** driving the controller as 16
logical zones — verified on the real 5515. So this module shells out to
`openrgb --noautoconnect`.

Prerequisites:
  * OpenRGB installed (the `OpenRGB` app in the Toolkit's Software tab).
  * The keyboard backlight **enabled in BIOS setup** (F2 -> Keyboard
    Backlight). Nothing lights until that's on.

CLI:
    dellg15_kbd.py on  [--color RRGGBB] [--brightness 0-100]
    dellg15_kbd.py off
    dellg15_kbd.py zone <0-3> --color RRGGBB [--brightness 0-100]
    dellg15_kbd.py effect <rainbow|spectrum|breathing|flashing> [--speed 0-100] [--brightness 0-100]
    dellg15_kbd.py gradient <RRGGBB> <RRGGBB> [--brightness 0-100]
    dellg15_kbd.py apply-saved
    dellg15_kbd.py info

Hardware effect modes come from the controller firmware (OpenRGB: Static,
Flashing, Morph, Spectrum Cycle, Rainbow Wave, Breathing). Effect *speed* is
a 0-100 percentage; effect *direction* is not exposed by the OpenRGB CLI
(GUI / SDK only), so it isn't offered.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

DEVICE = "Dell G Series LED Controller"   # OpenRGB's name for the AW-ELC on the G15
ZONE_COUNT = 4                            # physical zones
GKEY_ZONE = 0                             # the G-key sits in the leftmost zone
ZONE_NAMES = ["Left", "Middle", "Right", "Numpad"]
LOGICAL_ZONES = 16                       # OpenRGB drives it as 16; ~4 per physical zone
MIN_BRIGHTNESS = 0                        # OpenRGB's brightness-0 is recoverable here


class ElcError(RuntimeError):
    pass


def _hexify(c) -> str:
    return c.lstrip("#").upper() if isinstance(c, str) else "%02X%02X%02X" % tuple(c)


class Keyboard:
    """Compatibility shim: the GUI and sensors.py still call
    Keyboard()/_find()/set_zones()/set_brightness()/off(). Everything routes
    to the module-level openrgb helpers."""

    @staticmethod
    def _find():
        if not shutil.which("openrgb"):
            return None
        import glob
        for vp in glob.glob("/sys/bus/usb/devices/*/idVendor"):
            try:
                if open(vp).read().strip().lower() != "187c":
                    continue
                pid = open(vp.rsplit("/", 1)[0] + "/idProduct").read().strip().lower()
                if pid in ("0550", "0551"):
                    return "openrgb:" + DEVICE
            except OSError:
                pass
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def set_zones(self, colors: dict, brightness: int = 100) -> None:
        set_zones({z: _hexify(c) for z, c in colors.items()}, brightness)

    def set_all(self, color, brightness: int = 100) -> None:
        set_all(_hexify(color), brightness)

    def set_effect(self, name, speed=None, color=None, brightness: int = 100) -> None:
        set_effect(name, speed, color, brightness)

    def set_gradient(self, color_a, color_b, brightness: int = 100) -> None:
        set_gradient(color_a, color_b, brightness)

    def set_brightness(self, percent: int, zones=None) -> None:
        st = load_state()
        if st:
            set_zones(st[0], max(0, min(100, percent)))
        else:
            _run(["-b", str(max(0, min(100, percent)))])

    def off(self) -> None:
        off()


def _openrgb() -> str:
    exe = shutil.which("openrgb")
    if not exe:
        raise ElcError("openrgb not found — install the 'OpenRGB' app (Software tab). "
                       "The G15 5515 keyboard backlight is only reachable through OpenRGB.")
    return exe


def _run_once(args: list[str], server: bool = True) -> str:
    # server=True: talk to a running `openrgb --server` (dellg15-openrgb.service)
    # — ~1s, devices stay initialised. server=False: standalone scan (~4s), the
    # fallback when no server is up.
    cmd = [_openrgb()] + ([] if server else ["--noautoconnect"]) + ["-d", DEVICE, *args]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    # OpenRGB always exits 0 and prints an i2c/SMBus HTML warning to stdout;
    # a real failure shows up as "Device .* not found" / "Connection attempt".
    blob = (out.stdout + out.stderr)
    if "not found" in blob or "Connection attempt failed" in blob:
        raise ElcError(blob.strip().splitlines()[-1] if blob.strip() else "openrgb call failed")
    return blob


def _run(args: list[str]) -> str:
    """Apply an openrgb command, fast path first.

    Tries the running OpenRGB SDK server (~1s); falls back to a standalone
    scan (~4s) if there's no server. Then writes the same command a second
    time immediately — on this AW-ELC controller a lone write often doesn't
    'take' (keys flash the colour then go dark a beat later); the repeat
    locks it in, with no artificial delay so the GUI stays snappy."""
    server = True
    try:
        blob = _run_once(args, server=True)
    except (ElcError, subprocess.TimeoutExpired):
        server = False
        blob = _run_once(args, server=False)
    try:
        _run_once(args, server=server)
    except (ElcError, subprocess.TimeoutExpired):
        pass
    return blob


def _hexval(s: str) -> str:
    s = s.lstrip("#")
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise argparse.ArgumentTypeError("colour must be RRGGBB hex")
    return s.upper()


def _physical_to_logical(zone: int) -> list[int]:
    """Map one of the 4 physical zones to its block of OpenRGB logical zones.
    Even split across 16 — refine if the real mapping is uneven."""
    per = LOGICAL_ZONES // ZONE_COUNT
    start = zone * per
    return list(range(start, start + per))


# ---- operations ----------------------------------------------------------- #

def set_all(color, brightness: int = 100) -> None:
    _run(["-m", "Static", "-c", _hexify(color), "-b", str(max(0, min(100, brightness)))])


def set_zone(zone: int, color, brightness: int = 100) -> None:
    args = ["-m", "Static", "-b", str(max(0, min(100, brightness)))]
    for lz in _physical_to_logical(zone):
        args += ["-z", str(lz), "-c", _hexify(color)]
    _run(args)


def set_zones(colors: dict, brightness: int = 100) -> None:
    # When every zone is the same colour (the common case — "apply to all",
    # presets, the boot re-assert), use the single rock-solid whole-keyboard
    # command. The multi -z form is laggy and *intermittently blanks the
    # keyboard* on this controller, so only use it for genuinely mixed colours.
    hexes = {_hexify(c) for c in colors.values()}
    if len(hexes) == 1:
        set_all(next(iter(hexes)), brightness)
        return
    args = ["-m", "Static", "-b", str(max(0, min(100, brightness)))]
    for pz, col in sorted(colors.items()):
        for lz in _physical_to_logical(pz):
            args += ["-z", str(lz), "-c", _hexify(col)]
    _run(args)


def off() -> None:
    _run(["-b", "0"])


# Hardware effect modes the AW-ELC / "Dell G Series LED Controller" exposes
# through OpenRGB (from `openrgb -l`): Static Flashing Morph 'Spectrum Cycle'
# 'Rainbow Wave' Breathing. Speed is a 0-100 percentage where the mode
# supports it. DIRECTION IS NOT SETTABLE VIA THE OpenRGB CLI — only via the
# GUI or the SDK network protocol — so it isn't offered here.
EFFECT_MODES = {
    "rainbow": "Rainbow Wave",
    "spectrum": "Spectrum Cycle",
    "breathing": "Breathing",
    "flashing": "Flashing",
}


def set_effect(name: str, speed: int | None = None, color=None,
               brightness: int = 100) -> None:
    mode = EFFECT_MODES.get(name, name)
    args = ["-m", mode, "-b", str(max(0, min(100, brightness)))]
    if color is not None:
        args += ["-c", _hexify(color)]
    if speed is not None:
        args += ["-s", str(max(0, min(100, int(speed))))]
    _run(args)


def _to_rgb(c) -> tuple[int, int, int]:
    if isinstance(c, str):
        c = c.lstrip("#")
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    return tuple(c)


def set_gradient(color_a, color_b, brightness: int = 100) -> None:
    """Static colour gradient from A (left) to B (right/numpad), interpolated
    across the 16 logical zones."""
    a, b = _to_rgb(color_a), _to_rgb(color_b)
    args = ["-m", "Static", "-b", str(max(0, min(100, brightness)))]
    for lz in range(LOGICAL_ZONES):
        t = lz / (LOGICAL_ZONES - 1)
        mix = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
        args += ["-z", str(lz), "-c", _hexify(mix)]
    _run(args)


def info() -> dict:
    txt = subprocess.run([_openrgb(), "--noautoconnect", "-l"],
                         capture_output=True, text=True, timeout=40).stdout
    seen = DEVICE in txt or "AW-ELC" in txt or "AlienWare" in txt
    plat = ""
    for line in txt.splitlines():
        if "platform:" in line and ("Unknown" in line or "Known" in line):
            plat = line.split("platform:")[1].strip().split(",")[0].split()[0]
    return {"backend": "openrgb", "device": DEVICE, "detected": seen,
            "platform_id": plat or "?", "physical_zones": ZONE_COUNT}


# ---- persisted state (so a login/resume service can re-assert it) -------- #

def _state_path() -> str:
    override = os.environ.get("DELLG15_KBD_STATE")
    if override:
        return override
    user = os.environ.get("SUDO_USER") or os.environ.get("PKEXEC_USER")
    home = (__import__("pwd").getpwnam(user).pw_dir if user else os.path.expanduser("~"))
    return os.path.join(home, ".config", "dellg15-toolkit", "kbd.json")


def save_state(zone_colors: dict, brightness: int, mode: str = "zones",
               speed: int = 50, gradient: tuple | None = None) -> None:
    """Persist the current lighting so the boot/resume service can re-assert
    it. `mode` is one of: zones, rainbow, spectrum, breathing, flashing,
    gradient. `gradient` is (hexA, hexB) when mode == 'gradient'."""
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {"brightness": int(brightness),
           "mode": mode,
           "speed": int(speed),
           "zones": {str(z): _hexify(c) for z, c in zone_colors.items()}}
    if gradient:
        doc["gradient"] = [_hexify(gradient[0]), _hexify(gradient[1])]
    json.dump(doc, open(path, "w"), indent=2)
    user = os.environ.get("SUDO_USER") or os.environ.get("PKEXEC_USER")
    if user and os.geteuid() == 0:
        pw = __import__("pwd").getpwnam(user)
        os.chown(path, pw.pw_uid, pw.pw_gid)
        os.chown(os.path.dirname(path), pw.pw_uid, pw.pw_gid)


def load_state() -> tuple[dict[int, tuple[int, int, int]], int] | None:
    """Returns ({physical_zone: (r,g,b)}, brightness). RGB tuples for the
    GUI's benefit; the set_* helpers accept tuples or hex."""
    try:
        d = json.load(open(_state_path()))
    except (OSError, ValueError):
        return None
    zones = {}
    for z, c in d.get("zones", {}).items():
        if isinstance(c, str):
            c = c.lstrip("#")
            c = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
        zones[int(z)] = tuple(c)
    return (zones, int(d.get("brightness", 100))) if zones else None


def load_meta() -> dict:
    """Returns {'mode', 'speed', 'gradient'} from the saved state (defaults if
    absent). Separate from load_state() so its 2-tuple callers don't change."""
    try:
        d = json.load(open(_state_path()))
    except (OSError, ValueError):
        return {"mode": "zones", "speed": 50, "gradient": None}
    return {"mode": d.get("mode", "zones"),
            "speed": int(d.get("speed", 50)),
            "gradient": tuple(d["gradient"]) if d.get("gradient") else None}


# ---- CLI ---------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_on = sub.add_parser("on")
    p_on.add_argument("--color", type=_hexval, default="FFFFFF")
    p_on.add_argument("--brightness", type=int, default=100)

    sub.add_parser("off")

    p_z = sub.add_parser("zone")
    p_z.add_argument("index", type=int, choices=range(ZONE_COUNT))
    p_z.add_argument("--color", type=_hexval, required=True)
    p_z.add_argument("--brightness", type=int, default=None)

    p_e = sub.add_parser("effect")
    p_e.add_argument("name", choices=sorted(EFFECT_MODES))
    p_e.add_argument("--speed", type=int, default=50)
    p_e.add_argument("--brightness", type=int, default=100)

    p_g = sub.add_parser("gradient")
    p_g.add_argument("color_a", type=_hexval)
    p_g.add_argument("color_b", type=_hexval)
    p_g.add_argument("--brightness", type=int, default=100)

    sub.add_parser("apply-saved")
    sub.add_parser("info")

    a = p.parse_args(argv)
    try:
        if a.cmd == "info":
            for k, v in info().items():
                print(f"{k:15}: {v}")
        elif a.cmd == "on":
            set_all(a.color, a.brightness)
            save_state({z: a.color for z in range(ZONE_COUNT)}, a.brightness)
            print(f"backlight on: #{a.color.lower()} @ {a.brightness}%")
        elif a.cmd == "off":
            off()
            print("backlight off")
        elif a.cmd == "zone":
            st = load_state()
            colors = st[0] if st else {z: "FFFFFF" for z in range(ZONE_COUNT)}
            br = a.brightness if a.brightness is not None else (st[1] if st else 100)
            colors[a.index] = a.color
            set_zones(colors, br)
            save_state(colors, br)
            print(f"zone {a.index} ({ZONE_NAMES[a.index]}) -> #{a.color.lower()} @ {br}%")
        elif a.cmd == "effect":
            set_effect(a.name, a.speed, brightness=a.brightness)
            st = load_state()
            colors = st[0] if st else {z: "FFFFFF" for z in range(ZONE_COUNT)}
            save_state(colors, a.brightness, mode=a.name, speed=a.speed)
            print(f"effect {a.name} @ speed {a.speed}, {a.brightness}%")
        elif a.cmd == "gradient":
            set_gradient(a.color_a, a.color_b, a.brightness)
            st = load_state()
            colors = st[0] if st else {z: "FFFFFF" for z in range(ZONE_COUNT)}
            save_state(colors, a.brightness, mode="gradient",
                       gradient=(a.color_a, a.color_b))
            print(f"gradient #{a.color_a.lower()} -> #{a.color_b.lower()} @ {a.brightness}%")
        elif a.cmd == "apply-saved":
            st = load_state()
            if not st:
                print("no saved lighting state")
                return 0
            meta = load_meta()
            zones, br = st

            def _assert_saved():
                if meta["mode"] in EFFECT_MODES:
                    set_effect(meta["mode"], meta["speed"], brightness=br)
                elif meta["mode"] == "gradient" and meta["gradient"]:
                    set_gradient(meta["gradient"][0], meta["gradient"][1], br)
                else:
                    set_zones(zones, br)

            # Boot/resume: the controller may still be settling. Re-assert a
            # few times spaced out; _run already double-writes each call.
            last = None
            for i in range(3):
                if i:
                    time.sleep(1.5)
                try:
                    _assert_saved()
                    last = None
                except ElcError as exc:
                    last = exc
            if last:
                raise last
            print(f"re-applied saved lighting ({meta['mode']}) @ {br}%")
    except ElcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
