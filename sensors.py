#!/usr/bin/env python3
"""Sensor reads + Game Mode control — shared by dellg15_toolkit.py (GUI,
stdlib-only) and tray_monitor.py (PySide6). Deliberately has NO GUI
dependency of its own so the checkbox Toolkit doesn't need PySide6 just to
show live numbers.

Tested platform: Dell G15 5515 Ryzen Edition (Ryzen 7 5800H, RTX 3050 Ti
Mobile). CPU temp lookup assumes k10temp (AMD); GPU lookups auto-detect by
PCI vendor ID (0x1002 AMD / nvidia-smi for NVIDIA) so they should degrade
gracefully on a different Dell model rather than crash, but haven't been
verified on one.
"""
import glob
import os
import re
import shutil
import subprocess
import time


def which(cmd: str):
    return shutil.which(cmd)


def read_cpu_freq_ghz() -> str:
    try:
        freqs = []
        for path in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
            with open(path) as f:
                freqs.append(int(f.read().strip()))
        if freqs:
            return f"{max(freqs) / 1_000_000:.2f} GHz (peak core)"
    except OSError:
        pass
    return "n/a"


def read_cpu_freq_ghz_value() -> float:
    try:
        freqs = []
        for path in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
            with open(path) as f:
                freqs.append(int(f.read().strip()))
        if freqs:
            return max(freqs) / 1_000_000
    except OSError:
        pass
    return 0.0


def read_cpu_temp_c() -> str:
    val = read_cpu_temp_c_value()
    return f"{val:.0f} C" if val is not None else "n/a"


def read_cpu_temp_c_value():
    for name_path in glob.glob("/sys/class/hwmon/hwmon*/name"):
        try:
            with open(name_path) as f:
                if f.read().strip() != "k10temp":
                    continue
            hwmon_dir = name_path.rsplit("/", 1)[0]
            with open(f"{hwmon_dir}/temp1_input") as f:
                return int(f.read().strip()) / 1000
        except OSError:
            continue
    return None


def _amdgpu_card_dir():
    for card in glob.glob("/sys/class/drm/card[0-9]*/device"):
        vendor_path = f"{card}/vendor"
        try:
            with open(vendor_path) as f:
                if f.read().strip() == "0x1002":  # AMD
                    return card
        except OSError:
            continue
    return None


def has_amd_gpu() -> bool:
    return _amdgpu_card_dir() is not None


def has_nvidia_gpu() -> bool:
    if which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                return True
        except Exception:  # noqa: BLE001
            pass
    for vendor_path in glob.glob("/sys/bus/pci/devices/*/vendor"):
        try:
            with open(vendor_path) as f:
                if f.read().strip() == "0x10de":  # NVIDIA
                    return True
        except OSError:
            continue
    return False


def read_igpu_clock_temp() -> str:
    clock, temp = read_igpu_clock_temp_values()
    c = f"{clock} MHz" if clock is not None else "?"
    t = f"{temp:.0f} C" if temp is not None else "?"
    return f"{c}, {t}"


def read_igpu_clock_temp_values():
    card = _amdgpu_card_dir()
    if not card:
        return None, None
    clock = None
    try:
        with open(f"{card}/pp_dpm_sclk") as f:
            for line in f:
                if "*" in line:
                    m = re.search(r"(\d+)Mhz", line)
                    if m:
                        clock = int(m.group(1))
    except OSError:
        pass
    temp = None
    for hwmon in glob.glob(f"{card}/hwmon/hwmon*/temp1_input"):
        try:
            with open(hwmon) as f:
                temp = int(f.read().strip()) / 1000
        except OSError:
            pass
    return clock, temp


def read_dgpu_clock_temp_util() -> str:
    clock, temp, util, power = read_dgpu_values()
    if clock is None:
        return "n/a (no nvidia-smi / asleep)"
    pw = f", {power:.0f} W" if power is not None else ""
    return f"{clock} MHz, {temp} C, {util}% util{pw}"


