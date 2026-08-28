# Dell G15 Toolkit (Nobara Linux)

A checkbox-driven GUI + tray monitor for Dell gaming laptops on Nobara Linux
(dnf), built the same way as [WinUtil](https://winutil.christitus.com/)-style
Windows tweak tools: data-driven JSON config, live status detection per
item, reversible tweaks, one-directional app installs, and one-click
presets.

**Inspired by [Div-Acer-Manager-Max](https://github.com/PXDiv/Div-Acer-Manager-Max)**
(DAMX) — the Acer NitroSense/PredatorSense replacement for Linux this was
modeled after: same idea (performance profiles, fan/thermal state, live
monitoring dashboard, one-button dedicated-key binding), rebuilt for Dell's
side of the same problem. One useful difference from DAMX's situation: Acer's
EC interface isn't in mainline Linux at all, so DAMX depends on an
out-of-tree driver (Linuwu Sense) just to get a `platform_profile`-style
interface to exist; Dell's `dell-wmi` driver is already in-tree and shows up
automatically (confirmed via `cat /proc/bus/input/devices` on the actual
laptop, showing a "Dell WMI hotkeys" entry with no extra driver installed),
so this tool doesn't need an equivalent custom kernel module — just the
right keycode mapping for the dedicated key, the same way DAMX captures its
Nitro/PredatorSense button's scancode from an actual press rather than
assuming one.

**Compatibility**: built and tested against **one confirmed platform**, the
Dell G15 5515 Ryzen Edition (Ryzen 7 5800H + RTX 3050 Ti Mobile) — that's the
hardware every check/apply command in `config/*.json` was written against.
Like DAMX's own compatibility note, other Dell/Alienware laptops with
similar AMD+NVIDIA hybrid graphics and a `dell-wmi`-exposed hotkey may work
too, but nothing here has been verified on them. The dedicated key's
capture (evdev keycode `KEY_PERFORMANCE`) was confirmed via live
`evtest`/`dmesg`/`acpi_listen` capture on the actual laptop, not assumed.

This GUI superseded an earlier bash CLI prototype with the same tweak
catalog but an interactive terminal menu instead of checkboxes/live status —
same underlying idea, this repo is the maintained one.

**Package-name check**: every `dnf` package name referenced in `config/*.json`
was cross-checked against Fedora's dist-git (`src.fedoraproject.org`) to
confirm it actually exists before being trusted — this caught one real
error: `ttkbootstrap` is **not** packaged in Fedora/Nobara at all (404 on
dist-git), `pip install --user ttkbootstrap` is the only install path,
fixed in `dellg15_toolkit.py`'s own error message. `akmod-nvidia` lives in
RPM Fusion's separate dist-git (unreachable from this check, but its name
is standard/well-documented) rather than Fedora's own.

**Status: not yet run against real hardware.** Everything here has been
syntax-checked and GUI-smoke-tested on a different dev machine, but never
against actual Nobara or this laptop's real `dnf`/`grubby`/sysfs — see
"Known limitations" at the bottom.

## Tested hardware

Every check/apply command in `config/*.json`, the sensor probes in
`sensors.py`, and the dedicated-key capture were written against the single
machine below. Values were pulled from live `dmesg`, `evtest`,
`acpi_listen`, and `/proc/bus/input/devices` on that laptop (booted from a
Linux live USB for the capture — Nobara install pending).

| Component | What's in the test machine | Identifiers / notes |
|---|---|---|
| **Laptop** | Dell G15 5515, Ryzen Edition | SMBIOS `Dell G15 5515`, board `0R3CDX` |
| **BIOS** | Dell `1.31.0` | dated 2026-01-28; Secure Boot disabled during testing |
| **CPU** | AMD Ryzen 7 5800H (Cezanne, Zen 3) | family 19h model 50h stepping 0; 8 cores / 16 threads; `k10temp` |
| **iGPU** | AMD Radeon Graphics (Cezanne, Vega 8) | PCI `1002:1638`, driver `amdgpu`; hybrid via ATPX / `vga_switcheroo` |
| **dGPU** | NVIDIA GeForce RTX 3050 Ti Mobile (GA107), 4 GB | PCI `10de:25a0`, HDA `10de:2291`, VBIOS `94.07.2b.00.60`; captured under `nouveau` (GSP RM 570.144), NVIDIA proprietary is the toolkit's target |
| **RAM** | ~40 GB DDR4 | both SO-DIMM slots populated (2/2) |
| **Storage** | 2× NVMe SSD (ADATA) | PCI `1cc1:33f3` (`nvme0`, 4 partitions) + `1cc1:64ba` (`nvme1`) |
| **Ethernet** | Realtek RTL8125B 2.5 GbE | PCI `10ec:8125`, driver `r8169` |
| **Wi-Fi / BT** | Intel Wi-Fi 6 AX200 (Killer AX1650x, 200NGW) | PCI `8086:2723`, driver `iwlwifi` |
| **Audio** | Realtek ALC3254 codec + AMD/NVIDIA HDMI audio | `snd_hda_codec_alc269` (ALC3254 fixup) |
| **Touchpad** | Dell / ELAN I2C | `DELL0A6E:00 04F3:317E` (mouse + touchpad nodes) |
| **Keyboard** | AT Translated Set 2 | dedicated G-key → `KEY_PERFORMANCE` (701) with Fn Lock **off**, `KEY_F9` with Fn Lock **on** |
| **Vendor hotkeys** | in-tree `dell-wmi` (no extra driver) | "Dell WMI hotkeys" + "DELL Wireless hotkeys" input devices |
| **Capture OS** | Linux `7.0.0-14-generic` live USB | used only for the evdev/dmesg/acpi captures |
| **Target OS** | Nobara Linux (Fedora-based, `dnf`) | not yet run on the real hardware |

Firmware notes seen at boot: `WQBC` WMI data-block and `_S0W`
`AE_ALREADY_EXISTS` ACPI warnings — both harmless on this board.

Other Dell/Alienware models with an AMD + NVIDIA hybrid setup and a
`dell-wmi`-exposed hotkey may work, but nothing here has been verified on
them.

## Install (system-wide, adds it to the KDE menu)

```bash
cd DellG15Toolkit
sudo ./install.sh
```

Installs to `/opt/dellg15-toolkit`, a launcher at
`/usr/local/bin/dellg15-toolkit`, the flaming-gauge icon into the hicolor
theme, and a `.desktop` entry in `/usr/share/applications` — so **every user**
on the machine can find "Dell G15 Toolkit" in the KDE launcher / KRunner
search. It pulls the deps too (`python3-tkinter`, `ttkbootstrap` via pip
system-wide, and — best effort — `python3-pyside6` and `python3-evdev`).

`sudo ./install.sh --uninstall` removes all of that. (Tweaks you applied from
*inside* the tool are separate — revert those in the UI first if you want
them gone.)

## Running it from the source tree

```bash
cd DellG15Toolkit
pip install --user ttkbootstrap   # one-time; dark theme + round-toggle switches + gauges
python3 dellg15_toolkit.py
```

It self-elevates via `pkexec` (falls back to `sudo`) since tweaks touch the
kernel cmdline, udev rules, sysctl, and `dnf`. The `--user` pip install lands
in your home dir, which root normally can't see after elevation — the script
carries that path through on `PYTHONPATH` when it re-execs, so the `--user`
install is enough. (A system-wide `sudo pip install --break-system-packages
ttkbootstrap` also works if you prefer.)

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
- `dellg15_kbd.py` — standalone AW-ELC RGB keyboard driver (stdlib only)
- `assets/` — `icon.svg` (a flaming tachometer redlined into "G15") and the
  PNGs rendered from it, used as the window icon and tray icon. The
  **DesktopLauncher** tweak drops a `.desktop` entry into your app menu
  using `assets/dellg15-toolkit.desktop` with real paths filled in.

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
Reading clocks/temps needs no privileges at all. Every toggle (tray, G-key,
or the Dashboard switch) also raises a 10-second desktop notification —
*Game Mode: ON* / *Game Mode: OFF*.

## Keyboard tab (`dellg15_kbd.py`)

The 5515's 4-zone RGB keyboard is the Alienware **AW-ELC** controller (USB
`187c:0550`) — bound to `hid-generic`, no kernel driver, so no
`kbd_backlight` LED and no KDE control. OpenRGB detects it but has the wrong
zone count for this exact model (`platform 0x0E05` isn't in its quirk table,
so it drives 16 zones when there are 4) and its default brightness is 0 — net
result, nothing lights.

