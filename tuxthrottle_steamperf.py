#!/usr/bin/env python3
"""Steam "low-resource mode" — run the Steam *client* (not games) as light as
it goes on a low-end / hybrid laptop.

Steam's heaviness on Linux is almost entirely its embedded Chromium UI
(`steamwebhelper`): the store / library / friends views are web pages, GPU-
composited. On a machine like the G15 (iGPU-composited desktop, tight VRAM)
that GPU path also fights the compositor. This mode applies only the *safe*
levers — nothing that has been seen to break the client or get it OOM-killed:

    -silent                       start to the tray, don't paint a window until opened
    -cef-disable-gpu              no GPU acceleration in the web UI  (the big one)
    -cef-disable-gpu-compositing
    -cef-disable-breakpad         drop the web-UI crash reporter
    -cef-disable-extra-info-spew  quieter CEF logging (less log I/O)
    -noverifyfiles               skip the client file-integrity scan at startup
    -nobootstrapupdate           skip the bootstrap self-update check at startup
    -norepairfiles               don't run the auto file-repair pass at startup

The last three only cut the CPU/I-O spike each time Steam starts — Steam still
re-verifies on demand if it detects corruption. `off` removes them all.

Never used: a hard MemoryMax (that OOM-kills Steam), and the CEF process
flags -cef-single-process / -no-cef-sandbox / -no-browser. Those three were
an opt-in "--aggressive" tier until 2026-09-03, when they were confirmed to
crash-loop steamwebhelper (SIGTRAP in libcef roughly every 10 s, no usable
UI) on a current Steam build — the tier was removed. If Steam still looks
broken after `on`, check that your Steam-library drive is actually mounted;
an unmounted library looks identical (no games).

…the patched launcher also wraps Steam in a systemd scope with a **soft**
memory limit only (`systemd-run --user --scope -p MemoryHigh=1200M`):
MemoryHigh just makes the kernel reclaim page cache harder once Steam passes
~1.2 GB — it throttles, it never kills. No MemoryMax, no swap cap.

…plus every file-settable low-resource setting Steam has (needs Steam closed):
    localconfig.vdf  friends → SignIntoFriends = 0   (no auto chat → no friends
                                                      web-view renderer)
    localconfig.vdf  friends → AnimatedAvatars / AnimatedGameArt = 0
    config.vdf  ShaderCacheManager → EnableShaderBackgroundProcessing = 0
                                     (on ON only; OFF leaves it — it has its own
                                      toggle in the shader-cache box)

The Steam Overlay (system → EnableGameOverlay) is LEFT ON on purpose — Shift+Tab
and Steam screenshots stay working.

A few low-resource toggles live in Steam's internal store and can't be scripted
on the current UI — `on` prints them for you to tick by hand (see MANUAL_HINTS:
Library → Low Bandwidth Mode + Low Performance Mode, Interface → smooth
scrolling off, Downloads → Shader Pre-Caching off).

This installs the flags by writing a user-level `~/.local/share/applications/
steam.desktop` (which shadows the system launcher for the menu / tray) and
patching `~/.config/autostart/steam.desktop` if present. `off` removes the
override, restores autostart, and flips the localconfig keys back on. **Only
affects Steam started from the app menu / autostart — a running Steam or a
pinned-taskbar launcher keeps the old settings; fully quit and relaunch.**
Trade-off: you sign into chat manually. (The Steam Overlay stays on.)

Autostart: if there's no `~/.config/autostart/steam.desktop`, `on` creates one
(carrying the same flags) so Steam comes up on login straight to the tray,
never painting a window — `off` deletes the one it made. `--no-autostart`
skips that.

CLI:  tuxthrottle_steamperf.py on [--no-autostart]
      tuxthrottle_steamperf.py {off|status}
  (run as the real user; `status` → off / on [+autostart])
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import time
from pathlib import Path

MARKER = "X-TuxThrottle-LowResource"
FLAGS = ("-silent -cef-disable-gpu -cef-disable-gpu-compositing "
         "-cef-disable-breakpad -cef-disable-extra-info-spew "
         "-noverifyfiles -nobootstrapupdate -norepairfiles")

# Removed 2026-09-03: an opt-in `--aggressive` layer that added
# -cef-single-process / -no-cef-sandbox / -no-browser / -disablehighdpi /
# -skipinitialbootstrap plus a tighter MemoryHigh and cgroup CPU/IO weights.
# The three CEF process flags were confirmed to crash-loop steamwebhelper
# (SIGTRAP in libcef every ~10 s, no usable client) on a current Steam build,
# so the whole tier is gone. `--aggressive` is now a silently-deprecated
# no-op on the CLI. Do not re-add these flags without per-build testing.

# Soft memory pressure only. MemoryHigh throttles + makes the kernel reclaim
# page cache above the threshold (Chromium sheds its caches) — it NEVER kills a
# process. No MemoryMax (a hard cap is what OOM-kills Steam), no swap cap.
MEM_HIGH_MB = 1200


def _scope_prefix(mem_high_mb: int, extra_props: tuple = ()) -> str:
    props = [f"-p MemoryHigh={mem_high_mb}M", *(f"-p {p}" for p in extra_props)]
    return "systemd-run --user --scope --quiet --collect " + " ".join(props) + " -- "

_SYS_DESKTOP = "/usr/share/applications/steam.desktop"
_STEAM_BIN_RE = re.compile(r"^Exec=(?P<pre>.*?/)?steam(?P<post>\s+%[uU]\s*)$")
_PATCHED_MARK = ("-cef-disable-gpu", "MemoryHigh=")

# localconfig.vdf keys we flip to "0" (low-resource) / "1" (restore). Grouped
# by the block they live in; a key that's absent is inserted right after that
# block's opening brace. All are Steam's own supported low-resource levers —
# none break the client, none can OOM it.
_VDF_KEYS = {
    "friends": ("SignIntoFriends",      # don't auto-connect chat → no friends renderer
                "AnimatedAvatars",       # static avatars in the friends list
                "AnimatedGameArt"),      # static game art in chat
    # NOTE: the Steam Overlay (system → EnableGameOverlay) is deliberately left
    # ON — the user wants Shift+Tab and Steam screenshots. Disabling it would
    # save a bit of in-game RAM but that trade isn't wanted.
}
# Keys we no longer manage but may have set "0" in an earlier version — reset to
# "1" on every run so an upgrade un-does them.
_LEGACY_RESET = {"system": ("EnableGameOverlay",)}
# config.vdf: InstallConfigStore/Software/Valve/Steam/ShaderCacheManager/<key>.
# Forced to 0 when low-resource is turned ON; left as-is on OFF (the user has a
# dedicated toggle for it in the shader-cache box).
_SHADER_BG_KEY = "EnableShaderBackgroundProcessing"

# Settings that this tool CANNOT flip from a file on current Steam (the new UI
# keeps them in an internal store) — surfaced to the user to tick by hand.
MANUAL_HINTS = (
    "Steam → Settings → Library → enable “Low Bandwidth Mode” (no animated "
    "capsule / hero artwork downloads)",
    "Steam → Settings → Library → enable “Low Performance Mode” (no library "
    "animations / background video)",
    "Steam → Settings → Interface → disable “Enable smooth scrolling in web "
    "views”",
    "Steam → Settings → Downloads → disable “Enable Shader Pre-Caching” for the "
    "leanest possible (skips all pipeline pre-compilation)",
)
_STEAM_ROOTS = ("~/.steam/steam", "~/.local/share/Steam", "~/.steam/root")


def _steam_running() -> bool:
    try:
        import subprocess
        return subprocess.run(["pgrep", "-x", "steam"],
                              capture_output=True).returncode == 0
    except (OSError, ValueError):
        return False


def _localconfig_files() -> list[Path]:
    seen, out = set(), []
    for root in _STEAM_ROOTS:
        for f in glob.glob(os.path.expanduser(
                f"{root}/userdata/*/config/localconfig.vdf")):
            p = Path(f)
            try:
                key = p.resolve()
            except OSError:
                key = p
            if key not in seen and p.is_file():
                seen.add(key)
                out.append(p)
    return out


def _flip_block_keys(txt: str, block: str, keys: tuple, value: str,
                     insert_missing: bool = True) -> str:
    for k in keys:
        pat = re.compile(rf'("{k}"\s+")[01](")')
        if pat.search(txt):
            txt = pat.sub(rf"\g<1>{value}\g<2>", txt)
        elif insert_missing:
            txt = re.sub(rf'("{block}"\s*\n\s*\{{)',
                         rf'\g<1>\n\t\t\t"{k}"\t\t"{value}"', txt, count=1)
    return txt


def _write_atomic(p: Path, orig: str, txt: str) -> None:
    bak = p.with_name(p.name + ".tuxthrottle-bak")
    if not bak.exists():
        bak.write_text(orig)
    tmp = p.with_name(p.name + f".tux-tmp-{int(time.time())}")
    tmp.write_text(txt)
    os.replace(tmp, p)


def _steam_config_vdf() -> Path | None:
    for root in _STEAM_ROOTS:
        p = Path(os.path.expanduser(f"{root}/config/config.vdf"))
        if p.is_file():
            return p
    return None


def _apply_client_settings(low: bool) -> list[str]:
    """Flip every file-settable Steam low-resource lever: the localconfig.vdf
    friends/system keys, and (only when turning low-resource ON) the config.vdf
    background-shader key. value '0' = lean, '1' = restore. Skipped entirely
    while Steam runs (it rewrites these on exit)."""
    if _steam_running():
        return ["(Steam is running — client settings left unchanged; "
                "close Steam and press Enable again)"]
    value = "0" if low else "1"
    done = []
    for p in _localconfig_files():
        try:
            txt = p.read_text()
        except OSError:
            continue
        orig = txt
        for block, keys in _VDF_KEYS.items():
            txt = _flip_block_keys(txt, block, keys, value)
        for block, keys in _LEGACY_RESET.items():       # always undo old "0"s
            txt = _flip_block_keys(txt, block, keys, "1", insert_missing=False)
        if txt != orig:
            _write_atomic(p, orig, txt)
            done.append(str(p))
    if low:
        cf = _steam_config_vdf()
        if cf is not None:
            try:
                txt = cf.read_text()
                new = re.sub(rf'("{_SHADER_BG_KEY}"\s+")[01](")', r"\g<1>0\g<2>", txt)
                if new != txt:
                    _write_atomic(cf, txt, new)
                    done.append(f"{cf} (background shaders off)")
            except OSError:
                pass
    return done or ["(no config files needed changing)"]


def _user_desktop() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "applications" / "steam.desktop"


def _autostart() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "autostart" / "steam.desktop"


def _steam_bin() -> str:
    return shutil.which("steam") or "/usr/bin/steam"


def _stamp(text: str, val: str = "true") -> str:
    """Ensure exactly one `MARKER=<val>` line right under [Desktop Entry]."""
    lines = [ln for ln in text.splitlines() if not ln.startswith(MARKER + "=")]
    txt = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return txt.replace("[Desktop Entry]\n", f"[Desktop Entry]\n{MARKER}={val}\n", 1)


def _patch_exec_lines(text: str, flags: str = FLAGS,
                      mem_high_mb: int = MEM_HIGH_MB,
                      scope_props: tuple = ()) -> str:
    """Rewrite the primary `Exec=… steam %U` line (only that one — the steam://
    action Execs are left alone) to add `flags` and, if `systemd-run` is
    present, wrap it in the memory-scope prefix. Idempotent (a line already
    carrying our markers is first reset, then re-patched)."""
    have_run = shutil.which("systemd-run") is not None
    scope = _scope_prefix(mem_high_mb, scope_props)
    out = []
    for ln in text.splitlines():
        raw = ln.strip()
        if raw.startswith("Exec=") and "steam" in raw and any(t in raw for t in _PATCHED_MARK):
            # already patched (maybe with a different flag set) — normalise first
            m = re.search(r"(\S*/)?steam\b", raw)
            pre = (m.group(1) or "") if m else ""
            raw = f"Exec={pre}steam %U"
        m = _STEAM_BIN_RE.match(raw)
        if m:
            pre = m.group("pre") or ""
            core = f"{pre}steam {flags}{m.group('post').rstrip()}"
            out.append("Exec=" + (scope + core if have_run else core))
        else:
            out.append(ln)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _unpatch_exec_lines(text: str) -> str:
    """Reverse of _patch_exec_lines: any `[Desktop Entry]` Exec line we patched
    goes back to `Exec=<steam-bin> %U`."""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("Exec=") and "steam" in s and any(t in s for t in _PATCHED_MARK):
            m = re.search(r"(\S*/)?steam\b", s)
            pre = (m.group(1) or "") if m else ""
            out.append(f"Exec={pre}steam %U")
        else:
            out.append(ln)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _base_desktop_body() -> str:
    src = Path(_SYS_DESKTOP)
    if src.is_file():
        return src.read_text()
    return ("[Desktop Entry]\nType=Application\nName=Steam\n"
            f"Exec={_steam_bin()} %U\nIcon=steam\nTerminal=false\n"
            "Categories=Network;FileTransfer;Game;\n")


def enable(autostart: bool = True) -> tuple[bool, str]:
    flags = FLAGS
    mem = MEM_HIGH_MB

    def _patch(text: str) -> str:
        return _stamp(_patch_exec_lines(text, flags, mem, ()), "true")

    body = _patch(_base_desktop_body())
    dst = _user_desktop()
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(body)
    did = [str(dst)]

    au = _autostart()
    bak = au.with_name(au.name + ".tuxthrottle-bak")
    if au.is_file() and MARKER not in au.read_text():
        # a real pre-existing Steam autostart entry — back it up, patch in place
        if not bak.exists():
            bak.write_text(au.read_text())
        au.write_text(_patch(au.read_text()))
        did.append(f"{au} (patched)")
    elif au.is_file() and MARKER in au.read_text():
        # ours from a previous run — regenerate so a flag/level change lands
        created = "X-TuxThrottle-Created=true" in au.read_text()
        base = bak.read_text() if bak.is_file() else _base_desktop_body()
        new = _patch(base)
        if created:
            new = new.replace("[Desktop Entry]\n",
                              "[Desktop Entry]\nX-TuxThrottle-Created=true\n", 1)
        au.write_text(new)
        did.append(f"{au} (updated)")
    elif autostart:
        # no autostart entry at all + the user wants Steam to come up hidden
        au.parent.mkdir(parents=True, exist_ok=True)
        new = _patch(_base_desktop_body()).replace(
            "[Desktop Entry]\n", "[Desktop Entry]\nX-TuxThrottle-Created=true\n", 1)
        au.write_text(new)
        did.append(f"{au} (created — Steam autostarts to the tray, -silent)")

    settings = _apply_client_settings(True)
    cap = (f"memory scope: MemoryHigh={mem}M (soft — reclaims, never kills)"
           if shutil.which("systemd-run") else "memory scope: (systemd-run absent — skipped)")
    hints = "\n".join(f"    • {h}" for h in MANUAL_HINTS)
    return True, ("Steam low-resource mode ON — " + ", ".join(did)
                  + "\n  " + cap
                  + "\n  client settings (no auto chat / no friends animations / "
                  "no bg shaders; overlay kept): " + "; ".join(settings)
                  + "\n\nFully QUIT Steam (tray → Quit) and relaunch it from the "
                  "application menu — a Steam that's still running, or one "
                  "started from a pinned taskbar entry, keeps the old settings."
                  + "\n\nAlso tick these by hand (Steam keeps them in its own "
                  "store, can't be scripted):\n" + hints)


def disable() -> tuple[bool, str]:
    done = []
    dst = _user_desktop()
    if dst.is_file() and MARKER in dst.read_text():
        dst.unlink()
        done.append(f"removed {dst}")
    au = _autostart()
    bak = au.with_name(au.name + ".tuxthrottle-bak")
    if au.is_file() and "X-TuxThrottle-Created=true" in au.read_text():
        au.unlink()                                   # we made it — remove it
        if bak.is_file():
            bak.unlink()
        done.append(f"removed {au} (we created it)")
    elif bak.is_file():
        au.write_text(bak.read_text())
        bak.unlink()
        done.append(f"restored {au}")
    elif au.is_file() and MARKER in au.read_text():
        # patched with no backup — drop the marker line + un-patch the Exec
        txt = "\n".join(ln for ln in au.read_text().splitlines()
                        if not ln.startswith(MARKER + "="))
        au.write_text(_unpatch_exec_lines(txt))
        done.append(f"cleaned {au}")
    desktop_changed = bool(done)
    settings = _apply_client_settings(False)
    settings_changed = not (settings[0].startswith("(no ")
                            or settings[0].startswith("(Steam is running"))
    if not desktop_changed and not settings_changed:
        return True, "Steam low-resource mode was not enabled — nothing to do"
    done.append("client settings restored: " + "; ".join(settings))
    return True, ("Steam low-resource mode OFF — " + "; ".join(done)
                  + "\n(the background-shader setting is left as-is — use its own "
                  "toggle in the shader-cache box)"
                  + "\nRestart Steam for it to take effect.")


def status() -> str:
    """'off' / 'on' — plus '+autostart' when we created the login entry.
    A legacy 'X-TuxThrottle-LowResource=aggressive' marker also reads as 'on'."""
    dst = _user_desktop()
    try:
        if not (dst.is_file() and MARKER in dst.read_text()):
            return "off"
    except OSError:
        return "off"
    val = "on"
    au = _autostart()
    try:
        if au.is_file() and "X-TuxThrottle-Created=true" in au.read_text():
            val += "+autostart"
    except OSError:
        pass
    return val


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("state", choices=["on", "off", "status"])
    ap.add_argument("--aggressive", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-autostart", action="store_true",
                    help="don't create a hidden-Steam login entry if none exists")
    a = ap.parse_args(argv)
    if os.geteuid() == 0:
        print("run as the real user, not root", flush=True)
        return 2
    if a.state == "status":
        print(status())
        return 0
    if a.state == "on":
        if a.aggressive:
            print("note: --aggressive was removed (it crash-looped steamwebhelper); "
                  "enabling standard low-resource mode instead", flush=True)
        ok, msg = enable(autostart=not a.no_autostart)
    else:
        ok, msg = disable()
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
