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
| `tuxthrottle.py` | the GUI. ttkbootstrap `darkly` re-skinned into a dark "gaming-BIOS" look with the KDE accent colour (`apply_bios_style` / `read_desktop_accent`). **Palette lives in the `BIOS_*` / `SEM_*` / `CHART_AXIS` constants** — a contrast pass (fg×bg matrix) set every text colour to clear ≥5.5:1 on all surfaces; `SEM_DANGER`/`SEM_SUCCESS`/etc. override darkly's semantics because those drop below AA on the darker `BIOS_CARD` / `BIOS_PANEL_HI`. Labelframes draw a visible 1px `BIOS_BORDER` hairline; `Disclosure.TButton` + `Card.TFrame`/`CardRow.TFrame` are the collapsible-panel styles (used by About's "What's inside"). **Left sidebar nav** (`SidebarNav`, a drop-in for `tb.Notebook`), not a top tab strip, `RAIL_WIDTH = 256`. The rail is **scrollable** (`_nav_canvas` + `_nav_box` inside it, auto-hiding scrollbar via `_nav_reflow`, wheel via the shared `_scroll_canvases` handler, `select()` scrolls the active button into view); **About** and **Report a Bug** are pinned to `_rail_bottom` (`add(..., pin=True)` / `kind="support"`) so they stay visible below the scroll. Pages: Dashboard (8 ring gauges 2×4 — CPU temp/clock/power, iGPU clock, dGPU temp/clock/util/power), Keyboard, Fans (+ a 10-point **custom fan curve** editor with Silent/Balanced/Aggressive presets + "Linear fill" → `powerd.json`; `FAN_CURVE_POINTS`, `_FANCURVE_PRESETS`, `_fc_resample()` pads old 5-pt configs), **Power & Limits** (`_build_power_tab`: ryzenadj STAPM/fast/slow sliders + presets, a **Curve Optimizer** section (`_build_co_section` — 0…−40 slider + "Apply & stress-test 5 min" → `_co_stream` runs `tuxthrottle_co_stress.py apply` via `_run_stream`, "Keep (confirm)" / "Revert" → `_co_action`; hidden unless `sensors.ryzenadj_co_supported()`), NVIDIA `-pl` slider *or* a firmware-locked note, **`_build_gpuclock_section`** (NVIDIA graphics-clock lock → `nvclk.json`), EnvyControl hybrid-GPU radios, battery charge-limit via sysfs *or* libsmbios, **`_build_refresh_section`** (panel Hz), AC/battery auto-switch comboboxes (+ refresh); state → `~/.config/tuxthrottle/{tdp,co,nvpl,nvclk,battery,powerd}.json`), **Battery** (`_build_battery_health_tab` — `sensors.battery_health_info()`: design-vs-full capacity → **wear %** (green/amber/red), charge cycles, chemistry; a live "Now" card (charge/state/power-flow/**time-to-empty|full** from `eta_min`/`eta_kind`/voltage, `_bath_poll` every 4 s); reuses `_build_battery_section(frame, prefix="_bath_bat")` for the charge-limit control — `prefix` namespaces the IntVar/label so the twin instance on Power & Limits isn't clobbered, `_bat_apply(prefix)` keeps both in sync; plus a **Charging speed** standard/express radio (`_apply_charge_mode`, shown whenever `smbios-battery-ctl` exists — the g15 firmware can't *read* the mode back but *setting* it works) and an informational **Adaptive Sync** line from `sensors.vrr_status()`), **Profiles** (`_build_profiles_tab` — capture/apply/delete named full-state profiles via `tuxthrottle_profiles`, a snapshot list with per-row + "latest" rollback, a per-game auto-profile map + a **time-of-day schedule** (`_build_schedule_section` → `powerd.json` `schedule`) → `powerd.json`; `_apply_worker` also drops a `pre-apply-selected` snapshot before any tweak "Apply Selected"), Presets, Updates, **Setup Games**, **Game Tools**, then one per tweak/app category (Gaming first, incl. **KDE (Desktop GUI Tweaks)**), **About** (`_build_about_tab` — version, a collapsible "What's inside" dropdown, GitHub/issues/copy-link via `_open_url`), and **Report a Bug** last (amber `NavSupport` style — the log/GitHub-issue page). An item with `item.hidden = True` (set in `_apply_vendor_gate`: `RaplPowerPermissions` where RAPL is already world-readable, or `Item.requires_models` — a `"models": [...]` list not containing `sensors.model_id()`) is skipped entirely, not just greyed. **Setup Games** (`_build_games_tab`, from `config/games.json`) is a real top `tb.Notebook` tab-strip, one page per game (GTA V Online first); each game is an ordered list of step cards with a per-step `check` (status pill: done ✓ / to do / manual / optional) and either a `▶ Run step` button (streams to the log via `_run_game_step`→`_run_stream`, same busy-overlay path as `_run_updates`) or a manual step with a "Mark done" toggle + optional `⧉ Copy command`; a `▶▶ Run all N automatic steps` header button chains the `run` steps (`_run_game_all`/`_game_all_worker`, skips already-done). A step may carry `show_if` (bash) — the card is hidden (`pack_forget`) unless it exits 0; and a `run` button whose `check` already passes is greyed to "✓ Already done". Step results (incl. a `hidden` state) poll back on `_games_q` / `_poll_games_queue`. `{USER}`/`{TOOLKIT_DIR}`/`{APPID}` are substituted in games.json `check`/`show_if`/`run`/`copy` via `_game_subst`. **Game Tools** (`_build_gametools_tab`, scrollable — the any-game Steam/Proton helpers, split out of Setup Games so that tab is just walkthroughs): a **Proton prefix & save-file tools** box (`_prefix_scan` / `_prefix_relocate_entry` → `tuxthrottle_prefix_relocate.py`; save-vault rows → `tuxthrottle_savevault.py`); a **Shader / pipeline cache storage** box (`_build_shadercache_box`, see `tuxthrottle_shadercache.py` — shows 3 live `du` sizes total/Steam/rest via a bg worker + `_sc_size_poll` (never on the Tk thread — `_lo_refresh` must NOT touch the FS), separate "Save location" / "Apply shader cache size" buttons → `_sc_persist`, ↻ Refresh, Clean); a **launch-options builder** (`_build_launch_opts_box` / `_lo_refresh` — MangoHud / gamemoderun / gamescope (+W/H/fps) / PRIME-offload / persistent NVIDIA+Mesa+DXVK shader caches (paths + sizes from `shadercache.json`) / NVIDIA threaded optimizations / `RADV_PERFTEST=gpl` / disable-vsync+threaded-GL (AMD) / `DXVK_ASYNC` / `PROTON_LOG=0` / **anti-cheat safe** (`MANGOHUD=0 DISABLE_VKBASALT=1 VK_LOADER_LAYERS_DISABLE=~implicit~`, also suppresses the mangohud wrapper) → a `[env] [wrappers] %command%` string with ⧉ Copy **+ "Apply to every game"** (`_lo_apply_all` → `tuxthrottle_launchopts.py set-all --b64` for all installed Steam games, "only games with no options yet" toggle, needs Steam closed); the GTA V Online walkthrough step 11 copies this same clean-layer set + PRIME offload + `DXVK_ASYNC`); a **MangoHud overlay** box (`_build_mangohud_box` — `cpu_text`/`gpu_text`/`gpu_list`/`position`/`offset_x`/`offset_y` + a computed `width`; **`gpu_list` is remapped** by `_mh_gpu_list()` → `sensors.mangohud_gpu_order()` because MangoHud 0.7+ numbers GPUs by ascending DRM **render node** (`renderD128,129,…`), which on the g15 is the *reverse* of the GUI's discrete-first rows (card0=NVIDIA but renderD128=AMD) — so it emits e.g. `gpu_list=1,0`, keeping each `gpu_text` label on its own card instead of pinning the dGPU name on the iGPU line (`_mh_hud_width()` sizes it from the longest label × `font_size` — MangoHud's auto-width does NOT grow for a long custom `cpu_text`; calibrated vs vkcube, 18-char label @ fs20 ≈ 558) + 3 per-group detail toggles (`_mh_lvl` cpu/gpu/mem minimal↔full via `_MH_GROUPS`; on Write the whole stat section is stripped to `_MH_STAT_KEYS` then rebuilt as `_MH_ALWAYS` (fps/engine_version) + `_MH_FRAMEGRAPH` (frametime + frame_timing — its own "Frametime graph" toggle) + `_MH_GPUNAME` (`gpu_name`, MangoHud's active render-GPU name / "which card did PRIME land on" — its own **"GPU in use (name)"** toggle) + the chosen group sets. **`_MH_EXPLICIT` (frametime/frame_timing/gpu_name) is always written `=0` or `=1`, never removed** — MangoHud defaults them ON, so a missing line ≠ off (this was the "frametime graph still shows when disabled" bug); `_mh_load_conf` now also reads `key=0`/`key=1` element lines (into `elements` / a new `off` set). Each GPU-name row has a **⇅ swap** button (`_mh_swap_gpu_rows` — swaps the name *and* its PCI addr so `gpu_text` + the remapped `gpu_list` slot move together) merged into the MangoHud config keeping styling/keybind lines; fields load from the conf first via `_mh_load_conf`, else ↻ Detect via `sensors.cpu_model_name()` / `sensors.gpu_names()` off-thread; a **Per-game** entry (`_mh_game_var`) switches `_mh_conf_path()` to `~/.config/MangoHud/<exe>.conf`, `_mh_reload_from_conf` re-reads on change; **Place on screen…** → `_mh_position_dialog` — a translucent **full-screen** `tk.Toplevel` at the real resolution, drag the overlay box (`_MH_BOX_W/H`) — snaps to a 16×16 grid (toggle) — release → `_mh_anchor_offset()` snaps to the nearest of the 8 MangoHud anchors + `offset_x/y` (round-trips via `_mh_box_xy()`); Save position → `_mh_write_position()` (position keys only), Restore-last-saved / Restore-default / Cancel/Esc; **`_mh_write_conf(managed)`** is the shared writer — dedupes every key (last wins), keeps leading comments, drops blank/garbage lines, **writes via a `.tuxthrottle-tmp` + `os.replace` so MangoHud's inotify watcher never reads a truncated file**; `_mh_reset_conf` = minimal baseline + `.bak`. **`_mh_guard_global_preload()` (2026-09-03)** runs before every MangoHud write (`_mh_apply` / `_mh_reset_conf` / `_mh_write_position`): if an `environment.d/*.conf` has `LD_PRELOAD=…libMangoHud` (the old `MangoHudGlobalToggle` tweak wrote one), it prompts and strips just that line — the global preload pulls the overlay layer into `kwin_wayland`/`plasmashell`, and rewriting the live conf then **SIGABRTs KWin → Plasma session reset → tray loses the power/battery applets** (this was the "MangoHud Write crashes my desktop" bug). `MangoHudGlobalToggle` now writes **`MANGOHUD=1` only**, no `LD_PRELOAD`); and a **Last game session** card (`_build_last_session_card` reads `last_session.json`). App-wide "busy" modal overlay with a two-bar (overall + current-task) progress display + elapsed timer (`_begin_busy` / `_poll_busy_queue`). Hover tooltips via `self._tip(widget, text)` → module-level `_Tooltip` class (ttkbootstrap 2.2.x has no ToolTip); currently on the footer buttons, Presets/recommendations buttons and the whole Game Tools tab. Self-elevates via `pkexec`→`sudo`. |
| `tray_monitor.py` | PySide6 system-tray monitor + `--toggle`. **Left-click → opens the main GUI** (`_launch_gui()` → `tuxthrottle` on PATH, else `/opt/tuxthrottle/tuxthrottle`, else `python3 tuxthrottle.py`, detached); **middle-click → Game Mode**; right-click menu has a bold **Open TuxThrottle** item first (kept on `self.open_action` — a parent-less `QAction` added to a `QMenu` is GC'd out of it once `__init__` returns), then the live CPU/iGPU/dGPU rows, Game Mode, Power-profile / Fan-boost submenus, Quit. Tooltip = `APP_NAME — APP_BLURB` / `by BeanGreen247` / `Click to open · CPU … · dGPU …` (live). |
| `hotkey_listener.py` | `systemd --user` service, reads the G-key from evdev, toggles Game Mode. |
| `sensors.py` | **shared, no GUI deps.** Sensor reads + `set_game_mode()` + `notify()` + `detect_model()` + **fan control** (`read_fans`, `get/set_fan_boost`, `*_platform_profile`, `get_pwm_state`, `set_pwm_manual`, `restore_fan_auto`) + `dgpu_is_awake()` + **power/limits** (`ryzenadj_available` / `read_ryzenadj_info` / `set_ryzenadj_limits`; `battery_charge_limit_info` / `set_battery_charge_limit` — sysfs `charge_control_end_threshold` **or** Dell libsmbios `smbios-battery-ctl`; `nvidia_power_limit_info` / `set_nvidia_power_limit` — returns `supported: False` when the GPU's limit is firmware-locked; `gpu_mode_get` / `gpu_mode_set` via EnvyControl; `gamemode_status`) + `battery_health_info()` (design-vs-full → wear %, cycles, chemistry, live rate; pure `power_supply` sysfs) + `battery_charge_mode()` / `set_battery_charge_mode()` (Dell libsmbios standard/express fast-charge) + `nvidia_powerd_status()` (Dynamic Boost service) + `amd_pstate_mode()` + `vrr_status()` (informational). `which()` is `@cache`d. |
| `tuxthrottle_profiles.py` | **stdlib, `import sensors`.** `capture_state()` / `apply_state()` over the whole power surface (platform_profile, ryzenadj TDP, battery, NVIDIA limit, fan curve, autoswitch, optional hybrid-GPU, kbd). Named **profiles** in `~/.config/tuxthrottle/profiles/`; auto **snapshots** in `snapshots/` (pruned to 20) before any apply/rollback; `active_state.json` is what `reassert` (the `StateResume` sleep hook) re-applies. `_safe_name` blocks `..`. CLI: `capture/apply/list/show/delete/snapshot/snapshots/rollback/reassert`. |
| `tuxthrottle_powerd.py` | **stdlib daemon, `import sensors, tuxthrottle_profiles, tuxthrottle_control`.** One poll loop: (1) closed-loop **fan curve** — `interp()` maps temp through `powerd.json` `fan_curve.points` to an *additive* `fanN_boost`, cool-down hysteresis, restores auto on SIGTERM/SIGINT. (2) **AC/battery auto-switch** — on `AC*/online` transition, `apply_bundle()` sets platform_profile + a ryzenadj TDP preset. (3) **per-game auto-profiles** — `GameProfileController` scans `/proc` (comm + argv0) every `game_profiles.poll_s`; a matched exe (or `*` + a live Feral GameMode session) → snapshot + `apply_state(load_profile(mapped))`, and on exit `default` or roll back the pre-game snapshot. **While a game runs it also samples tctl/clocks/power each tick and writes a `~/.config/tuxthrottle/last_session.json` summary on exit** (max temps, avg clocks, throttle %) — the GUI's "Last game session" card reads it. (4) **`ThermalWatcher`** — `thermal_notify` config block: sustained tctl ≥ `tjmax_c` for `tjmax_sustain_s`, a 0-RPM fan while `read_temp("max") ≥ stalled_fan_hot_c`, or `platform_profile in (performance,custom)` on battery under `battery_perf_min_pct` → `sensors.notify()` + a `THERMAL-EVENT` log line, deduped per kind by `cooldown_s`. With **`stalled_fan_recover: true`** a stalled-fan event also kicks `platform_profile → performance` (the only thing that revives a fan stuck by the G15 5515/5525 firmware bug), holds it `stalled_fan_recover_s`, then restores the previous profile once the fans turn again (`_kick_fan_recover` / `_end_fan_recover`). (5) **control socket** — `_build_dispatch()` registers `status/reload/apply_profile/snapshot/rollback/set` on a `tuxthrottle_control.ControlServer` (only when root and mode `run`); `reload` flips `reload_flag` so the loop re-reads config. Config re-read on mtime change. `run`/`once`. Installed by `FanCurveDaemon` (unit has `RuntimeDirectory=tuxthrottle`). |
| `tuxthrottle_control.py` | **stdlib, no deps.** Newline-delimited-JSON RPC over `/run/tuxthrottle/control.sock`. `ControlServer` (a `ThreadingUnixStreamServer`, socket chmod 0660 root:root, `methods` dict attached to the `_Srv` instance for the handler); `call(method, params)` returns the response dict or `None` if the socket isn't connectable; `available()` is a connect probe. Server lives in `tuxthrottle_powerd`; clients are `tuxthrottlectl` + (future) the GUI. |
| `tuxthrottle_co_stress.py` | **stdlib, root.** Ryzen Curve Optimizer undervolt harness. `apply <-N> [--minutes M] [--no-gpu]` → `profiles.snapshot("pre-curve-optimizer")`, write `co.json` `{offset, confirmed:false}`, arm `/run/tuxthrottle/co_watchdog`, `sensors.set_co_offset(-N)`, run `stress-ng --cpu 0` (fallback `yes`×nproc) + a GPU load (`glmark2`/`vkmark`/`glxgears`) for M min while polling `dmesg --level=err` for `mce|whea|hardware error|…` → **any fault reverts to 0, deletes co.json, exit 1**; clean run leaves it applied, exit 0. `confirm` sets `confirmed:true` (now the boot service re-applies it). `revert` → CO 0 + forget. `reapply` (boot/resume hook) applies `co.json` **only if `confirmed:true`** — an offset that hung the box pre-confirm never comes back. `status` (read-only, non-root OK). Wired to `RyzenCurveOptimizer` tweak + the GUI Power & Limits "Curve Optimizer" section (`_build_co_section` / `_co_stress` streams via `_run_stream`). |
| `tuxthrottlectl.py` | **stdlib argparse CLI over `sensors.py` + `tuxthrottle_profiles` + `tuxthrottle_control`**, installed as `/usr/local/bin/tuxthrottlectl` by `install.sh`. `status` / `watch [interval]` (clear-screen one-line summary) / `get {power-profile,tdp,fans,battery,nvpl,gamemode,clocks,gpumode}` / `set {power-profile,tdp,fan-boost,battery,nvpl,gpumode,refresh,gpu-clock}` / `gamemode {on,off,toggle}` / `schedule {show,on,off}` / `profile {list,apply,save,show,delete}` / `snapshot [label]` / `rollback [last\|<file>]` / `daemon {status,ping,reload}` / `collect-model [--slug] [--out]` (→ `tuxthrottle_modelgen`); `--json`; non-zero exit on failure. `set`/`profile apply`/`rollback` need root. When `tuxthrottle_control.available()`, `set` and `profile apply` are routed through the daemon socket (`_daemon_set()`), else they act directly. |
| `tuxthrottle_kde_panel.py` | **stdlib helper for the fiddly panel-applet KDE tweaks.** `clock-seconds {on\|off}` finds every `org.kde.plasma.digitalclock` applet in `plasma-org.kde.plasma.desktop-appletsrc` (tracks the `[Containments][C][Applets][A]` header) and `kwriteconfig6`s `showSeconds`; `classic-menu {on\|off}` swaps `plugin=org.kde.plasma.{kickoff,kickerdash}` ↔ `kicker`; `panel-floating {on\|off}` finds every `org.kde.panel` containment (`find_panels`, tracks the top-level `[Containments][N]` header) and `kwriteconfig6`s `[General] floating` true/false — off = panel flush to the screen edge, on = the default floating gap; `battery-tray {on\|off}` → `systray_show()` **stops plasmashell first** (a running one flushes its in-memory appletsrc on exit and clobbers the edit — that was the "did nothing" bug), then merges `org.kde.plasma.battery` into `shownItems` (and out of `hiddenItems`) in `[Containments][C][Applets][A][General]` — the SAME group as `knownItems`/`extraItems`, NOT `[Configuration][General]` (v1 wrote the wrong group; the fixed version also scrubs the stale key) — on every `org.kde.plasma.systemtray` applet = the Battery & Brightness icon pinned Always-shown. `launcher-power {on\|off}` → `systemFavorites` (the session/power footer row) to the full set / cleared on every kickoff/kicker/kickerdash applet's `[Configuration][General]` (also stop-plasmashell-first — plasmashell owns this key). `clock-seconds`/`classic-menu`/`panel-floating` restart `plasmashell`; `battery-tray`/`launcher-power` start it back after the stop (`systemctl --user restart plasma-plasmashell.service`). Called by the tweaks via the shared user-session wrapper. |
| `tuxthrottle_kbd.py` | AW-ELC RGB keyboard driver: an `openrgb` CLI wrapper. The 5515 keyboard is **one controllable zone** (see hardware notes) → whole-keyboard solid colour + brightness + the firmware **Spectrum Cycle** only. `set_zones`/`set_zone` collapse to `set_all`. The `rainbow_wave`/`gradient_wave` software daemons + the `_Sdk` SDK-socket client + `stop_fx()`/`fx.pid` + the OKLab/OKLCH colour maths + `rainbow-test`/`gradient-test` self-checks were **deleted** 2026-08-31 (branch `chore/kbd-dead-code`) — 1293 → ~480 lines. **Anti-flicker locks (2026-09-03):** every openrgb call in `_run()` holds `flock(/tmp/tuxthrottle-kbd.write.lock)` so concurrent writers can't interleave; `apply-saved` takes `/tmp/tuxthrottle-kbd.reassert.lock` non-blocking and **bails if held** (boot service vs sleep-hook vs tray vs a live GUI apply no longer stack re-assert storms), asserts **2×** (was 6) and reads the Plasma accent **once** up front — fixes "keyboard flickers between my colour and the desktop-accent colour". Lockfiles are chmod 0666 so a root boot service and the unprivileged tray share them. |
| `tuxthrottle_automount.py` | scans `lsblk`, adds `/etc/fstab` entries mounting fixed internal data disks at `/mnt/<label>` with `nofail`. |
| `config/tweaks.json`, `apps.json`, `presets.json` | the data. Tweaks have `check` / `check_pending` (staged-but-needs-reboot) / `apply` / `undo` / optional `reinstall_if` (glob list for `apply_tweak.py --only-if-present`) / optional `recommended: true` (dev's curated pick — drives the per-section "★ Apply section recommendations" button in the `SidebarNav` header via `Item.recommended` → `_recommended_for(category)` / `_on_nav_page` / `_apply_ids_worker`; button only shows when that category has an un-applied recommendation. The **Presets tab** also has a global "★ Apply all recommendations" → `_recommended_all()` / `_on_apply_all_recommended`, which folds in `FanCurveDaemon` when it's not yet enabled). Apps have `manager` (`dnf`/`flatpak`/`shell`), `package`, `check`, optional `install`, and — for cross-manager "already installed" detection — optional `provides` (extra shell probes), `binary` (`command -v`), `flatpak_id`. `{USER}` and `{TOOLKIT_DIR}` are substituted (in `apply`/`undo` **and** inside the heredoc script bodies). Power & Limits tweaks: `RyzenAdjTDP` (installs ryzenadj from repo or source build + `/usr/local/bin/tuxthrottle-tdp` re-apply script + boot/resume unit + sudoers), `BatteryChargeLimit` (sysfs boot service), `DellBatteryThreshold` (just `dnf install libsmbios` — Dell stores the interval in NVRAM), `NvidiaPowerLimit` (boot service + sudoers, `requires_vendor: nvidia`), `NvidiaClockLock` (`/usr/local/bin/tuxthrottle-nvclk` reads `nvclk.json` `{gr_min,gr_max}` → `nvidia-smi --lock-gpu-clocks`; boot svc + sleep hook + sudoers; `requires_vendor: nvidia` — the GPU-clock lever that works when `-pl` is firmware-locked), `FanCurveDaemon` (`tuxthrottle_powerd.py` unit, `RuntimeDirectory=tuxthrottle`), `RyzenCurveOptimizer` (`requires_vendor: amd`; boot service + sleep hook run `tuxthrottle_co_stress.py reapply` — confirmed offsets only; no sudoers, the GUI is already elevated), `StateResume` (systemd-sleep hook + boot service → `tuxthrottle_profiles.py --user {USER} reassert` — note `--user` **before** the subcommand; the profiles CLI now also accepts it after, via a `parents=[common]` SUPPRESS parent), `GameModeBridge` (`dnf install gamemode` + `/etc/gamemode.ini` custom start/end → `gaming-performance` / `gaming-balanced`). **Gaming-research batch (2026-08-31, from a web survey of asusctl/LenovoLegionLinux/CoreCtrl/Goverlay + r/linux_gaming):** `NvidiaShaderCache` (`/etc/environment.d/` `__GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1` etc. — stops the every-launch shader recompile, `requires_vendor: nvidia`), `NvidiaDynamicBoost` (`systemctl enable nvidia-powerd` — un-caps the dGPU, `requires_vendor: nvidia`), `SplitLockMitigateOff` (`sysctl kernel.split_lock_mitigate=0`; graceful no-op where the key is absent, e.g. Zen 3), `SchedExtGaming` (`dnf install scx-scheds` + a self-owned `tuxthrottle-scx.service` running `scx_lavd`, the Steam-Deck scheduler; `apply` waits ≤25 s for `/sys/kernel/sched_ext/root/ops`), `KwinAllowTearing` (KDE cat — `kwinrc [Compositing] AllowTearing=true`), `AcpiBacklightNvidiaWmiEc` (grubby kernel arg — fixes brightness on some Optimus G15s, `check_pending`), `AmdPstateActive` (grubby `amd_pstate=active`, `requires_vendor: amd`, `check_pending`). **Aggressive-extras batch (2026-09-03, "optimise the crap out of it"):** `KernelUltraCmdline` (grubby `mitigations=off nvme_core.default_ps_max_latency_us=0 pcie_aspm=off amdgpu.ppfeaturemask=0xffffffff iommu=pt zswap.enabled=0` — the spicy args `KernelGamingCmdline` left out; `check_pending`, `advanced`), `VmWritebackSysctl` (`/etc/sysctl.d/99-tuxthrottle-vm.conf` — bounded `dirty_bytes`/`dirty_background_bytes`, `page-cluster=0`, `watermark_scale_factor=125`/`watermark_boost_factor=0`; `recommended`), `NvidiaAggressiveModule` (`/etc/modprobe.d/99-tuxthrottle-nvidia-aggr.conf` — `NVreg_UsePageAttributeTable=1 NVreg_InitializeSystemMemoryAllocations=0 NVreg_EnableResizableBar=1 NVreg_RegistryDwords="PowerMizerEnable=0x1;PerfLevelSrc=0x3333;PowerMizerDefaultAC=0x1"` + dracut; `requires_vendor: nvidia`, `check_pending`, runs the dGPU HOT), `GpuGamingEnv` (`/etc/environment.d/95-tuxthrottle-gpu-gaming.conf` — `RADV_PERFTEST=gpl,nggc,sam mesa_glthread=true __GL_THREADED_OPTIMIZATIONS=1 __GL_YIELD=USLEEP __GL_MaxFramesAllowed=2 __GL_VRR_ALLOWED=1`), `ServiceMaskGaming` (mask cups/avahi/ModemManager/sssd/sssd-kcm/plocate-updatedb.timer; `advanced`), `AnanicyCpp` (`dnf install ananicy-cpp` + `systemctl enable --now`; `recommended`, safe), `KernelVerboseBoot` (Stability, `check_pending` — grubby drops `quiet splash`, adds `loglevel=7 printk.time=1 systemd.show_status=1 systemd.status_unit_format=combined plymouth.enable=0` for the full scrolling boot log; console-display only, no extra journal/SSD writes; no LUKS on the g15). `KdeLauncherPowerButtons` (KDE cat, `recommended` — `tuxthrottle_kde_panel.py launcher-power on` → `systemFavorites=suspend,hibernate,reboot,shutdown,lock-screen,logout,save-session,switch-user` on every kickoff/kicker/kickerdash applet's `[Configuration][General]`; stop-plasmashell-first like battery-tray — fixes "launcher only shows Log Out"). Preset **`MaximumPerformance`** ("Maximum Performance (aggressive)") bundles the curated + aggressive tweaks. **Deliberately excludes every fan/thermal lever** (`FanCurveDaemon`, `GameModeBridge`) AND **`IrqThreadsRt`** (RT IRQ scheduling — see below) AND `SchedExtGaming`. Fans stay on the stock auto curve; G-Mode / the G-key are untouched. Reboot-gated / security-affecting ones (`KernelUltraCmdline`, `NvidiaAggressiveModule`, `ServiceMaskGaming`) are shipped but NOT auto-applied — user opts in via the Presets button. **`IrqThreadsRt`** (`advanced`, RISKY, **not in any preset, never `recommended`**) — `/usr/local/bin/tuxthrottle-irq-rt` + boot service `chrt -f` the `irq/*-{amdgpu,nvidia}` threads to FIFO 50 / `irq/*-nvme` to FIFO 45. RT IRQ scheduling can monopolise a CPU and **freeze the box** under any scheduler stress (an alt scheduler stalling, a driver hiccup, an I/O-hung disk); `undo` drops the threads back to SCHED_OTHER live. **`SchedExtGaming` — CONFIRMED to hard-freeze this g15 on kernel 7.2** (scx_lavd stalls, `scx_watchdog_workfn` in dmesg, ~30 s whole-system freezes, often triggered by the GUI's startup subprocess burst); if scx keeps coming back after undo it's Nobara's own loader: `systemctl disable --now --mask scx_loader.service`. **`GameModeQuietIni`** (safe, recommended) — writes `/etc/gamemode.ini` with renice/ioprio/softrealtime/`desiredgov=performance` **only**, no `[custom]` platform_profile/fan script (the quiet alternative to `GameModeBridge` — both own `/etc/gamemode.ini`, enable one). Launch-opts builder also gained an **ntsync** toggle (`_lo["ntsync"]`, default on → `PROTON_USE_NTSYNC=1`). Every one of these is an individual category toggle AND folded into `MaximumPerformance` (now 27 tweaks). **KDE (Desktop GUI Tweaks)** category — 14 Plasma-6 toggles (`KdeAnimationsOff`, `KwinGamingCompositor`, `KdeScreenEdgesOff`, `KdeAutoShowBattery` (`recommended` — `tuxthrottle_kde_panel.py battery-tray on`, pins the Battery & Brightness tray icon to Always-shown; also makes the power/battery UI reappear right after a plasmashell restart), incl. the `[Effect-*] BorderActivate=9` hot corners, `KdeActivitiesRecentOff`, `KdeThumbnailIoLimit`, `KdeLaunchFeedbackOff`, `KdeSplashOff`, `KdeClockSeconds`, `KdeClassicMenu`, `KdePanelFlush`, `KdeKwalletOff`, `KdeMetaKeyOff`, `KwinAllowTearing` — `KdeClockSeconds`/`KdeClassicMenu`/`KdePanelFlush` via `tuxthrottle_kde_panel.py`; `KdeKwalletOff` sets `kwalletrc [Wallet] Enabled=false` + `First Use=false` and `kquitapp6 kwalletd6` — kills the login wallet-unlock prompt + KWallet GPG/SSH passphrase caching, `recommended: true`; `KdeMetaKeyOff` sets `kwinrc [ModifierOnlyShortcuts] Meta=""` — a lone Meta/Super/Win tap no longer opens the launcher (Meta+key combos still work), `recommended: true`; `KdePanelFlush` sets `[Containments][N][General] floating=false` in appletsrc **and** `[PlasmaViews][Panel N] floating=0` in `plasmashellrc` — the latter is the one that actually removes the gap, see the KDE-gotchas list): each runs `kwriteconfig6` **as the user with their session env** (`sudo -u {USER} -H env XDG_RUNTIME_DIR=/run/user/$(id -u {USER}) DBUS_SESSION_BUS_ADDRESS=unix:path=.../bus bash -lc '…'`) then reloads the live component (KWin `reconfigure` via `qdbus-qt6`→`qdbus`→`dbus-send`, `kquitapp6 kactivitymanagerd`, or a `plasmashell` restart). `check`s use `kreadconfig6`. |
| `tuxthrottle_prefix_relocate.py` | stdlib helper. Prefix side: `<appid>` moves a Steam game's `compatdata/<appid>` Proton prefix off an NTFS/exFAT drive (which can't host it — `:` in `dosdevices/c:` → `OSError [Errno 22]`, game won't start) onto the native Steam library and symlinks it back; `--check` = exit 1 if it needs moving; `--scan` lists all; `--all` does all. Save side: `--saves-scan` finds prefix known-folders (Documents / Saved Games / AppData) symlinked onto another filesystem **and** loose `Documents`/`My Games` folders at a Steam drive's root (orphaned Wine redirects); `--saves <appid>` / `--saves-all` pull the symlinked ones back into the prefix; `--saves-import <appid>` copies a drive's loose Documents into that game's prefix. All copy-not-delete; refuse root or a running Steam. Wired into the GUI's **Proton prefix & save-file tools** box; also standalone. |
| `tuxthrottle_vram.py` | **stdlib, `import sensors`.** Model-agnostic VRAM budget for a KDE Plasma 6 / Wayland laptop (every tier is plain KWin/Plasma KConfig, no vendor calls). All state files resolve against the **real user's home** via `_real_pw()` — never `~`/`/root` under pkexec/sudo (the "state landed in /root, kwinrc split-brain" bug); `_save()` also `_chown_to_user()`s. `status [--json]` (per-GPU used/total/GTT + top consumers from `/proc/*/fdinfo` drm-*-vram + nvidia-smi), `profile {regular|medium|extreme}` (KWin/Plasma tiers via session-wrapped `kwriteconfig6`: HiddenPreviews/GLTextureFilter/blur/contrast/animations, +extreme: overview/windowview/desktopgrid unload + solid-colour wallpaper via appletsrc + plasmashell restart; captures `~/.config/tuxthrottle/vram-baseline.json` on first non-regular apply so `regular` restores exactly), `free [--json] [--restart-compositor]` (AMD: **reads** `/sys/kernel/debug/dri/<N>/amdgpu_evict_{vram,gtt}` — current kernels evict on read, EACCES on write even as root; Intel: writes `0xf` to `i915_gem_drop_caches`/`xe_drop_caches`; reports before/after; optional `plasma-kwin_wayland` restart), `compositor-gpu {auto|igpu|dgpu}` (writes/removes `09-tuxthrottle-gpu.sh` exporting `KWIN_DRM_DEVICES`). **`igpu` lists ONLY the integrated card's real `/dev/dri/cardN`** — putting the NVIDIA node in the list makes KWin bail in `performStartup()` and SIGSEGV in `~GpuManager`/`~RenderDevice`, SDDM relaunches → login loop (hit live 2026-09-02). The env script `[ -e … ]`-guards each node so a vanished one is skipped, not fatal, and carries a `Ctrl+Alt+F3 → rm` recovery comment. `dgpu` is refused when `_is_muxless()` (integrated GPU is `boot_vga` — most hybrid laptops; the panel is wired to the iGPU). GUI greys the unavailable modes; the desktop-GPU radio captions + the live bars are built from `sensors.drm_gpus()`/`gpu_devices()` (dynamic, never hardcoded). `extreme` also blanks `kwinrc [Wayland] InputMethod` (Maliit on-screen keyboard, ~72 MiB — fully applies next login). New `sensors` helpers: `drm_gpus()` / `vram_info()` / `vram_consumers()` / `nvidia_runtime_pm()` / `set_nvidia_runtime_pm()` / `session_cmd()` (public `_session_cmd`). GUI **VRAM** nav page (`_build_vram_tab`, after Battery) — live per-GPU bars + consumer list polled by `_vram_poll` only while visible (gated in `_on_nav_page`), tier radios, desktop-GPU radios, a "Free VRAM now" / "Restart compositor" pair, and a dGPU runtime-PM checkbox. CLI: `tuxthrottlectl vram <...>` (REMAINDER-delegates to `tuxthrottle_vram.main`). Boot-persistent dGPU power-down = the `NvidiaRuntimePM` tweak (`NVreg_DynamicPowerManagement=0x02` in `/etc/modprobe.d/` + dracut, `check_pending`, `requires_vendor: nvidia`). **On the g15 KWin already renders on the iGPU** (boot_vga) and the dGPU idles near-empty (~40 MiB) — the pressure is the iGPU carveout (75–82 % full, spilling to GTT), so the tiers mostly target iGPU/compositor VRAM. |
| `tuxthrottle_launchopts.py` | **stdlib** (uses the `vdf` module when importable, else a built-in mini KeyValues parser/serializer). Bulk-writes one launch-options string into **every installed Steam game's** `LaunchOptions` in `userdata/*/config/localconfig.vdf` (`Software/Valve/Steam/apps/<appid>`, under the file's single root key). `list` / `set-all '<str>'` \| `set-all --b64 <blob>` `[--only-empty] [--dry-run]` / `clear-all`. Refuses while `steam` runs (Steam rewrites the file on exit); backs each file up to `*.tuxthrottle-bak-<epoch>`; refuses root. Wired to the Game Tools launch-options box's **"Apply to every game"** button (`_lo_apply_all` → base64 of `_lo_out` → `su - {USER} -c … --b64` via `_run_stream`) + an "only games with no options yet" toggle (`_lo_only_empty`, default on). |
| `tuxthrottle_savevault.py` | stdlib helper (imports the Steam-library helpers from `tuxthrottle_prefix_relocate`). `list` / `export` / `import` a save-game **vault** — a folder on a drive that is *not* the home/OS filesystem (enforced by `st_dev` check), laid out `<vault>/<appid>/{Documents,Saved Games,AppData/Roaming,AppData/LocalLow}/…` + `.tuxthrottle-name`. `export`/`import` take an `<appid>` or `all`; `import` refuses while Steam runs. Wired to the **Save-game vault** row of the GUI's prefix/saves box; vault path persists in `~/.config/tuxthrottle/saves_vault`. |
| `tuxthrottle_steamperf.py` | **stdlib, non-root.** Steam-*client* low-resource mode (2026-09-03, refined across several rounds). `{on\|off\|status}`. `on` writes a user-level `~/.local/share/applications/steam.desktop` (shadows the system launcher; `steam://` action Execs untouched) whose primary `Exec=` becomes `systemd-run --user --scope --quiet --collect -p MemoryHigh=1200M -- /usr/bin/steam -silent -cef-disable-gpu -cef-disable-gpu-compositing -cef-disable-breakpad -cef-disable-extra-info-spew -noverifyfiles -nobootstrapupdate -norepairfiles %U` (the last three cut only the per-launch file-scan/self-update/repair spike; `-skipinitialbootstrap` left out — can break a launch after a Steam update), stamps `X-TuxThrottle-LowResource=true`, patches `~/.config/autostart/steam.desktop` (`.tuxthrottle-bak`). **Safe levers only** — `-cef-single-process` / `-no-cef-sandbox` / `-no-browser` broke the client and a hard `MemoryMax` OOM-killed it, so all four are out; `MemoryHigh` is *soft* (kernel reclaims page cache above ~1.2 GB, never kills). `_patch_exec_lines`/`_unpatch_exec_lines` = idempotent transforms; `MEM_HIGH_MB` const. `_apply_client_settings(low)` also flips every file-settable Steam setting (skipped while Steam runs — it rewrites these on exit; one-time `.tuxthrottle-bak`; `_flip_block_keys` inserts a missing key after its VDF block's `{`): `localconfig.vdf` `friends`→`SignIntoFriends`/`AnimatedAvatars`/`AnimatedGameArt`=0, and ON-only `config.vdf` `ShaderCacheManager/EnableShaderBackgroundProcessing`=0 (OFF leaves it — its own toggle owns it). **Steam Overlay + screenshots kept** (`system`→`EnableGameOverlay` left ON per the user; `_LEGACY_RESET` forces it back to `1` each run in case an old version set it 0). `MANUAL_HINTS` (printed to the log by `on`) = the toggles the new Steam UI keeps in an internal leveldb store, not scriptable: Library Low Bandwidth / Low Performance Mode, Interface smooth-scroll off, Downloads Shader Pre-Caching off. `off` deletes the override, restores autostart, flips the localconfig keys back to `1`. **`--aggressive` was REMOVED 2026-09-03** — it had layered on `-cef-single-process -no-cef-sandbox -no-browser -disablehighdpi -skipinitialbootstrap` + `MEM_HIGH_AGGR_MB`=1000 + cgroup CPU/IO weights, and the three CEF process flags were confirmed on the g15 to crash-loop `steamwebhelper` (SIGTRAP in `libcef` ~every 10 s, no usable client) on a current Steam build — a genuine per-build hazard, NOT the NtfsForceMount drive this time (mount was clean). `--aggressive` is now a `SUPPRESS`ed CLI no-op that prints a deprecation note and enables the standard tier; `AGGRESSIVE_FLAGS`/`MEM_HIGH_AGGR_MB`/`_AGGR_SCOPE_PROPS` deleted; a legacy `X-TuxThrottle-LowResource=aggressive` marker on disk still reads as `on` so `off` cleans it. Do not re-add those flags without per-build testing. **Autostart-create:** if `~/.config/autostart/steam.desktop` is absent and `autostart` (default true), `on` creates it (same flags, `-silent` → Steam comes up hidden to the tray on login) stamped `X-TuxThrottle-Created=true`; `off` deletes a created one, restores a pre-existing one from `.tuxthrottle-bak`. `_stamp()` normalises the marker line; `_patch_exec_lines(text, flags)` now takes the flag set and self-normalises an already-patched line so re-`on` at a different level lands. `status` → `off` / `on` (+`+autostart`). CLI: `on [--no-autostart]`. **Only affects Steam launched from the app menu / autostart — not a running instance or a KDE pinned-taskbar entry (caches its own Exec); user must fully quit + relaunch.** Trade-off: manual chat sign-in. GUI **Steam client — low-resource mode** box on Game Tools (`_build_steamperf_box` + `_sp_set`/`_sp_refresh`/`_sp_worker`/`_sp_poll`; Enable/Disable, `_sp_autostart` toggle, status label). |
| `tuxthrottle_shadercache.py` | stdlib helper. One configurable folder for **every** shader/pipeline cache — `mesa-shader-cache` / `dxvk-state-cache` / `nv-shader-cache` / `steam-shadercache` subdirs. Config `~/.config/tuxthrottle/shadercache.json` `{dir, max_size_gb}` (defaults `~/.cache/tuxthrottle-shaders`, 80). `show [--json]` prints 3 sizes (total / Steam's cache / the other three combined, via `du -sb` + `_human`); `set <dir> [--size GB]` persists + `ensure_dirs()` **and `_relink_steam_shadercache()`** — repoints any existing Steam `steamapps/shadercache` symlink at the new folder (without this, moving the cache dir left the link dangling → **Steam "disk write error"** on every download/verify, a real bug hit 2026-09-01); `link-steam [--undo]` moves each Steam library's `steamapps/shadercache` into `steam-shadercache/` and symlinks it back (refuses while Steam runs; **now also repairs a broken/mispointed existing symlink** instead of skipping it); `link-check [--json]` → `steam_link_status()` reports each library's link as `ok` / `broken` (dangling or elsewhere) / `unlinked`, exit 1 if any broken; `clean [name|all]` empties a subdir (optional — caches self-rebuild). **`rebuild [all|<AppID>]` (2026-09-03)** deletes **only Steam's** shader cache (the fossilize cache in `steamapps/shadercache` + our linked `steam-shadercache/` folder) so Steam regenerates it on next launch — Mesa/DXVK/NVIDIA caches untouched; `_all_steam_shadercache_dirs()` covers our folder + every library's real `steamapps/shadercache` (resolve-deduped); refuses while Steam runs. **`steam-bg-shaders {on|off|status}`** flips `config.vdf` → `…/ShaderCacheManager/EnableShaderBackgroundProcessing` = Steam Settings→Downloads→"Allow background processing of Vulkan shaders" (stops the `fossilize_replay` CPU burn); backs the file up, atomic `os.replace`, refuses while Steam runs. GUI **Shader / pipeline cache storage** box on Game Tools (`_build_shadercache_box`: dir Entry + Browse, size Spinbox, Save location, Link Steam cache, **Check links** → `_sc_link_check` (bg worker → `_sc_link_lbl`, auto-runs on open + logs a repair hint when broken), Show sizes, Clean, **"Force-rebuild Steam's shader cache" → `_sc_rebuild`** (`rebuild all` via `_run_stream` — Steam's fossilize cache only), and a **Steam background Vulkan shader processing** Off/On pair → `_sc_bg_shaders` + a status label refreshed by `_sc_bg_refresh`/`_sc_bg_poll`); `_lo_refresh` points `MESA_SHADER_CACHE_DIR` / `DXVK_STATE_CACHE_PATH` / `__GL_SHADER_DISK_CACHE_PATH` + sizes here; the `NvidiaShaderCache` tweak's `apply` bash-parses the same json for its `/etc/environment.d` file (re-apply after moving the dir — `_sc_save` auto-runs `apply_tweak.py NvidiaShaderCache` when that tweak is already installed). |
| `config/games.json` | Setup Games data. `{gid: {Content, Tab, order, Description, steps:[…]}}`. Each step: `id`, `title`, `desc`, optional `check` (bash, exit 0 = done), optional **`show_if`** (bash, exit 0 = show the card — evaluated off-thread in `_refresh_game_steps`; non-zero → `card.pack_forget()`, re-packed after `prev_card` if it later passes), then either `run` (bash streamed to the log; user-context bits go through `su - {USER} -c '…'`) or `manual: true` (+ optional `copy` string for the Copy button). A `run` step whose `check` passes has its button greyed to "✓ Already done" (`_apply_game_state`). `{USER}` / `{TOOLKIT_DIR}` / `{APPID}` substituted in `check`/`show_if`/`run`/`copy`. The `prefixfs` step (all four GTA entries) gates on `tuxthrottle_prefix_relocate.py {APPID} --relevant` — shown only when the game's Steam library is a colon-hostile (NTFS/exFAT, i.e. non-Steam) drive: `needs-fix`/`symlink` → show, `ok`/native-Linux/not-installed → hide. Missing file → the page is skipped. GTA V Online steps mirror README "GTA V Online → Route A". |
| `install.sh` | system-wide install → `/opt/tuxthrottle`, launcher, hicolor icon, `/usr/share/applications` desktop entry. Also stamps `/opt/tuxthrottle/.version` (date-based `YY.MM.DD` from the last commit). At the end it runs `apply_tweak.py <id> --only-if-present` for `KbdBacklightFix` + `CpuMaxPerformance` so an **already-enabled** service tweak's unit files + `/usr/local/bin` scripts get refreshed to the new version (no-op if the feature was never turned on). `--uninstall` removes just the app. |
| `apply_tweak.py` | runs one tweak's `apply` list from `config/tweaks.json` (`{USER}`/`{TOOLKIT_DIR}` substituted). `--only-if-present` = do nothing unless the tweak's `check` passes now OR a path in its `reinstall_if` glob-list exists; exits 3 when skipped. Used by `install.sh`; also standalone (`sudo python3 apply_tweak.py KbdBacklightFix`). |
| `uninstall.sh` | remove the tool (default: app + per-user config, tweaks kept). `--purge` also undoes every tweak's system bits (services, helper scripts, sudoers, drop-ins); `--grub` / `--fstab` / `--pip` / `--all` for the boot-affecting extras. Never touches installed apps. |
| `verify-install.sh` | read-only post-install sanity check — run on the target (`sudo ./verify-install.sh`): no legacy residue, `/opt/tuxthrottle` intact, launcher + `--report` + module self-tests OK, **tier-3 modules present + `model_id()==g15-5515` + `tuxthrottlectl daemon status` runs**, GUI builds with "Report a Bug" wired, hw-bundle prefix. Prints `N passed, M failed` (**27/0 on the g15**). |
| `models/` | one JSON hardware profile per supported board (`g15-5515.json` = the reference), matched on DMI by `sensors.model_profile()` / `model_id()` (falls back to `g15-5515`; `_`-prefixed files like `_test-fixture.json` are never auto-matched). **Load-bearing since Phase 0 (2026-08-31):** `sensors.py` reads the CPU/fan hwmon names, `platform_profile` path, PWM floor, fan count, the per-fan boost attribute, the max RPM and which `platform_profile` value = Game Mode from the matched profile via `_cpu_temp_hwmon()` / `_fan_hwmon()` / `_fan_pwm_hwmon()` / `_platform_profile_path()` / `_pwm_floor()` / `_fan_indices()` / `_fan_boost_attr()` / `_fan_rpm_max()` / `_game_mode_value()`; `tuxthrottle_kbd.py` reads `openrgb_device` + `usb`; `hotkey_listener.py` reads `gkey.device` + `keycode_fnlock_off`. Every field falls back to the 5515 value when absent. `sensors.model_allows(models)` + `sensors.model_skips_tweak(id)` drive `_apply_vendor_gate`'s per-board hiding (`"models": [...]` list gate + the profile's `tweaks_skip`). `TUXTHROTTLE_MODEL=<slug>` forces a profile (dev/testing; loud stderr warning). The `config/*.json` `check`/`apply` command strings are still 5515-specific — gate per model with `"models"`. See `models/README.md`. |
| `tuxthrottle_modelgen.py` | **stdlib, `import sensors`.** `build_scaffold()` probes this machine (DMI, CPU/fan hwmon, fan count, `platform_profile` choices, GPU PCI ids, OpenRGB device, battery method) → a `models/<slug>.json` scaffold with undetectable fields left `null` and listed under `_todo`. CLI + `tuxthrottlectl collect-model [--slug] [--out]`. Never writes without `--out`. |
| `clients/` | optional panel front-ends, shipped to `/opt`: `waybar/tuxthrottle-waybar` (a `return-type:json` module over `tuxthrottlectl status --json`, `--toggle` flips balanced↔performance), `plasmoid/package/` (a Plasma 6 applet, `executable` DataSource polling `tuxthrottlectl`, install via `kpackagetool6`), and `mangohud/tuxthrottle-mangohud` (a one-line `exec=` bridge for MangoHud — profile + CPU W/°C + dGPU °C, read-only). All read-mostly; the profile buttons need root (daemon socket or a sudoers rule). |
| `packaging/` | `tuxthrottle.spec` (noarch — pure Python/JSON/shell, installs the tree to `/opt/tuxthrottle` to match `install.sh`, `%version`/`%release`/`%gittag` via `--define`) + `packaging/README.md`. `.github/workflows/copr.yml` builds the SRPM on a `v*` tag (or manual dispatch) and hands it to COPR (`COPR_CONFIG`/`COPR_PROJECT` secrets). Tweaks stay opt-in — never touched by `%post`. **Excluded from `install.sh`'s `/opt` copy.** |
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
- **The software `rainbow_wave` / `gradient_wave` daemons were deleted**
  2026-08-31 (branch `chore/kbd-dead-code`), along with `_Sdk`, `_stream_wave`,
  `stop_fx`, `fx.pid`, the OKLab/OKLCH colour maths and the
  `rainbow-test`/`gradient-test` self-checks. A software per-LED wave can't work
  here anyway: the controller repaints irregularly at ~2–3 fps over USB *and*
  is single-zone. `verify-install.sh` now just smoke-tests `tuxthrottle_kbd.py
  --help`.
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
  a warning, plus a closed-loop **custom fan curve** (10-point table →
  `tuxthrottle_powerd.py`, additive boost only).
- **CPU TDP (ryzenadj) — verified on the g15.** `ryzenadj -i` parses fine
  (`read_ryzenadj_info`); setting `--stapm/--fast/--slow-limit` moves the
  limits live. **The Cezanne SMU floors STAPM near the `slow` limit** — you
  can't set STAPM much below `slow`, so `_TDP_PRESETS` / `powerd.TDP_PRESETS`
  keep STAPM ≥ slow. Dell's stock envelope here is 65 / 65 / 54 W
  (STAPM / fast / slow).
- **Battery charge threshold — the 5515 has no
  `/sys/class/power_supply/BAT*/charge_control_end_threshold`.** Use Dell
  firmware instead: `libsmbios` → `smbios-battery-ctl --get-charging-cfg` /
  `--set-custom-charge-interval=<start> <end>` (start<end, 50–100, gap ≥ 5;
  `--set-charging-mode=standard` for 100 %). Stored in NVRAM, persists with
  no service. `DellBatteryThreshold` tweak = `dnf install libsmbios`.
- **NVIDIA board power limit is firmware-locked on the RTX 3050 Ti Mobile.**
  `nvidia-smi --query-gpu=power.limit` → `[N/A]`; `nvidia-smi -pl <n>` →
  "Changing power management limit is not supported in current scope" **and
  still exits 0**. `nvidia_power_limit_info()` returns `supported: False`
  (query worked, `power.limit` is N/A) so the GUI shows a note, not a slider.
  Code kept for laptops where `-pl` works.
- **`dnf` GPG**: Nobara serves some rawhide-based (`.fc44`) packages signed
  with the **Fedora 44** key, which ships on disk un-imported. Fix once with
  `sudo rpm --import /etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-44-primary` (the
  Updates tab has a "Fix Fedora GPG keys" button).
- **Updates tab** wraps `nobara-sync` (check-updates / `cli` / install-updates
  / install-fixups / repair) + per-manager sections (dnf, Flatpak, fwupd) +
  an "Update everything". The pending-update counter uses `dnf -q --cacheonly
  check-update` — a plain `dnf check-update` can take **3+ min** on this box's
  mirrors, so the count is "as of last metadata sync" (shown as the age of
  `/var/cache/{dnf,libdnf5}/*/repodata/repomd.xml` via `_dnf_metadata_age()`)
  and self-corrects after any check/update.
- **RAPL** (`/sys/class/powercap/*/energy_uj`) is already world-readable on
  Nobara 43 — the `RaplPowerPermissions` tweak is a no-op there, so the GUI
  now `item.hidden`s it (unless its udev rule already exists, so an existing
  install can still undo it).
- Nobara ships `nobara-automount` (transient `/run/media` model) — this repo
  uses fstab instead for a stable path.
- **CPU-vendor detection for the Curve Optimizer:** the g15's DMI `sys_vendor`
  is `Dell Inc.`, **not** an AMD string — `ryzenadj_co_supported()` keys off
  `/proc/cpuinfo` `AuthenticAMD` (`sensors._cpu_is_amd()`), not DMI. `ryzenadj`
  can't read a CO offset back, so `co.json` is the only record of the current
  value; the boot service (`tuxthrottle_co_stress.py reapply`) re-applies it
  **only when `confirmed:true`**, so a hang before `confirm` is self-healing.
- **Control socket verified live on the g15** (2026-08-31): `tuxthrottled`
  creates `/run/tuxthrottle/control.sock` `srw-rw---- root root`,
  `tuxthrottlectl daemon status` returns the full JSON through it, and the
  socket is removed on SIGTERM. `ControlServer` must copy `self.methods` onto
  the inner `_Srv` instance — the `StreamRequestHandler` reads `self.server`,
  which is `_Srv`, not the `ControlServer` wrapper.
- The user is **not** in `input` by default (KDE); `HotkeyListener` adds them
  and must `systemctl --user` as the user with `XDG_RUNTIME_DIR` set, **not**
  `systemctl --user -M user@` (that fails to create the enable symlink).
- **KDE tweaks — gotchas learned the hard way (all 3 caused "applied but no
  effect / reverts"):**
  1. **`qdbus6` does not exist on Fedora 44 / Nobara** — the binary is
     `qdbus-qt6` (or plain `qdbus`). `dbus-send --session … org.kde.KWin
     .reconfigure` is the always-present fallback. Using `qdbus6` = silent
     no-op → KWin keeps stale in-memory config and flushes it back = "revert".
  2. **`kwriteconfig6` writes the file; the running component does not
     re-read it on its own.** After writing you must reload: KWin
     `reconfigure` (kwinrc groups), `kquitapp6 <daemon>` (kactivitymanagerd),
     or restart `plasmashell` (applet config in
     `plasma-org.kde.plasma.desktop-appletsrc`). And run the writes **as the
     user with their session bus** (`sudo -u {USER} -H env XDG_RUNTIME_DIR=…
     DBUS_SESSION_BUS_ADDRESS=unix:path=…/bus`), not `su - {USER} -c` (no bus).
  3. **KWin `reconfigure` re-reads effect config but does NOT unload a running
     effect.** To actually turn an effect off live you need
     `org.kde.kwin.Effects.unloadEffect "<name>"` (and `loadEffect` for the
     default-on ones on undo). `blurEnabled=false` alone leaves blur running.
  4. **Hot corners ≠ `[ElectricBorders]`.** That group is drag-to-edge window
     management. The corner-triggered *effects* (Overview default top-left,
     Present Windows, Desktop Grid) are `[Effect-overview]` /
     `[Effect-windowview]` / `[Effect-desktopgrid]` / `[TabBox]`
     `BorderActivate` — set to `9` (= "None" in the Screen Edges KCM).
  5. **`kactivitymanagerdrc [main] enabled=false` is not a real setting** —
     Activities is core Plasma, can't be disabled. The actual idle-cost knob
     is `kactivitymanagerd-pluginsrc [Plugins]
     org.kde.ActivityManager.ResourceScoringEnabled=false` (the usage
     journaling to SQLite).
  6. **Panel "floating" lives in TWO files.** `plasma-org.kde.plasma.desktop-appletsrc`
     `[Containments][N][General] floating=true/false` is only a hint — the
     effective panel gap is `plasmashellrc [PlasmaViews][Panel N] floating=1/0`
     (int, and any `…[Panel N][Screen M]` children). `tuxthrottle_kde_panel.py
     panel_floating()` writes both (`_plasmashell_view_groups()`), and
     `KdePanelFlush`'s `check` now also requires plasmashellrc `floating != 1`.
     Panel-only write = "flush tweak does nothing" (fixed 2026-09-01).

## Newer features (2026-09-01) — peer-comparison backlog, all on the g15

- **Panel refresh switch:** `sensors.panel_modes()` / `set_panel_refresh(hz)`
  (kscreen-doctor `-j`; `sensors._session_cmd()` hops root→user session for
  Wayland/D-Bus tools). GUI Power & Limits "Panel refresh rate" + `powerd.json`
  `autoswitch.refresh_ac`/`refresh_battery` (daemon flips on AC change). The
  5515 panel is **144 Hz** (60 Hz = mode id 2), resolution preserved.
- **NVIDIA graphics-clock lock:** `sensors.nvidia_clock_info()` /
  `set_nvidia_clock_lock(gr_min,gr_max)` / `reset_nvidia_clocks()`
  (`nvidia-smi --lock-gpu-clocks` — works where `-pl` is firmware-locked). GUI
  `_build_gpuclock_section`, state `nvclk.json`, `NvidiaClockLock` tweak
  (`/usr/local/bin/tuxthrottle-nvclk` + boot svc + sleep hook + sudoers).
- **`KdeMetaKeyOff` tweak** (`recommended`) — `kwinrc [ModifierOnlyShortcuts]
  Meta=""` (the "Win key drops me out of a game" fix); check via
  `kreadconfig6 --default __UNSET__`.
- **Tray menu:** `tray_monitor.py` "Power profile" + "Fan boost" submenus →
  `_ctl()` = `pkexec tuxthrottlectl set …` (sudo -n fallback).
- **Fan curve editor:** `FAN_CURVE_POINTS = 10` module constant, two-column
  grid, `_fc_resample()` pads an old 5-pt `powerd.json` on open, "Linear fill"
  button (place two endpoints, interpolate the rest). `powerd`'s `interp()`
  was already N-agnostic.
- **Profiles cover both new levers:** `tuxthrottle_profiles.capture_state()` /
  `apply_state()` now save `refresh_hz` (from `panel_modes()`) and `nvclk`
  (`{gr_min,gr_max}`, read from `nvclk.json` — the lock can't be queried back,
  same as the Curve Optimizer). apply writes `nvclk.json` via the shared
  `profiles.write_config(name, data|None, user)` so the boot service re-applies.
- **CLI / daemon parity:** `tuxthrottlectl set refresh <hz>` and `set gpu-clock
  {<max-mhz> [--min MHZ] | reset}`; matching `refresh` / `gpu-clock` targets in
  `tuxthrottle_powerd._build_dispatch()._set`. Both routed through the daemon
  socket when up, else direct.
- **USB PowerShare: tried, then dropped (2026-09-01).** The G15 5515 exposes no
  libsmbios token and no other board was available to test the parser against,
  so the `sensors.dell_usb_powershare()` helpers + Battery-tab control were
  removed rather than shipped as untested dead code. Revisit if a Dell that has
  "USB PowerShare" in BIOS setup turns up.
- **Time-of-day schedule (`schedule` block in `powerd.json`).**
  `tuxthrottle_powerd.ScheduleController` — rules `{from,to,apply,days}` where
  `apply` is a bundle (Quiet/Balanced/Performance) or a saved profile; `outside`
  applies when no rule matches. Overnight wrap (`from > to`) handled; acts only
  on a *change* of target; `sched.tick(cfg, games.active)` yields while a
  per-game profile is live. GUI `_build_schedule_section` on the Profiles tab.
- **Version string — date-based `YY.MM.DD` (Xylonic-style, since 2026-09-02).**
  The version *is* the day of the last commit; no manual bump, no git tags
  needed. `toolkit_version()` (and `install.sh`'s `.version` stamp):
  `.version` deploy stamp → `git log -1 --date=format:%y.%m.%d` from a checkout
  → the committed `VERSION` file (source tarball, no git) → "unknown". The
  `+g<sha>[-dirty]` suffix and the `git describe` fallback were dropped — a
  checkout with uncommitted edits still shows the last *pushed* date, clean.
  Keep `VERSION` (and the RPM `%version` default) roughly in sync with the
  latest release date for the no-git path; a `vYY.MM.DD` tag still feeds COPR
  if you cut one.
- **Bug-report bundle expanded for new-laptop onboarding.** `collect_hw_bundle()`
  now also drops **`model-scaffold.json`** (`tuxthrottle_modelgen.build_scaffold()`,
  no writes) plus `cpu.txt` (lscpu + `ryzenadj -i`), `battery.txt` (power_supply
  tree + upower + smbios-battery-ctl), `vendor-platform.txt` (`/sys/devices/platform`
  vendor WMI + `/sys/class/leds` + module params), `firmware.txt` (`fwupdmgr`),
  `dsdt.b64` (base64 ACPI DSDT/SSDT for `iasl -d`), `display.txt` (kscreen-doctor
  / xrandr / drm modes), `smbios-tokens.txt`, fuller `nvidia-smi -q`. README in
  the tarball spells out the finish-the-model steps. Run the collector as root.

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
  every `config/*.json` entry and hardware path assumes the G15 5515. The
  groundwork is in: `models/<slug>.json` + `sensors.model_profile()` / `model_id()`
  (DMI-matched, falls back to `g15-5515`) and the `Item.requires_models` gate.
  Still to do — actually route the `sensors.py` sysfs paths through the chosen
  profile so a second board works without code changes, and populate a real
  second `models/*.json` from that machine's `--collect` bundle.
- **Tier 3 status (2026-08-31):** COPR/RPM packaging (`packaging/`), thermal-event
  notifications (`ThermalWatcher`), Ryzen Curve Optimizer + stress harness
  (`tuxthrottle_co_stress.py` + `RyzenCurveOptimizer`), multi-model DMI gating
  groundwork (`models/`), panel-widget clients (`clients/`), and the single-writer
  control plane (`tuxthrottle_control.py` socket + daemon dispatch, GUI/CLI
  fall back to direct writes) are **done + on the g15**. The dead
  `rainbow_wave`/`gradient_wave`/`_Sdk`/`_stream_wave`/`stop_fx` keyboard code
  was **deleted** 2026-08-31 (branch `chore/kbd-dead-code`, 1293 → ~480 lines).
  Still deferred: the D-Bus/polkit step (socket is enough for now).

- **Generalisation effort (2026-08-31), done + on the g15:** `sensors.py` /
  `tuxthrottle_kbd.py` / `hotkey_listener.py` hardware specifics now come from
  `models/<slug>.json` (5515 values as fallback); `tuxthrottlectl collect-model`
  + `tuxthrottle_modelgen.py` scaffold a new board's profile; `TUXTHROTTLE_MODEL`
  env override; `sensors.model_allows()` / `model_skips_tweak()` gate tweaks per
  board; a **Battery** health page + a `clients/mangohud/` bridge; CI gained
  `ruff` (blocking) + advisory `mypy` / headless GUI-smoke.
- **D-Bus system-bus service: attempted then reverted (2026-08-31).** A
  `DbusPolkitIntegration` tweak's `system.d` bus policy **hard-bricked the g15's
  boot** — `dbus-broker` refuses to start the system bus over a `send_interface=`
  clause in an `<allow>` rule, and only parses that file at boot (passed a live
  `ReloadConfig`). The whole D-Bus layer was removed; **`/run/tuxthrottle/control.sock`
  is the only IPC control plane.** Any future D-Bus work: boot-test in a VM
  first, never `send_interface` in a policy `<allow>`.
- **polkit (no D-Bus): `PolkitTuxthrottlectl` tweak (2026-08-31).** Installs
  ONE `.policy` file (`polkit/org.tuxthrottle.policy`) to
  `/usr/share/polkit-1/actions/` — action `org.tuxthrottle.manage`,
  `allow_active=yes` (`allow_any`/`allow_inactive`=`auth_admin`),
  `exec.path=/usr/local/bin/tuxthrottlectl`. So `pkexec tuxthrottlectl set …` is
  passwordless for an active local user. No `.rules` file, no bus policy — a
  `.policy` file can't wedge anything; polkitd skips one it can't parse. The
  `apply` verifies with `pkaction` and self-rolls-back on rejection. The
  waybar (`_ctl_root`) + plasmoid (`setProfile`) clients use `pkexec
  tuxthrottlectl` for the profile switch.
- COPR release (direction A) still deferred. Full plan in `tasks/plan.md` + `tasks/todo.md`.