def read_dgpu_values():
    """Returns (clock_mhz, temp_c, util_pct, power_w) — any may be None."""
    if not which("nvidia-smi"):
        return None, None, None, None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=clocks.gr,temperature.gpu,utilization.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None, None, None, None
        clk, temp, util, power = [x.strip() for x in out.stdout.strip().split(",")]
        return int(clk), int(temp), int(util), float(power)
    except Exception:  # noqa: BLE001
        return None, None, None, None


def _powercap_energy_paths():
    """Cumulative-energy sysfs files for CPU package power (RAPL, both
    Intel's intel-rapl and the AMD equivalent share this powercap class).
    Reading these needs no root — but the kernel's RAPL side-channel
    mitigation (post-2020) makes them root-only by default on stock
    permissions; see the RaplPowerPermissions tweak."""
    paths = []
    for name_path in glob.glob("/sys/class/powercap/*/name"):
        try:
            with open(name_path) as f:
                name = f.read().strip()
            if "package" in name.lower() or "core" in name.lower():
                energy_path = name_path.rsplit("/", 1)[0] + "/energy_uj"
                if glob.glob(energy_path):
                    paths.append(energy_path)
        except OSError:
            continue
    return paths


def read_cpu_power_watts():
    """Delta-measures CPU package power over a short window via RAPL
    powercap energy counters. Returns None if unreadable (permissions) or
    unsupported (no RAPL zone — some AMD kernels/BIOS combos)."""
    paths = _powercap_energy_paths()
    if not paths:
        return None
    try:
        def read_total():
            total = 0
            for p in paths:
                with open(p) as f:
                    total += int(f.read().strip())
            return total

        e1 = read_total()
        t1 = time.monotonic()
        time.sleep(0.1)
        e2 = read_total()
        t2 = time.monotonic()
        delta_uj = e2 - e1
        if delta_uj < 0:  # counter wrapped
            return None
        return (delta_uj / 1_000_000) / (t2 - t1)
    except (OSError, PermissionError):
        return None


def rapl_permissions_ok() -> bool:
    """Checks the world-readable bit on the RAPL energy file directly,
    rather than 'can I open it' — the Toolkit GUI runs fully elevated
    (root), which can always read these regardless of the actual
    permission bits, so a naive open()-succeeds check would never catch
    the problem that unprivileged readers (tray icon, hotkey listener)
    actually hit."""
    import stat
    paths = _powercap_energy_paths()
    if not paths:
        return True  # nothing to check — not a permissions problem
    try:
        mode = os.stat(paths[0]).st_mode
        return bool(mode & stat.S_IROTH)
    except OSError:
        return False


def get_game_mode_state() -> bool:
    if not which("powerprofilesctl"):
        return False
    try:
        out = subprocess.run(["powerprofilesctl", "get"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "performance"
    except Exception:  # noqa: BLE001
        return False


def set_game_mode(enable: bool) -> tuple[bool, str]:
    cmds = []
    if enable:
        if which("gaming-performance"):
            cmds.append("gaming-performance")
        if which("amdgpu-perf-high"):
            cmds.append("amdgpu-perf-high")
        if which("nvidia-max-perf"):
            cmds.append("nvidia-max-perf")
    else:
        if which("gaming-balanced"):
            cmds.append("gaming-balanced")
        if which("amdgpu-perf-auto"):
            cmds.append("amdgpu-perf-auto")
    if not cmds:
        return False, "None of the Game Mode helper scripts are installed — run the Toolkit's Presets first."
    shell = " ; ".join(cmds)
    try:
        # Try passwordless sudo first (needed for the hotkey path, which has
        # no way to answer a GUI prompt) — only succeeds if the
        # PasswordlessGameModeToggle tweak's sudoers rule is installed.
        result = subprocess.run(["sudo", "-n", "sh", "-c", shell], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, ""
        # Fall back to an interactive GUI prompt (fine for tray-icon clicks,
        # will just sit there unanswered if triggered from the hotkey
        # listener with no sudoers rule installed).
        result = subprocess.run(["pkexec", "sh", "-c", shell], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "pkexec failed").strip()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def toggle_game_mode_external():
    """Entry point for external callers (tray icon click, hotkey listener)
    to flip Game Mode without going through any particular GUI."""
    enable = not get_game_mode_state()
    return set_game_mode(enable)
