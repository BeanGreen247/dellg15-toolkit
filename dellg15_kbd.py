#!/usr/bin/env python3
"""Alienware AW-ELC keyboard-backlight control for the Dell G15 5515.

Why this exists
---------------
The 5515's per-zone RGB keyboard is the "AW-ELC" controller (USB 187c:0550),
bound to hid-generic with no kernel driver — so Linux has no `kbd_backlight`
LED, no UPower KbdBacklight, and no KDE brightness control. The backlight
lights in BIOS/POST but goes dark once the OS takes over, because nothing
tells the controller to turn on.

OpenRGB *detects* the controller but has the wrong zone count for this exact
model: its quirk table lists the G15 5511 (platform 0x0E03) and 5530
(0x0E0A) as 4-zone, but not the 5515 (0x0E05), so it drives it as 16 zones
and every packet lands misaligned. It also defaults "dim" (brightness) to 0.

This module speaks the AW-ELC feature-report protocol directly over
/dev/hidraw*, with the correct 4 zones (Left / Middle / Right / Numpad) and
an explicit brightness, using only the Python standard library (fcntl ioctl).
Protocol reverse-engineered by the OpenRGB project (GPL-2.0-or-later,
Controllers/AlienwareController); this is a clean-room stdlib reimpl of the
same packet layout.

CLI
---
    dellg15_kbd.py on  [--color RRGGBB] [--brightness 0-100]
    dellg15_kbd.py off
    dellg15_kbd.py zone <0-3> --color RRGGBB [--brightness 0-100]
    dellg15_kbd.py info
"""
from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import sys
import time

VID = 0x187C
PIDS = (0x0550, 0x0551)

REPORT_LEN = 34  # 1 report-id byte + 33 payload (hidapi convention)

# ---- commands (usb_buf[2]) ----
CMD_REPORT = 0x20
CMD_USER_ANIM = 0x21
CMD_SELECT_ZONES = 0x23
CMD_ADD_ACTION = 0x24
CMD_DIM = 0x26
CMD_RESET = 0x28

# REPORT subcommands (usb_buf[3])
REPORT_FIRMWARE = 0x00
REPORT_CONFIG = 0x02

# USER_ANIM subcommands (16-bit, usb_buf[3..4])
ANIM_NEW = 0x0001
ANIM_FINISH_PLAY = 0x0003
ANIM_KEYBOARD = 0xFFFF  # non-saved "live" animation slot

MODE_COLOR = 0x00
TEMPO_MAX = 0x00FA
DURATION_2000 = 2000

ZONE_NAMES = ["Left", "Middle", "Right", "Numpad"]
ZONE_COUNT = 4
GKEY_ZONE = 0  # the G-key sits in the leftmost zone


def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


def _HIDIOCSFEATURE(size: int) -> int:  # noqa: N802
    return _ioc(3, 0x06, size)


def _HIDIOCGFEATURE(size: int) -> int:  # noqa: N802
    return _ioc(3, 0x07, size)


class ElcError(RuntimeError):
    pass


