#!/usr/bin/env python3
"""Dell G15 dedicated G-key listener — toggles Game Mode on press.

Confirmed on the G15 5515 Ryzen Edition: with Fn Lock OFF, the G-key sends
evdev keycode KEY_PERFORMANCE (701) on the "AT Translated Set 2 keyboard"
device — a distinct, purpose-built Linux keycode, not aliased to anything
else, so no udev remap is needed (unlike DAMX's Acer button, which needed
one). With Fn Lock ON the same key instead sends plain KEY_F9, which this
listener deliberately ignores — it only acts on KEY_PERFORMANCE, so it can
never hijack a real F9 press elsewhere on the keyboard.

Runs as a systemd --user service (installed by the HotkeyListener tweak in
config/tweaks.json). Needs python3-evdev only — NOT PySide6, unlike
tray_monitor.py, since this never draws anything. Reading the keyboard
device only needs 'input' group membership (usually already granted to the
desktop user); the actual toggle re-uses sensors.set_game_mode(), which
tries passwordless sudo first (see the PasswordlessGameModeToggle tweak)
and falls back to a GUI pkexec prompt.
"""
import sys
import time
from pathlib import Path

try:
    import evdev
    from evdev import ecodes
except ImportError:
    print("python3-evdev not found. Install with: dnf install python3-evdev")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sensors import toggle_game_mode_external  # noqa: E402

TARGET_KEYCODE = ecodes.KEY_PERFORMANCE  # 701
DEVICE_NAME_HINTS = ("AT Translated Set 2 keyboard",)


def find_device():
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if dev.name in DEVICE_NAME_HINTS:
            return dev
    return None


def main():
    while True:
        dev = find_device()
        if dev is None:
            print("Keyboard device not found, retrying in 5s...")
            time.sleep(5)
            continue

        print(f"Listening on {dev.path} ({dev.name}) for KEY_PERFORMANCE...")
        try:
            for event in dev.read_loop():
                if event.type != ecodes.EV_KEY:
                    continue
                if event.code != TARGET_KEYCODE:
                    continue
                if event.value != 1:  # only on key-down, ignore repeat/release
                    continue
                ok, err = toggle_game_mode_external()
                if not ok:
                    print(f"Toggle failed: {err}")
        except OSError:
            print("Device disconnected, retrying...")
            time.sleep(2)


if __name__ == "__main__":
    main()
