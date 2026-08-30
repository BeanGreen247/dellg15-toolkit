#!/usr/bin/env python3
"""Move a Steam/Proton compatdata prefix off a filesystem that can't host it.

NTFS (ntfs3, mounted with `windows_names`) and exFAT/FAT reject ':' in a
filename, so Proton can't create the `pfx/dosdevices/c:` … drive-letter
symlinks and the prefix build dies with:

    OSError: [Errno 22] Invalid argument:
        '<lib>' -> '<lib>/steamapps/compatdata/<appid>/pfx/dosdevices/s:'

…and the game never starts. Steam has no "prefix elsewhere" option, so the
fix is to keep the game where it is, move just its `compatdata/<appid>`
directory onto a Linux-native drive, and drop a symlink back in its place
(the symlink name is the numeric appid — no ':' — so NTFS accepts it).

Usage:
    python3 tuxthrottle_prefix_relocate.py <appid>            # do the move
    python3 tuxthrottle_prefix_relocate.py <appid> --check    # exit 0 if OK,
                                                              # 1 if it needs moving
    python3 tuxthrottle_prefix_relocate.py --scan             # list every prefix
                                                              # and its status

Run it as the real user (not root); close Steam / the game first.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

STEAM_ROOTS = ["~/.local/share/Steam", "~/.steam/steam", "~/.steam/root"]


def steam_root() -> Path:
    for c in STEAM_ROOTS:
        p = Path(os.path.expanduser(c))
        if (p / "steamapps" / "libraryfolders.vdf").is_file():
            return p.resolve()
    sys.exit("no Steam install found (looked in %s)" % ", ".join(STEAM_ROOTS))


def all_libraries(root: Path) -> list[Path]:
    """Every library-folder path in libraryfolders.vdf (root's own included)."""
    vdf = (root / "steamapps" / "libraryfolders.vdf").read_text(
        encoding="utf-8", errors="replace")
    libs = [Path(p) for p in re.findall(r'"path"\s*"([^"]+)"', vdf)]
    if root not in libs:
        libs.insert(0, root)
    return [p for p in libs if p.is_dir()]


def library_for_appid(root: Path, appid: str) -> Path | None:
    """The library-folder whose libraryfolders.vdf block lists <appid>, or
    (fallback) whichever library actually has its appmanifest on disk."""
    vdf = (root / "steamapps" / "libraryfolders.vdf").read_text(
        encoding="utf-8", errors="replace")
    best: str | None = None
    for m in re.finditer(r'"path"\s*"([^"]+)"(.*?)(?=\n\t*"\d+"\s*\n\t*{|\Z)',
                         vdf, re.S):
        path, body = m.group(1), m.group(2)
        if re.search(r'"%s"\s' % re.escape(appid), body):
            best = path
    if best:
        return Path(best)
    for lib in all_libraries(root):
        if (lib / "steamapps" / f"appmanifest_{appid}.acf").is_file() or \
           (lib / "steamapps" / "compatdata" / appid).exists():
            return lib
    return None


def appid_name(root: Path, appid: str) -> str:
    for lib in all_libraries(root):
        acf = lib / "steamapps" / f"appmanifest_{appid}.acf"
        try:
            m = re.search(r'"name"\s*"([^"]+)"', acf.read_text(errors="replace"))
            if m:
                return m.group(1)
        except OSError:
            pass
    return ""


def colon_ok(directory: Path) -> bool:
    """True if `directory`'s filesystem allows ':' in a filename."""
    probe = directory / "tuxthrottle_colon_probe:tmp"
    try:
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def classify(lib: Path, appid: str) -> tuple[str, Path]:
    """Return (status, prefix_path). status ∈ {symlink, absent, ok, needs-fix}."""
    src = lib / "steamapps" / "compatdata" / appid
    if src.is_symlink():
        return "symlink", src
    probe = src if src.is_dir() else (lib / "steamapps" / "compatdata")
    if not probe.exists():
        return "absent", src
    return ("ok" if colon_ok(probe) else "needs-fix"), src


def steam_running() -> bool:
    try:
        return subprocess.run(["pgrep", "-x", "steam"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


def do_scan(root: Path) -> int:
    seen: set[str] = set()
    rows: list[tuple[str, str, str, str]] = []
    for lib in all_libraries(root):
        cd = lib / "steamapps" / "compatdata"
        if not cd.is_dir():
            continue
        for entry in sorted(cd.iterdir(), key=lambda p: p.name):
            if not entry.name.isdigit() or entry.name in seen or entry.name == "0":
                continue
            seen.add(entry.name)
            status, _ = classify(lib, entry.name)
            rows.append((entry.name, appid_name(root, entry.name) or "?",
                         status, str(lib)))
    if not rows:
        print("no Proton prefixes found.")
        return 0
    w = max(len(n) for n, *_ in rows)
    need = 0
    for appid, name, status, lib in rows:
        flag = {"needs-fix": "NEEDS FIX", "symlink": "relocated",
                "ok": "ok", "absent": "not built"}[status]
        if status == "needs-fix":
            need += 1
        print(f"  {appid:<{w}}  {flag:<10}  {name[:44]:<44}  {lib}")
    print()
    if need:
        print(f"{need} prefix(es) NEED FIX — run:  "
              f"python3 tuxthrottle_prefix_relocate.py <appid>   (Steam closed)")
    else:
        print("all prefixes are fine.")
    return 0


def do_one(root: Path, appid: str, check: bool) -> int:
    lib = library_for_appid(root, appid)
    if lib is None:
        print(f"appid {appid} not found in any Steam library — nothing to do "
              f"(is it installed? launched once?)")
        return 0

    status, src = classify(lib, appid)
    dst = root / "steamapps" / "compatdata" / appid

    if status == "symlink":
        print(f"OK — prefix already relocated: {src} -> {os.readlink(src)}")
        return 0
    if status == "absent":
        print(f"OK — no prefix on disk yet ({src} absent); it'll build in place.")
        return 0
    if status == "ok":
        print(f"OK — {src} is on a filesystem that allows ':' in names; "
              f"no relocation needed.")
        return 0

    # status == needs-fix
    if check:
        print(f"NEEDS FIX — {src} is on a filesystem that rejects ':' in "
              f"filenames; Proton can't build drive-letter links there.")
        return 1

    if steam_running():
        sys.exit("Steam is running — close Steam (and the game) first, then retry.")
    if src.resolve() == dst.resolve():
        sys.exit(f"the game's own library ({lib}) is the bad filesystem and it "
                 f"is also the main Steam library — move the game to a Linux "
                 f"drive instead.")
    if not src.exists():
        sys.exit(f"{src} vanished — aborting")
    if dst.exists() or dst.is_symlink():
        sys.exit(f"{dst} already exists — move or remove it first, then retry")

    name = appid_name(root, appid)
    print(f"moving prefix for {name or appid}:")
    print(f"  {src}\n    ->  {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    os.symlink(str(dst), str(src))
    print(f"done — {src} is now a symlink to {dst}")
    print("re-launch the game from Steam; the prefix will finish building on "
          "the Linux drive.")
    return 0


def main() -> int:
    if os.geteuid() == 0 and not os.environ.get("TUXTHROTTLE_ALLOW_ROOT"):
        sys.exit("run this as your normal user, not root (it edits ~/.local/share/Steam)")

    root = steam_root()
    if "--scan" in sys.argv:
        return do_scan(root)

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args or not args[0].isdigit():
        sys.exit("usage: tuxthrottle_prefix_relocate.py <appid> [--check] | --scan")
    return do_one(root, args[0], "--check" in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