class Keyboard:
    def __init__(self, path: str | None = None):
        self.path = path or self._find()
        if not self.path:
            raise ElcError(
                "AW-ELC keyboard (187c:0550) not found. Check `lsusb` and that "
                "your user can read /dev/hidraw* (input group / uaccess)."
            )
        try:
            self.fd = open(self.path, "wb+", buffering=0)
        except PermissionError as exc:
            raise ElcError(
                f"No permission to open {self.path} — add your user to the "
                "'input' group (log out/in) or run as root."
            ) from exc

    @staticmethod
    def _find() -> str | None:
        for hidraw in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
            try:
                uevent = open(f"{hidraw}/device/uevent").read()
            except OSError:
                continue
            # HID_ID=0003:0000187C:00000550
            for line in uevent.splitlines():
                if line.startswith("HID_ID="):
                    parts = line.split(":")
                    if len(parts) == 3:
                        vid = int(parts[1], 16) & 0xFFFF
                        pid = int(parts[2], 16) & 0xFFFF
                        if vid == VID and pid in PIDS:
                            return "/dev/" + hidraw.rsplit("/", 1)[1]
        return None

    def close(self) -> None:
        try:
            self.fd.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- raw transport ----

    def _send(self, payload: bytes, *, settle: float = 0.06) -> None:
        buf = bytearray(REPORT_LEN)
        buf[0] = 0x00           # report id (unnumbered)
        buf[1] = 0x03           # AW-ELC packet marker
        buf[2 : 2 + len(payload)] = payload
        fcntl.ioctl(self.fd, _HIDIOCSFEATURE(REPORT_LEN), bytes(buf))
        time.sleep(settle)      # the controller crashes if commands are spammed

    def _recv(self) -> bytes:
        buf = bytearray(REPORT_LEN)
        buf[0] = 0x00
        fcntl.ioctl(self.fd, _HIDIOCGFEATURE(REPORT_LEN), buf, True)
        return bytes(buf)

    def _report(self, subcommand: int) -> bytes:
        self._send(bytes([CMD_REPORT, subcommand]))
        return self._recv()

    # ---- introspection ----

    def info(self) -> dict:
        cfg = self._report(REPORT_CONFIG)
        fw = self._report(REPORT_FIRMWARE)
        # Response layout: [0]=report id, [1]=0x03, [2]=cmd echo, [3]=subcmd
        # echo, [4..5]=platform id, [6]=zone count (see OpenRGB AlienwareController).
        platform_id = (cfg[4] << 8) | cfg[5]
        reported_zones = cfg[6]
        return {
            "device": self.path,
            "platform_id": f"0x{platform_id:04X}",
            "reported_zones": reported_zones,
            "driven_zones": ZONE_COUNT,
            "firmware": f"{fw[4]}.{fw[5]}.{fw[6]}",
        }

    # ---- lighting ----

    def _anim(self, subcommand: int) -> None:
        long_wait = subcommand == ANIM_FINISH_PLAY
        self._send(
            bytes([
                CMD_USER_ANIM,
                subcommand >> 8, subcommand & 0xFF,
                ANIM_KEYBOARD >> 8, ANIM_KEYBOARD & 0xFF,
                0x00, 0x00,          # duration
            ]),
            settle=1.0 if long_wait else 0.06,
        )
        self._recv()

    def _select_zone(self, zone: int) -> None:
        self._send(bytes([CMD_SELECT_ZONES, 0x01, 0x00, 0x01, zone]))
        self._recv()

    def _add_color_action(self, rgb: tuple[int, int, int]) -> None:
        r, g, b = rgb
        self._send(bytes([
            CMD_ADD_ACTION,
            MODE_COLOR,
            DURATION_2000 >> 8, DURATION_2000 & 0xFF,
            TEMPO_MAX >> 8, TEMPO_MAX & 0xFF,
            r, g, b,
        ]))
        self._recv()

    def _dim(self, raw: int, zones: list[int]) -> None:
        """raw is the controller's DIM value: 0 = full brightness,
        100 = fully dimmed (off). Inverted vs. how humans think about it —
        see set_brightness()."""
        raw = max(0, min(100, raw))
        self._send(bytes([CMD_DIM, raw, 0x00, len(zones), *zones]))
        self._recv()

    def set_brightness(self, percent: int, zones: list[int] | None = None) -> None:
        """percent: 0 = off, 100 = full. Converts to the controller's
        inverted DIM scale."""
        z = zones if zones is not None else list(range(ZONE_COUNT))
        self._dim(100 - max(0, min(100, percent)), z)

    def set_zones(self, colors: dict[int, tuple[int, int, int]], brightness: int = 100) -> None:
        """colors: {zone_index: (r, g, b)}. brightness: 0 (off) .. 100 (full)."""
        self._anim(ANIM_NEW)
        for zone in sorted(colors):
            self._select_zone(zone)
            self._add_color_action(colors[zone])
        self._anim(ANIM_FINISH_PLAY)
        self.set_brightness(brightness, sorted(colors) or list(range(ZONE_COUNT)))

    def set_all(self, rgb: tuple[int, int, int], brightness: int = 100) -> None:
        self.set_zones({z: rgb for z in range(ZONE_COUNT)}, brightness)

    def off(self) -> None:
        self.set_brightness(0)


