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
import socket
import struct
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


# ------------------------------------------------------------------------- #
#  OpenRGB SDK (network protocol) client — hand-rolled, stdlib only.
#
#  The `openrgb` CLI can't set an effect's *direction* and maps speed coarsely.
#  Talking the SDK server directly (dellg15-openrgb.service, :6742) lets us
#  read a mode's real speed/direction/brightness ranges and write them back,
#  and push a true per-LED gradient. Protocol ref: OpenRGB NetworkProtocol
#  (this build negotiates v5).
# ------------------------------------------------------------------------- #

_SDK_HOST, _SDK_PORT = "127.0.0.1", 6742
_PKT_CONTROLLER_COUNT = 0
_PKT_CONTROLLER_DATA = 1
_PKT_PROTOCOL_VERSION = 40
_PKT_SET_CLIENT_NAME = 50
_PKT_UPDATE_LEDS = 1050
_PKT_UPDATE_MODE = 1054

MODE_FLAG_HAS_SPEED = 1 << 0
MODE_FLAG_HAS_DIR_LR = 1 << 1
MODE_FLAG_HAS_DIR_UD = 1 << 2
MODE_FLAG_HAS_DIR_HV = 1 << 3
MODE_FLAG_HAS_BRIGHTNESS = 1 << 4
MODE_FLAG_HAS_PER_LED = 1 << 5
_MODE_FLAG_ANY_DIR = MODE_FLAG_HAS_DIR_LR | MODE_FLAG_HAS_DIR_UD | MODE_FLAG_HAS_DIR_HV

# direction constants (OpenRGB mode_direction)
DIR_LEFT, DIR_RIGHT, DIR_UP, DIR_DOWN, DIR_HORIZONTAL, DIR_VERTICAL = range(6)
DIRECTIONS = {"left": DIR_LEFT, "right": DIR_RIGHT, "up": DIR_UP,
              "down": DIR_DOWN, "horizontal": DIR_HORIZONTAL, "vertical": DIR_VERTICAL}


