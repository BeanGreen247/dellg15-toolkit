# Dell G15 Toolkit (Nobara Linux)

A checkbox-driven GUI + tray monitor for Dell gaming laptops on Nobara Linux
(dnf), built the same way as `~/windows11ultimateperformancetool/UltimateToolkit`:
data-driven JSON config, live status detection per item, reversible tweaks,
one-directional app installs, and one-click presets.

**Inspired by [Div-Acer-Manager-Max](https://github.com/PXDiv/Div-Acer-Manager-Max)**
(DAMX) — the Acer NitroSense/PredatorSense replacement for Linux this was
modeled after: same idea (performance profiles, fan/thermal state, live
monitoring dashboard, one-button dedicated-key binding), rebuilt for Dell's
side of the same problem. One useful difference from DAMX's situation: Acer's
EC interface isn't in mainline Linux at all, so DAMX depends on an
out-of-tree driver (Linuwu Sense) just to get a `platform_profile`-style
interface to exist; Dell's `dell-wmi` driver is already in-tree and shows up
automatically (see the "Dell WMI hotkeys" input device in the hwinfo dump),
so this tool doesn't need an equivalent custom kernel module — just the
right keycode mapping for the dedicated key, the same way DAMX captures its
Nitro/PredatorSense button's scancode from an actual press rather than
assuming one.

**Compatibility**: built and tested against **one confirmed platform**, the
Dell G15 5515 Ryzen Edition (Ryzen 7 5800H + RTX 3050 Ti Mobile) — that's the
hardware every check/apply command in `config/*.json` was written against.
Like DAMX's own compatibility note, other Dell/Alienware laptops with
similar AMD+NVIDIA hybrid graphics and a `dell-wmi`-exposed hotkey may work
too, but nothing here has been verified on them.

This is the GUI counterpart to the CLI in `../dellg15-linux-setup/` — same
underlying logic, exposed as checkboxes with live "Applied"/"Installed"
status instead of an interactive terminal menu.

**Status: local prototype only.** Not committed to git, not published anywhere.

## Running it

```bash
cd DellG15Toolkit
pip install --user ttkbootstrap   # one-time; dark theme + round-toggle switches + gauges
python3 dellg15_toolkit.py
```

It self-elevates via `pkexec` (falls back to `sudo`) since tweaks touch the
kernel cmdline, udev rules, sysctl, and `dnf`.

## The Dashboard tab

A live "system info" view like DAMX's monitoring dashboard, built into the
main window (not just the tray icon): CPU temp/clock, dGPU temp/clock/util
as gauges, iGPU/dGPU details as text, and a big round-toggle for Game Mode —
same effect as the G-key or the tray icon, all sharing one source of truth
(`sensors.py`) so they never disagree with each other.

## Hardware-aware gating

Items tagged `requires_vendor: "nvidia"` or `"amd"` in the JSON (the NVIDIA
driver, EnvyControl, CoreCtrl, the GPU perf-state scripts) are automatically
greyed out and labeled "unsupported" if that GPU vendor isn't detected on
the running system — the same "dynamic UI hides unsupported features"
principle DAMX uses, done via one detection pass (`sensors.has_nvidia_gpu()`
/ `has_amd_gpu()`) at startup rather than a hardcoded per-model table.

## Files

- `dellg15_toolkit.py` — the checkbox GUI + in-window Dashboard tab (needs `ttkbootstrap`)
- `tray_monitor.py` — the system-tray-only equivalent (needs `PySide6`)
- `hotkey_listener.py` — the G-key → Game Mode binding (needs `python3-evdev`)
- `sensors.py` — shared sensor reads + Game Mode logic, **no GUI dependency**, used by all three above so they never disagree on state

## Tray monitor (`tray_monitor.py`)

The DAMX-style live dashboard piece — a system tray icon (unprivileged
process) showing CPU/iGPU/dGPU clocks and temps, with a checkable "Game Mode"
menu item (also toggled by left-clicking the tray icon) that runs the
`gaming-performance`/`amdgpu-perf-high`/`nvidia-max-perf` helper scripts
installed by the tweaks above — install those first (Presets > Safe Baseline
or Competitive Gaming) or the toggle has nothing to call.

```bash
# needs PySide6: dnf install python3-pyside6   (or: pip install --user PySide6)
python3 tray_monitor.py
```

The toggle tries passwordless `sudo` first, falling back to a `pkexec` GUI
prompt (see `PasswordlessGameModeToggle` below for making that prompt-free).
Reading clocks/temps needs no privileges at all.

### Dedicated key binding — confirmed working

Unlike DAMX's Acer button (which needs a udev hwdb remap because its
scancode aliases to an unrelated keycode), the G15 5515's G-key turned out
simpler once captured from a live press: **with Fn Lock OFF**, it sends
evdev keycode `KEY_PERFORMANCE` (701) on the "AT Translated Set 2 keyboard"
device — a distinct, purpose-built Linux keycode with nothing else bound to
it. **With Fn Lock ON**, the same physical key instead sends plain `KEY_F9`
(expected — Fn Lock swaps that row back to standard F-keys).