# --------------------------------------------------------------------------- #

def _state_path() -> str:
    """Where the last-applied lighting is saved so a login/resume service can
    re-assert it (the controller forgets it on reboot/replug). Honours SUDO_USER
    so the GUI, running elevated, still writes to the real user's config."""
    override = os.environ.get("DELLG15_KBD_STATE")
    if override:
        return override
    user = os.environ.get("SUDO_USER") or os.environ.get("PKEXEC_USER")
    if user:
        import pwd
        home = pwd.getpwnam(user).pw_dir
    else:
        home = os.path.expanduser("~")
    return os.path.join(home, ".config", "dellg15-toolkit", "kbd.json")


def save_state(zone_colors: dict[int, tuple[int, int, int]], brightness: int) -> None:
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "brightness": int(brightness),
        "zones": {str(z): list(c) for z, c in zone_colors.items()},
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    user = os.environ.get("SUDO_USER") or os.environ.get("PKEXEC_USER")
    if user and os.geteuid() == 0:
        import pwd
        pw = pwd.getpwnam(user)
        os.chown(path, pw.pw_uid, pw.pw_gid)
        os.chown(os.path.dirname(path), pw.pw_uid, pw.pw_gid)


def load_state() -> tuple[dict[int, tuple[int, int, int]], int] | None:
    try:
        with open(_state_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    zones = {int(z): tuple(c) for z, c in data.get("zones", {}).items()}
    if not zones:
        return None
    return zones, int(data.get("brightness", 100))


def _parse_hex(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    if len(s) != 6:
        raise argparse.ArgumentTypeError("color must be RRGGBB hex")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def contrasting_color(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Pick a high-contrast accent for the given backlight colour: red for
    G-Mode by convention, unless the base is already reddish, in which case
    cyan."""
    r, g, b = rgb
    if r > 140 and g < 90 and b < 90:
        return (0, 200, 255)   # base is red-ish -> cyan
    return (255, 0, 0)         # G-Mode red


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_on = sub.add_parser("on", help="static colour on the whole keyboard")
    p_on.add_argument("--color", type=_parse_hex, default=(255, 255, 255))
    p_on.add_argument("--brightness", type=int, default=100)

    sub.add_parser("off", help="turn the backlight off (dim 0)")

    p_z = sub.add_parser("zone", help="set one zone (0=Left 1=Middle 2=Right 3=Numpad)")
    p_z.add_argument("index", type=int, choices=range(ZONE_COUNT))
    p_z.add_argument("--color", type=_parse_hex, required=True)
    p_z.add_argument("--brightness", type=int, default=None,
                     help="0-100; defaults to the last saved brightness, or 100")

    sub.add_parser("info", help="print platform id / firmware / zone count")
    sub.add_parser("apply-saved", help="re-apply the last saved lighting (for a login/resume service)")

    args = p.parse_args(argv)

    try:
        with Keyboard() as kb:
            if args.cmd == "info":
                for k, v in kb.info().items():
                    print(f"{k:16}: {v}")
            elif args.cmd == "on":
                kb.set_all(args.color, args.brightness)
                save_state({z: args.color for z in range(ZONE_COUNT)}, args.brightness)
                print(f"backlight on: #{args.color[0]:02x}{args.color[1]:02x}{args.color[2]:02x} @ {args.brightness}%")
            elif args.cmd == "off":
                kb.off()
                print("backlight off")
            elif args.cmd == "zone":
                st = load_state()
                colors = st[0] if st else {z: (255, 255, 255) for z in range(ZONE_COUNT)}
                brightness = args.brightness if args.brightness is not None else (st[1] if st else 100)
                colors[args.index] = args.color
                kb.set_zones(colors, brightness)
                save_state(colors, brightness)
                print(f"zone {args.index} ({ZONE_NAMES[args.index]}) set @ {brightness}%")
            elif args.cmd == "apply-saved":
                st = load_state()
                if not st:
                    print("no saved lighting state — nothing to apply")
                    return 0
                colors, brightness = st
                kb.set_zones(colors, brightness)
                print(f"re-applied saved lighting @ {brightness}%")
    except ElcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