`dellg15_kbd.py` talks the AW-ELC feature-report protocol directly (stdlib
only) with the right 4 zones — **Left / Middle / Right / Numpad** — and an
un-inverted brightness. The Toolkit's **Keyboard tab** wraps it: a brightness
slider, whole-keyboard colour + presets, and a per-zone colour picker (the
G-key lives in the Left zone). CLI too:

```bash
python3 dellg15_kbd.py on --color 00aaff --brightness 80
python3 dellg15_kbd.py zone 0 --color ff2200
python3 dellg15_kbd.py off
python3 dellg15_kbd.py info          # platform id / firmware / zone count
```

The controller forgets its state on reboot/replug/resume, so the
**KbdBacklightFix** tweak (Power tab) installs a systemd service + a
`systemd-sleep` hook that re-apply whatever you last set (saved to
`~/.config/dellg15-toolkit/kbd.json`) at boot and after every wake — this is
also the fix for the backlight being stuck off after boot.

### Dedicated key binding — confirmed working

Unlike DAMX's Acer button (which needs a udev hwdb remap), the G15 5515's
G-key sends a clean, purpose-built keycode — `KEY_PERFORMANCE` (701) on the
"AT Translated Set 2 keyboard" — captured live. What *produces* it depends
on Fn Lock:

| | bare tap of the key | Fn + F9 |
|---|---|---|
| **Fn Lock OFF** | `KEY_PERFORMANCE` | `KEY_F9` |
| **Fn Lock ON** | `KEY_F9` (plain F9) | `KEY_PERFORMANCE` |

`hotkey_listener.py` only ever acts on `KEY_PERFORMANCE`, so it can never
hijack a real F9 press.

**What the key does — same as Windows.** On the G15 the dedicated key
toggles **G-Mode**: on Windows that's Alienware Command Center flipping the
firmware thermal profile to its top setting (fans ramp, CPU/GPU power limits
raised) and back. On this hardware Linux exposes that same firmware toggle
as the `performance` **platform_profile** (driver `alienware-wmi`) — the
kernel's own docs note *"if the device supports G-Mode, it is also toggled
when selecting the performance profile."* So one press → `performance`
(G-Mode on), press again → `balanced` (off). The `gaming-performance` /
`gaming-balanced` scripts do exactly that, and additionally slam the
`alienware-wmi` fan boost (`fanN_boost` → max / back to 0) for the AWCC-style
100 % fan on top of the profile's own ramp. The listener also lights the
**Left keyboard zone red** while G-Mode is active (like AWCC's red G-key),
if the RGB keyboard + a base colour from the Keyboard tab are set up.

Default trigger is **one press** (matches Windows). `KEY_PERFORMANCE` fires
as an instant down+up so a long-press can't be used; if a bare tap is too
easy to hit by accident, set `DELLG15_HOTKEY_MODE=double` on the service for
a double-tap trigger (window = `DELLG15_HOTKEY_DOUBLE_MS`, default 600 ms).

Install via the Toolkit GUI (Gaming tab):
- **HotkeyListener** — installs `python3-evdev` and a `systemd --user`
  service that listens for the key and calls the same toggle the tray icon
  uses. Install `PowerProfileScripts` / `AmdgpuPerfScripts` / `NvidiaMaxPerf`
  first, or the toggle has nothing to call. Reading the keyboard device
  needs `input`-group membership, which Nobara 43 KDE does **not** grant the
  desktop user by default — the tweak runs `usermod -aG input`, so **log out
  and back in once** after applying it before the service can see the key.
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
  this model), **RaplPowerPermissions** (see "Power reporting" below).
- **Performance** — USB autosuspend off, flat mouse accel, swappiness, zram
  tuning, KDE Baloo indexer off. Plus a curated **kernel-cmdline** set
  (`split_lock_detect=off`, `nowatchdog`, `preempt=full`, `threadirqs`, … —
  reboot required; the Intel-only / laptop-dangerous entries from typical
  desktop lists are deliberately left out), optional `ipv6.disable=1`,
  **CpuMaxPerformance** (pin the `performance` governor at boot),
  **BtrfsNoatime**, **NvmeIoTune**, **WifiPowersaveOff**,
  **NetLatencySysctl** (BBR + fq + TCP Fast Open), **DnfSpeed** (parallel
  downloads + fastestmirror), **GamingResourceLimits** (nofile / map-count /
  inotify for Wine + Proton), **AutoMountDrives** (permanent `/etc/fstab`
  entries mounting every fixed internal data partition — a second games/data
  NVMe, NTFS via `ntfs3` included — at a stable `/mnt/<label>` with `nofail`;
  skips OS/boot/swap/fstab/LUKS/removable).
