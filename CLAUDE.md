# CLAUDE.md — TuxThrottle

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
| `tuxthrottle.py` | the GUI. ttkbootstrap `darkly` re-skinned into a dark "gaming-BIOS" look with the KDE accent colour (`apply_bios_style` / `read_desktop_accent`). **Left sidebar nav** (`SidebarNav`, a drop-in for `tb.Notebook`), not a top tab strip. Pages: Dashboard, Keyboard, Fans, Presets, Updates, **Setup Games**, then one per tweak/app category (Gaming first), and **Report a Bug** last (detached at the rail bottom, amber `NavSupport` style — the log/GitHub-issue page). **Setup Games** (`_build_games_tab`, from `config/games.json`) is a real top `tb.Notebook` tab-strip, one page per game (GTA V Online first); each game is an ordered list of step cards with a per-step `check` (status pill: done ✓ / to do / manual / optional) and either a `▶ Run step` button (streams to the log via `_run_game_step`→`_run_stream`, same busy-overlay path as `_run_updates`) or a manual step with a "Mark done" toggle + optional `⧉ Copy command`; a `▶▶ Run all N automatic steps` header button chains the `run` steps (`_run_game_all`/`_game_all_worker`, skips already-done). Step results poll back on `_games_q` / `_poll_games_queue`. Above the notebook: a **Proton prefix tools** box (`_prefix_scan` / `_prefix_relocate_entry`) wrapping `tuxthrottle_prefix_relocate.py` — "Scan Steam prefixes" + an AppID field + "Relocate this prefix" for *any* Steam game, not just the walkthroughs. `{USER}`/`{TOOLKIT_DIR}`/`{APPID}` are substituted in games.json `check`/`run`/`copy` via `_game_subst`. App-wide "busy" modal overlay with a two-bar (overall + current-task) progress display + elapsed timer (`_begin_busy` / `_poll_busy_queue`). Self-elevates via `pkexec`→`sudo`. |
| `tray_monitor.py` | PySide6 system-tray equivalent + `--toggle`. |
| `hotkey_listener.py` | `systemd --user` service, reads the G-key from evdev, toggles Game Mode. |
| `sensors.py` | **shared, no GUI deps.** Sensor reads + `set_game_mode()` + `notify()` + `detect_model()` + **fan control** (`read_fans`, `get/set_fan_boost`, `*_platform_profile`, `get_pwm_state`, `set_pwm_manual`, `restore_fan_auto`) + `dgpu_is_awake()`. `which()` is `lru_cache`d. |
| `tuxthrottle_kbd.py` | AW-ELC RGB keyboard driver: an `openrgb` CLI wrapper. The 5515 keyboard is **one controllable zone** (see hardware notes) → whole-keyboard solid colour + brightness + the firmware **Spectrum Cycle** only. `set_zones`/`set_zone` collapse to `set_all`. The `rainbow_wave`/`gradient_wave` SDK-socket daemons (`_Sdk`, `<statedir>/fx.pid`, `stop_fx()`) are dead code kept only for the `*-test` self-checks. |
| `tuxthrottle_automount.py` | scans `lsblk`, adds `/etc/fstab` entries mounting fixed internal data disks at `/mnt/<label>` with `nofail`. |
| `config/tweaks.json`, `apps.json`, `presets.json` | the data. Tweaks have `check` / `check_pending` (staged-but-needs-reboot) / `apply` / `undo`. Apps have `manager` (`dnf`/`flatpak`/`shell`), `package`, `check`, optional `install`, and — for cross-manager "already installed" detection — optional `provides` (extra shell probes), `binary` (`command -v`), `flatpak_id`. `{USER}` and `{TOOLKIT_DIR}` are substituted. |
| `tuxthrottle_prefix_relocate.py` | stdlib helper. Prefix side: `<appid>` moves a Steam game's `compatdata/<appid>` Proton prefix off an NTFS/exFAT drive (which can't host it — `:` in `dosdevices/c:` → `OSError [Errno 22]`, game won't start) onto the native Steam library and symlinks it back; `--check` = exit 1 if it needs moving; `--scan` lists all; `--all` does all. Save side: `--saves-scan` finds prefix known-folders (Documents / Saved Games / AppData) symlinked onto another filesystem **and** loose `Documents`/`My Games` folders at a Steam drive's root (orphaned Wine redirects); `--saves <appid>` / `--saves-all` pull the symlinked ones back into the prefix; `--saves-import <appid>` copies a drive's loose Documents into that game's prefix. All copy-not-delete; refuse root or a running Steam. Wired into the GUI's **Proton prefix & save-file tools** box; also standalone. |
| `tuxthrottle_savevault.py` | stdlib helper (imports the Steam-library helpers from `tuxthrottle_prefix_relocate`). `list` / `export` / `import` a save-game **vault** — a folder on a drive that is *not* the home/OS filesystem (enforced by `st_dev` check), laid out `<vault>/<appid>/{Documents,Saved Games,AppData/Roaming,AppData/LocalLow}/…` + `.tuxthrottle-name`. `export`/`import` take an `<appid>` or `all`; `import` refuses while Steam runs. Wired to the **Save-game vault** row of the GUI's prefix/saves box; vault path persists in `~/.config/tuxthrottle/saves_vault`. |
| `config/games.json` | Setup Games data. `{gid: {Content, Tab, order, Description, steps:[…]}}`. Each step: `id`, `title`, `desc`, optional `check` (bash, exit 0 = done), then either `run` (bash streamed to the log; user-context bits go through `su - {USER} -c '…'`) or `manual: true` (+ optional `copy` string for the Copy button). `{USER}` / `{TOOLKIT_DIR}` substituted in `check`/`run`. Missing file → the page is skipped. GTA V Online steps mirror README "GTA V Online → Route A". |
| `install.sh` | system-wide install → `/opt/tuxthrottle`, launcher, hicolor icon, `/usr/share/applications` desktop entry. Also stamps `/opt/tuxthrottle/.version` (git describe). At the end it runs `apply_tweak.py <id> --only-if-present` for `KbdBacklightFix` + `CpuMaxPerformance` so an **already-enabled** service tweak's unit files + `/usr/local/bin` scripts get refreshed to the new version (no-op if the feature was never turned on). `--uninstall` removes just the app. |
| `apply_tweak.py` | runs one tweak's `apply` list from `config/tweaks.json` (`{USER}`/`{TOOLKIT_DIR}` substituted). `--only-if-present` = do nothing unless the tweak's `check` passes now OR a path in its `reinstall_if` glob-list exists; exits 3 when skipped. Used by `install.sh`; also standalone (`sudo python3 apply_tweak.py KbdBacklightFix`). |
| `uninstall.sh` | remove the tool (default: app + per-user config, tweaks kept). `--purge` also undoes every tweak's system bits (services, helper scripts, sudoers, drop-ins); `--grub` / `--fstab` / `--pip` / `--all` for the boot-affecting extras. Never touches installed apps. |
| `verify-install.sh` | read-only post-install sanity check — run on the target (`sudo ./verify-install.sh`): no legacy residue, `/opt/tuxthrottle` intact, launcher + `--report` + module self-tests OK, GUI builds with "Report a Bug" wired, hw-bundle prefix. Prints `N passed, M failed`. |
| `purge-legacy-dellg15.sh` | one-shot cleanup of a **pre-rebrand** install (`dellg15-*` units / bins / drop-ins / `~/.config/dellg15-toolkit` / menu entry). Only touches paths whose name contains `dellg15`; `--migrate-config` copies old `kbd.json`/`state.json` to `~/.config/tuxthrottle` first; `--dry-run` previews. Already run on g15. |
| `.github/ISSUE_TEMPLATE/` | GitHub bug-report template — asks for the output of the Report a Bug page / `--debug`. |
| `assets/` | `icon.svg` (Tux centred in a redlined throttle gauge, amber boost flame, graphite plate) + rendered PNGs. |

