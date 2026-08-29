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
| `dellg15_toolkit.py` | the GUI. ttkbootstrap `darkly` re-skinned into a dark "gaming-BIOS" look with the KDE accent colour (`apply_bios_style` / `read_desktop_accent`). **Left sidebar nav** (`SidebarNav`, a drop-in for `tb.Notebook`), not a top tab strip. Pages: Dashboard, Keyboard, Fans, Presets, Updates, then one per tweak/app category (Gaming first). App-wide "busy" modal overlay with a two-bar (overall + current-task) progress display + elapsed timer (`_begin_busy` / `_poll_busy_queue`). Self-elevates via `pkexec`→`sudo`. |
| `tray_monitor.py` | PySide6 system-tray equivalent + `--toggle`. |
| `hotkey_listener.py` | `systemd --user` service, reads the G-key from evdev, toggles Game Mode. |
| `sensors.py` | **shared, no GUI deps.** Sensor reads + `set_game_mode()` + `notify()` + `detect_model()` + **fan control** (`read_fans`, `get/set_fan_boost`, `*_platform_profile`, `get_pwm_state`, `set_pwm_manual`, `restore_fan_auto`) + `dgpu_is_awake()`. `which()` is `lru_cache`d. |
| `dellg15_kbd.py` | AW-ELC RGB keyboard driver: an `openrgb` CLI wrapper for static/zone colours + firmware effects, **plus** stdlib software animation daemons (`rainbow_wave`, `gradient_wave`) that stream per-LED frames over a hand-rolled OpenRGB SDK socket client (`_Sdk`). Detached daemons tracked by `<statedir>/fx.pid` + `stop_fx()`. |
| `dellg15_automount.py` | scans `lsblk`, adds `/etc/fstab` entries mounting fixed internal data disks at `/mnt/<label>` with `nofail`. |
| `config/tweaks.json`, `apps.json`, `presets.json` | the data. Tweaks have `check` / `check_pending` (staged-but-needs-reboot) / `apply` / `undo`. `{USER}` and `{TOOLKIT_DIR}` are substituted. |
| `install.sh` | system-wide install → `/opt/dellg15-toolkit`, launcher, hicolor icon, `/usr/share/applications` desktop entry. `--uninstall` removes just the app. |
| `uninstall.sh` | remove the tool (default: app + per-user config, tweaks kept). `--purge` also undoes every tweak's system bits (services, helper scripts, sudoers, drop-ins); `--grub` / `--fstab` / `--pip` / `--all` for the boot-affecting extras. Never touches installed apps. |
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
- **RGB keyboard = OpenRGB only.** Alienware **AW-ELC** (USB `187c:0550`),
  `hid-generic`, no kernel driver, no SMBIOS keyboard tokens (`location=0xffff`
  → no `kbd_backlight` LED, dead Fn key), mainline `alienware-wmi` has no RGB.
  Hand-rolled HID writes are ACK'd but never light up. What works — verified
  live — is **OpenRGB** (`openrgb -d "Dell G Series LED Controller" -m Static
  -c RRGGBB -b …`), 16 logical LEDs. **Prereqs:** OpenRGB installed + backlight
  **enabled in BIOS setup** (off by default). 4 physical zones
  (Left/Middle/Right/Numpad) = blocks of 4 logical LEDs.
- **Keyboard brightness is inverted / degenerate.** Every mode reports
  `brightness_min=100 / brightness_max=0`. Empirically **`-b 100` lights the
  backlight**, lower values dim it, `-b 0` = off. (A "fix" that special-cased
  `-b 0` for effects turned them off — reverted.)
- **The controller repaints only ~2–4×/sec over USB.** The OpenRGB *server*
  ingests 60 fps in microseconds, but the device shows a handful of frames/sec
  — measured live. So a **software** per-LED wave (`rainbow_wave` /
  `gradient_wave` streaming `UPDATE_LEDS` over `_Sdk`) can only be a **slow
  ambient** effect: capped at `_HW_MAX_FPS` (5), frame-skip, ~24 s cycles.
  For a **smooth, fast** rainbow use the **firmware Spectrum Cycle** mode
  (`set_effect("spectrum", …)`) — it runs on the MCU. The firmware *Rainbow
  Wave* mode is washed-out with dark gaps and takes no colour/direction —
  unusable. The GUI "Rainbow Cycle" button = firmware Spectrum Cycle;
  "Gradient wave" = the slow software daemon (1–6 OKLab anchor colours, or a
  1-anchor comet).
