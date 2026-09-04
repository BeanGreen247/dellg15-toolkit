#!/usr/bin/env python3
"""Dell G15 tray monitor — CPU/GPU clocks & temps + Game Mode toggle.

The "live dashboard + dedicated-key toggle" piece of this Toolkit, inspired
by Div-Acer-Manager-Max's (DAMX) monitoring dashboard and Nitro/PredatorSense
button binding: https://github.com/PXDiv/Div-Acer-Manager-Max

Runs as a normal user process (unprivileged) and lives in the system tray
via Qt's StatusNotifierItem support (native on KDE Plasma). Only the actual
Game Mode toggle shells out through pkexec/sudo (see sensors.set_game_mode),
since that needs root — the monitor itself never needs elevation.

All sensor reads and the Game Mode toggle live in sensors.py (no GUI
dependency), shared with tuxthrottle.py's in-app Dashboard tab.

Requires: PySide6 (dnf install python3-pyside6, or: pip install --user PySide6)
Reuses the gaming-performance/gaming-balanced, amdgpu-perf-high/auto, and
nvidia-max-perf helper scripts installed by tuxthrottle.py's tweaks —
install those first (Presets > Safe Baseline covers the power-profile ones;
Competitive Gaming covers the GPU perf-state ones).
"""
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
import sensors  # noqa: E402

APP_NAME = "TuxThrottle"
APP_BLURB = "Dell G15 power & gaming tuning"
APP_AUTHOR = "by BeanGreen247"
PROJECT_URL = "https://github.com/BeanGreen247/tuxthrottle"


def _reassert_keyboard_rgb() -> None:
    """Re-apply the last saved AW-ELC keyboard colour/effect when the tray
    starts (login). Best-effort and off the Qt thread — the helper waits for
    the OpenRGB SDK server + USB HID device to come up, which can take a few
    seconds after a cold boot. No-op if nothing is saved or OpenRGB is
    absent."""
    def _run():
        try:
            import tuxthrottle_kbd
            tuxthrottle_kbd.main(["apply-saved"])
        except Exception as exc:  # noqa: BLE001
            print(f"tray: keyboard re-assert skipped ({exc})", file=sys.stderr)
    threading.Thread(target=_run, name="kbd-reassert", daemon=True).start()


def _launch_gui() -> tuple[bool, str]:
    """Open the main TuxThrottle window.

    The GUI self-elevates with `pkexec`, and pkexec can only reach the
    polkit-kde auth agent if the process it spawns is still attached to the
    graphical session — so do NOT start a new session here, and prefer
    `kstart`, which launches the command as a proper session app.
    """
    cmd = (shutil.which("tuxthrottle")
           or ("/opt/tuxthrottle/tuxthrottle"
               if os.path.exists("/opt/tuxthrottle/tuxthrottle") else None))
    base = [cmd] if cmd else [sys.executable, str(BASE_DIR / "tuxthrottle.py")]

    attempts = []
    if shutil.which("kstart"):
        attempts.append(["kstart"] + base)
    attempts.append(base)

    last = ""
    for argv in attempts:
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True, ""
        except (OSError, subprocess.SubprocessError) as exc:
            last = str(exc)
    return False, last or "no launcher found"