## Working on the real hardware

The actual laptop is reachable as **`ssh g15`** (SSH host alias; hostname
`Ashblade`, user `bean`). Workflow used during development:

```bash
rsync -az --exclude=.git ./ g15:~/tuxthrottle/     # push source
ssh g15 'cd ~/tuxthrottle && sudo ./install.sh'    # system-install to /opt
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
  -c RRGGBB -b …`). **Prereqs:** OpenRGB installed + backlight **enabled in BIOS
  setup** (off by default).
- **The 5515 keyboard is a SINGLE controllable zone.** OpenRGB reports 4/16
  zones — its `RGBController_Alienware.cpp` platform-id quirk table has the
  5511/5530/5505 but **not** the 5515, so it falls back to `report.data[6]`=16.
  But the firmware ignores every zone-scoped write: `openrgb -z`, the SDK
  per-LED buffer (`update_leds`, any of 4/8/16 entries, L/R split), and a raw
  HID user-animation with per-zone `SELECT_ZONES` all paint the **whole
  keyboard one colour** (last colour wins) — camera-verified 2026-08-30. So
  **no per-zone colour, no gradient.** `set_zones`/`set_zone` collapse to
  `set_all`. `ZONE_COUNT`/`LOGICAL_ZONES` survive only for the `kbd.json`
  schema + the `*-wave` self-tests.
- **Firmware effects on fw 1.1.12:** only **Spectrum Cycle** actually animates
  (MCU, smooth). Breathing / Flashing just hold a steady colour. Rainbow Wave
  animates but is washed-out with a travelling dark gap and takes no
  colour/direction. `EFFECT_MODES` = `{"spectrum": "Spectrum Cycle"}` only.
