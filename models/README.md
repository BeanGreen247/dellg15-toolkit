# `models/` — per-board hardware profiles

One JSON file per supported laptop, keyed by DMI. `sensors.model_profile()`
reads the current machine's `/sys/class/dmi/id/{product_name,board_name}` and
returns the first file whose `match` block matches, falling back to
**`g15-5515`** (the reference platform — see `../CLAUDE.md`).

## Why it exists

Every `check` / `apply` command in `config/*.json` and every sysfs path in
`sensors.py` was written for the Dell G15 5515. To add another board without
breaking that one:

1. Collect the target's hardware with `python3 tuxthrottle.py --collect` on it
   and open a "New hardware support" issue with the bundle.
2. Add `models/<slug>.json` (copy `g15-5515.json`, fill in its fan hwmon
   names, pwm paths, keycodes, RGB controller, battery method).
3. Gate any tweak/app that differs by adding `"models": ["g15-5515", "<slug>"]`
   to its entry in `config/tweaks.json` / `apps.json`. An entry **without** a
   `models` key applies everywhere (current behaviour — nothing regresses).

## Schema (informal)

| key | meaning |
|---|---|
| `id` / `name` | slug (must equal the filename stem) and a human label |
| `match` | `{product_name: [...], board_name: [...]}` — any value matches |
| `cpu` / `igpu` / `dgpu` | vendor, driver, PCI id, ryzenadj support, stock PPT |
| `fans` | hwmon names, RPM inputs, additive-boost attrs, pwm paths, floor, platform_profile path + values |
| `game_mode` | how Game Mode is toggled on this board |
| `keyboard` | RGB backend, OpenRGB device name, zone count, usable effects |
| `gkey` | evdev device + keycodes for the dedicated performance key |
| `battery` | sysfs threshold vs libsmbios, allowed interval |
| `tweaks_skip` | tweak ids that must never run on this board |

The fields are advisory today — `sensors.py` still hard-codes the 5515 paths.
Wiring the profile through `sensors.py` (so a second board Just Works) is the
next step; the file + the `models:` gate are the foundation for it.
