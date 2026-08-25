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
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
sys.path.insert(0, str(BASE_DIR))

try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import SUCCESS, SECONDARY, WARNING, INFO, DANGER
except ImportError:
    print("ttkbootstrap not found. Install with: pip install --user ttkbootstrap")
    print("(not packaged in Fedora/Nobara's repos — pip is the only path)")
    sys.exit(1)

import sensors  # noqa: E402  (local module, no GUI deps)

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
        self.var = None  # tk.BooleanVar, set when widget built
        self.status_label = None
        self.checkbutton = None


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
        root.title("Dell G15 Toolkit — Nobara Linux")
        root.geometry("1080x760")

        self.has_nvidia = sensors.has_nvidia_gpu()
        self.has_amd = sensors.has_amd_gpu()

        self.items: dict[str, Item] = {}
        self._load_items()
        self.presets = load_json("presets.json")

        self.log_queue: queue.Queue = queue.Queue()
        self.dash_queue: queue.Queue = queue.Queue()
        self.worker_running = False
        self.gamemode_var = tk.BooleanVar(value=False)
        self._suppress_gamemode_signal = False

        self._build_ui()
        self.root.after(100, self._poll_log_queue)
        self.root.after(100, self._poll_dash_queue)
        threading.Thread(target=self._refresh_all_status, daemon=True).start()

        self.dash_running = True
        threading.Thread(target=self._dashboard_loop, daemon=True).start()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self.dash_running = False
        self.root.destroy()

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
        header = tb.Frame(self.root, padding=10)
        header.pack(fill="x")
        tb.Label(
            header,
            text="Dell G15 5515 Ryzen Edition",
            font=("Sans", 15, "bold"),
        ).pack(side="left")
        tb.Label(
            header, text="  — Nobara Linux, this laptop's hardware only",
            font=("Sans", 10), bootstyle=SECONDARY,
        ).pack(side="left")
        tb.Label(header, text=f"running as {self.user} (elevated)", bootstyle=SECONDARY).pack(side="right")

        self.notebook = tb.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._build_dashboard_tab()
        self._build_presets_tab()

        categories = sorted(
            {item.category for item in self.items.values()},
            key=lambda c: CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else 99,
        )
        for cat in categories:
            self._build_category_tab(cat)

        btn_bar = tb.Frame(self.root, padding=10)
        btn_bar.pack(fill="x")
        tb.Button(btn_bar, text="Refresh Status", bootstyle=INFO, command=self._on_refresh_click).pack(side="left", padx=4)
        tb.Button(btn_bar, text="Apply Selected", bootstyle=SUCCESS, command=self._on_apply_click).pack(side="left", padx=4)
        self.status_var = tk.StringVar(value="Ready.")
        tb.Label(btn_bar, textvariable=self.status_var, bootstyle=SECONDARY).pack(side="right", padx=4)

        log_frame = tb.Frame(self.root, padding=(10, 0, 10, 10))
        log_frame.pack(fill="both", expand=False)
        tb.Label(log_frame, text="Log", bootstyle=SECONDARY).pack(anchor="w")
        self.log_text = tk.Text(log_frame, height=9, font=("Monospace", 9), bg="#111417", fg="#d0d5da",
                                 insertbackground="#d0d5da", relief="flat", wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def _build_dashboard_tab(self):
        frame = tb.Frame(self.notebook, padding=16)
        self.notebook.add(frame, text="⌂ Dashboard")

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

    def _build_category_tab(self, category: str):
        outer = tb.Frame(self.notebook)
        self.notebook.add(outer, text=category)

        canvas = tk.Canvas(outer, highlightthickness=0, bg=self.root.style.colors.bg)
        scrollbar = tb.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tb.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for item in self.items.values():
            if item.category != category:
                continue
            row = tb.Frame(inner, padding=10, bootstyle="secondary")
            row.pack(fill="x", padx=8, pady=4)

            item.var = tk.BooleanVar(value=False)
            cb = tb.Checkbutton(row, variable=item.var, bootstyle="round-toggle")
            cb.pack(side="left", anchor="n", padx=(0, 10))
            item.checkbutton = cb
            if not item.hw_supported:
                item.var.set(False)
                cb.configure(state="disabled")

            text_frame = tb.Frame(row)
            text_frame.pack(side="left", fill="x", expand=True)
            title_row = tb.Frame(text_frame)
            title_row.pack(anchor="w", fill="x")
            tb.Label(title_row, text=item.content, font=("Sans", 10, "bold")).pack(side="left")
            if item.risk == "advanced":
                tb.Label(title_row, text=" ADVANCED", bootstyle=(WARNING, "inverse"), font=("Sans", 7, "bold")).pack(side="left", padx=6)
            tb.Label(text_frame, text=item.description, wraplength=760, bootstyle=SECONDARY, justify="left").pack(anchor="w")

            item.status_label = tb.Label(row, text="checking…", width=16, bootstyle=SECONDARY, anchor="e")
            item.status_label.pack(side="right", anchor="n")

    def _build_presets_tab(self):
        frame = tb.Frame(self.notebook, padding=14)
        self.notebook.add(frame, text="Presets")
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
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_log_queue)

    def _refresh_all_status(self):
        for item in self.items.values():
            if not item.hw_supported:
                item.applied = False
                continue
            if not item.check_cmd:
                item.applied = False
                continue
            ok, _ = run_cmd(item.check_cmd)
            item.applied = ok
        self.root.after(0, self._apply_status_to_widgets)

    def _apply_status_to_widgets(self):
        for item in self.items.values():
            if item.status_label is None:
                continue
            if not item.hw_supported:
                item.status_label.configure(text="unsupported", bootstyle=SECONDARY)
                continue
            label = "Applied" if item.applied else "Not applied"
            style = SUCCESS if item.applied else SECONDARY
            if item.kind == "app":
                label = "Installed" if item.applied else "Not installed"
            item.status_label.configure(text=label, bootstyle=style)
            if item.var is not None:
                item.var.set(item.applied)
        self.status_var.set("Status refreshed.")

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
        for item_id in item_ids:
            item = self.items[item_id]
            checked = item.var.get() if item.var else False
            if item.kind == "tweak":
                if checked and not item.applied:
                    self._run_item_apply(item)
                elif not checked and item.applied and item.undo_cmds:
                    self._run_item_undo(item)
            else:  # app: one-directional install only
                if checked and not item.applied:
                    self._run_item_apply(item)
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
            if not item.applied:
                self._run_item_apply(item)
            else:
                self._log(f"[skip, already applied] {item.content}")
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
