# CLAUDE.md — Dell G15 5515 Toolkit

Context for AI assistants working on this repo. Human-facing docs are in `README.md`.

## What this is

A checkbox GUI + tray monitor + G-key listener that applies **hardware-specific
tweaks, drivers and gaming setup** to **one** machine: the **Dell G15 5515 Ryzen
Edition** (Ryzen 7 5800H + RTX 3050 Ti Mobile) running **Nobara Linux** (Fedora
43 base, `dnf`, KDE Plasma / Wayland). It is *not* a general distro tool — every
`check`/`apply` command in `config/*.json` is written against that board.

## Layout

| File | Role |
|---|---|
| `dellg15_toolkit.py` | the GUI (ttkbootstrap `darkly`). Tabs: Dashboard, Keyboard, Presets, one per tweak/app category. Self-elevates via `pkexec`→`sudo`. |
| `tray_monitor.py` | PySide6 system-tray equivalent + `--toggle`. |
| `hotkey_listener.py` | `systemd --user` service, reads the G-key from evdev, toggles Game Mode. |
| `sensors.py` | **shared, no GUI deps.** Sensor reads + `set_game_mode()` + `notify()` + `detect_model()`. Used by all three above. |
| `dellg15_kbd.py` | standalone stdlib driver for the AW-ELC RGB keyboard (see quirks). |
| `dellg15_automount.py` | scans `lsblk`, adds `/etc/fstab` entries mounting fixed internal data disks at `/mnt/<label>` with `nofail`. |
| `config/tweaks.json`, `apps.json`, `presets.json` | the data. Tweaks have `check` / `check_pending` (staged-but-needs-reboot) / `apply` / `undo`. `{USER}` and `{TOOLKIT_DIR}` are substituted. |
| `install.sh` | system-wide install → `/opt/dellg15-toolkit`, launcher, hicolor icon, `/usr/share/applications` desktop entry. `--uninstall` reverses it. |
| `assets/` | `icon.svg` (flaming tachometer on a wine plate) + rendered PNGs. |

## Working on the real hardware

The actual laptop is reachable as **`ssh g15`** (SSH host alias; hostname
`Ashblade`, user `bean`). Workflow used during development:

```bash
rsync -az --exclude=.git ./ g15:~/DellG15Toolkit/     # push source
ssh g15 'cd ~/DellG15Toolkit && sudo ./install.sh'    # system-install to /opt
```

- Passwordless sudo on the box is via `/etc/sudoers.d/claude-test` (`bean
  ALL=(ALL) NOPASSWD:ALL`) — the **user adds/removes it on request**; don't
  assume it's present.
- GUI smoke without a real display: monkeypatch `t.self_elevate = lambda: None`,
  build `ToolkitApp` on a `tb.Window`, pump `root.update()` in a loop. Run as
  root with `DISPLAY=:0 XAUTHORITY=$(ls -t /run/user/1000/xauth* | head -1)`.

## Hardware facts learned the hard way

- **G-key** = evdev `KEY_PERFORMANCE` (701) on `"AT Translated Set 2 keyboard"`
  (`/dev/input/event2`) when Fn-Lock is **off**; `KEY_F9` when on (or Fn+F9 with
  lock on → `KEY_PERFORMANCE`). Fn is an EC key, never reaches evdev.
- **Game Mode / "G-Mode"** = the `performance` **`platform_profile`** (driver
  `alienware-wmi`). Kernel docs: performance profile toggles firmware G-Mode.
  `gaming-performance`/`gaming-balanced` also slam `alienware_wmi` `fanN_boost`
  (hwmon) to 255/0 for the AWCC-style fan.
- **RGB keyboard** = Alienware **AW-ELC** (USB `187c:0550`), `hid-generic`, no
  kernel driver. Its platform id is **`0x0E05`** — *missing from OpenRGB's
  quirk table*, so OpenRGB drives 16 zones when it has **4**
  (Left/Middle/Right/Numpad), and its "dim" value is **inverted** (0 = full,
  100 = off). `dellg15_kbd.py` speaks the feature-report protocol directly.
- **`dnf` GPG**: Nobara serves some rawhide-based (`.fc44`) packages signed
  with the **Fedora 44** key, which ships on disk un-imported. Fix once with
  `sudo rpm --import /etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-44-primary`.
- **RAPL** (`/sys/class/powercap/*/energy_uj`) is already world-readable on
  Nobara 43 — the `RaplPowerPermissions` tweak is usually a no-op there.
- Nobara ships `nobara-automount` (transient `/run/media` model) — this repo
  uses fstab instead for a stable path.
- The user is **not** in `input` by default (KDE); `HotkeyListener` adds them
  and must `systemctl --user` as the user with `XDG_RUNTIME_DIR` set, **not**
  `systemctl --user -M user@` (that fails to create the enable symlink).

## Conventions

- Commit messages: subject + body only. **No `Co-Authored-By` / attribution
  trailers** (per the user's global rule).
- Feature branches → merge to `main` (`--no-ff`), delete the branch.
- Helper `check`/`apply` scripts must `exit 0` on harmless noise — a non-zero
  exit from e.g. `nvidia-settings` used to surface as "Game Mode failed".
- Tk is not thread-safe: worker threads hand results back via a `queue` + an
  `after()` poller (see `status_queue` / `_poll_status_queue`).
