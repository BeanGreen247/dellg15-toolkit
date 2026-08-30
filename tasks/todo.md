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

## Queued
- [ ] Phase 2: Battery charge limit (core + tweak + slider)
- [ ] Phase 3: NVIDIA power-limit slider (core + tweak + slider)
- [ ] Phase 4: Feral GameMode bridge (`GameModeBridge` tweak + dashboard status)
- [ ] Phase 5: Closed-loop fan curve (`tuxthrottle_powerd.py` + `FanCurveDaemon` tweak + Fans editor)
- [ ] Phase 6: AC/battery auto profile switch (into powerd + Power&Limits dropdowns)
- [ ] Phase 7: `tuxthrottlectl` CLI
- [ ] Phase 8: Hybrid graphics mode switch (GPU tab, EnvyControl wrapper)

## Post
- [ ] Deploy to g15 + relaunch, `--report` clean, GUI smoke
- [ ] verify-install.sh green
- [ ] README + CLAUDE.md updated
- [ ] Update memory `tuxthrottle-improvement-backlog`