def _ctl(*args: str) -> tuple[bool, str]:
    """Run `tuxthrottlectl <args>` with privilege. `set` needs root; pkexec +
    the PolkitTuxthrottlectl policy make it passwordless for an active local
    user, and tuxthrottlectl itself routes through the daemon socket when it
    is up. Falls back to `sudo -n`."""
    ctl = shutil.which("tuxthrottlectl") or "/usr/local/bin/tuxthrottlectl"
    for launcher in (["pkexec"], ["sudo", "-n"], []):
        if launcher and not shutil.which(launcher[0]):
            continue
        try:
            r = subprocess.run([*launcher, ctl, *args],
                               capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        if r.returncode == 0:
            return True, (r.stdout or "").strip()
        last = (r.stderr or r.stdout or "").strip()
    return False, last or "tuxthrottlectl failed"

try:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QAction, QIcon
    from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon
except ImportError:
    print("PySide6 not found. Install with: dnf install python3-pyside6")
    print("(or: pip install --user PySide6)")
    sys.exit(1)

POLL_MS = 2000


class TrayMonitor:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        icon_file = Path(__file__).resolve().parent / "assets" / "icon-256.png"
        icon = QIcon(str(icon_file)) if icon_file.is_file() else QIcon()
        if icon.isNull():
            icon = QIcon.fromTheme("utilities-system-monitor")
        if icon.isNull():
            icon = QIcon.fromTheme("computer")
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip(
            f"{APP_NAME} — {APP_BLURB}\n{APP_AUTHOR}\nClick to open")

        self.menu = QMenu()
        # keep a ref on self — a parent-less QAction added to a QMenu is not
        # owned by it and would be GC'd out of the menu once __init__ returns
        self.about_action = QAction(f"{APP_NAME} {APP_AUTHOR}", self.menu)
        self.about_action.triggered.connect(self._open_project_page)
        self.menu.addAction(self.about_action)
        self.open_action = QAction(f"Open {APP_NAME}", self.menu)
        _f = self.open_action.font()
        _f.setBold(True)
        self.open_action.setFont(_f)
        self.open_action.triggered.connect(self._open_gui)
        self.menu.addAction(self.open_action)
        self.menu.addSeparator()
        self.cpu_action = self._info_action("CPU: …")
        self.igpu_action = self._info_action("iGPU: …")
        self.dgpu_action = self._info_action("dGPU: …")
        self.rapl_warning_action = self._info_action("")
        self.rapl_warning_action.setVisible(False)
        self.menu.addSeparator()

        self.gamemode_action = QAction("Game Mode", checkable=True)
        self.gamemode_action.toggled.connect(self._on_gamemode_toggled)
        self.menu.addAction(self.gamemode_action)

        prof_menu = self.menu.addMenu("Power profile")
        for label, value in (("Balanced", "balanced"),
                             ("Performance", "performance")):
            act = QAction(label, prof_menu)
            act.triggered.connect(lambda _c=False, v=value: self._set_profile(v))
            prof_menu.addAction(act)

        fan_menu = self.menu.addMenu("Fan boost")
        for label, pct in (("Off (0%)", 0), ("Half (50%)", 50), ("Max (100%)", 100)):
            act = QAction(label, fan_menu)
            act.triggered.connect(lambda _c=False, p=pct: self._set_fan_boost(p))
            fan_menu.addAction(act)

        self.menu.addSeparator()
        quit_action = QAction("Quit")
        quit_action.triggered.connect(self.app.quit)
        self.menu.addAction(quit_action)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        self._suppress_toggle_signal = False
        self._refresh()
        self._sync_gamemode_state()

        self.timer = QTimer()
        self.timer.timeout.connect(self._refresh)
        self.timer.start(POLL_MS)

        self.state_timer = QTimer()
        self.state_timer.timeout.connect(self._sync_gamemode_state)
        self.state_timer.start(10_000)

    def _info_action(self, text: str) -> QAction:
        a = QAction(text)
        a.setEnabled(False)
        self.menu.addAction(a)
        return a

    def _refresh(self):
        cpu_power = sensors.read_cpu_power_watts()
        power_txt = f", {cpu_power:.1f} W" if cpu_power is not None else ""
        self.cpu_action.setText(f"CPU: {sensors.read_cpu_freq_ghz()}, {sensors.read_cpu_temp_c()}{power_txt}")
        self.igpu_action.setText(f"iGPU: {sensors.read_igpu_clock_temp()}")
        self.dgpu_action.setText(f"dGPU: {sensors.read_dgpu_clock_temp_util()}")
        if sensors.rapl_permissions_ok():
            self.rapl_warning_action.setVisible(False)
        else:
            self.rapl_warning_action.setText("⚠ CPU power locked — install RaplPowerPermissions tweak")
            self.rapl_warning_action.setVisible(True)
        self.tray.setToolTip(
            f"{APP_NAME} — {APP_BLURB}\n{APP_AUTHOR}\n"
            f"Click to open  ·  CPU {sensors.read_cpu_temp_c()}  ·  "
            f"dGPU {sensors.read_dgpu_clock_temp_util()}"
        )

    def _sync_gamemode_state(self):
        state = sensors.get_game_mode_state()
        self._suppress_toggle_signal = True
        self.gamemode_action.setChecked(state)
        self._suppress_toggle_signal = False

    def _on_gamemode_toggled(self, checked: bool):
        if self._suppress_toggle_signal:
            return
        ok, err = sensors.set_game_mode(checked)
        if not ok:
            QMessageBox.warning(None, "Game Mode", f"Failed: {err}")
            self._sync_gamemode_state()
        self._refresh()

    def _set_profile(self, value: str):
        ok, msg = _ctl("set", "power-profile", value)
        if not ok:
            QMessageBox.warning(None, "Power profile", f"Failed: {msg}")
        self._refresh()

    def _set_fan_boost(self, pct: int):
        ok, msg = _ctl("set", "fan-boost", "both", str(pct))
        if not ok:
            QMessageBox.warning(None, "Fan boost", f"Failed: {msg}")
        self._refresh()

    def _open_gui(self):
        ok, err = _launch_gui()
        if not ok:
            QMessageBox.warning(None, APP_NAME, f"Couldn't open the window:\n{err}")

    def _open_project_page(self):
        try:
            webbrowser.open(PROJECT_URL)
        except Exception:  # noqa: BLE001
            pass

    def _on_tray_activated(self, reason):
        Reason = QSystemTrayIcon.ActivationReason
        if reason == Reason.Trigger:                 # left-click → open the GUI
            self._open_gui()
        elif reason == Reason.MiddleClick:           # middle-click → Game Mode
            self.gamemode_action.setChecked(not self.gamemode_action.isChecked())

    def run(self):
        sys.exit(self.app.exec())


def _single_instance_or_exit() -> None:
    """One tray icon only — autostart + a manual 'Launch tray now' must not
    stack. Hold an flock on a runtime lockfile for the life of the process."""
    import fcntl
    rundir = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    lock = open(os.path.join(rundir, "tuxthrottle-tray.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("TuxThrottle tray is already running.", file=sys.stderr)
        sys.exit(0)
    _single_instance_or_exit._lock = lock  # keep the fd alive


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--toggle":
        ok, err = sensors.toggle_game_mode_external()
        if not ok:
            print(f"Toggle failed: {err}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    _single_instance_or_exit()
    _reassert_keyboard_rgb()
    TrayMonitor().run()
