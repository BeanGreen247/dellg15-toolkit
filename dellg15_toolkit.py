#!/usr/bin/env python3
"""Dell G15 5515 (Ryzen Edition) Toolkit — Nobara Linux.

Checkbox-driven GUI for hardware-specific tweaks, drivers, and gaming
software, built the same way as the Windows UltimateToolkit this mirrors:
data-driven JSON config, live status detection, apply/undo, presets.
Inspired by Div-Acer-Manager-Max (DAMX): https://github.com/PXDiv/Div-Acer-Manager-Max

Not a general-purpose distro tool — targets this one laptop's hardware only.

Requires: ttkbootstrap (pip install --user ttkbootstrap — confirmed NOT
packaged in Fedora/Nobara's repos, pip is the only install path) for the
themed dark UI + round-toggle switches + gauge widgets on the Dashboard tab.
"""
import configparser
import json
import os
import pwd
import queue
import re
import shlex
import shutil
import site
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
ASSETS_DIR = BASE_DIR / "assets"
sys.path.insert(0, str(BASE_DIR))

try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import SUCCESS, SECONDARY, WARNING, INFO, DANGER
except ImportError:
    print("ttkbootstrap not found. Install with: pip install --user ttkbootstrap")
    print("(not packaged in Fedora/Nobara's repos — pip is the only path)")
    sys.exit(1)

import sensors  # noqa: E402  (local module, no GUI deps)

try:
    import dellg15_kbd  # noqa: E402  (AW-ELC RGB keyboard, stdlib-only)
except Exception:  # noqa: BLE001
    dellg15_kbd = None

CATEGORY_ORDER = ["Gaming", "GPU", "Power", "Performance", "Software", "Monitoring", "Streaming", "RGB"]
THEME = "darkly"


def resolve_real_user() -> str:
    for var in ("PKEXEC_UID", "SUDO_UID"):
        val = os.environ.get(var)
        if val:
            try:
                return pwd.getpwuid(int(val)).pw_name
            except (KeyError, ValueError):
                pass
    for var in ("SUDO_USER", "PKEXEC_USER"):
        val = os.environ.get(var)
        if val:
            return val
    return pwd.getpwuid(os.getuid()).pw_name


DISPLAY_VARS = ["DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"]


def self_elevate():
    if os.geteuid() == 0:
        return
    script = str(Path(__file__).resolve())
    # pkexec/sudo scrub the environment on re-exec, dropping DISPLAY/XAUTHORITY
    # (or their Wayland equivalents) — without these the elevated process
    # can't reach the X/Wayland session at all ("no display name" crash).
    present = {v: os.environ[v] for v in DISPLAY_VARS if v in os.environ}

    # pkexec/sudo re-exec as root, which no longer sees the invoking user's
    # ~/.local/lib/pythonX.Y/site-packages — where `pip install --user
    # ttkbootstrap` (the documented install path, since it isn't packaged for
    # Fedora/Nobara) lands. Carry that dir forward on PYTHONPATH so the import
    # at the top of this file still resolves after elevation.
    user_site = site.getusersitepackages()
    if os.path.isdir(user_site):
        existing = os.environ.get("PYTHONPATH", "")
        present["PYTHONPATH"] = f"{user_site}:{existing}" if existing else user_site

    if shutil.which("pkexec"):
        env_pairs = [f"{k}={v}" for k, v in present.items()]
        os.execvp("pkexec", ["pkexec", "env", *env_pairs, sys.executable, script])
    if shutil.which("sudo"):
        args = ["sudo"]
        if present:
            args.append("--preserve-env=" + ",".join(present))
        args += [sys.executable, script]
        os.execvp("sudo", args)
    print("Need root. Run: sudo python3 dellg15_toolkit.py")
    sys.exit(1)


def _maximize(root: "tb.Window") -> None:
    """Start maximised. '-zoomed' is the reliable path on X11/XWayland (KDE);
    fall back to sizing the window to the screen if the WM rejects it."""
    try:
        root.attributes("-zoomed", True)
        root.update_idletasks()
        if root.winfo_width() > 100:  # WM honoured it
            return
    except tk.TclError:
        pass
    try:
        root.state("zoomed")  # works on some builds/WMs
        return
    except tk.TclError:
        pass
    root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")


def load_json(name: str) -> dict:
    path = CONFIG_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
#  Look & feel — a "gaming BIOS" dark shell with the KDE accent colour.
# --------------------------------------------------------------------------- #

ACCENT_FALLBACK = "#3daee9"   # Breeze blue, if the desktop accent can't be read
# One flat surface for every widget background (frames, labels, labelframes,
# scales, nav rail) — a per-widget mismatch here is what showed up as "black
# boxes" behind labels/sliders. BIOS_SUNKEN is only used for scale/progress
# troughs and the window ground behind everything.
BIOS_PANEL = "#141a21"        # the surface — all widget backgrounds
BIOS_SUNKEN = "#0b0e12"       # troughs / window ground (darker, so troughs read)
BIOS_BG = BIOS_SUNKEN         # back-compat alias (busy overlay etc.)
BIOS_PANEL_HI = "#1e2731"     # hover / selected nav row
BIOS_FG = "#e6edf3"
BIOS_MUTED = "#93a1b1"
BIOS_BORDER = "#2a3340"


def _rgb_str_to_hex(s: str) -> str | None:
    nums = [int(n) for n in re.findall(r"\d+", s)][:3]
    return "#%02x%02x%02x" % tuple(nums) if len(nums) == 3 else None


def read_desktop_accent(default: str = ACCENT_FALLBACK) -> str:
    """Best-effort read of the Plasma accent colour from ~/.config/kdeglobals
    (of the *invoking* user, since we run elevated). Falls back to a preset."""
    user = resolve_real_user()
    try:
        home = pwd.getpwnam(user).pw_dir
    except KeyError:
        home = os.path.expanduser("~")
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        cp.read(os.path.join(home, ".config", "kdeglobals"))
    except (configparser.Error, OSError):
        return default
    for sec, key in (("General", "AccentColor"),
                     ("Colors:Selection", "DecorationFocus"),
                     ("Colors:Selection", "BackgroundNormal")):
        if cp.has_option(sec, key):
            hexv = _rgb_str_to_hex(cp.get(sec, key))
            if hexv:
                return hexv
    return default


def _mix(hex_a: str, hex_b: str, t: float) -> str:
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _rel_luminance(hex_c: str) -> float:
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(int(hex_c[i:i + 2], 16)) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg: str, bg: str) -> float:
    a, b = _rel_luminance(fg), _rel_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def readable_on(fg: str, bg: str, target: float = 4.5) -> str:
    """Return `fg`, or a lightened/darkened version of it, so it clears the
    WCAG contrast `target` against `bg`. Used so a dark desktop accent colour
    doesn't become unreadable text on the dark panels."""
    if _contrast_ratio(fg, bg) >= target:
        return fg
    toward = "#ffffff" if _rel_luminance(bg) < 0.4 else "#000000"
    cand = fg
    for i in range(1, 21):
        cand = _mix(fg, toward, i / 20.0)
        if _contrast_ratio(cand, bg) >= target:
            return cand
    return cand


def apply_bios_style(style: "tb.Style", accent: str) -> None:
    """Re-skin the ttkbootstrap 'darkly' base into the BIOS look. All wrapped
    defensively — a theming quirk must never take the app down."""
    # accent as *text* on the dark panels must stay legible whatever the
    # desktop accent is
    accent_txt = readable_on(accent, BIOS_PANEL, 4.5)
    accent_txt_hi = readable_on(accent, BIOS_PANEL_HI, 4.5)
    try:
        c = style.colors
        c.primary = accent
        c.info = accent
        c.selectbg = accent
        c.bg = BIOS_PANEL
        c.dark = BIOS_PANEL
        c.light = BIOS_PANEL
        c.border = BIOS_BORDER
        c.active = BIOS_PANEL_HI
        c.inputbg = BIOS_SUNKEN
    except Exception:  # noqa: BLE001
        pass

    specs = {
        ".": {"background": BIOS_PANEL, "foreground": BIOS_FG,
              "fieldbackground": BIOS_SUNKEN, "troughcolor": BIOS_SUNKEN,
              "bordercolor": BIOS_BORDER, "lightcolor": BIOS_PANEL,
              "darkcolor": BIOS_PANEL},
        "TFrame": {"background": BIOS_PANEL},
        "TLabel": {"background": BIOS_PANEL, "foreground": BIOS_FG},
        "TLabelframe": {"background": BIOS_PANEL, "bordercolor": BIOS_BORDER,
                        "darkcolor": BIOS_PANEL, "lightcolor": BIOS_PANEL,
                        "relief": "flat"},
        "TLabelframe.Label": {"background": BIOS_PANEL, "foreground": accent_txt,
                              "font": ("Sans", 10, "bold")},
        "TCheckbutton": {"background": BIOS_PANEL, "foreground": BIOS_FG},
        "TRadiobutton": {"background": BIOS_PANEL, "foreground": BIOS_FG},
        "TSeparator": {"background": BIOS_BORDER},
        "Nav.TFrame": {"background": BIOS_PANEL},
        "Header.TLabel": {"background": BIOS_PANEL, "foreground": BIOS_FG,
                          "font": ("Sans", 18, "bold")},
        "Horizontal.TProgressbar": {"background": accent, "troughcolor": BIOS_SUNKEN,
                                    "bordercolor": BIOS_SUNKEN, "lightcolor": accent,
                                    "darkcolor": accent},
        "Horizontal.TScale": {"background": BIOS_PANEL, "troughcolor": BIOS_SUNKEN},
        "TScale": {"background": BIOS_PANEL, "troughcolor": BIOS_SUNKEN},
        "Nav.TButton": {"background": BIOS_PANEL, "foreground": BIOS_MUTED,
                        "bordercolor": BIOS_PANEL, "focuscolor": "",
                        "font": ("Sans", 10, "bold"), "anchor": "w",
                        "padding": (16, 11), "relief": "flat"},
        "NavActive.TButton": {"background": BIOS_PANEL_HI, "foreground": accent_txt_hi,
                              "bordercolor": accent, "focuscolor": "",
                              "font": ("Sans", 10, "bold"), "anchor": "w",
                              "padding": (16, 11), "relief": "flat"},
    }
    for name, opts in specs.items():
        try:
            style.configure(name, **opts)
        except Exception:  # noqa: BLE001
            pass
    hover = readable_on(_mix(accent, "#ffffff", 0.18), BIOS_PANEL_HI, 4.0)
    for name in ("Nav.TButton", "NavActive.TButton"):
        try:
            style.map(name, background=[("active", BIOS_PANEL_HI)],
                      foreground=[("active", hover)])
        except Exception:  # noqa: BLE001
            pass