- **Colour persistence** is opt-in via the `KbdBacklightFix` tweak (installs
  `dellg15-openrgb.service` SDK server + `dellg15-kbd.service` `apply-saved` at
  boot + a systemd-sleep hook). State: `~/.config/dellg15-toolkit/kbd.json`
  (`mode`/`speed`/`zones`/optional `gradient` block). **`dellg15_kbd._state_path()`
  must resolve the real user via `PKEXEC_UID`/`SUDO_UID` too** — pkexec sets no
  `PKEXEC_USER`, so a naive `~` lands in `/root` and the boot service never
  sees the GUI's saves (this was the "colour doesn't persist" bug).
- **Backlight "freezes"** = the OpenRGB SDK server wedged after many mode
  changes (CLI still exits 0, hardware stuck). Fix: restart
  `dellg15-openrgb` — `dellg15_kbd.restart_server()` / `reset()`, GUI "↻ Reset
  backlight" button.
- **Fans (5515):** `hwmon/alienware_wmi` has `fan{1,2}_input` (RPM, ro),
  `fan{1,2}_label` = CPU/GPU Fan, and **`fan{1,2}_boost` 0–255 RW** — an
  *additive* AWCC-style boost (can't slow a fan below the auto curve → the
  safe lever). `hwmon/dell_smm` has `pwm{1,2}` + `pwm{1,2}_enable`
  (0 full / 1 manual / 2 auto; reads "No data" in auto) — real manual control,
  risky, floored at `sensors.PWM_FLOOR` (77). Thermal profile =
  `/sys/firmware/acpi/platform_profile` (`balanced performance custom`). The
  Fans tab exposes profile + boost sliders + presets, with manual PWM behind
  a warning.
- **`dnf` GPG**: Nobara serves some rawhide-based (`.fc44`) packages signed
  with the **Fedora 44** key, which ships on disk un-imported. Fix once with
  `sudo rpm --import /etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-44-primary` (the
  Updates tab has a "Fix Fedora GPG keys" button).
- **Updates tab** wraps `nobara-sync` (check-updates / `cli` / install-updates
  / install-fixups / repair) + per-manager sections (dnf, Flatpak, fwupd) +
  an "Update everything". The pending-update counter uses `dnf -q --cacheonly
  check-update` — a plain `dnf check-update` can take **3+ min** on this box's
  mirrors, so the count is "as of last metadata sync" and self-corrects after
  any check/update.
- **RAPL** (`/sys/class/powercap/*/energy_uj`) is already world-readable on
  Nobara 43 — the `RaplPowerPermissions` tweak is usually a no-op there.
- Nobara ships `nobara-automount` (transient `/run/media` model) — this repo
  uses fstab instead for a stable path.
- The user is **not** in `input` by default (KDE); `HotkeyListener` adds them
  and must `systemctl --user` as the user with `XDG_RUNTIME_DIR` set, **not**
  `systemctl --user -M user@` (that fails to create the enable symlink).

## Conventions

- Commit messages: **no `Co-Authored-By` / attribution / session trailers of
  any kind** (per the user's global rule) — the message is just the change.
- Helper `check`/`apply` scripts must `exit 0` on harmless noise — a non-zero
  exit from e.g. `nvidia-settings` used to surface as "Game Mode failed".
- Tk is not thread-safe: worker threads hand results back via a `queue` + an
  `after()` poller (see `status_queue` / `_poll_status_queue` /
  `_poll_busy_queue`). The keyboard software daemons run as **detached**
  processes, not threads.
- **Item status** = `evaluate_item()` sets `item.state` (one of `applied`,
  `not_applied`, `pending`, `error`, `drifted`, `failed`, `unsupported`).
  `applied`/`pending`/`done` are derived read-only properties. The per-tweak
  `check` command is still authoritative for *current* state; the **apply
  ledger** (`~/.config/dellg15-toolkit/state.json`, written by
  `ledger_record()` from `_run_item_apply`/`_run_item_undo`) adds "we set this"
  → `drifted` (we applied it, check now fails) and `failed` (our last attempt
  errored). "Status report" button / `python3 dellg15_toolkit.py --report`
  print the full table (`format_status_report`).
- Colour maths in `dellg15_kbd.py` is **stdlib only** (`colorsys` + a small
  sRGB↔linear↔OKLab↔OKLCH set) — no numpy/Pillow.
- No-hardware self-tests: `dellg15_kbd.py rainbow-test` / `gradient-test`.