- **Keyboard brightness is inverted / degenerate.** Every mode reports
  `brightness_min=100 / brightness_max=0`. Empirically **`-b 100` lights the
  backlight**, lower values dim it, `-b 0` = off. (A "fix" that special-cased
  `-b 0` for effects turned them off — reverted.)
- **The software `rainbow_wave` / `gradient_wave` daemons are dead code** kept
  only for `rainbow-test` / `gradient-test` (which `verify-install.sh` runs).
  Not wired to any button. A software per-LED wave can't work here anyway: the
  controller repaints irregularly at ~2–3 fps over USB *and* is single-zone.
- **DANGER — don't spam the AW-ELC with raw HID feature reports.** Programming
  user-animations (NEW/SELECT/ADD_ACTION/FINISH_PLAY) in a loop can **hard-hang
  the MCU**: it drops off USB entirely (`usb 3-3: device not accepting address,
  error -71`, won't enumerate). A warm `reboot` and USB re-authorize do **not**
  recover it — needs a **full power-off** (shutdown, wait ~10 s, power on).
- **Colour persistence** is opt-in via the `KbdBacklightFix` tweak (installs
  `tuxthrottle-openrgb.service` SDK server + `tuxthrottle-kbd.service` `apply-saved` at
  boot + a systemd-sleep hook). State: `~/.config/tuxthrottle/kbd.json`
  (`mode` is `zones` or `spectrum`; plus `speed`/`zones`). **`tuxthrottle_kbd._state_path()`
  must resolve the real user via `PKEXEC_UID`/`SUDO_UID` too** — pkexec sets no
  `PKEXEC_USER`, so a naive `~` lands in `/root` and the boot service never
  sees the GUI's saves (this was the "colour doesn't persist" bug).
- **Backlight "freezes"** = the OpenRGB SDK server wedged after many mode
  changes (CLI still exits 0, hardware stuck). Usual fix: restart
  `tuxthrottle-openrgb` — `tuxthrottle_kbd.restart_server()` / `reset()`, GUI "↻ Reset
  backlight" button. If even that fails and the colour won't change at all,
  the MCU itself is hung (see the raw-HID danger note) → full power-off.
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
  ledger** (`~/.config/tuxthrottle/state.json`, written by
  `ledger_record()` from `_run_item_apply`/`_run_item_undo`) adds "we set this"
  → `drifted` (we applied it, check now fails) and `failed` (our last attempt
  errored). "Status report" button / `python3 tuxthrottle.py --report`
  print the full table (`format_status_report`).
- **App-install detection** (`Item.__init__`, apps only): an app counts as
  installed if present by *any* route — its own `check` OR-folded with each
  `provides` probe, a `command -v {binary}`, and (for `manager: flatpak`) a
  system-or-per-user probe of the app-id. `_run_item_apply` re-runs that check
  right before installing and skips if it now passes, so Apply/Presets never
  add a second, colliding copy. **Native rpm is preferred**: Heroic / Prism /
  Protontricks / Minigalaxy / Bottles / Discord / Sunshine are `manager: dnf`
  with the Flatpak id kept only as a probe. **Steam / OpenRGB / gamescope /
  MangoHud / vkBasalt are native-only** — a Flatpak of these is *not* accepted
  (sandbox breaks controller/udev, mods, `LD_PRELOAD` layers; SteamOS ships
  Steam native too).
- Colour maths in `tuxthrottle_kbd.py` is **stdlib only** (`colorsys` + a small
  sRGB↔linear↔OKLab↔OKLCH set) — no numpy/Pillow.
- No-hardware self-tests: `tuxthrottle_kbd.py rainbow-test` / `gradient-test`.
- Diagnostics: `collect_debug_report()` (module-level, `_DEBUG_CMDS` list +
  `_diag_fans`) assembles the readable hw/OS/toolkit dump — every shell probe
  runs as `timeout -k 2 12 bash -lc …`. `collect_hw_bundle()` (`_HW_BUNDLE_FILES`
  + `_decode_key_caps` which turns `/proc/bus/input/devices` `B: KEY=` bitmaps
  into KEY_ names) writes a `.tar.gz` of raw dumps for onboarding a new laptop
  model. CLI: `--debug` / `--report` / `--collect [dir]`. GUI **Report a Bug**
  page (nav rail: detached at the bottom, amber `NavSupport.TButton` style, an
  amber "reads only, nothing uploaded" banner + `WARNING`-framed report box —
  it's the one non-hardware page): Generate report → "⧉ Copy for GitHub issue"
  (wrapped `<details>` block via `wrap_issue_block`), "Copy full issue",
  "Collect hardware bundle". Two issue templates in `.github/ISSUE_TEMPLATE/`.
  `RingGauge` canvas gauges: never name a field `self._w` on a `tk.Canvas`
  subclass — it clobbers the widget pathname.
- **Roadmap:** this is meant to become a general gaming-laptop tool; right now
  every `config/*.json` entry and hardware path assumes the G15 5515. When
  generalising, gate per-model (DMI) rather than assuming.