class RingGauge(tk.Canvas):
    """A self-drawn 270° ring gauge — replaces ttkbootstrap's Meter, which
    doesn't re-colour cleanly under the custom theme and rendered thin/odd.
    `set(value)` redraws; `size` scales the whole thing."""

    def __init__(self, master, *, caption="", unit="", maximum=100.0,
                 color=None, size=150, fmt="{:.0f}"):
        super().__init__(master, width=size, height=size + 20,
                         bg=BIOS_PANEL, highlightthickness=0, bd=0)
        self._max = float(maximum) or 1.0
        self._color = color or ACCENT_FALLBACK
        self._size = size
        self._unit = unit
        self._caption = caption
        self._fmt = fmt
        self._ring = max(8, size // 11)       # ring thickness
        self._value = 0.0
        self._draw()

    def _draw(self):
        self.delete("all")
        s, w = self._size, self._ring
        pad = w // 2 + 3
        box = (pad, pad, s - pad, s - pad)
        frac = max(0.0, min(1.0, self._value / self._max))
        # track + value arc — 270° sweep with a symmetric gap at the bottom
        self.create_arc(*box, start=225, extent=-270, style="arc",
                        outline=BIOS_SUNKEN, width=w)
        if frac > 0.001:
            self.create_arc(*box, start=225, extent=-270 * frac, style="arc",
                            outline=self._color, width=w)
        self.create_text(s / 2, s / 2 - 4, text=self._fmt.format(self._value),
                         fill=BIOS_FG, font=("Sans", int(s * 0.19), "bold"))
        if self._unit:
            self.create_text(s / 2, s / 2 + int(s * 0.16), text=self._unit,
                             fill=BIOS_MUTED, font=("Sans", int(s * 0.09)))
        self.create_text(s / 2, s + 8, text=self._caption, fill=BIOS_MUTED,
                         font=("Sans", 9))

    def set(self, value):
        try:
            self._value = float(value or 0.0)
        except (TypeError, ValueError):
            self._value = 0.0
        self._draw()


class SidebarNav(tb.Frame):
    """Minimal drop-in for tb.Notebook that renders a left nav rail + a single
    swapped content pane and a big page header (gaming-BIOS layout).

    Pages are still created as `tb.Frame(self)` and registered with
    `.add(frame, text=...)`; `.tabs()` / `.tab()` / `.select()` keep the few
    Notebook call-sites (and the smoke tests) working."""

    def __init__(self, master):
        super().__init__(master)
        self.rail = tb.Frame(self, width=212, style="Nav.TFrame")
        self.rail.pack(side="left", fill="y")
        self.rail.pack_propagate(False)
        tb.Separator(self, orient="vertical").pack(side="left", fill="y")

        right = tb.Frame(self)
        right.pack(side="left", fill="both", expand=True)
        self._header = tb.Label(right, text="", style="Header.TLabel",
                                anchor="w", padding=(24, 18, 24, 14))
        self._header.pack(fill="x")
        tb.Separator(right).pack(fill="x")
        self._stack = tb.Frame(right)
        self._stack.pack(fill="both", expand=True)

        self._pages: list = []      # (text, frame, button)
        self._current = None

    def add(self, frame, text: str = ""):
        frame.master  # noqa: B018  (frame was created as tb.Frame(self); fine)
        btn = tb.Button(self.rail, text=text, style="Nav.TButton",
                        takefocus=False, command=lambda f=frame: self.select(f))
        btn.pack(fill="x", padx=0, pady=1)
        self._pages.append((text, frame, btn))
        if self._current is None:
            self.select(frame)

    def select(self, frame=None):
        if frame is None:
            return self._current
        for text, f, b in self._pages:
            on = f is frame
            try:
                b.configure(style="NavActive.TButton" if on else "Nav.TButton")
            except tk.TclError:
                pass
            if on:
                f.pack(in_=self._stack, fill="both", expand=True)
                self._header.configure(text=text)
            else:
                f.pack_forget()
        self._current = frame

    # ---- tb.Notebook compatibility ----
    def tabs(self):
        return [str(f) for _, f, _ in self._pages]

    def tab(self, ref, option="text"):
        if isinstance(ref, int):
            return self._pages[ref][0]
        for text, f, _ in self._pages:
            if f is ref or str(f) == str(ref):
                return text
        return ""


class Item:
    """A tweak or app entry, normalized for the GUI."""

    def __init__(self, item_id: str, data: dict, kind: str, user: str):
        self.id = item_id
        self.kind = kind  # "tweak" or "app"
        self.content = data.get("Content", item_id)
        self.description = data.get("Description", "")
        self.category = data.get("category", "Other")
        self.risk = data.get("risk", "safe")
        self.requires_vendor = data.get("requires_vendor")  # "nvidia" | "amd" | None
        self.hw_supported = True  # set by ToolkitApp after GPU detection

        def sub(cmd: str) -> str:
            return cmd.replace("{USER}", user).replace("{TOOLKIT_DIR}", str(BASE_DIR))

        self.check_cmd = sub(data.get("check", ""))
        # Optional: a tweak whose real effect only lands after a reboot (kernel
        # cmdline) can declare `check_pending` — true once the change is staged
        # in the bootloader but not yet live. The UI shows "Pending reboot" and
        # Apply/Presets skip it, so the user doesn't re-select and re-run it.
        self.check_pending_cmd = sub(data.get("check_pending", ""))
        if kind == "tweak":
            self.apply_cmds = [sub(c) for c in data.get("apply", [])]
            self.undo_cmds = [sub(c) for c in data.get("undo", [])]
        else:
            manager = data.get("manager", "dnf")
            if "install" in data:
                self.apply_cmds = [sub(c) for c in data["install"]]
            elif manager == "dnf":
                self.apply_cmds = [f"dnf install -y {data.get('package', item_id)}"]
            elif manager == "flatpak":
                pkg = data.get("package", item_id)
                self.apply_cmds = [
                    "dnf install -y flatpak",
                    "flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo",
                    f"flatpak install -y flathub {pkg}",
                ]
            else:
                self.apply_cmds = []
            self.undo_cmds = []
        # live status — set by _refresh_all_status. `state` is the single
        # source of truth; `applied`/`pending` are kept as derived bools so the
        # rest of the code doesn't change.
        #   applied      check exited 0
        #   not_applied  check exited non-zero, cleanly
        #   pending      check_pending exited 0 (staged, needs reboot)
        #   error        the check command couldn't run (not our no/yes answer)
        #   drifted      we applied it OK (per the ledger) but the check now fails
        #   failed       our last apply/undo of this item failed (per the ledger)
        self.state = "unknown"
        self.check_rc: int | None = None
        self.check_out = ""
        self.ledger: dict | None = None   # {"action","ok","ts","note"} or None
        self.var = None  # tk.BooleanVar, set when widget built
        self.status_label = None
        self.checkbutton = None

    @property
    def applied(self) -> bool:
        return self.state == "applied"

    @property
    def pending(self) -> bool:
        return self.state == "pending"

    @property
    def done(self) -> bool:
        """Already in the desired state — nothing to (re-)apply."""
        return self.state in ("applied", "pending")


def run_cmd(cmd: str) -> tuple[bool, str]:
    ok, _rc, out = run_cmd3(cmd)
    return ok, out


def run_cmd3(cmd: str, timeout: int = 1800) -> tuple[bool, int, str]:
    """(ok, returncode, combined-output). rc is -1 if the command itself
    couldn't be launched (used to tell "check says no" from "check broke")."""
    try:
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode == 0, r.returncode, out
    except Exception as exc:  # noqa: BLE001
        return False, -1, str(exc)


# --------------------------------------------------------------------------- #
#  Apply ledger — what the toolkit itself has applied/undone, and how it went.
#  The per-tweak `check` command is still the source of truth for the *current*
#  state; the ledger adds "...and we're the ones who set it" / "...and our last
#  attempt failed", which is how "Reverted" and "Apply failed" are told apart
#  from a plain "Not applied".
# --------------------------------------------------------------------------- #

def _ledger_path() -> Path:
    try:
        home = Path(pwd.getpwnam(resolve_real_user()).pw_dir)
    except (KeyError, Exception):  # noqa: BLE001
        home = Path.home()
    return home / ".config" / "dellg15-toolkit" / "state.json"


def ledger_load() -> dict:
    try:
        d = json.loads(_ledger_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def ledger_record(item_id: str, action: str, ok: bool, note: str = "") -> None:
    p = _ledger_path()
    data = ledger_load()
    data[item_id] = {"action": action, "ok": bool(ok),
                     "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "note": note[:200]}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, sort_keys=True))
        if os.geteuid() == 0:
            pw = pwd.getpwnam(resolve_real_user())
            os.chown(p, pw.pw_uid, pw.pw_gid)
            os.chown(p.parent, pw.pw_uid, pw.pw_gid)
    except (OSError, KeyError):
        pass


# state key → (short label, ttkbootstrap style). App items relabel
# applied/not_applied to installed/not installed at render time.
_STATE_UI = {
    "applied":     ("Applied", SUCCESS),
    "pending":     ("Pending reboot", INFO),
    "not_applied": ("Not applied", SECONDARY),
    "error":       ("Check error", WARNING),
    "drifted":     ("Reverted", WARNING),
    "failed":      ("Apply failed", DANGER),
    "unsupported": ("unsupported", SECONDARY),
    "unknown":     ("checking…", SECONDARY),
}


def format_status_report(items) -> str:
    """Plain-text table of every item's state + the check that decided it +
    the toolkit's last action. Used by the GUI dialog and `--report`."""
    rows = sorted(items, key=lambda i: (i.category, i.kind, i.content.lower()))
    out = [f"Dell G15 Toolkit — status report   {time.strftime('%Y-%m-%d %H:%M:%S')}",
           "=" * 100]
    cur = None
    counts: dict[str, int] = {}
    for it in rows:
        counts[it.state] = counts.get(it.state, 0) + 1
        if it.category != cur:
            cur = it.category
            out.append(f"\n[{cur}]")
        led = it.ledger
        led_s = (f"{led['action']} {'ok' if led['ok'] else 'FAILED'} {led['ts']}"
                 + (f" — {led['note']}" if led.get("note") else "")) if led else "—"
        rc = "" if it.check_rc in (None, 0) else f" (rc {it.check_rc})"
        out.append(f"  {it.state.upper():<12} {it.content[:44]:<44} "
                   f"check{rc}: {it.check_cmd[:60] or '(none)'}")
        if it.check_out and it.state in ("error", "drifted", "failed", "not_applied"):
            out.append(f"    ↳ check said: {it.check_out.splitlines()[-1][:88]}")
        if led:
            out.append(f"    ↳ toolkit:    {led_s}")
    out.append("\n" + "-" * 100)
    out.append("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return "\n".join(out) + "\n"


def evaluate_item(item: "Item", ledger: dict) -> None:
    """Run `item`'s check(s), consult the ledger, and set item.state /
    check_rc / check_out / ledger."""
    item.check_rc, item.check_out = None, ""
    item.ledger = ledger.get(item.id)
    if not item.hw_supported:
        item.state = "unsupported"
        return
    if not item.check_cmd:
        item.state = "not_applied"
        return
    ok, rc, out = run_cmd3(item.check_cmd)
    item.check_rc, item.check_out = rc, out[:600]
    if ok:
        item.state = "applied"
        return
    led = item.ledger
    if rc in (-1, 127):
        item.state = "error"
    elif item.check_pending_cmd and run_cmd3(item.check_pending_cmd)[0]:
        item.state = "pending"
    elif led and led.get("action") == "apply" and led.get("ok"):
        item.state = "drifted"        # we applied it OK; something undid it
    elif led and not led.get("ok"):
        item.state = "failed"         # our last apply/undo of it errored
    else:
        item.state = "not_applied"


class ToolkitApp:
    def __init__(self, root: "tb.Window"):
        self.root = root
        self.user = resolve_real_user()
        root.title("Dell G15 5515 Toolkit — Nobara Linux")
        root.geometry("1080x760")  # fallback size if the WM ignores maximise
        _maximize(root)
        self._set_window_icon(root)

        self.accent = read_desktop_accent()
        try:
            apply_bios_style(root.style, self.accent)
            root.configure(background=BIOS_PANEL)
        except Exception:  # noqa: BLE001
            self.accent = ACCENT_FALLBACK

        self.has_nvidia = sensors.has_nvidia_gpu()
        self.has_amd = sensors.has_amd_gpu()

        self.items: dict[str, Item] = {}
        self._load_items()
        self.presets = load_json("presets.json")

        self.log_queue: queue.Queue = queue.Queue()
        self.dash_queue: queue.Queue = queue.Queue()
        self.status_queue: queue.Queue = queue.Queue()
        self.worker_running = False
        self.gamemode_var = tk.BooleanVar(value=False)
        self._suppress_gamemode_signal = False

        # App-wide "a long task is running" lock: every tab's long operation
        # (Apply Selected, presets, system updates) calls _begin_busy() on the
        # main thread and hands _end_busy() back via _busy_queue when done.
        # While busy, a click-eating overlay covers the whole notebook and the
        # footer buttons disable, so nothing else can be launched mid-run.
        self._busy = False
        self._busy_queue: queue.Queue = queue.Queue()
        self._prog_q: queue.Queue = queue.Queue()   # (overall:int|None, step:str|None, phase:str|None)
        self._busy_overlay = None
        self._busy_steps = 0
        self._cur_step = ""
        self._footer_btns: list = []

        self._scroll_canvases: list = []   # every scrollable tab body (for the wheel)
        self._log_lines: list[str] = []    # full log buffer, mirrored to any popped-out window
        self._pop_win = None               # detached log Toplevel, when open
        self._pop_text = None
        self._log_collapsed = False

        self._build_ui()
        self.root.after(100, self._poll_log_queue)
        self.root.after(100, self._poll_dash_queue)
        self.root.after(100, self._poll_status_queue)
        self.root.after(120, self._poll_busy_queue)
        threading.Thread(target=self._refresh_all_status, daemon=True).start()

        self.dash_running = True
        threading.Thread(target=self._dashboard_loop, daemon=True).start()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self.dash_running = False
        self._fan_live = False
        if self._pop_win is not None:
            try:
                self._pop_win.destroy()
            except tk.TclError:
                pass
        self.root.destroy()

    # ---------- scrolling ----------

    def _scroll_body(self, parent, pad: int = 0):
        """A vertically-scrollable frame. Returns the inner frame to fill.
        Mouse-wheel is handled globally by _global_wheel via _scroll_canvases."""
        canvas = tk.Canvas(parent, highlightthickness=0, bg=self.root.style.colors.bg)
        vsb = tb.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = tb.Frame(canvas, padding=pad)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        self._scroll_canvases.append(canvas)
        return inner

    def _global_wheel(self, event):
        w = self.root.winfo_containing(event.x_root, event.y_root)
        if w is None:
            return
        wp = str(w)
        for cv in self._scroll_canvases:
            cp = str(cv)
            if wp == cp or wp.startswith(cp + "."):
                num, delta = getattr(event, "num", 0), getattr(event, "delta", 0)
                step = -1 if (num == 4 or delta > 0) else 1
                cv.yview_scroll(step, "units")
                return

    def _set_window_icon(self, root):
        for name in ("icon-256.png", "icon-128.png", "icon.png"):
            path = ASSETS_DIR / name
            if not path.is_file():
                continue
            try:
                self._icon_img = tk.PhotoImage(file=str(path))  # keep a reference
                root.iconphoto(True, self._icon_img)
            except tk.TclError:
                pass
            return

    def _load_items(self):
        tweaks = load_json("tweaks.json")
        apps = load_json("apps.json")
        for item_id, data in tweaks.items():
            item = Item(item_id, data, "tweak", self.user)
            self._apply_vendor_gate(item)
            self.items[item_id] = item
        for item_id, data in apps.items():
            item = Item(item_id, data, "app", self.user)
            self._apply_vendor_gate(item)
            self.items[item_id] = item

    def _apply_vendor_gate(self, item: Item):
        if item.requires_vendor == "nvidia" and not self.has_nvidia:
            item.hw_supported = False
            item.description += "  (no NVIDIA GPU detected on this system — disabled)"
        elif item.requires_vendor == "amd" and not self.has_amd:
            item.hw_supported = False
            item.description += "  (no AMD GPU detected on this system — disabled)"

    # ---------- UI construction ----------

    def _build_ui(self):
        # global mouse-wheel dispatch for every scrollable tab body
        for seq in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
            self.root.bind_all(seq, self._global_wheel, add="+")

        header = tb.Frame(self.root, padding=(16, 12, 16, 8))
        header.pack(fill="x")
        if getattr(self, "_icon_img", None) is not None:
            try:
                small = self._icon_img.subsample(max(1, self._icon_img.width() // 40))
                tb.Label(header, image=small).pack(side="left", padx=(0, 12))
                self._icon_small = small  # keep a ref
            except tk.TclError:
                pass
        titlebox = tb.Frame(header)
        titlebox.pack(side="left")
        tb.Label(titlebox, text="Dell G15 5515 Toolkit",
                 font=("Sans", 16, "bold")).pack(anchor="w")
        tb.Label(titlebox,
                 text="Nobara Linux · Ryzen 7 5800H + RTX 3050 Ti — this board only",
                 font=("Sans", 9), bootstyle=SECONDARY).pack(anchor="w")
        tb.Label(header, text=f"elevated · {self.user}",
                 bootstyle=(SECONDARY, "inverse"), font=("Sans", 8, "bold"),
                 padding=(8, 3)).pack(side="right")

        # actual DMI identity of the machine this is running on
        m = sensors.detect_model()
        if m["is_target"]:
            txt = f"✓  Detected: {m['vendor']} {m['product']}" + (f" (board {m['board']})" if m['board'] else "") + \
                  f", BIOS {m['bios']} — matches the target platform."
            style = SUCCESS
        elif m["is_close"]:
            txt = (f"⚠  Detected: {m['vendor']} {m['product']} — a G15 5515 variant, but not the exact "
                   f"unit this was built against. Most things should work; some sysfs paths may differ.")
            style = WARNING
        else:
            txt = (f"⚠  Detected: {m['vendor']} {m['product']} — this is NOT a Dell G15 5515. "
                   f"The Toolkit's checks and tweaks are written for that board; expect breakage.")
            style = DANGER
        tb.Label(self.root, text=txt, bootstyle=style, padding=(16, 2, 16, 8),
                 wraplength=1600, justify="left").pack(fill="x")

        tb.Separator(self.root).pack(fill="x")

        self.notebook = SidebarNav(self.root)
        self.notebook.pack(fill="both", expand=True)
        self._content = self.notebook   # overlay target for _begin_busy

        self._build_dashboard_tab()
        self._build_keyboard_tab()
        self._build_fan_tab()
        self._build_presets_tab()
        self._build_updates_tab()
        self._build_diagnostics_tab()

        categories = sorted(
            {item.category for item in self.items.values()},
            key=lambda c: CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else 99,
        )
        for cat in categories:
            self._build_category_tab(cat)

        # ---- footer: actions + status ----
        tb.Separator(self.root).pack(fill="x", padx=16)
        btn_bar = tb.Frame(self.root, padding=(16, 10))
        btn_bar.pack(fill="x")
        btn_refresh = tb.Button(btn_bar, text="↻  Refresh Status", bootstyle=(INFO, "outline"),
                                command=self._on_refresh_click)
        btn_refresh.pack(side="left")
        btn_apply = tb.Button(btn_bar, text="✓  Apply Selected", bootstyle=SUCCESS,
                              command=self._on_apply_click)
        btn_apply.pack(side="left", padx=8)
        btn_report = tb.Button(btn_bar, text="≣  Status report", bootstyle=(SECONDARY, "outline"),
                               command=self._show_status_report)
        btn_report.pack(side="left")
        self._footer_btns = [btn_refresh, btn_apply, btn_report]
        self.status_var = tk.StringVar(value="Ready.")
        tb.Label(btn_bar, textvariable=self.status_var, bootstyle=SECONDARY,
                 font=("Sans", 9)).pack(side="right")
        self._busy_bar = tb.Progressbar(btn_bar, mode="indeterminate", length=160,
                                        bootstyle=(INFO, "striped"))
        # packed only while busy (see _begin_busy / _end_busy)

        # ---- log console (collapsible / detachable) ----
        self.log_frame = tb.Frame(self.root, padding=(16, 0, 16, 12))
        self.log_frame.pack(fill="both", expand=False)
        bar = tb.Frame(self.log_frame)
        bar.pack(fill="x", pady=(0, 4))
        tb.Label(bar, text="LOG", font=("Sans", 8, "bold"), bootstyle=SECONDARY).pack(side="left")
        self.log_popout_btn = tb.Button(bar, text="⇱ pop out", bootstyle=(SECONDARY, "link"),
                                        command=self._toggle_log_popout)
        self.log_popout_btn.pack(side="right")
        self.log_collapse_btn = tb.Button(bar, text="▾ hide", bootstyle=(SECONDARY, "link"),
                                          command=self._toggle_log_collapse)
        self.log_collapse_btn.pack(side="right")
        self.log_text = self._make_log_text(self.log_frame)
        self.log_text.pack(fill="both", expand=True)
        self._toggle_log_collapse()   # start collapsed; expand on demand

    @staticmethod
    def _make_log_text(parent) -> tk.Text:
        t = tk.Text(parent, height=9, font=("Monospace", 9), bg="#0e1116", fg="#c9d1d9",
                    insertbackground="#c9d1d9", relief="flat", wrap="word",
                    padx=10, pady=8, borderwidth=0)
        t.configure(state="disabled")
        return t

    def _toggle_log_collapse(self):
        self._log_collapsed = not self._log_collapsed
        if self._log_collapsed:
            self.log_text.pack_forget()
            self.log_collapse_btn.configure(text="▸ show")
        else:
            self.log_text.pack(fill="both", expand=True)
            self.log_collapse_btn.configure(text="▾ hide")

    def _toggle_log_popout(self):
        if self._pop_win is None:
            self._pop_win = tk.Toplevel(self.root)
            self._pop_win.title("Dell G15 5515 Toolkit — Log")
            self._pop_win.geometry("900x480")
            if getattr(self, "_icon_img", None) is not None:
                try:
                    self._pop_win.iconphoto(True, self._icon_img)
                except tk.TclError:
                    pass
            self._pop_text = self._make_log_text(self._pop_win)
            self._pop_text.pack(fill="both", expand=True, padx=8, pady=8)
            self._pop_text.configure(state="normal")
            self._pop_text.insert("end", "\n".join(self._log_lines[-2000:]) + ("\n" if self._log_lines else ""))
            self._pop_text.configure(state="disabled")
            self._pop_text.see("end")
            self._pop_win.protocol("WM_DELETE_WINDOW", self._toggle_log_popout)
            if not self._log_collapsed:
                self._toggle_log_collapse()
            self.log_popout_btn.configure(text="⇲ dock")
        else:
            try:
                self._pop_win.destroy()
            except tk.TclError:
                pass
            self._pop_win = self._pop_text = None
            self.log_popout_btn.configure(text="⇱ pop out")
            if self._log_collapsed:
                self._toggle_log_collapse()

    def _build_dashboard_tab(self):
        outer = tb.Frame(self.notebook)
        self.notebook.add(outer, text="Dashboard")
        frame = self._scroll_body(outer, pad=16)

        gauges = tb.Frame(frame)
        gauges.pack(fill="x", pady=(0, 18))
        acc = getattr(self, "accent", ACCENT_FALLBACK)
        specs = [
            ("meter_cpu_temp",  "CPU temp",  "°C",  100, acc,       "{:.0f}"),
            ("meter_cpu_freq",  "CPU clock", "GHz", 5.0, "#3fb950", "{:.2f}"),
            ("meter_cpu_power", "CPU power", "W",    65, acc,       "{:.0f}"),
            ("meter_dgpu_temp", "dGPU temp", "°C",  100, "#d29922", "{:.0f}"),
            ("meter_dgpu_util", "dGPU util", "%",   100, "#f85149", "{:.0f}"),
            ("meter_dgpu_power","dGPU power","W",    80, "#d29922", "{:.0f}"),
        ]
        for i, (attr, cap, unit, mx, col, fmt) in enumerate(specs):
            g = RingGauge(gauges, caption=cap, unit=unit, maximum=mx,
                          color=col, fmt=fmt)
            g.grid(row=0, column=i, padx=8, sticky="n")
            gauges.columnconfigure(i, weight=1)
            setattr(self, attr, g)

        self.rapl_warning = tb.Label(
            frame, text="", bootstyle=WARNING, wraplength=900,
        )
        self.rapl_warning.pack(anchor="w", pady=(0, 12))

        details = tb.Labelframe(frame, text="Details", padding=12)
        details.pack(fill="x", pady=(0, 20))
        self.dash_cpu_label = tb.Label(details, text="CPU: …", font=("Monospace", 10))
        self.dash_cpu_label.pack(anchor="w")
        self.dash_igpu_label = tb.Label(details, text="iGPU: …", font=("Monospace", 10))
        self.dash_igpu_label.pack(anchor="w")
        self.dash_dgpu_label = tb.Label(details, text="dGPU: …", font=("Monospace", 10))
        self.dash_dgpu_label.pack(anchor="w")

        toggle_frame = tb.Labelframe(frame, text="Game Mode", padding=16)
        toggle_frame.pack(fill="x")
        row = tb.Frame(toggle_frame)
        row.pack(fill="x")
        tb.Checkbutton(
            row, text="Performance profile + GPU perf-state forcing",
            variable=self.gamemode_var, bootstyle="round-toggle",
            command=self._on_gamemode_toggle,
        ).pack(side="left")
        tb.Label(
            toggle_frame,
            text="Same effect as pressing the G-key or clicking the tray icon. "
                 "Needs the Power/GPU tweaks below installed first.",
            bootstyle=SECONDARY, wraplength=900,
        ).pack(anchor="w", pady=(6, 0))

    # ---------- keyboard RGB tab ----------

    _KBD_PRESETS = [
        ("White", "#ffffff"), ("Red", "#ff0000"), ("Green", "#00ff00"),
        ("Blue", "#0000ff"), ("Cyan", "#00ffff"), ("Magenta", "#ff00ff"),
        ("Amber", "#ff6a00"),
    ]

    def _build_keyboard_tab(self):
        outer = tb.Frame(self.notebook)
        self.notebook.add(outer, text="Keyboard")
        frame = self._scroll_body(outer, pad=16)

        import shutil as _sh
        have_openrgb = _sh.which("openrgb") is not None
        detected = dellg15_kbd is not None and dellg15_kbd.Keyboard._find() is not None
        if not have_openrgb:
            tb.Label(
                frame, bootstyle=WARNING, justify="left", wraplength=1000,
                text="Install the OpenRGB app (Software tab) to use this.\n\n"
                     "The G15 5515's AW-ELC keyboard has no kernel driver and ignores raw HID "
                     "writes — OpenRGB's 16-zone protocol is the only thing that drives it. "
                     "The backlight must also be enabled in BIOS setup (F2 -> Keyboard "
                     "Backlight) or nothing lights.",
            ).pack(anchor="w")
            return
        if not detected:
            tb.Label(
                frame, bootstyle=SECONDARY, justify="left",
                text="No Alienware AW-ELC RGB keyboard (USB 187c:0550) found.\n"
                     "This tab drives the 4-zone RGB backlight on the Dell G15 5515.",
            ).pack(anchor="w")
            return

        note = tb.Labelframe(frame, text="How this works", padding=12)
        note.pack(fill="x", pady=(0, 14))
        tb.Label(
            note, wraplength=1100, justify="left", bootstyle=SECONDARY,
            text="Driven through OpenRGB (the AW-ELC has no kernel driver and ignores raw HID). "
                 "The backlight must be enabled in BIOS setup (F2 -> Keyboard Backlight) first — "
                 "if the keys stay dark, that's why. It's a 4-zone board (Left / Middle / Right / "
                 "Numpad), not per-key. Effects run on the controller itself; speed is adjustable "
                 "(100 = fastest), direction is not — none of the six firmware modes exposes a "
                 "direction field. Colours don't persist a reboot on their own — apply the "
                 "KbdBacklightFix tweak (Power tab) to re-assert the last setting at login and "
                 "after resume.",
        ).pack(anchor="w")

        self._kbd_busy = False
        self.kbd_brightness = tk.IntVar(value=100)
        self.kbd_all_hex = tk.StringVar(value="#ffffff")
        self.kbd_speed = tk.IntVar(value=50)
        self.kbd_zone_vars: dict[int, tk.StringVar] = {}

        # pre-fill from saved state if present
        saved = dellg15_kbd.load_state()
        if saved:
            zc, br = saved
            self.kbd_brightness.set(br)
            if zc:
                r, g, b = tuple(sorted(zc.items())[0][1])
                self.kbd_all_hex.set("#%02x%02x%02x" % (r, g, b))
                for z, rgb in zc.items():
                    self.kbd_zone_vars.setdefault(z, tk.StringVar()).set("#%02x%02x%02x" % tuple(rgb))
        _meta = dellg15_kbd.load_meta()
        self.kbd_speed.set(_meta.get("speed", 50))
        # if the saved mode is a gradient, restore its anchors into the swatches
        _g = _meta.get("gradient") or {}
        for i, hexc in enumerate((_g.get("colors") or [])[:dellg15_kbd.ZONE_COUNT]):
            self.kbd_zone_vars.setdefault(i, tk.StringVar()).set("#" + hexc.lower())

        # ---- brightness ----
        br_box = tb.Labelframe(frame, text="Brightness", padding=12)
        br_box.pack(fill="x", pady=(0, 12))
        scale = tb.Scale(br_box, from_=0, to=100, variable=self.kbd_brightness, orient="horizontal")
        scale.pack(side="left", fill="x", expand=True, padx=(0, 10))
        scale.bind("<ButtonRelease-1>", lambda _e: self._kbd_apply_brightness())
        tb.Label(br_box, textvariable=self.kbd_brightness, width=4).pack(side="left")

        # ---- whole keyboard ----
        whole = tb.Labelframe(frame, text="Whole keyboard", padding=12)
        whole.pack(fill="x", pady=(0, 12))
        r1 = tb.Frame(whole)
        r1.pack(fill="x")
        self._kbd_swatch(r1, self.kbd_all_hex).pack(side="left", padx=(0, 8))
        tb.Button(r1, text="Pick colour…", bootstyle=SECONDARY,
                  command=lambda: self._kbd_pick(self.kbd_all_hex)).pack(side="left", padx=4)
        tb.Button(r1, text="Apply to all zones", bootstyle=SUCCESS,
                  command=self._kbd_apply_all).pack(side="left", padx=4)
        r2 = tb.Frame(whole)
        r2.pack(fill="x", pady=(8, 0))
        for name, hexv in self._KBD_PRESETS:
            tb.Button(r2, text=name, bootstyle=SECONDARY, width=8,
                      command=lambda h=hexv: (self.kbd_all_hex.set(h), self._kbd_apply_all())
                      ).pack(side="left", padx=2)

        # ---- per-zone ----
        pz = tb.Labelframe(frame, text="Per-zone", padding=12)
        pz.pack(fill="x", pady=(0, 12))
        for zi, zname in enumerate(dellg15_kbd.ZONE_NAMES):
            row = tb.Frame(pz)
            row.pack(fill="x", pady=3)
            tb.Label(row, text=zname, width=10).pack(side="left")
            hv = self.kbd_zone_vars.setdefault(zi, tk.StringVar(value="#ffffff"))
            self._kbd_swatch(row, hv).pack(side="left", padx=(0, 8))
            tb.Button(row, text="Pick…", bootstyle=SECONDARY,
                      command=lambda v=hv: self._kbd_pick(v)).pack(side="left", padx=4)
            tb.Button(row, text="Apply", bootstyle=SUCCESS,
                      command=lambda z=zi: self._kbd_apply_zone(z)).pack(side="left", padx=4)

        # ---- effects ----
        fx = tb.Labelframe(frame, text="Effects  (run on the controller)", padding=12)
        fx.pack(fill="x", pady=(0, 12))
        srow = tb.Frame(fx)
        srow.pack(fill="x", pady=(0, 6))
        tb.Label(srow, text="Speed", width=10).pack(side="left")
        sp = tb.Scale(srow, from_=0, to=100, variable=self.kbd_speed, orient="horizontal")
        sp.pack(side="left", fill="x", expand=True, padx=(0, 10))
        tb.Label(srow, textvariable=self.kbd_speed, width=4).pack(side="left")
        # Firmware effects run on the controller itself — smooth and fast.
        # (The AW-ELC only repaints a few times/sec over USB, so a software
        # per-LED wave can't move fast without visibly stepping; the software
        # gradient below is a slow ambient option.)
        frow = tb.Frame(fx)
        frow.pack(fill="x")
        for label, key in (("Rainbow Cycle", "spectrum"), ("Breathing", "breathing"),
                           ("Flashing", "flashing")):
            tb.Button(frow, text=label, bootstyle=SECONDARY,
                      command=lambda k=key: self._kbd_apply_effect(k)).pack(side="left", padx=3)

        frow2 = tb.Frame(fx)
        frow2.pack(fill="x", pady=(8, 0))
        tb.Button(frow2, text="Gradient wave  (slow ambient drift through the per-zone colours)",
                  bootstyle=INFO, command=self._kbd_apply_gradient).pack(side="left", padx=3)

        frow3 = tb.Frame(fx)
        frow3.pack(fill="x", pady=(8, 0))
        tb.Button(frow3, text="Solid colour  (leave effects, apply the colours above)",
                  bootstyle=SUCCESS, command=self._kbd_apply_solid).pack(side="left", padx=3)

        bottom = tb.Frame(frame)
        bottom.pack(fill="x", pady=(4, 0))
        tb.Button(bottom, text="Turn backlight off", bootstyle=(SECONDARY, "outline"),
                  command=self._kbd_off).pack(side="left")
        tb.Button(bottom, text="↻ Reset backlight  (unfreeze)", bootstyle=(WARNING, "outline"),
                  command=self._kbd_reset).pack(side="left", padx=8)

    def _kbd_swatch(self, parent, hexvar: tk.StringVar):
        lbl = tk.Label(parent, width=4, relief="solid", bd=1, bg=self._safe_hex(hexvar.get()))
        hexvar.trace_add("write", lambda *_: lbl.configure(bg=self._safe_hex(hexvar.get())))
        return lbl

    @staticmethod
    def _safe_hex(s: str) -> str:
        s = s if s.startswith("#") else "#" + s
        return s if len(s) == 7 else "#ffffff"

    def _kbd_pick(self, hexvar: tk.StringVar):
        from tkinter import colorchooser
        _rgb, hx = colorchooser.askcolor(color=self._safe_hex(hexvar.get()),
                                         parent=self.root, title="Keyboard colour")
        if hx:
            hexvar.set(hx)

    @staticmethod
    def _hex_to_rgb(hx: str) -> tuple[int, int, int]:
        hx = hx.lstrip("#")
        return (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16))

    def _kbd_run(self, fn, desc: str):
        if self._kbd_busy:
            self._log("[Keyboard] busy — try again in a moment")
            return
        self._kbd_busy = True
        self._log(f"[Keyboard] {desc} …")

        def work():
            try:
                kb = dellg15_kbd.Keyboard()
                try:
                    fn(kb)
                finally:
                    kb.close()
                self._log(f"[Keyboard] {desc} ✓")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[Keyboard FAILED] {exc}")
            finally:
                self._kbd_busy = False

        threading.Thread(target=work, daemon=True).start()

    def _kbd_all_colors(self) -> dict[int, tuple[int, int, int]]:
        rgb = self._hex_to_rgb(self._safe_hex(self.kbd_all_hex.get()))
        return {z: rgb for z in range(dellg15_kbd.ZONE_COUNT)}

    def _kbd_zone_colors(self) -> dict[int, tuple[int, int, int]]:
        return {z: self._hex_to_rgb(self._safe_hex(v.get()))
                for z, v in self.kbd_zone_vars.items()}

    def _kbd_apply_brightness(self):
        b = self.kbd_brightness.get()
        hx = self._safe_hex(self.kbd_all_hex.get())
        colors = self._kbd_all_colors()
        self._kbd_run(lambda kb: (kb.set_all(hx, b),
                                  dellg15_kbd.save_state(colors, b, mode="zones")),
                      f"brightness {b}%")

    def _kbd_apply_all(self):
        hx = self._safe_hex(self.kbd_all_hex.get())
        b = self.kbd_brightness.get()
        colors = self._kbd_all_colors()
        for v in self.kbd_zone_vars.values():
            v.set(hx)
        self._kbd_run(lambda kb: (kb.set_all(hx, b),
                                  dellg15_kbd.save_state(colors, b, mode="zones")),
                      f"colour {hx} @ {b}%")

    def _kbd_apply_zone(self, z: int):
        b = self.kbd_brightness.get()
        colors = self._kbd_zone_colors()
        self._kbd_run(lambda kb: (kb.set_zones(colors, b),
                                  dellg15_kbd.save_state(colors, b, mode="zones")),
                      f"zone {dellg15_kbd.ZONE_NAMES[z]} -> {self._safe_hex(self.kbd_zone_vars[z].get())} @ {b}%")

    def _kbd_apply_effect(self, key: str):
        b = self.kbd_brightness.get()
        sp = self.kbd_speed.get()
        colors = self._kbd_all_colors()
        self._kbd_run(lambda kb: (kb.set_effect(key, sp, b),
                                  dellg15_kbd.save_state(colors, b, mode=key, speed=sp)),
                      f"effect {key} @ speed {sp}, {b}%")

    def _kbd_apply_gradient(self):
        b = self.kbd_brightness.get()
        sp = self.kbd_speed.get()
        # the four per-zone swatches are the gradient anchors; drop consecutive
        # duplicates so "all one colour" → a single-anchor comet, two distinct
        # → a two-stop gradient, etc.
        raw = [self._safe_hex(self.kbd_zone_vars[z].get()).lstrip("#").upper()
               for z in range(dellg15_kbd.ZONE_COUNT)]
        anchors = [c for i, c in enumerate(raw) if i == 0 or c != raw[i - 1]]
        block = {"colors": anchors, "wavelength": 1.0, "blend": "oklab",
                 "direction": "ltr", "min_value": 0.15, "max_value": 1.0,
                 "fps": 60, "smooth": 0.12, "dither": True, "ease": "linear"}
        colors = self._kbd_zone_colors()
        self._kbd_run(lambda kb: (
            dellg15_kbd.start_gradient(anchors, sp, b),
            dellg15_kbd.save_state(colors, b, mode="gradient", speed=sp,
                                   gradient=block)),
            f"gradient [{', '.join('#' + c for c in anchors)}] @ speed {sp}")

    def _kbd_apply_solid(self):
        b = self.kbd_brightness.get()
        colors = self._kbd_zone_colors()
        self._kbd_run(lambda kb: (kb.set_zones(colors, b),
                                  dellg15_kbd.save_state(colors, b, mode="zones")),
                      f"solid colour @ {b}%")

    def _kbd_reset(self):
        self._kbd_run(lambda kb: kb.reset(), "reset backlight (restart OpenRGB + re-apply)")

    def _kbd_off(self):
        self.kbd_brightness.set(0)
        self._kbd_run(lambda kb: kb.off(), "backlight off")

    # ---------- fan control ----------

    def _build_fan_tab(self):
        outer = tb.Frame(self.notebook)
        self.notebook.add(outer, text="Fans")
        frame = self._scroll_body(outer, pad=16)

        fans = sensors.read_fans()
        if not fans:
            tb.Label(frame, bootstyle=WARNING, justify="left", wraplength=1000,
                     text="No fan interface found (expected the alienware_wmi / "
                          "dell_smm hwmon devices). Is the alienware-wmi kernel "
                          "module loaded on this Dell G15 5515?").pack(anchor="w")
            return

        self._fan_rpm_labels: dict = {}
        self._fan_boost_vars: dict = {}
        self._fan_pwm_vars: dict = {}
        self._fan_manual = tk.BooleanVar(value=False)

        note = tb.Labelframe(frame, text="How this works", padding=12)
        note.pack(fill="x", pady=(0, 14))
        tb.Label(
            note, wraplength=1100, justify="left", bootstyle=SECONDARY,
            text="Thermal profile + fan boost steer the firmware (AWCC-style) fan "
                 "curve — boost only adds airflow on top, it can't slow a fan below "
                 "the automatic curve. Manual PWM (advanced) takes the EC off its "
                 "curve entirely; it's floored so the fans never fully stop, but "
                 "watch temperatures and hit “Restore automatic” when done. Nothing "
                 "here persists across a reboot yet.",
        ).pack(anchor="w")

        choices = sensors.platform_profile_choices()
        if choices:
            pf = tb.Labelframe(frame, text="Thermal profile", padding=12)
            pf.pack(fill="x", pady=6)
            prow = tb.Frame(pf); prow.pack(anchor="w")
            self._fan_profile_var = tk.StringVar(value=sensors.get_platform_profile())
            for c in choices:
                tb.Radiobutton(prow, text=c.capitalize(), value=c,
                               variable=self._fan_profile_var, bootstyle="toolbutton",
                               command=lambda v=c: self._fan_set_profile(v)
                               ).pack(side="left", padx=4)

        lf = tb.Labelframe(frame, text="Fans & boost", padding=12)
        lf.pack(fill="x", pady=6)
        boosts = sensors.get_fan_boost()
        for k, fan in enumerate(fans):
            i = fan["index"]
            r = tb.Frame(lf); r.pack(fill="x", pady=6)
            tb.Label(r, text=fan["label"], font=("Sans", 10, "bold"), width=16,
                     anchor="w").pack(side="left")
            rpm_lab = tb.Label(r, text="— rpm", width=11, anchor="w",
                               bootstyle=SECONDARY)
            rpm_lab.pack(side="left")
            self._fan_rpm_labels[i] = rpm_lab
            bv = tk.IntVar(value=round((boosts[k] if k < len(boosts) else 0) / 255 * 100))
            self._fan_boost_vars[i] = bv
            tb.Label(r, text="Boost").pack(side="left", padx=(12, 4))
            sc = tb.Scale(r, from_=0, to=100, variable=bv, orient="horizontal", length=240)
            sc.pack(side="left", fill="x", expand=True)
            sc.bind("<ButtonRelease-1>", lambda _e, idx=i: self._fan_set_boost(idx))
            tb.Label(r, textvariable=bv, width=4).pack(side="left")
            for lbl, pct in (("0", 0), ("50", 50), ("Max", 100)):
                tb.Button(r, text=lbl, bootstyle=(SECONDARY, "outline"), width=4,
                          command=lambda idx=i, p=pct, v=bv: (v.set(p),
                                                              self._fan_set_boost(idx))
                          ).pack(side="left", padx=2)

        pr = tb.Frame(frame); pr.pack(anchor="w", pady=(8, 0))
        tb.Label(pr, text="Presets:", bootstyle=SECONDARY).pack(side="left", padx=(0, 6))
        tb.Button(pr, text="Auto / silent", bootstyle=SUCCESS,
                  command=lambda: self._fan_preset("auto")).pack(side="left", padx=4)
        tb.Button(pr, text="Cooler", bootstyle=(INFO, "outline"),
                  command=lambda: self._fan_preset("cool")).pack(side="left", padx=4)
        tb.Button(pr, text="Max cooling", bootstyle=(DANGER, "outline"),
                  command=lambda: self._fan_preset("max")).pack(side="left", padx=4)

        if sensors.get_pwm_state():
            adv = tb.Labelframe(frame, text="Manual PWM — advanced / risky",
                                bootstyle=DANGER, padding=12)
            adv.pack(fill="x", pady=(14, 6))
            tb.Checkbutton(adv, variable=self._fan_manual, bootstyle="round-toggle",
                           text="Enable manual PWM control (takes the EC off its "
                                "automatic curve)",
                           command=self._fan_manual_toggle).pack(anchor="w")
            self._fan_pwm_box = tb.Frame(adv)
            self._fan_pwm_box.pack(fill="x", pady=(8, 0))
            for fan in fans:
                i = fan["index"]
                r = tb.Frame(self._fan_pwm_box); r.pack(fill="x", pady=4)
                tb.Label(r, text=fan["label"], width=16, anchor="w").pack(side="left")
                pv = tk.IntVar(value=50)
                self._fan_pwm_vars[i] = pv
                sc = tb.Scale(r, from_=30, to=100, variable=pv, orient="horizontal",
                              length=280)
                sc.pack(side="left", fill="x", expand=True)
                sc.bind("<ButtonRelease-1>", lambda _e, idx=i: self._fan_set_pwm(idx))
                tb.Label(r, textvariable=pv, width=4).pack(side="left")
            tb.Button(adv, text="Restore automatic", bootstyle=SUCCESS,
                      command=self._fan_restore).pack(anchor="w", pady=(10, 0))
            self._fan_manual_toggle()

        self._fan_live = True
        self._fan_poll()

    def _fan_set_profile(self, name: str):
        ok, err = sensors.set_platform_profile(name)
        self._log(f"[Fans] thermal profile → {name}" + ("" if ok else f"  FAILED: {err}"))

    def _fan_set_boost(self, index: int):
        pct = self._fan_boost_vars[index].get()
        ok, err = sensors.set_fan_boost(index, round(pct * 255 / 100))
        self._log(f"[Fans] fan {index} boost → {pct}%" + ("" if ok else f"  FAILED: {err}"))

    def _fan_manual_toggle(self):
        on = self._fan_manual.get()
        if on and not messagebox.askyesno(
            "Manual fan control",
            "This takes the fans off the firmware's automatic curve. They stay "
            "floored so they can't stop, but keep an eye on temperatures and use "
            "“Restore automatic” when you're done.\n\nProceed?"):
            self._fan_manual.set(False)
            on = False
        for r in self._fan_pwm_box.winfo_children():
            for w in r.winfo_children():
                try:
                    w.configure(state="normal" if on else "disabled")
                except tk.TclError:
                    pass
        if on:
            for i in self._fan_pwm_vars:
                self._fan_set_pwm(i)

    def _fan_set_pwm(self, index: int):
        if not self._fan_manual.get():
            return
        pct = self._fan_pwm_vars[index].get()
        ok, err = sensors.set_pwm_manual(index, round(pct * 255 / 100))
        self._log(f"[Fans] fan {index} manual PWM → {pct}%" + ("" if ok else f"  FAILED: {err}"))

    def _fan_restore(self):
        ok, err = sensors.restore_fan_auto()
        self._fan_manual.set(False)
        if hasattr(self, "_fan_pwm_box"):
            self._fan_manual_toggle()
        for bv in self._fan_boost_vars.values():
            bv.set(0)
        self._log("[Fans] restored automatic control" + ("" if ok else f"  (errors: {err})"))

    def _fan_preset(self, kind: str):
        prof = {"auto": "balanced", "cool": "performance", "max": "performance"}[kind]
        boost = {"auto": 0, "cool": 60, "max": 100}[kind]
        if kind == "auto":
            sensors.restore_fan_auto()
        if hasattr(self, "_fan_profile_var") and prof in sensors.platform_profile_choices():
            sensors.set_platform_profile(prof)
            self._fan_profile_var.set(prof)
        for i, bv in self._fan_boost_vars.items():
            bv.set(boost)
            sensors.set_fan_boost(i, round(boost * 255 / 100))
        self._log(f"[Fans] preset: {kind} (profile {prof}, boost {boost}%)")

    def _fan_poll(self):
        if not getattr(self, "_fan_live", False):
            return
        for fan in sensors.read_fans():
            lab = self._fan_rpm_labels.get(fan["index"])
            if lab is not None:
                try:
                    lab.configure(text=f"{fan['rpm']} rpm")
                except tk.TclError:
                    pass
        self.root.after(2000, self._fan_poll)

    def _build_category_tab(self, category: str):
        outer = tb.Frame(self.notebook)
        self.notebook.add(outer, text=category)
        inner = self._scroll_body(outer)

        for item in self.items.values():
            if item.category != category:
                continue
            row = tb.Frame(inner, padding=16, bootstyle="dark")
            row.pack(fill="x", padx=2, pady=4)

            item.var = tk.BooleanVar(value=False)
            cb = tb.Checkbutton(row, variable=item.var, bootstyle="round-toggle")
            cb.pack(side="left", anchor="n", padx=(0, 14))
            item.checkbutton = cb
            if not item.hw_supported:
                item.var.set(False)
                cb.configure(state="disabled")

            item.status_label = tb.Label(row, text="checking…", width=15, anchor="e",
                                         font=("Sans", 9, "bold"), bootstyle=SECONDARY)
            item.status_label.pack(side="right", anchor="n", padx=(14, 0))

            text_frame = tb.Frame(row, bootstyle="dark")
            text_frame.pack(side="left", fill="both", expand=True)
            title_row = tb.Frame(text_frame, bootstyle="dark")
            title_row.pack(anchor="w", fill="x")
            tb.Label(title_row, text=item.content, font=("Sans", 11, "bold"),
                     bootstyle="inverse-dark").pack(side="left")
            if item.risk == "advanced":
                tb.Label(title_row, text="ADVANCED", bootstyle=(WARNING, "inverse"),
                         font=("Sans", 7, "bold"), padding=(5, 1)).pack(side="left", padx=8)
            tb.Label(text_frame, text=item.description, wraplength=1250,
                     bootstyle="inverse-dark", justify="left").pack(anchor="w", pady=(4, 0))

    def _build_presets_tab(self):
        outer = tb.Frame(self.notebook)
        self.notebook.add(outer, text="Presets")
        frame = self._scroll_body(outer, pad=14)
        tb.Label(frame, text="One click applies a curated bundle of tweaks + installs apps.", bootstyle=SECONDARY).pack(anchor="w", pady=(0, 12))
        for preset_id, data in self.presets.items():
            box = tb.Frame(frame, padding=14, bootstyle="secondary")
            box.pack(fill="x", pady=6)
            tb.Label(box, text=data["Content"], font=("Sans", 12, "bold")).pack(anchor="w")
            tb.Label(box, text=data["Description"], wraplength=900, bootstyle=SECONDARY).pack(anchor="w", pady=(2, 8))
            tb.Button(
                box, text="Apply This Preset", bootstyle=SUCCESS,
                command=lambda pid=preset_id: self._on_apply_preset(pid)
            ).pack(anchor="e")

    # ---------- app-wide busy lock ----------

    def _begin_busy(self, text: str = "Working…", steps: int = 0) -> None:
        """Lock the UI for a long task. MAIN THREAD ONLY (call from the button
        handler, not the worker). Covers the notebook with a click-eating
        overlay showing two progress bars — overall (determinate when `steps`
        is known) and current task (indeterminate) — plus a step/phase line
        and an elapsed timer. Reversed by _poll_busy_queue on _busy_queue."""
        self._busy = True
        self.worker_running = True
        self._busy_t0 = time.monotonic()
        self._busy_steps = steps
        self._cur_step = ""
        for btn in self._footer_btns:
            btn.configure(state="disabled")
        self.status_var.set(text)
        self._busy_bar.pack(side="right", padx=(8, 0))
        self._busy_bar.start(12)
        if self._busy_overlay is None:
            ov = tk.Frame(self.notebook, cursor="watch", bg="#0e1116")
            ov.place(x=0, y=0, relwidth=1, relheight=1)
            # swallow every pointer/key event so no tab control can be used
            for seq in ("<Button>", "<Key>", "<MouseWheel>", "<Button-4>", "<Button-5>"):
                ov.bind(seq, lambda _e: "break")
            box = tb.Frame(ov, padding=28, bootstyle="dark")
            box.place(relx=0.5, rely=0.4, anchor="center")
            self._busy_label = tb.Label(box, text=text, bootstyle="inverse-dark",
                                        font=("Sans", 12, "bold"))
            self._busy_label.pack(anchor="w", pady=(0, 16))

            tb.Label(box, text="OVERALL", bootstyle="inverse-dark",
                     font=("Sans", 8, "bold")).pack(anchor="w")
            self._busy_bar_overall = tb.Progressbar(box, length=400,
                                                    bootstyle=(SUCCESS, "striped"))
            self._busy_bar_overall.pack(fill="x", pady=(2, 1))
            self._busy_overall_lbl = tb.Label(box, text="", bootstyle="inverse-dark",
                                              font=("Sans", 9))
            self._busy_overall_lbl.pack(anchor="w", pady=(0, 14))

            tb.Label(box, text="CURRENT TASK", bootstyle="inverse-dark",
                     font=("Sans", 8, "bold")).pack(anchor="w")
            self._busy_bar_task = tb.Progressbar(box, length=400, mode="indeterminate",
                                                 bootstyle=(INFO, "striped"))
            self._busy_bar_task.pack(fill="x", pady=(2, 1))
            self._busy_bar_task.start(12)
            self._busy_step = tb.Label(box, text="Preparing…", bootstyle="inverse-dark",
                                       font=("Sans", 9), wraplength=400, justify="left")
            self._busy_step.pack(anchor="w", pady=(0, 14))

            self._busy_elapsed = tb.Label(box, text="Elapsed: 0s",
                                          bootstyle="inverse-dark", font=("Sans", 10))
            self._busy_elapsed.pack(anchor="w")
            tb.Label(box, text="Full output is in the log console below.",
                     bootstyle="inverse-dark", font=("Sans", 9)).pack(anchor="w", pady=(4, 0))
            self._busy_overlay = ov
        else:
            self._busy_label.configure(text=text)
            self._busy_elapsed.configure(text="Elapsed: 0s")
            self._busy_step.configure(text="Preparing…")
            self._busy_overall_lbl.configure(text="")

        ob = self._busy_bar_overall
        if steps > 0:
            ob.stop()
            ob.configure(mode="determinate", maximum=steps, value=0)
        else:
            ob.configure(mode="indeterminate")
            ob.start(16)
        self._busy_overlay.lift()
        self._tick_busy()

    def _progress(self, overall: int | None = None, step: str | None = None,
                  phase: str | None = None) -> None:
        """Thread-safe: feed the two-bar overlay. `overall` = completed-step
        count, `step` = what's being worked on, `phase` = downloading /
        installing / …  (drained in _poll_busy_queue)."""
        self._prog_q.put((overall, step, phase))

    @staticmethod
    def _phase_from_line(line: str) -> str | None:
        low = line.lower()
        pairs = (("downloading", "downloading"), ("get:", "downloading"),
                 ("fetching", "downloading"), ("resolving dependencies", "resolving"),
                 ("dependencies resolved", "resolving"),
                 ("running transaction check", "checking"),
                 ("running scriptlet", "running scripts"),
                 ("running transaction", "installing"),
                 ("upgrading ", "upgrading"), ("installing ", "installing"),
                 ("reinstalling ", "installing"), ("removing ", "removing"),
                 ("erasing ", "removing"), ("verifying ", "verifying"),
                 ("importing gpg key", "importing keys"))
        for needle, label in pairs:
            if needle in low:
                return label
        return None

    @staticmethod
    def _fmt_dur(sec: float) -> str:
        sec = int(sec)
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        if h:
            return f"{h}h {m:02d}m {s:02d}s"
        if m:
            return f"{m}m {s:02d}s"
        return f"{s}s"

    def _tick_busy(self) -> None:
        """1 Hz elapsed-time updater for the running task; stops itself when
        the busy lock clears."""
        if not self._busy:
            return
        el = self._fmt_dur(time.monotonic() - self._busy_t0)
        try:
            self._busy_elapsed.configure(text=f"Elapsed: {el}")
            self.status_var.set(f"{self._busy_label.cget('text')}   ·   {el}")
        except (tk.TclError, AttributeError):
            pass
        self.root.after(1000, self._tick_busy)

    def _poll_busy_queue(self) -> None:
        """Drain completion signals from worker threads and unlock the UI."""
        # live progress → two-bar overlay
        try:
            while True:
                ov, step, phase = self._prog_q.get_nowait()
                if self._busy_overlay is None:
                    continue
                try:
                    if ov is not None and self._busy_steps > 0:
                        self._busy_bar_overall.configure(value=ov)
                        self._busy_overall_lbl.configure(text=f"{ov} / {self._busy_steps}")
                    if step is not None:
                        self._cur_step = step
                    if step is not None or phase is not None:
                        base = self._cur_step or "Working…"
                        self._busy_step.configure(
                            text=f"{base}   —   {phase}" if phase else base)
                except tk.TclError:
                    pass
        except queue.Empty:
            pass

        done = None
        try:
            while True:
                done = self._busy_queue.get_nowait()
        except queue.Empty:
            pass
        if done is not None:
            elapsed = self._fmt_dur(time.monotonic() - getattr(self, "_busy_t0", time.monotonic()))
            self._busy = False
            self.worker_running = False
            for btn in self._footer_btns:
                btn.configure(state="normal")
            self._busy_bar.stop()
            self._busy_bar.pack_forget()
            self.status_var.set(f"{done}   ·   took {elapsed}")
            if self._busy_overlay is not None:
                self._busy_overlay.destroy()
                self._busy_overlay = None
            # post-task follow-ups from _run_updates (failure detail / reboot)
            info, self._upd_last = getattr(self, "_upd_last", None), None
            if info:
                if not info["ok"]:
                    self._show_output_dialog(
                        f"{info['desc']} — failed (exit {info['rc']})", info["tail"])
                elif info["reboot"] and messagebox.askyesno(
                    "Reboot recommended",
                    f"{info['desc']} finished.\n\nNobara recommends a reboot after a "
                    "system update. Reboot now?"):
                    subprocess.Popen(["systemctl", "reboot"])
            if hasattr(self, "_upd_count_var"):
                self._refresh_update_count()
        if hasattr(self, "_upd_count_q"):
            try:
                self._upd_count_var.set(self._upd_count_q.get_nowait())
            except queue.Empty:
                pass
        self.root.after(150, self._poll_busy_queue)

    def _show_output_dialog(self, title: str, lines: list[str]) -> None:
        """Modal scrollable dump of a task's captured output (used on failure)."""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("900x520")
        win.transient(self.root)
        tb.Label(win, text=title, bootstyle=DANGER, font=("Sans", 10, "bold"),
                 padding=(12, 10)).pack(anchor="w")
        tb.Label(win, text="Full output is in the log console at the bottom of the "
                 "main window.", bootstyle=SECONDARY, padding=(12, 0)).pack(anchor="w")
        txt = self._make_log_text(win)
        txt.pack(fill="both", expand=True, padx=12, pady=10)
        txt.configure(state="normal")
        txt.insert("end", "\n".join(lines[-400:]))
        txt.see("end")
        txt.configure(state="disabled")
        tb.Button(win, text="Close", bootstyle=SECONDARY,
                  command=win.destroy).pack(pady=(0, 12))

    # ---------- updates ----------

    def _build_updates_tab(self):
        outer = tb.Frame(self.notebook)
        self.notebook.add(outer, text="Updates")
        frame = self._scroll_body(outer, pad=16)

        have_ns = shutil.which("nobara-sync") is not None
        have_flatpak = shutil.which("flatpak") is not None
        have_fwupd = shutil.which("fwupdmgr") is not None

        note = tb.Labelframe(frame, text="System updates", padding=12)
        note.pack(fill="x", pady=(0, 14))
        mgrs = ", ".join(m for m, ok in (("dnf" if have_ns else "dnf (no nobara-sync)", True),
                                         ("flatpak", have_flatpak), ("fwupd", have_fwupd)) if ok)
        tb.Label(
            note, wraplength=1100, justify="left", bootstyle=SECONDARY,
            text=f"Package managers found: {mgrs}. Each has its own section below; "
                 "“Update everything” runs the system + Flatpak updates back to back. "
                 "Output streams to the log console at the bottom of the window; a reboot "
                 "is recommended after a system or firmware update.",
        ).pack(anchor="w")

        self._upd_count_q: queue.Queue = queue.Queue()
        self._upd_count_var = tk.StringVar(value="Updates available:  checking…")
        crow = tb.Frame(note); crow.pack(anchor="w", fill="x", pady=(8, 0))
        tb.Label(crow, textvariable=self._upd_count_var, bootstyle=SECONDARY,
                 font=("Sans", 10, "bold")).pack(side="left")
        tb.Button(crow, text="↻ recount", bootstyle=(INFO, "link"),
                  command=self._refresh_update_count).pack(side="left", padx=8)

        def add(parent, text, style, cmd, desc, reboot=False):
            tb.Button(parent, text=text, bootstyle=style,
                      command=lambda: self._run_updates(cmd, desc, reboot=reboot)
                      ).pack(side="left", padx=4, pady=4)

        def section(title, style):
            lf = tb.Labelframe(frame, text=title, bootstyle=style, padding=12)
            lf.pack(fill="x", pady=6)
            row = tb.Frame(lf); row.pack(anchor="w")
            return row

        sys_update = (f"nobara-sync cli {shlex.quote(self.user)}" if have_ns
                      else "dnf upgrade --refresh -y")

        # ---- Overall ----
        r0 = section("Everything", SUCCESS)
        check_all = " ; ".join(
            [f"echo '### {n}' ; {c} || true" for n, c in (
                ("dnf", "nobara-sync check-updates" if have_ns else "dnf check-update"),
                ("flatpak", "flatpak remote-ls --updates"),
                ("fwupd", "fwupdmgr get-updates")) if (
                n != "flatpak" or have_flatpak) and (n != "fwupd" or have_fwupd)])
        add(r0, "Check everything", (INFO, "outline"), check_all, "check all package managers")
        update_all = " ; ".join([sys_update] + (["flatpak update -y"] if have_flatpak else []))
        add(r0, "Update everything (system + Flatpak)", SUCCESS,
            update_all, "update everything", reboot=True)

        # ---- dnf / Nobara ----
        r1 = section("System — dnf" + (" / nobara-sync" if have_ns else ""), INFO)
        if have_ns:
            add(r1, "Check for updates", (INFO, "outline"),
                "nobara-sync check-updates || true", "check for updates")
            add(r1, "Update system", SUCCESS,
                f"nobara-sync cli {shlex.quote(self.user)}", "update system + fixups",
                reboot=True)
            add(r1, "Apply known fixups", (WARNING, "outline"),
                "nobara-sync install-fixups", "apply known fixups")
            add(r1, "Repair (distro-sync)", (WARNING, "outline"),
                "nobara-sync repair", "repair via distro-sync", reboot=True)
            add(r1, "List enabled repos", (SECONDARY, "outline"),
                "nobara-sync check-repos || true", "list enabled repos")
        else:
            add(r1, "Check for updates", (INFO, "outline"),
                "dnf check-update || true", "check for updates")
        add(r1, "dnf upgrade --refresh", (SECONDARY, "outline"),
            "dnf upgrade --refresh -y", "dnf upgrade --refresh", reboot=True)
        add(r1, "Clean dnf cache", (SECONDARY, "outline"),
            "dnf clean all && dnf makecache", "clean + rebuild dnf cache")
        add(r1, "Fix Fedora GPG keys", (DANGER, "outline"),
            "rpm --import /etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-*-primary "
            "&& dnf clean all && dnf makecache",
            "import Fedora GPG keys + rebuild cache")

        # ---- Flatpak ----
        if have_flatpak:
            rf = section("Flatpak", INFO)
            add(rf, "Check updates", (INFO, "outline"),
                "flatpak remote-ls --updates || true", "check Flatpak updates")
            add(rf, "Update Flatpaks", SUCCESS, "flatpak update -y", "update Flatpaks")
            add(rf, "Remove unused runtimes", (SECONDARY, "outline"),
                "flatpak uninstall --unused -y", "remove unused Flatpak runtimes")

        # ---- Firmware ----
        if have_fwupd:
            rw = section("Firmware — fwupd", WARNING)
            add(rw, "Refresh metadata", (INFO, "outline"),
                "fwupdmgr refresh --force || true", "refresh firmware metadata")
            add(rw, "Show updates", (INFO, "outline"),
                "fwupdmgr get-updates || true", "list firmware updates")
            add(rw, "Apply firmware updates", (DANGER, "outline"),
                "fwupdmgr update -y", "apply firmware updates", reboot=True)

        tb.Label(
            frame, bootstyle=SECONDARY, wraplength=1100, justify="left",
            text="Tip: if an update aborts with a GPG signature error (Nobara ships some "
                 ".fc44 packages signed with a key that's on disk but not imported), run "
                 "“Fix Fedora GPG keys” then retry. Firmware updates can need the charger "
                 "plugged in and a reboot to complete.",
        ).pack(anchor="w", pady=(12, 0))

        self._refresh_update_count()

    def _refresh_update_count(self) -> None:
        """Count pending updates per package manager, off-thread. Result goes to
        _upd_count_var via _upd_count_q (drained in _poll_busy_queue).

        dnf is queried with --cacheonly so this never blocks on slow mirrors —
        the number is "as of the last metadata sync" and becomes exact right
        after any Check/Update action (which refreshes the cache; this then
        re-runs). '?' means the query failed or timed out."""
        self._upd_count_var.set("Updates available:  checking…")

        def sh(cmd: str, timeout: int) -> tuple[int, str]:
            try:
                p = subprocess.run(["bash", "-c", cmd], capture_output=True,
                                   text=True, timeout=timeout)
                return p.returncode, p.stdout
            except Exception:  # noqa: BLE001
                return -1, ""

        def work():
            parts = []
            rc, out = sh("dnf -q --cacheonly check-update 2>/dev/null", 60)
            if rc in (0, 100):
                n = sum(1 for ln in out.splitlines()
                        if ln[:1].isalnum() and len(ln.split()) >= 3
                        and "." in ln.split()[0])
                parts.append(("dnf", str(n)))
            else:
                parts.append(("dnf", "?"))
            if shutil.which("flatpak"):
                rc, out = sh("flatpak remote-ls --updates --columns=application 2>/dev/null", 45)
                parts.append(("flatpak", str(sum(1 for ln in out.splitlines() if ln.strip()))
                              if rc == 0 else "?"))
            if shutil.which("fwupdmgr"):
                rc, out = sh("fwupdmgr get-updates -y 2>/dev/null", 45)
                parts.append(("firmware", str(out.count("New version:")) if rc in (0, 2) else "?"))
            total = sum(int(v) for _, v in parts if v.isdigit())
            detail = "  ·  ".join(f"{k} {v}" for k, v in parts)
            self._upd_count_q.put(f"Updates available:  {total}   ({detail})")

        threading.Thread(target=work, daemon=True).start()

    def _run_updates(self, cmd: str, desc: str, reboot: bool = False):
        if self._busy:
            messagebox.showinfo("Busy", "An operation is already running — check the log.")
            return
        self._begin_busy(f"Updates — {desc}", steps=0)
        self._progress(step=desc)
        self._log(f"[Updates] {desc} …")

        def work():
            rc, tail = -1, []
            try:
                proc = subprocess.Popen(
                    ["bash", "-c", cmd], stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    self._log(line)
                    tail.append(line)
                    del tail[:-400]
                    ph = self._phase_from_line(line)
                    if ph:
                        self._progress(step=desc, phase=ph)
                rc = proc.wait()
            except Exception as exc:  # noqa: BLE001
                self._log(f"[Updates FAILED] {exc}")
                tail.append(f"[Updates FAILED] {exc}")
            finally:
                result = "done ✓" if rc == 0 else f"exit {rc}"
                self._log(f"[Updates] {desc} — {result}")
                # _poll_busy_queue (main thread) reads this after unlocking, to
                # pop a failure dialog or the reboot prompt — Tk isn't thread-safe.
                self._upd_last = {"ok": rc == 0, "rc": rc, "desc": desc,
                                  "reboot": reboot, "tail": tail}
                self._busy_queue.put(f"Updates: {desc} — {result}")

        threading.Thread(target=work, daemon=True).start()

    # ---------- diagnostics / debug report ----------

    def _build_diagnostics_tab(self):
        outer = tb.Frame(self.notebook)
        self.notebook.add(outer, text="Diagnostics")
        frame = tb.Frame(outer, padding=16)      # NOT _scroll_body — the report
        frame.pack(fill="both", expand=True)     # box scrolls itself and must be tall
        self._diag_q: queue.Queue = queue.Queue()
        self._diag_running = False
        self._diag_raw = ""                      # unwrapped report, for Save

        tb.Label(
            frame, wraplength=1200, justify="left", bootstyle=SECONDARY,
            text="Collects hardware + OS + toolkit state for bug reports: kernel & DMI, "
                 "CPU/GPU, thermal/fan, the keyboard / hotkey / media-key evdev map "
                 "(/proc/bus/input/devices + capability bitmaps), OpenRGB, package "
                 "versions, filtered dmesg / journal. All read-only, hard-timed-out. "
                 "Run the toolkit with sudo for dmesg / RAPL. Review it for your "
                 "username / hostname before sharing.",
        ).pack(anchor="w", pady=(0, 10))

        row = tb.Frame(frame)
        row.pack(anchor="w", pady=(0, 8))
        self._diag_btn = tb.Button(row, text="Generate report", bootstyle=SUCCESS,
                                   command=self._gen_diag)
        self._diag_btn.pack(side="left", padx=(0, 6))
        tb.Button(row, text="⧉ Copy for GitHub issue", bootstyle=INFO,
                  command=lambda: self._to_clipboard(self._diag_text.get("1.0", "end-1c"))
                  ).pack(side="left", padx=4)
        tb.Button(row, text="Copy full issue (template + report)", bootstyle=(INFO, "outline"),
                  command=self._copy_full_issue).pack(side="left", padx=4)
        tb.Button(row, text="Save .txt…", bootstyle=(SECONDARY, "outline"),
                  command=self._save_diag).pack(side="left", padx=4)

        box = tb.Labelframe(
            frame, padding=10, bootstyle=INFO,
            text="  ⧉  GITHUB ISSUE BLOCK — “Copy for GitHub issue” copies exactly what's "
                 "in here (a collapsible <details> block); paste it straight into the issue  ")
        box.pack(fill="both", expand=True, pady=(4, 0))
        self._diag_text = self._make_log_text(box)
        self._diag_text.configure(height=28)
        self._diag_text.pack(fill="both", expand=True)
        self._set_diag("Click “Generate report”.\n\nTerminal equivalent:\n"
                       "  sudo python3 /opt/dellg15-toolkit/dellg15_toolkit.py --debug\n")

    def _to_clipboard(self, text: str):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set("Copied to clipboard.")
        except tk.TclError:
            pass

    def _set_diag(self, text: str):
        self._diag_text.configure(state="normal")
        self._diag_text.delete("1.0", "end")
        self._diag_text.insert("end", text)
        self._diag_text.see("1.0")
        self._diag_text.configure(state="disabled")

    def _copy_full_issue(self):
        if not self._diag_raw:
            self.status_var.set("Generate the report first.")
            return
        self._to_clipboard(GITHUB_ISSUE_TEMPLATE.replace(
            "PASTE THE DEBUG REPORT HERE",
            self._diag_raw.replace("```", "``​`").strip()))
        self.status_var.set("Full issue (template + report) copied — paste it on GitHub.")

    def _gen_diag(self):
        if self._diag_running:
            return
        self._diag_running = True
        self._diag_btn.configure(state="disabled", text="Collecting…")
        self._set_diag("Collecting hardware / OS / toolkit info — ~15–30 s…\n")
        items = list(self.items.values())

        def work():
            try:
                self._diag_raw = collect_debug_report(items, wrap=False)
                rep = wrap_issue_block(self._diag_raw)
            except Exception as exc:  # noqa: BLE001
                self._diag_raw = rep = f"debug report failed: {exc}"
            self._diag_q.put(rep)

        threading.Thread(target=work, daemon=True).start()

    def _save_diag(self):
        from tkinter import filedialog
        rep = self._diag_raw.strip()
        if not rep:
            self.status_var.set("Generate the report first.")
            return
        try:
            home = pwd.getpwnam(self.user).pw_dir
        except KeyError:
            home = os.path.expanduser("~")
        name = f"dellg15-debug-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        path = filedialog.asksaveasfilename(parent=self.root, initialdir=home,
                                            initialfile=name, defaultextension=".txt")
        if not path:
            return
        try:
            with open(path, "w") as f:
                f.write(rep + "\n")
            if os.geteuid() == 0:
                pw = pwd.getpwnam(self.user)
                os.chown(path, pw.pw_uid, pw.pw_gid)
            self.status_var.set(f"Saved {path}")
        except (OSError, KeyError) as exc:
            self.status_var.set(f"Save failed: {exc}")

    # ---------- dashboard loop ----------

    def _dashboard_loop(self):
        while self.dash_running:
            cpu_temp = sensors.read_cpu_temp_c_value()
            cpu_freq = sensors.read_cpu_freq_ghz_value()
            cpu_power = sensors.read_cpu_power_watts()  # blocks ~0.1s, fine on this bg thread
            igpu_clock, igpu_temp = sensors.read_igpu_clock_temp_values()
            dgpu_clock, dgpu_temp, dgpu_util, dgpu_power = sensors.read_dgpu_values()
            rapl_ok = sensors.rapl_permissions_ok()
            gamemode = sensors.get_game_mode_state()
            self.dash_queue.put((cpu_temp, cpu_freq, cpu_power, igpu_clock, igpu_temp,
                                  dgpu_clock, dgpu_temp, dgpu_util, dgpu_power, rapl_ok, gamemode))
            for _ in range(19):  # ~2s poll total (0.1s already spent above), checkable for shutdown
                if not self.dash_running:
                    return
                threading.Event().wait(0.1)

    def _poll_dash_queue(self):
        try:
            while True:
                (cpu_temp, cpu_freq, cpu_power, igpu_clock, igpu_temp,
                 dgpu_clock, dgpu_temp, dgpu_util, dgpu_power, rapl_ok, gamemode) = self.dash_queue.get_nowait()
                self.meter_cpu_temp.set(cpu_temp)
                self.meter_cpu_freq.set(cpu_freq)
                self.meter_cpu_power.set(cpu_power)
                self.meter_dgpu_temp.set(dgpu_temp)
                self.meter_dgpu_util.set(dgpu_util)
                self.meter_dgpu_power.set(dgpu_power)
                cpu_power_txt = f", {cpu_power:.1f} W" if cpu_power is not None else ""
                self.dash_cpu_label.configure(text=f"CPU: {cpu_freq:.2f} GHz, {cpu_temp:.0f} C{cpu_power_txt}" if cpu_temp else "CPU: n/a")
                if igpu_clock is not None:
                    self.dash_igpu_label.configure(text=f"iGPU: {igpu_clock} MHz, {igpu_temp:.0f} C" if igpu_temp else f"iGPU: {igpu_clock} MHz")
                else:
                    self.dash_igpu_label.configure(text="iGPU: n/a")
                if dgpu_clock is not None:
                    dgpu_power_txt = f", {dgpu_power:.0f} W" if dgpu_power is not None else ""
                    self.dash_dgpu_label.configure(text=f"dGPU: {dgpu_clock} MHz, {dgpu_temp} C, {dgpu_util}% util{dgpu_power_txt}")
                else:
                    self.dash_dgpu_label.configure(text="dGPU: n/a (asleep or no nvidia-smi)")
                if not rapl_ok:
                    self.rapl_warning.configure(
                        text="⚠ CPU power reads 0/blank — Linux locks RAPL power counters to root by default. "
                             "Install the 'RaplPowerPermissions' tweak (Power tab) to fix this."
                    )
                else:
                    self.rapl_warning.configure(text="")
                self._suppress_gamemode_signal = True
                self.gamemode_var.set(gamemode)
                self._suppress_gamemode_signal = False
        except queue.Empty:
            pass
        self.root.after(300, self._poll_dash_queue)

    def _on_gamemode_toggle(self):
        if self._suppress_gamemode_signal:
            return
        enable = self.gamemode_var.get()
        threading.Thread(target=self._gamemode_worker, args=(enable,), daemon=True).start()

    def _gamemode_worker(self, enable: bool):
        ok, err = sensors.set_game_mode(enable)
        if not ok:
            self._log(f"[Game Mode FAILED] {err}")
        else:
            self._log(f"[Game Mode] {'ON' if enable else 'OFF'}")

    # ---------- status / logging ----------

    def _log(self, line: str):
        self.log_queue.put(line)

    def _poll_log_queue(self):
        new = []
        try:
            while True:
                new.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        if new:
            self._log_lines.extend(new)
            del self._log_lines[:-4000]
            chunk = "\n".join(new) + "\n"
            for widget in (self.log_text, self._pop_text):
                if widget is None:
                    continue
                widget.configure(state="normal")
                widget.insert("end", chunk)
                widget.see("end")
                widget.configure(state="disabled")
        if hasattr(self, "_diag_q"):
            try:
                rep = self._diag_q.get_nowait()
                self._set_diag(rep)
                self._diag_running = False
                self._diag_btn.configure(state="normal", text="Regenerate report")
                self.status_var.set("Debug report ready — Copy, or Copy the issue template.")
            except queue.Empty:
                pass
        self.root.after(120, self._poll_log_queue)

    def _refresh_all_status(self):
        ledger = ledger_load()
        # Each check spawns a shell; running them serially made startup crawl.
        # They're independent and I/O-bound, so fan them out over a pool.
        items = list(self.items.values())
        if items:
            with ThreadPoolExecutor(max_workers=min(12, len(items))) as ex:
                list(ex.map(lambda it: evaluate_item(it, ledger), items))
        # Hand back to the main thread via a queue — Tk is not thread-safe and
        # calling root.after() from here races the interpreter (crashes as
        # "main thread is not in main loop"). Mirrors the log/dash queues.
        self.status_queue.put(True)

    def _poll_status_queue(self):
        drained = False
        try:
            while True:
                self.status_queue.get_nowait()
                drained = True
        except queue.Empty:
            pass
        if drained:
            self._apply_status_to_widgets()
        self.root.after(200, self._poll_status_queue)

    def _apply_status_to_widgets(self):
        n_done = n_total = n_attention = 0
        for item in self.items.values():
            if item.status_label is None:
                continue
            if not item.hw_supported:
                item.status_label.configure(text="unsupported", bootstyle=SECONDARY)
                continue
            n_total += 1
            label, style = _STATE_UI.get(item.state, _STATE_UI["unknown"])
            if item.kind == "app":
                label = {"Applied": "Installed", "Not applied": "Not installed"}.get(label, label)
            if item.done:
                n_done += 1
            if item.state in ("error", "drifted", "failed"):
                n_attention += 1
            item.status_label.configure(text=label, bootstyle=style)
            if item.var is not None:
                item.var.set(item.done)
        msg = (f"{n_done} of {n_total} applied/installed — "
               f"{n_total - n_done} available.")
        if n_attention:
            msg += f"  ⚠ {n_attention} need attention (see Status report)."
        self.status_var.set(msg)

    def _on_refresh_click(self):
        self.status_var.set("Refreshing status…")
        threading.Thread(target=self._refresh_all_status, daemon=True).start()

    def _show_status_report(self):
        """Scrollable, copyable table of every item: state, the check that
        decided it (+ exit code), and the last thing the toolkit did to it."""
        win = tk.Toplevel(self.root)
        win.title("Dell G15 Toolkit — status report")
        win.geometry("1040x640")
        win.transient(self.root)
        tb.Label(win, text="Status report", font=("Sans", 11, "bold"),
                 padding=(12, 10)).pack(anchor="w")
        tb.Label(win, bootstyle=SECONDARY, padding=(12, 0), justify="left",
                 text="State = the item's own check command. “Reverted” = the toolkit "
                      "applied it but the check now fails; “Apply failed” = our last "
                      "attempt errored; “Check error” = the check couldn't run.").pack(anchor="w")
        txt = self._make_log_text(win)
        txt.pack(fill="both", expand=True, padx=12, pady=10)
        txt.configure(state="normal")
        txt.insert("end", format_status_report(self.items.values()))
        txt.configure(state="disabled")
        bar = tb.Frame(win); bar.pack(pady=(0, 12))
        tb.Button(bar, text="Re-check now", bootstyle=(INFO, "outline"),
                  command=lambda: (win.destroy(), self._on_refresh_click())).pack(side="left", padx=4)
        tb.Button(bar, text="Copy", bootstyle=(SECONDARY, "outline"),
                  command=lambda: (self.root.clipboard_clear(),
                                   self.root.clipboard_append(
                                       format_status_report(self.items.values())))
                  ).pack(side="left", padx=4)
        tb.Button(bar, text="Close", bootstyle=SECONDARY, command=win.destroy).pack(side="left", padx=4)

    # ---------- apply logic ----------

    def _stream_apply_cmd(self, cmd: str) -> bool:
        """Run one apply/undo command, streaming its output to the log and
        feeding phase hints (downloading / installing / …) to the overlay."""
        try:
            proc = subprocess.Popen(["bash", "-c", cmd], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
        except Exception as exc:  # noqa: BLE001
            self._log(str(exc))
            return False
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self._log(line)
                ph = self._phase_from_line(line)
                if ph:
                    self._progress(phase=ph)
        try:
            return proc.wait(timeout=3600) == 0
        except subprocess.TimeoutExpired:
            proc.kill()
            self._log("[TIMEOUT] command ran over 60 min — killed")
            return False

    def _run_item_apply(self, item: Item):
        self._log(f"--- Applying: {item.content} ---")
        for n, cmd in enumerate(item.apply_cmds, 1):
            if not self._stream_apply_cmd(cmd):
                self._log(f"[FAILED] {cmd}")
                ledger_record(item.id, "apply", False,
                              f"failed at step {n}/{len(item.apply_cmds)}: {cmd}")
                return False
        self._log(f"[OK] {item.content}")
        ledger_record(item.id, "apply", True, f"{len(item.apply_cmds)} cmd(s) ok")
        return True

    def _run_item_undo(self, item: Item):
        self._log(f"--- Reverting: {item.content} ---")
        for n, cmd in enumerate(item.undo_cmds, 1):
            if not self._stream_apply_cmd(cmd):
                self._log(f"[FAILED] {cmd}")
                ledger_record(item.id, "undo", False,
                              f"failed at step {n}/{len(item.undo_cmds)}: {cmd}")
                return False
        self._log(f"[OK reverted] {item.content}")
        ledger_record(item.id, "undo", True, f"{len(item.undo_cmds)} cmd(s) ok")
        return True

    def _on_apply_click(self):
        if self._busy:
            messagebox.showinfo("Busy", "An operation is already running — check the log.")
            return
        selected_ids = [i.id for i in self.items.values() if i.var is not None and i.hw_supported]

        def _runnable(it):
            checked = it.var.get() if it.var else False
            if checked and not it.done:
                return True
            return bool(it.kind == "tweak" and not checked and it.applied and it.undo_cmds)

        n = sum(1 for iid in selected_ids if _runnable(self.items[iid]))
        self._begin_busy("Applying selected tweaks / apps", steps=max(1, n))
        threading.Thread(target=self._apply_worker, args=(selected_ids,), daemon=True).start()

    def _apply_worker(self, item_ids: list[str]):
        n_skipped = 0
        done = 0
        for item_id in item_ids:
            item = self.items[item_id]
            checked = item.var.get() if item.var else False
            if item.kind == "tweak":
                if checked and not item.done:
                    self._progress(overall=done, step=f"Applying {item.content}")
                    self._run_item_apply(item)
                    done += 1
                elif checked and item.done:
                    n_skipped += 1
                elif not checked and item.applied and item.undo_cmds:
                    self._progress(overall=done, step=f"Reverting {item.content}")
                    self._run_item_undo(item)
                    done += 1
            else:  # app: one-directional install only
                if checked and not item.done:
                    self._progress(overall=done, step=f"Installing {item.content}")
                    self._run_item_apply(item)
                    done += 1
                elif checked and item.done:
                    n_skipped += 1
        self._progress(overall=done)
        if n_skipped:
            self._log(f"[skipped {n_skipped} already-applied/installed item(s)]")
        self._log("=== Done. Click Refresh Status to confirm. ===")
        self._busy_queue.put("Done — refresh to confirm.")
        threading.Thread(target=self._refresh_all_status, daemon=True).start()

    def _on_apply_preset(self, preset_id: str):
        if self._busy:
            messagebox.showinfo("Busy", "An operation is already running — check the log.")
            return
        preset = self.presets[preset_id]
        if not messagebox.askyesno(
            "Confirm preset",
            f"Apply preset '{preset['Content']}'?\n\n{len(preset.get('tweaks', []))} tweaks + "
            f"{len(preset.get('apps', []))} apps will be applied/installed.",
        ):
            return
        ids = list(preset.get("tweaks", [])) + list(preset.get("apps", []))
        n = sum(1 for i in ids
                if (it := self.items.get(i)) and it.hw_supported and not it.done)
        self._begin_busy(f"Applying preset — {preset['Content']}", steps=max(1, n))
        threading.Thread(target=self._preset_worker, args=(ids,), daemon=True).start()

    def _preset_worker(self, item_ids: list[str]):
        done = 0
        for item_id in item_ids:
            item = self.items.get(item_id)
            if not item or not item.hw_supported:
                continue
            if not item.done:
                verb = "Installing" if item.kind == "app" else "Applying"
                self._progress(overall=done, step=f"{verb} {item.content}")
                self._run_item_apply(item)
                done += 1
            else:
                state = "pending reboot" if item.pending else ("installed" if item.kind == "app" else "applied")
                self._log(f"[skip, already {state}] {item.content}")
        self._progress(overall=done)
        self._log("=== Preset done. Click Refresh Status to confirm. ===")
        self._busy_queue.put("Preset done — refresh to confirm.")
        threading.Thread(target=self._refresh_all_status, daemon=True).start()


def _load_all_items() -> list:
    """Build the Item list (with vendor gating) without a ToolkitApp — shared
    by the --report / --debug CLI paths."""
    user = resolve_real_user()
    has_nv, has_amd = sensors.has_nvidia_gpu(), sensors.has_amd_gpu()
    items = []
    for kind, fn in (("tweak", "tweaks.json"), ("app", "apps.json")):
        for iid, data in load_json(fn).items():
            it = Item(iid, data, kind, user)
            if it.requires_vendor == "nvidia" and not has_nv:
                it.hw_supported = False
            elif it.requires_vendor == "amd" and not has_amd:
                it.hw_supported = False
            items.append(it)
    return items


def toolkit_version() -> str:
    for p in (BASE_DIR / ".version",):
        try:
            v = p.read_text().strip()
            if v:
                return v
        except OSError:
            pass
    ok, _rc, out = run_cmd3(f"git -C {BASE_DIR} describe --tags --always --dirty 2>/dev/null "
                            f"|| git -C {BASE_DIR} rev-parse --short HEAD 2>/dev/null")
    return out.strip() or "unknown"


def _diag_fans() -> str:
    lines = []
    try:
        lines.append(f"platform_profile: {sensors.get_platform_profile()}  "
                     f"choices={sensors.platform_profile_choices()}")
        fans = sensors.read_fans()
        for f in fans or []:
            lines.append(f"  {f['label']}: {f['rpm']} rpm  (max {f['max']}, boost {f['boost']})")
        if not fans:
            lines.append("  (no alienware_wmi / dell_smm fan interface found)")
        lines.append(f"dell_smm pwm state (enable,value): {sensors.get_pwm_state()}")
        lines.append(f"dGPU awake: {sensors.dgpu_is_awake()}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"(fan probe failed: {exc})")
    return "\n".join(lines)


# Groups of (title, shell-command, max-lines). Kept read-only + quick; every
# command is best-effort. Inspired by the evtest / /proc/bus/input/devices /
# dmesg dumps used to bring this board up in the first place.
_DEBUG_CMDS = [
    ("── SYSTEM ──", None, 0),
    ("OS", "cat /etc/os-release 2>/dev/null | grep -E '^(NAME|VERSION|VARIANT|ID|BUILD)' ", 12),
    ("Kernel / cmdline", "uname -a; echo; cat /proc/cmdline", 6),
    ("Firmware / DMI", "for f in sys_vendor product_name product_sku board_name board_version "
     "bios_vendor bios_version bios_date chassis_type; do "
     "printf '%-16s %s\\n' \"$f\" \"$(cat /sys/class/dmi/id/$f 2>/dev/null)\"; done", 16),
    ("Desktop session", "for s in $(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $1}'); do "
     "t=$(loginctl show-session \"$s\" -p Type --value 2>/dev/null); "
     "case \"$t\" in wayland|x11) loginctl show-session \"$s\" -p Name -p Type -p Desktop -p Active "
     "-p Remote 2>/dev/null; break;; esac; done; "
     "echo \"XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-} XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-}\"", 10),
    ("Uptime / load", "uptime", 3),
    ("── CPU / MEMORY ──", None, 0),
    ("CPU", "lscpu 2>/dev/null | grep -E 'Model name|^CPU\\(s\\)|Thread|Core|Socket|CPU max|Vendor'; "
     "echo \"governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null) "
     "epp: $(cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null)\"", 16),
    ("Memory / zram", "free -h; echo; zramctl 2>/dev/null; swapon --show 2>/dev/null", 14),
    ("── GPU ──", None, 0),
    ("PCI display devices", "lspci -nnk 2>/dev/null | grep -iA3 -E 'vga compatible|3d controller|display controller'", 24),
    ("NVIDIA", "nvidia-smi 2>/dev/null | head -18 || echo '(nvidia-smi unavailable — driver missing or dGPU runtime-suspended)'", 20),
    ("NVIDIA runtime PM", "for d in /sys/bus/pci/devices/*; do [ \"$(cat $d/vendor 2>/dev/null)\" = 0x10de ] && "
     "echo \"$(basename $d)  class=$(cat $d/class 2>/dev/null)  power=$(cat $d/power/runtime_status 2>/dev/null)\"; done", 6),
    ("AMD iGPU", "for c in /sys/class/drm/card[0-9]*/device; do [ \"$(cat $c/vendor 2>/dev/null)\" = 0x1002 ] && { "
     "echo \"$c\"; echo \" dpm: $(cat $c/power_dpm_force_performance_level 2>/dev/null)\"; "
     "cat $c/pp_dpm_sclk 2>/dev/null; }; done", 20),
    ("Mesa / GL", "glxinfo -B 2>/dev/null | grep -E 'OpenGL renderer|OpenGL version|Device:|Video memory' "
     "|| echo '(glxinfo not installed)'", 10),
    ("── THERMAL / POWER ──", None, 0),
    ("platform_profile", "echo \"current: $(cat /sys/firmware/acpi/platform_profile 2>/dev/null)\"; "
     "echo \"choices: $(cat /sys/firmware/acpi/platform_profile_choices 2>/dev/null)\"", 4),
    ("power-profiles-daemon", "powerprofilesctl get 2>/dev/null; echo '---'; powerprofilesctl 2>/dev/null | head -24", 26),
    ("Fans / hwmon", _diag_fans, None),
    ("sensors", "sensors 2>/dev/null || echo '(lm_sensors not installed)'", 45),
    ("RAPL (CPU power)", "ls -l /sys/class/powercap/*/energy_uj 2>/dev/null; "
     "(head -c1 /sys/class/powercap/intel-rapl:0/energy_uj >/dev/null 2>&1 && echo 'RAPL readable') "
     "|| echo 'RAPL NOT readable without root (kernel side-channel mitigation)'", 10),
    ("── KEYBOARD / HOTKEYS / MEDIA KEYS ──", None, 0),
    ("Loaded modules", "lsmod | grep -E '^(dell|alienware|i8k|sparse_keymap|hid_|nvidia|amdgpu)' | sort", 30),
    ("Alienware USB LED controller", "lsusb 2>/dev/null | grep -iE '187c:|alienware' || echo '(187c:0550 AW-ELC not seen on USB)'", 4),
    ("HID devices", "for h in /sys/bus/hid/devices/*; do [ -e \"$h\" ] || continue; "
     "printf '%-24s %s\\n' \"$(basename $h)\" \"$(cat $h/input/input*/name 2>/dev/null | head -1)\"; done", 20),
    ("input devices (evdev + KEY capability bitmaps)", "cat /proc/bus/input/devices", 140),
    ("event device names", "for e in /dev/input/event*; do "
     "printf '%-22s %s\\n' \"$e\" \"$(cat /sys/class/input/$(basename $e)/device/name 2>/dev/null)\"; done", 30),
    ("Dell WMI / hotkey / media-key devices", "for e in /dev/input/event*; do "
     "n=$(cat /sys/class/input/$(basename $e)/device/name 2>/dev/null); "
     "case \"$n\" in *WMI*|*wireless\\ hotkey*|*Wireless\\ hotkey*|*Translated\\ Set\\ 2*|*Video\\ Bus*) "
     "echo \"$e  $n\";; esac; done", 15),
    ("Fn-Lock / G-key note", "echo 'G-key = KEY_PERFORMANCE(701) on \"AT Translated Set 2 keyboard\" when Fn-Lock OFF, "
     "KEY_F9 when ON. Media keys (vol/mute) come via \"Dell WMI hotkeys\". Fn is an EC key and never reaches evdev.'", 4),
    ("input group membership", "u=$(logname 2>/dev/null || echo \"${SUDO_USER:-}\"); "
     "echo \"desktop user: $u\"; id \"$u\" 2>/dev/null; getent group input; "
     "id -nG \"$u\" 2>/dev/null | tr ' ' '\\n' | grep -qx input "
     "&& echo 'OK: user is in the input group' || echo 'WARN: user NOT in input group "
     "(the G-key HotkeyListener needs it)'", 8),
    ("── RGB KEYBOARD (OpenRGB) ──", None, 0),
    ("OpenRGB", "openrgb --version 2>/dev/null | head -1 || echo '(openrgb not installed)'", 4),
    ("OpenRGB devices", "openrgb --noautoconnect -l 2>/dev/null | grep -vE '<[a-z]|i2c|SMBus|help.openrgb' | head -40", 45),
    ("kbd services", "for s in dellg15-openrgb.service dellg15-kbd.service; do "
     "printf '%-26s enabled=%-9s active=%s\\n' \"$s\" "
     "\"$(systemctl is-enabled $s 2>/dev/null)\" \"$(systemctl is-active $s 2>/dev/null)\"; done", 6),
    ("kbd saved state", "u=$(logname 2>/dev/null || echo \"${SUDO_USER:-$USER}\"); "
     "h=$(getent passwd \"$u\" | cut -d: -f6); cat \"$h/.config/dellg15-toolkit/kbd.json\" 2>/dev/null "
     "|| echo '(no kbd.json — colour not saved / KbdBacklightFix not used)'", 24),
    ("── TWEAK SERVICES / SUDOERS ──", None, 0),
    ("dellg15 units", "systemctl list-unit-files 2>/dev/null | grep -E 'dellg15|hotkey' ; "
     "systemctl --user list-unit-files 2>/dev/null | grep -E 'dellg15|hotkey'", 12),
    ("sudoers drop-ins", "ls -l /etc/sudoers.d/ 2>/dev/null | grep -E 'dellg15|gamemode|claude' || echo '(none)'", 8),
    ("── PACKAGES ──", None, 0),
    ("Kernels installed", "rpm -q kernel --qf '%{VERSION}-%{RELEASE}.%{ARCH}\\n' 2>/dev/null | sort -V", 10),
    ("NVIDIA packages", "rpm -qa 2>/dev/null | grep -iE 'nvidia|akmod-nvidia|cuda' | sort "
     "|| echo '(no NVIDIA packages — driver may be from a -NV image or missing)'", 12),
    ("Relevant packages", "rpm -q openrgb gamemode mangohud goverlay gamescope vkbasalt lm_sensors "
     "nobara-updater tlp auto-cpufreq 2>&1 | sed 's/ is not installed/  — NOT installed/'", 14),
    ("Update tooling", "dnf --version 2>/dev/null | head -1; command -v nobara-sync >/dev/null && echo 'nobara-sync: present'; "
     "command -v flatpak >/dev/null && flatpak --version; command -v fwupdmgr >/dev/null && echo 'fwupd: present'", 6),
    ("── LOGS ──", None, 0),
    ("dmesg (filtered, deduped)", "dmesg 2>/dev/null "
     "| grep -iE 'dell_|dell-|alienware|aw-elc|187c:0550|hid-generic 0003:187C|i8042|"
     "firmware bug|thermal (throttl|event)|MCE|hardware error|"
     "(nvidia|amdgpu|nouveau).*(error|fail|warn|timed? ?out|reset|hang|fault|Xid)|"
     "platform.?profile|pstate' "
     "| grep -viE 'Mode Validation Warning|Unknown Status failed|Console: switching|fbcon' "
     "| sed -E 's/^\\[[0-9. ]+\\] //' | awk '!seen[$0]++' | tail -40 "
     "|| echo '(dmesg not readable — run the toolkit with sudo, or kernel.dmesg_restrict=1)'", 42),
    ("journal errors (this boot)", "journalctl -b -p err --no-pager 2>/dev/null "
     "| grep -viE 'Module lib.*from rpm|^ *Module |drkonqi|KCrash|Stack trace|"
     "^ *#[0-9]+ +0x|libQt6|libKF6|libc\\.so|__libc_start' "
     "| awk '!seen[$0]++' | tail -35 || echo '(journalctl unavailable)'", 37),
    ("journal — kbd / fan / gpu units (this boot)", "journalctl -b --no-pager "
     "-u 'dellg15-*' -u 'dellg15-*.service' 2>/dev/null | tail -25; "
     "journalctl -b --no-pager 2>/dev/null | grep -iE "
     "'openrgb\\[|dell_smm|alienware_wmi|nvidia-persistenced|(nvidia|amdgpu).*(Xid|GPU has fallen|ring .* timeout)' "
     "| grep -viE 'audit\\[|sudo\\[|Mode Validation' | awk '!seen[$0]++' | tail -20 || echo '(none)'", 40),
]


def collect_debug_report(items=None, wrap: bool = False) -> str:
    """Assemble a hardware + OS + toolkit-state report for bug reports. All
    commands are read-only, hard-timed-out and best-effort. Run as root for
    the complete picture (dmesg, RAPL, privileged checks). `wrap=True` returns
    it inside a GitHub `<details>` + fenced block, ready to paste."""
    hdr = [
        "Dell G15 Toolkit — debug report",
        f"generated {time.strftime('%Y-%m-%d %H:%M:%S %Z')}   toolkit {toolkit_version()}   "
        f"euid={os.geteuid()}",
        "REVIEW BEFORE PASTING — this contains your username, hostname and hardware IDs.",
        "=" * 92, "",
    ]
    body = []
    for title, cmd, maxlines in _DEBUG_CMDS:
        if cmd is None:                       # section divider
            body.append(f"\n{title}")
            continue
        try:
            if callable(cmd):
                out = cmd()
            else:  # hard cap via coreutils `timeout` so nothing can wedge
                out = run_cmd3(f"timeout -k 2 12 bash -lc {shlex.quote(cmd)}", timeout=16)[2]
        except Exception as exc:              # noqa: BLE001
            out = f"(error: {exc})"
        out = out.strip() or "(no output)"
        if maxlines:
            ls = out.splitlines()
            if len(ls) > maxlines:
                out = "\n".join(ls[:maxlines]) + f"\n… ({len(ls) - maxlines} more lines trimmed)"
        body.append(f"\n### {title}\n{out}")

    body.append("\n\n── TOOLKIT: KEYBOARD DRIVER ──")
    try:
        info = __import__("dellg15_kbd").info()
        body.append("\n### dellg15_kbd info\n" +
                    "\n".join(f"{k:16}: {v}" for k, v in info.items()))
    except Exception as exc:  # noqa: BLE001
        body.append(f"\n### dellg15_kbd info\n(error: {exc})")

    body.append("\n\n── TOOLKIT: APPLY STATUS ──")
    if items is None:
        items = _load_all_items()
        led = ledger_load()
        with ThreadPoolExecutor(max_workers=12) as ex:
            list(ex.map(lambda it: evaluate_item(it, led), items))
    body.append("\n" + format_status_report(items))

    body.append("\n── TOOLKIT: APPLY LEDGER (state.json) ──\n" +
                json.dumps(ledger_load(), indent=2, sort_keys=True))
    report = "\n".join(hdr) + "\n".join(body) + "\n"
    return wrap_issue_block(report) if wrap else report


def wrap_issue_block(report: str) -> str:
    """Wrap a raw report in a GitHub-ready collapsible fenced block."""
    return ("<details><summary>debug report — Dell G15 Toolkit</summary>\n\n"
            "```\n" + report.replace("```", "``​`").rstrip() + "\n```\n\n</details>\n")


GITHUB_ISSUE_TEMPLATE = """\
### What happened


### What you expected instead


### Where in the toolkit (which page / button / tweak)


### Steps to reproduce
1.
2.
3.

### Is your hardware the Dell G15 5515 Ryzen Edition on Nobara?
<!-- This tool is written for exactly that one machine. On anything else most
     checks/tweaks won't apply — say what you're on. -->
- [ ] yes, G15 5515 Ryzen + Nobara
- [ ] close (other G15 / other Dell hybrid) — details:
- [ ] no — details:

### Debug report
<!-- Toolkit → Diagnostics page → "Generate report" → "Copy report",
     or a terminal:  sudo python3 /opt/dellg15-toolkit/dellg15_toolkit.py --debug
     Review it for your username/hostname, then paste between the ``` fences. -->
<details><summary>debug report</summary>

```
PASTE THE DEBUG REPORT HERE
```

</details>

### Screenshot / log console output (if relevant)

"""


def cli_report() -> int:
    """`--report`: print the status table, no GUI."""
    items = _load_all_items()
    ledger = ledger_load()
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(lambda it: evaluate_item(it, ledger), items))
    print(format_status_report(items))
    if os.geteuid() != 0:
        print("note: not running as root — privileged checks may read as "
              "'Not applied'/'Check error'. Re-run with sudo for accuracy.")
    return 0


def cli_debug() -> int:
    """`--debug`: print the full hardware/OS/toolkit debug report."""
    print(collect_debug_report())
    if os.geteuid() != 0:
        print("\nnote: run with sudo for dmesg / RAPL / privileged checks.", file=sys.stderr)
    return 0


def main():
    if "--report" in sys.argv:
        raise SystemExit(cli_report())
    if "--debug" in sys.argv or "--diag" in sys.argv:
        raise SystemExit(cli_debug())
    self_elevate()
    root = tb.Window(themename=THEME)
    ToolkitApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
