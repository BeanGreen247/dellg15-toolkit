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
import json
import os
import pwd
import queue
import shutil
import site
import subprocess
import sys
import threading
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

CATEGORY_ORDER = ["GPU", "Power", "Performance", "Software", "Monitoring", "Streaming", "RGB", "Gaming"]
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
        self.applied = False
        self.pending = False  # staged (bootloader) but needs a reboot to be live
        self.var = None  # tk.BooleanVar, set when widget built
        self.status_label = None
        self.checkbutton = None

    @property
    def done(self) -> bool:
        """Already in the desired state — nothing to (re-)apply."""
        return self.applied or self.pending


def run_cmd(cmd: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=1800)
        ok = result.returncode == 0
        out = (result.stdout or "") + (result.stderr or "")
        return ok, out.strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


class ToolkitApp:
    def __init__(self, root: "tb.Window"):
        self.root = root
        self.user = resolve_real_user()
        root.title("Dell G15 5515 Toolkit — Nobara Linux")
        root.geometry("1080x760")  # fallback size if the WM ignores maximise
        _maximize(root)
        self._set_window_icon(root)

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

        self._scroll_canvases: list = []   # every scrollable tab body (for the wheel)
        self._log_lines: list[str] = []    # full log buffer, mirrored to any popped-out window
        self._pop_win = None               # detached log Toplevel, when open
        self._pop_text = None
        self._log_collapsed = False

        self._build_ui()
        self.root.after(100, self._poll_log_queue)
        self.root.after(100, self._poll_dash_queue)
        self.root.after(100, self._poll_status_queue)
        threading.Thread(target=self._refresh_all_status, daemon=True).start()

        self.dash_running = True
        threading.Thread(target=self._dashboard_loop, daemon=True).start()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self.dash_running = False
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

        tb.Separator(self.root).pack(fill="x", padx=16)

        self.notebook = tb.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=12)

        self._build_dashboard_tab()
        self._build_keyboard_tab()
        self._build_presets_tab()

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
        tb.Button(btn_bar, text="↻  Refresh Status", bootstyle=(INFO, "outline"),
                  command=self._on_refresh_click).pack(side="left")
        tb.Button(btn_bar, text="✓  Apply Selected", bootstyle=SUCCESS,
                  command=self._on_apply_click).pack(side="left", padx=8)
        self.status_var = tk.StringVar(value="Ready.")
        tb.Label(btn_bar, textvariable=self.status_var, bootstyle=SECONDARY,
                 font=("Sans", 9)).pack(side="right")

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
        self.notebook.add(outer, text="⌂ Dashboard")
        frame = self._scroll_body(outer, pad=16)

        gauges = tb.Frame(frame)
        gauges.pack(fill="x", pady=(0, 20))

        self.meter_cpu_temp = tb.Meter(
            gauges, amounttotal=100, amountused=0, metersize=170,
            subtext="CPU °C", bootstyle=INFO, interactive=False, textright="°C",
        )
        self.meter_cpu_temp.grid(row=0, column=0, padx=14)

        self.meter_cpu_freq = tb.Meter(
            gauges, amounttotal=50, amountused=0, metersize=170,
            subtext="CPU GHz (x0.1)", bootstyle=SUCCESS, interactive=False,
        )
        self.meter_cpu_freq.grid(row=0, column=1, padx=14)

        self.meter_dgpu_temp = tb.Meter(
            gauges, amounttotal=100, amountused=0, metersize=170,
            subtext="dGPU °C", bootstyle=WARNING, interactive=False, textright="°C",
        )
        self.meter_dgpu_temp.grid(row=0, column=2, padx=14)

        self.meter_dgpu_util = tb.Meter(
            gauges, amounttotal=100, amountused=0, metersize=170,
            subtext="dGPU Util %", bootstyle=DANGER, interactive=False, textright="%",
        )
        self.meter_dgpu_util.grid(row=0, column=3, padx=14)

        gauges2 = tb.Frame(frame)
        gauges2.pack(fill="x", pady=(0, 8))

        self.meter_cpu_power = tb.Meter(
            gauges2, amounttotal=65, amountused=0, metersize=170,
            subtext="CPU Package W", bootstyle=INFO, interactive=False, textright="W",
        )
        self.meter_cpu_power.grid(row=0, column=0, padx=14)

        self.meter_dgpu_power = tb.Meter(
            gauges2, amounttotal=80, amountused=0, metersize=170,
            subtext="dGPU W", bootstyle=WARNING, interactive=False, textright="W",
        )
        self.meter_dgpu_power.grid(row=0, column=1, padx=14)

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
        self.notebook.add(outer, text="⌨ Keyboard")
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

        note = tb.Labelframe(frame, text="How this works", bootstyle=INFO, padding=12)
        note.pack(fill="x", pady=(0, 14))
        tb.Label(
            note, wraplength=1100, justify="left", bootstyle="inverse-info",
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
        self.kbd_speed.set(dellg15_kbd.load_meta().get("speed", 50))

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
        frow = tb.Frame(fx)
        frow.pack(fill="x")
        for label, key in (("Rainbow Wave", "rainbow"), ("Spectrum Cycle", "spectrum"),
                           ("Breathing", "breathing"), ("Flashing", "flashing")):
            tb.Button(frow, text=label, bootstyle=SECONDARY,
                      command=lambda k=key: self._kbd_apply_effect(k)).pack(side="left", padx=3)

        bottom = tb.Frame(frame)
        bottom.pack(fill="x", pady=(4, 0))
        tb.Button(bottom, text="Turn backlight off", bootstyle=(SECONDARY, "outline"),
                  command=self._kbd_off).pack(side="left")

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

    def _kbd_off(self):
        self.kbd_brightness.set(0)
        self._kbd_run(lambda kb: kb.off(), "backlight off")

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
                self.meter_cpu_temp.configure(amountused=cpu_temp or 0)
                self.meter_cpu_freq.configure(amountused=(cpu_freq or 0) * 10)
                self.meter_cpu_power.configure(amountused=cpu_power or 0)
                self.meter_dgpu_temp.configure(amountused=dgpu_temp or 0)
                self.meter_dgpu_util.configure(amountused=dgpu_util or 0)
                self.meter_dgpu_power.configure(amountused=dgpu_power or 0)
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
        self.root.after(50, self._poll_log_queue)

    def _refresh_all_status(self):
        for item in self.items.values():
            item.pending = False
            if not item.hw_supported:
                item.applied = False
                continue
            if not item.check_cmd:
                item.applied = False
                continue
            ok, _ = run_cmd(item.check_cmd)
            item.applied = ok
            if not ok and item.check_pending_cmd:
                item.pending = run_cmd(item.check_pending_cmd)[0]
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
        n_done = n_total = 0
        for item in self.items.values():
            if item.status_label is None:
                continue
            if not item.hw_supported:
                item.status_label.configure(text="unsupported", bootstyle=SECONDARY)
                continue
            n_total += 1
            if item.pending:
                label, style = "Pending reboot", INFO
            elif item.applied:
                label = "Installed" if item.kind == "app" else "Applied"
                style = SUCCESS
            else:
                label = "Not installed" if item.kind == "app" else "Not applied"
                style = SECONDARY
            if item.done:
                n_done += 1
            item.status_label.configure(text=label, bootstyle=style)
            if item.var is not None:
                item.var.set(item.done)
        self.status_var.set(
            f"{n_done} of {n_total} already applied/installed — "
            f"{n_total - n_done} available. Ticked = already done; Apply skips those."
        )

    def _on_refresh_click(self):
        self.status_var.set("Refreshing status…")
        threading.Thread(target=self._refresh_all_status, daemon=True).start()

    # ---------- apply logic ----------

    def _run_item_apply(self, item: Item):
        self._log(f"--- Applying: {item.content} ---")
        for cmd in item.apply_cmds:
            ok, out = run_cmd(cmd)
            if out:
                self._log(out)
            if not ok:
                self._log(f"[FAILED] {cmd}")
                return False
        self._log(f"[OK] {item.content}")
        return True

    def _run_item_undo(self, item: Item):
        self._log(f"--- Reverting: {item.content} ---")
        for cmd in item.undo_cmds:
            ok, out = run_cmd(cmd)
            if out:
                self._log(out)
            if not ok:
                self._log(f"[FAILED] {cmd}")
                return False
        self._log(f"[OK reverted] {item.content}")
        return True

    def _on_apply_click(self):
        if self.worker_running:
            messagebox.showinfo("Busy", "An operation is already running — check the log.")
            return
        selected_ids = [i.id for i in self.items.values() if i.var is not None and i.hw_supported]
        threading.Thread(target=self._apply_worker, args=(selected_ids,), daemon=True).start()

    def _apply_worker(self, item_ids: list[str]):
        self.worker_running = True
        self.status_var.set("Applying…")
        n_skipped = 0
        for item_id in item_ids:
            item = self.items[item_id]
            checked = item.var.get() if item.var else False
            if item.kind == "tweak":
                if checked and not item.done:
                    self._run_item_apply(item)
                elif checked and item.done:
                    n_skipped += 1
                elif not checked and item.applied and item.undo_cmds:
                    self._run_item_undo(item)
            else:  # app: one-directional install only
                if checked and not item.done:
                    self._run_item_apply(item)
                elif checked and item.done:
                    n_skipped += 1
        if n_skipped:
            self._log(f"[skipped {n_skipped} already-applied/installed item(s)]")
        self._log("=== Done. Click Refresh Status to confirm. ===")
        self.status_var.set("Done — refresh to confirm.")
        self.worker_running = False
        threading.Thread(target=self._refresh_all_status, daemon=True).start()

    def _on_apply_preset(self, preset_id: str):
        if self.worker_running:
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
        threading.Thread(target=self._preset_worker, args=(ids,), daemon=True).start()

    def _preset_worker(self, item_ids: list[str]):
        self.worker_running = True
        self.status_var.set("Applying preset…")
        for item_id in item_ids:
            item = self.items.get(item_id)
            if not item or not item.hw_supported:
                continue
            if not item.done:
                self._run_item_apply(item)
            else:
                state = "pending reboot" if item.pending else ("installed" if item.kind == "app" else "applied")
                self._log(f"[skip, already {state}] {item.content}")
        self._log("=== Preset done. Click Refresh Status to confirm. ===")
        self.status_var.set("Preset done — refresh to confirm.")
        self.worker_running = False
        threading.Thread(target=self._refresh_all_status, daemon=True).start()


def main():
    self_elevate()
    root = tb.Window(themename=THEME)
    ToolkitApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