- **Software / Monitoring / Streaming / RGB / Gaming** — Steam, Lutris,
  Heroic, GameMode/MangoHud, gamescope, vkBasalt, btop/nvtop/GOverlay/
  fastfetch, Discord/OBS/Sunshine/Moonlight, OpenRGB, controller udev rules,
  the G-key hotkey listener + its passwordless-sudo rule,
  **MangoHudGlobalToggle** (see "Global MangoHud" below). For Wine/Proton:
  **ProtonUp-Qt**, **Protontricks**, **Bottles**, and the Steam **Proton
  BattlEye / EasyAntiCheat Runtime** installers — see "GTA V Online" below.
- **Keyboard backlight** — `dellg15_kbd.py`, a standalone stdlib driver for
  the AW-ELC RGB controller (USB `187c:0550`). The 5515's `platform 0x0E05`
  is missing from OpenRGB's zone-quirk table, so OpenRGB drives it as 16
  zones when it's really 4 and nothing lights; this talks the AW-ELC
  feature-report protocol directly with the correct 4 zones + explicit
  brightness. `dellg15_kbd.py on|off|zone N|info`.
- **Game Mode notifications** — pressing the G-key (or toggling from the
  tray/Dashboard) raises a 10-second desktop notification, *Game Mode: ON*
  / *Game Mode: OFF*.

## Global MangoHud (works on any game, not just per-title launch options)

The normal MangoHud workflow is pasting `mangohud %command%` into every
single Steam game's launch options one at a time. The `MangoHudGlobalToggle`
tweak installs `mangohud-global-on`/`mangohud-global-off` instead — they
write/remove `~/.config/environment.d/mangohud.conf` containing:

```
MANGOHUD=1
LD_PRELOAD=/usr/$LIB/mangohud/libMangoHud.so
```

`MANGOHUD=1` alone is enough for Vulkan games — the Vulkan loader has a
built-in "enable this implicit layer only if this env var is set" mechanism
that MangoHud's own layer manifest uses, so no per-game opt-in needed. The
`LD_PRELOAD` line (using the dynamic linker's own `$LIB` token, resolved by
`ld.so` at process-launch time — deliberately NOT shell-expanded when this
file is written) additionally catches the rare pure-OpenGL title Vulkan's
mechanism wouldn't reach.

**This is systemd `environment.d`, not a live shell variable** — it applies
to processes launched by your graphical session going forward, which takes
effect after your next login (or at minimum, restarting Steam so it and
everything it launches picks up the new session environment). Once active,
MangoHud's own in-game keybind (`Shift_R+F12` by default) shows/hides the
overlay display without touching this global on/off state — that's the
"pause the HUD for one round" control; `mangohud-global-off` is the "stop
hooking every process" control.

## Power reporting — a real permissions gotcha

Both the Dashboard tab and the tray icon read CPU package power from Linux's
RAPL energy counters (`/sys/class/powercap/*/energy_uj`, delta-measured over
a short window since RAPL only exposes cumulative energy, not instantaneous
watts). **Since 2020, the kernel locks these to root-only by default** as a
side-channel mitigation — since the tray icon and hotkey listener
deliberately run unprivileged (only the Game Mode toggle itself elevates),
CPU power reads as blank/zero for them out of the box. The `RaplPowerPermissions`
tweak adds a udev rule (`chmod -R a+r /sys/class/powercap`) — the same fix
MangoHud's own documentation recommends for its power display — making
these readable by any local user. Trade-off: this is a known, low-severity
timing side-channel exposure, not "safe" by risk tag, since it affects
every user of the machine, not just this tool. GPU power (NVIDIA via
`nvidia-smi`, no permission issue there) and all clock/temp reads need no
such fix — they're readable unprivileged by default.

Both the Dashboard and the tray icon show a warning inline when this isn't
fixed, rather than silently displaying wrong/blank numbers.

## GTA V Online (Wine / Proton)

GTA Online runs on Linux, but it's driven by **BattlEye** anti-cheat, so the
prefix has to carry the BattlEye runtime. There are two supported routes; the
toolkit's Software tab installs the tooling for both (`ProtonUp-Qt`,
`Protontricks`, `Bottles`, `Steam: Proton BattlEye Runtime`,
`Steam: Proton EasyAntiCheat Runtime`, plus `Lutris`, `MangoHud`, `GameMode`,
`vkBasalt`).

