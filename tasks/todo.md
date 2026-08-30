# TuxThrottle backlog — todo

## Done
- [x] SSH `g15` → 192.168.0.100 + host key re-trusted (verified same ed25519 key)
- [x] Dashboard iGPU + dGPU clock gauges (2×4 grid), wired to poll
- [x] Memory: "load all skills each session" standing rule
- [x] tasks/plan.md written
- [x] Phase 1: ryzenadj TDP — sensors core + `RyzenAdjTDP` tweak + Power&Limits tab.
      VERIFIED on g15: `read_ryzenadj_info` parses (STAPM/fast/slow/tctl), preset
      apply moves the limits (fast 65→54, slow 54→42). NOTE: SMU floors STAPM near
      the slow limit — can't push STAPM far below slow; presets now keep STAPM>=slow.
- [x] Phase 2: Battery charge limit — sensors core + `BatteryChargeLimit` tweak +
      Power&Limits section. NOTE: G15 5515 does NOT expose
      `charge_control_end_threshold` → section shows "not supported". Real Dell
      battery-threshold support would need libsmbios/smbios-battery-ctl (future).
- [x] Phase 3: NVIDIA power limit — sensors core + `NvidiaPowerLimit` tweak +
      Power&Limits section. NOTE: RTX 3050 Ti Mobile power limit is FIRMWARE-LOCKED
      (Dynamic Boost) — `power.limit` reads [N/A], `nvidia-smi -pl` rejected.
      Section shows the firmware-locked note instead of a slider. Code kept for
      other laptops where -pl works.
- [x] Phase 4: `GameModeBridge` tweak (feral gamemode already installed on g15).
- [x] Deploy to g15 + install.sh + GUI smoke (17 pages, Power&Limits present,
      gauges present) + `--report` clean + `verify-install.sh` 21/0. GUI relaunched
      on the g15 desktop (transient unit `tuxthrottle-gui`).
- [x] Left nav rail is now scrollable (`SidebarNav`: Canvas + inner `_nav_box`,
      auto-hiding scrollbar via `_nav_reflow`, wheel driven by the global
      `_scroll_canvases` handler, `select()` scrolls the active button into view).
      About + Report a Bug are pinned to `_rail_bottom` (always visible).
- [x] New **About** page (`_build_about_tab`) — pinned above Report a Bug:
      version, what-it-is blurb, feature list, GitHub / issues / copy-link
      buttons (`PROJECT_URL`, `_open_url` via `sudo -u {user} xdg-open`),
      hardware + MIT-license details. Verified on g15: 18 pages, verify-install 21/0.

## Done (cont.)
- [x] Phase 5: Closed-loop fan curve — `tuxthrottle_powerd.py` (stdlib daemon:
      temp→curve→additive fanN_boost, hysteresis, restores auto on SIGTERM),
      `FanCurveDaemon` tweak (systemd unit runs it from {TOOLKIT_DIR}), Fans-tab
      editor (5-point table + live curve canvas + sensor/hysteresis + Save).
      VERIFIED on g15: `once` mode read 58C→52% boost, applied, restored to [0,0].
- [x] Phase 6: AC/battery auto-switch — folded into `tuxthrottle_powerd.py`
      (watches AC*/online, applies Quiet/Balanced/Performance bundle =
      platform_profile + ryzenadj preset). Power&Limits section: enable toggle +
      On-AC / On-battery comboboxes + Save (merges into powerd.json).
- [x] Phase 7: `tuxthrottlectl` — stdlib argparse CLI over sensors.py
      (status / get / set profile|tdp|fan-boost|battery|nvpl|gpumode / gamemode,
      --json, non-zero exit on failure). install.sh/uninstall.sh manage the
      /usr/local/bin/tuxthrottlectl launcher. VERIFIED on g15.
- [x] Phase 8: Hybrid graphics — `sensors.gpu_mode_get/set` (EnvyControl wrapper),
      Power&Limits section (integrated/hybrid/nvidia radios + Apply + logout
      warning); hidden with an "install EnvyControl" note when it's absent
      (that path confirmed on g15). Also `tuxthrottlectl get/set gpumode`.

## Post (polish) — DONE 2026-08-30
- [x] README: Dashboard 8-gauge grid, Fans custom curve, new Power & Limits
      section, `tuxthrottlectl` section, Files list, "What's in it", gating note.
- [x] CLAUDE.md: layout table (powerd / ctl / Power & Limits / scrollable nav /
      `item.hidden`) + Hardware-facts (STAPM floor, no battery sysfs → libsmbios,
      NVPL firmware-lock, dnf-metadata-age, RAPL auto-hide).
- [x] Battery threshold via libsmbios — `sensors` uses `smbios-battery-ctl`
      when sysfs is absent; `DellBatteryThreshold` tweak; battery section note
      points at it. `smbios-battery-ctl` already present on g15 → section works.
- [x] Updates stale-count → tagged with `_dnf_metadata_age()` ("dnf list as of
      41 min ago"). Verified on g15.
- [x] RAPL tweak auto-hide (`item.hidden` in `_apply_vendor_gate`): hidden when
      RAPL already world-readable AND its udev rule not installed. On g15 it
      stays visible because the rule was already applied (so undo still works).
- [x] Memory `tuxthrottle-improvement-backlog` updated.

## Deferred (NOT polish — own branch, needs kbd hardware)
- [ ] Delete dead `rainbow_wave`/`gradient_wave`/`_Sdk`/`_stream_wave`/`stop_fx`
      from `tuxthrottle_kbd.py` (~250 interwoven lines; `stop_fx()` in live
      paths; `rainbow-test`/`gradient-test` feed `verify-install.sh`). Driver
      refactor, camera-verify the keyboard after.