class _Sdk:
    def __init__(self, timeout: float = 4.0):
        self.sock = socket.create_connection((_SDK_HOST, _SDK_PORT), timeout)
        self.sock.settimeout(timeout)
        self._send(0, _PKT_SET_CLIENT_NAME, b"dellg15-toolkit\0")
        self._send(0, _PKT_PROTOCOL_VERSION, struct.pack("<I", 5))
        _, _, body = self._recv()
        self.version = min(5, struct.unpack("<I", body[:4])[0]) if len(body) >= 4 else 1

    # -- framing --
    def _send(self, dev: int, pid: int, data: bytes = b"") -> None:
        self.sock.sendall(b"ORGB" + struct.pack("<III", dev, pid, len(data)) + data)

    def _recvn(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ElcError("OpenRGB server closed the connection")
            buf += chunk
        return buf

    def _recv(self):
        hdr = self._recvn(16)
        if hdr[:4] != b"ORGB":
            raise ElcError("bad reply from OpenRGB server")
        dev, pid, size = struct.unpack("<III", hdr[4:16])
        return dev, pid, self._recvn(size)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- high level --
    def _count(self) -> int:
        self._send(0, _PKT_CONTROLLER_COUNT)
        _, _, body = self._recv()
        return struct.unpack("<I", body[:4])[0]

    def _controller(self, idx: int) -> bytes:
        self._send(idx, _PKT_CONTROLLER_DATA, struct.pack("<I", self.version))
        _, _, body = self._recv()
        return body

    def find(self, name_sub: str) -> dict:
        for i in range(self._count()):
            dev = _parse_controller(self._controller(i), self.version)
            if name_sub.lower() in dev["name"].lower():
                dev["index"] = i
                return dev
        raise ElcError(f"OpenRGB server has no device matching {name_sub!r}")

    def set_mode(self, idx: int, mode: dict) -> None:
        self._send(idx, _PKT_UPDATE_MODE, _pack_mode(mode, self.version, idx))

    def update_leds(self, idx: int, rgb_list: list) -> None:
        data = struct.pack("<H", len(rgb_list))
        for r, g, b in rgb_list:
            data += struct.pack("<BBBB", r, g, b, 0)
        self._send(idx, _PKT_UPDATE_LEDS, struct.pack("<I", len(data) + 4) + data)


def _parse_controller(b: bytes, version: int) -> dict:
    off = [0]

    def u(fmt: str):
        v = struct.unpack_from("<" + fmt, b, off[0])
        off[0] += struct.calcsize("<" + fmt)
        return v

    def rstr() -> str:
        (ln,) = u("H")
        s = b[off[0]:off[0] + ln]
        off[0] += ln
        return s.split(b"\0", 1)[0].decode("utf-8", "replace")

    u("I")            # data_size
    u("I")            # device type (4 bytes on this protocol build)
    name = rstr()
    if version >= 1:
        rstr()        # vendor
    rstr(); rstr(); rstr(); rstr()   # description, version, serial, location
    (num_modes,) = u("H")
    (active_mode,) = u("i")
    modes = []
    for _ in range(num_modes):
        m = {"name": rstr()}
        m["value"], m["flags"], m["speed_min"], m["speed_max"] = u("iIII")
        if version >= 3:
            m["brightness_min"], m["brightness_max"] = u("II")
        m["colors_min"], m["colors_max"] = u("II")
        (m["speed"],) = u("I")
        if version >= 3:
            (m["brightness"],) = u("I")
        m["direction"], m["color_mode"], ncol = u("IIH")
        m["colors"] = [u("BBBB")[:3] for _ in range(ncol)]
        modes.append(m)
    return {"name": name, "active_mode": active_mode, "modes": modes}


def _pack_mode(m: dict, version: int, idx: int) -> bytes:
    raw = m["name"].encode("utf-8") + b"\0"
    body = struct.pack("<I", idx)
    body += struct.pack("<H", len(raw)) + raw
    body += struct.pack("<iIII", m["value"], m["flags"], m["speed_min"], m["speed_max"])
    if version >= 3:
        body += struct.pack("<II", m.get("brightness_min", 0), m.get("brightness_max", 0))
    body += struct.pack("<II", m["colors_min"], m["colors_max"])
    body += struct.pack("<I", m["speed"])
    if version >= 3:
        body += struct.pack("<I", m.get("brightness", m.get("brightness_max", 0)))
    body += struct.pack("<IIH", m["direction"], m["color_mode"], len(m.get("colors", [])))
    for r, g, bl in m.get("colors", []):
        body += struct.pack("<BBBB", r, g, bl, 0)
    return struct.pack("<I", len(body) + 4) + body


def _scale_into(pct: int, lo: int, hi: int) -> int:
    """Map a 0-100 % onto [lo, hi] (handles hi < lo, i.e. inverted ranges)."""
    pct = max(0, min(100, pct))
    return round(lo + (hi - lo) * (pct / 100.0))


def sdk_set_effect(mode_name: str, speed_pct: int | None = None,
                   direction: int | None = None, brightness_pct: int = 100) -> dict:
    """Switch the device to `mode_name` via the SDK, applying speed/direction/
    brightness within that mode's real ranges. Returns the mode dict used.
    Raises ElcError / OSError if the server isn't reachable."""
    with _Sdk() as c:
        dev = c.find(DEVICE)
        idx = next((i for i, m in enumerate(dev["modes"])
                    if m["name"].lower() == mode_name.lower()), None)
        if idx is None:
            raise ElcError(f"device has no mode {mode_name!r}")
        m = dev["modes"][idx]
        if speed_pct is not None and (m["flags"] & MODE_FLAG_HAS_SPEED):
            # On this controller the raw speed value is a delay: LOWER = FASTER
            # (Rainbow Wave range 15..250). Invert the % so the UI's "100%"
            # gives the fastest animation.
            m["speed"] = _scale_into(100 - speed_pct, m["speed_min"], m["speed_max"])
        if direction is not None and (m["flags"] & _MODE_FLAG_ANY_DIR):
            m["direction"] = int(direction)
        if m["flags"] & MODE_FLAG_HAS_BRIGHTNESS:
            m["brightness"] = _scale_into(brightness_pct, m.get("brightness_min", 0),
                                          m.get("brightness_max", 100))
        c.set_mode(dev["index"], m)
        return m


def sdk_set_leds(rgb_list: list) -> None:
    """Push explicit per-LED colours (used for the gradient). Sets the device
    to its first per-LED-capable mode first, then streams the colours."""
    with _Sdk() as c:
        dev = c.find(DEVICE)
        idx = next((i for i, m in enumerate(dev["modes"])
                    if m["flags"] & MODE_FLAG_HAS_PER_LED), None)
        if idx is None:
            idx = next((i for i, m in enumerate(dev["modes"])
                        if m["name"].lower() == "static"), 0)
        c.set_mode(dev["index"], dev["modes"][idx])
        c.update_leds(dev["index"], rgb_list)


def sdk_mode_caps(mode_name: str) -> dict | None:
    """Best-effort: what does this mode support? {'speed','direction'} bools +
    ranges. None if the server can't be reached."""
    try:
        with _Sdk() as c:
            dev = c.find(DEVICE)
            for m in dev["modes"]:
                if m["name"].lower() == mode_name.lower():
                    return {
                        "speed": bool(m["flags"] & MODE_FLAG_HAS_SPEED),
                        "direction": bool(m["flags"] & _MODE_FLAG_ANY_DIR),
                        "speed_min": m["speed_min"], "speed_max": m["speed_max"],
                    }
    except (OSError, ElcError, struct.error):
        return None
    return None


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
# through OpenRGB: Static Flashing Morph 'Spectrum Cycle' 'Rainbow Wave'
# Breathing. Verified live via the SDK: *none* of the six modes carries a
# direction flag, so direction genuinely can't be controlled on this board
# (not a CLI limitation — the firmware/plugin doesn't expose it). Speed is a
# raw firmware value in [15..250] for the wave modes where LOWER = FASTER, so
# the 0-100 % from the UI is inverted before it's mapped in.
EFFECT_MODES = {
    "rainbow": "Rainbow Wave",
    "spectrum": "Spectrum Cycle",
    "breathing": "Breathing",
    "flashing": "Flashing",
}


def set_effect(name: str, speed: int | None = None, color=None,
               brightness: int = 100) -> None:
    """Switch to a hardware effect. Prefers the SDK (real speed range, snappy);
    falls back to the openrgb CLI if the server isn't up."""
    mode = EFFECT_MODES.get(name, name)
    if speed is not None:
        speed = max(0, min(100, int(speed)))
    try:
        sdk_set_effect(mode, speed_pct=speed, brightness_pct=brightness)
        return
    except (OSError, ElcError, struct.error):
        pass
    args = ["-m", mode, "-b", str(max(0, min(100, brightness)))]
    if color is not None:
        args += ["-c", _hexify(color)]
    if speed is not None:
        # CLI's -s is 0-100 %; invert so higher % = faster, matching the SDK.
        args += ["-s", str(100 - speed)]
    _run(args)


def _to_rgb(c) -> tuple[int, int, int]:
    if isinstance(c, str):
        c = c.lstrip("#")
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    return tuple(c)


def set_gradient(color_a, color_b, brightness: int = 100) -> None:
    """Colour gradient from A (Left) to B (Numpad), interpolated across the
    device's LEDs. Uses the SDK's per-LED path — the CLI's -z form just shows
    a solid colour on this controller."""
    a, b = _to_rgb(color_a), _to_rgb(color_b)
    n = LOGICAL_ZONES
    leds = [tuple(round(a[i] + (b[i] - a[i]) * (k / (n - 1))) for i in range(3))
            for k in range(n)]
    try:
        sdk_set_leds(leds)
        return
    except (OSError, ElcError, struct.error):
        pass
    # last-ditch CLI attempt (may render solid)
    args = ["-m", "Static", "-b", str(max(0, min(100, brightness)))]
    for lz, mix in enumerate(leds):
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