### Route A — Steam copy + Proton (most reliable)

1. Software tab: install **Steam**, **GameMode + MangoHud**, **ProtonUp-Qt**,
   **Steam: Proton BattlEye Runtime**.
2. In ProtonUp-Qt: install the latest **GE-Proton** for Steam.
3. Steam → GTA V → Properties → Compatibility → force **GE-Proton**.
4. Steam → Library → Tools → make sure **Proton BattlEye Runtime** is
   installed (the toolkit tries to trigger this; do it by hand if the entry
   isn't there).
5. Launch options:
   ```
   DXVK_ASYNC=1 gamemoderun mangohud %command%
   ```
   BattlEye installs itself into the prefix on first launch — let it finish,
   then start again.
6. If the Rockstar/Social Club window is black: switch GE-Proton version, or
   add `PROTON_USE_WINED3D=0`; if sign-in loops, set the prefix to Windows 10
   (`protontricks <appid> --gui` → *win10*).

### Route B — Rockstar Games Launcher (non-Steam copy)

Use **Lutris** (simplest) or **Bottles**:

- **Lutris:** add game → search *"Rockstar Games Launcher"* → install the
  community script. It pulls a wine-GE runner + DXVK automatically. In the
  game's *Runner options* enable **BattlEye**. If RGL won't open, set the
  runner to a current **wine-ge** / **GE-Proton** build and Windows version
  to 10.
- **Bottles:** new bottle (type *Gaming*) → Dependencies: `dxvk`,
  `vcrun2019`, `d3dcompiler_47`, `corefonts` → run the RGL installer inside
  it → add GTA V's `PlayGTAV.exe` as a program. Turn off the Rockstar
  overlay (it crashes under Wine).

Common to both: keep the machine on **Game Mode** (G-key / tray) while
playing, and apply the **WifiPowersaveOff** + **NetLatencySysctl** tweaks —
GTA Online is sensitive to Wi-Fi latency spikes.

## How status/apply works

Every item has a `check` shell command. On launch (and **Refresh Status**),
each runs and the checkbox **pre-ticks** with an **Applied** / **Installed**
tag if true — this isn't guesswork, it reflects the actual system state, and
it detects things installed outside this tool too (Nobara pre-ships Steam,
GameMode, MangoHud, gamescope, the NVIDIA driver, … — those show up as
already done). The status bar shows a running count: *"N of M already
applied/installed — K available."*

A kernel-cmdline tweak that's been staged in the bootloader but needs a
reboot to be live gets a third state, **Pending reboot** (via an optional
`check_pending` command) — pre-ticked, and skipped by Apply, so you don't
re-select and re-run it in the window between applying and rebooting.

**Apply Selected** is a diff, not a blind re-run:
- Tweak checked + not applied → applies it.
- Tweak checked + already applied / pending reboot → **skipped** (logged).
- Tweak unchecked + currently applied → reverts it (if it has an `undo`).
- App checked + not installed → installs it (apps don't auto-uninstall on
  uncheck, same as the Windows tool's Software tab).

The log pane at the bottom streams every command's output live, and prints
how many already-done items it skipped.

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
  "check_pending": "shell command, exit 0 = staged but needs a reboot",  // optional, tweaks.json
  "apply": ["cmd1", "cmd2"],      // tweaks.json
  "undo": ["cmd1"],               // tweaks.json only
  "manager": "dnf" | "flatpak" | "shell",  // apps.json
  "package": "pkgname",           // apps.json, dnf/flatpak id
  "install": ["cmd1", "cmd2"]     // apps.json, overrides manager/package for custom (git/pip) installs
}
```

`{USER}` and `{TOOLKIT_DIR}` in any command are substituted — the user is
resolved from `PKEXEC_UID`/`SUDO_UID` since the whole app runs elevated.

## Known limitations (prototype)

- No backup/snapshot system like the Windows tool's `state.json` — undo
  relies on each tweak's own `undo` commands being correct, not a generic
  "restore whatever was there before."
- Runs commands sequentially on one background thread — a slow `dnf install`
  blocks the rest of a batch/preset behind it (log still streams live).
- Not tested against real Nobara/this laptop yet — `dnf`/`grubby`/etc. simply
  aren't installed on the dev machine this was built on, so only the UI
  layer and command-runner plumbing have been smoke-tested.
