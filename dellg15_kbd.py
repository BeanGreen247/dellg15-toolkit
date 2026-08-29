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

CLI (unchanged from before, so the Keyboard tab / KbdBacklightFix keep working):
    dellg15_kbd.py on  [--color RRGGBB] [--brightness 0-100]
    dellg15_kbd.py off
    dellg15_kbd.py zone <0-3> --color RRGGBB [--brightness 0-100]
    dellg15_kbd.py apply-saved
    dellg15_kbd.py info
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


def save_state(zone_colors: dict, brightness: int) -> None:
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"brightness": int(brightness),
               "zones": {str(z): _hexify(c) for z, c in zone_colors.items()}},
              open(path, "w"), indent=2)
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
        elif a.cmd == "apply-saved":
            st = load_state()
            if not st:
                print("no saved lighting state")
                return 0
            # Boot/resume: the controller may still be settling. Re-assert a
            # few times spaced out; _run already double-writes each call.
            last = None
            for i in range(3):
                if i:
                    time.sleep(1.5)
                try:
                    set_zones(*st)
                    last = None
                except ElcError as exc:
                    last = exc
            if last:
                raise last
            print(f"re-applied saved lighting @ {st[1]}%")
    except ElcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