Practical result: **keep Fn Lock off**, and `hotkey_listener.py` only ever
acts on `KEY_PERFORMANCE` — it can never hijack a real F9 press elsewhere on
the keyboard, so no remap or quirk table needed.

Install via the Toolkit GUI (Gaming tab):
- **HotkeyListener** — installs `python3-evdev` and a `systemd --user`
  service that listens for the key and calls the same toggle the tray icon
  uses. Install `PowerProfileScripts` / `AmdgpuPerfScripts` / `NvidiaMaxPerf`
  first, or the toggle has nothing to call.
- **PasswordlessGameModeToggle** — a narrow `/etc/sudoers.d` rule scoped to
  exactly the five toggle scripts (not general root access), so pressing the
  key doesn't sit waiting on an unanswerable GUI password prompt. Optional,
  but effectively required for the hotkey path specifically — skip it and
  the tray icon still works fine (its `pkexec` prompt has someone there to
  answer it).

## What's in it

- **Presets tab** — Safe Baseline, Competitive Gaming, Streaming Rig: one
  button applies a curated bundle.
- **Stability** — the C-state freeze fix (and the alternative `idle=nomwait`),
  `clocksource=tsc`.
- **GPU** — NVIDIA driver check/install, EnvyControl (AMD+NVIDIA hybrid
  switching), CoreCtrl, ryzenadj, amdgpu/nvidia perf-state scripts.
- **Power** — TLP or auto-cpufreq (pick one), `gaming-performance`/
  `gaming-balanced` toggle scripts, i8kutils fan control (DAMX's fan-tab
  equivalent — best-effort, `dell-smm-hwmon` isn't confirmed to whitelist
  this model).
- **Performance** — USB autosuspend off, flat mouse accel, swappiness, zram
  tuning, KDE Baloo indexer off.
- **Software / Monitoring / Streaming / RGB / Gaming** — Steam, Lutris,
  Heroic, GameMode/MangoHud, gamescope, btop/nvtop/GOverlay/fastfetch,
  Discord/OBS/Sunshine/Moonlight, OpenRGB, keyboard backlight brightness
  (DAMX's backlight-timeout equivalent — Dell has no WMI timeout knob, so
  this just sets max brightness), controller udev rules, the G-key hotkey
  listener + its passwordless-sudo rule.

## How status/apply works

Every item has a `check` shell command. On launch (and **Refresh Status**),
each runs and the checkbox pre-ticks with an **Applied**/**Installed** tag if
true — this isn't guesswork, it reflects the actual system state.

**Apply Selected** is a diff, not a blind re-run:
- Tweak checked + not applied → applies it.
- Tweak unchecked + currently applied → reverts it (if it has an `undo`).
- App checked + not installed → installs it (apps don't auto-uninstall on
  uncheck, same as the Windows tool's Software tab).
- Already in the state you want → skipped.

The log pane at the bottom streams every command's output live.

## Extending it

Same as the Windows tool: add an entry to `config/tweaks.json` or
`config/apps.json`, no code changes needed. Schema:

```jsonc
"SomeId": {
  "Content": "Display name",
  "Description": "...",
  "category": "Performance",     // becomes a tab
  "risk": "safe" | "advanced",
  "check": "shell command, exit 0 = applied/installed",
  "apply": ["cmd1", "cmd2"],      // tweaks.json
  "undo": ["cmd1"],               // tweaks.json only
  "manager": "dnf" | "flatpak" | "shell",  // apps.json
  "package": "pkgname",           // apps.json, dnf/flatpak id
  "install": ["cmd1", "cmd2"]     // apps.json, overrides manager/package for custom (git/pip) installs
}
```

`{USER}` in any command is substituted with the real invoking user (resolved
from `PKEXEC_UID`/`SUDO_UID`, since the whole app runs elevated).

## Known limitations (prototype)

- No backup/snapshot system like the Windows tool's `state.json` — undo
  relies on each tweak's own `undo` commands being correct, not a generic
  "restore whatever was there before."
- Runs commands sequentially on one background thread — a slow `dnf install`
  blocks the rest of a batch/preset behind it (log still streams live).
- Not tested against real Nobara/this laptop yet — `dnf`/`grubby`/etc. simply
  aren't installed on the dev machine this was built on, so only the UI
  layer and command-runner plumbing have been smoke-tested.
