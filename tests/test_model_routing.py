"""Phase 0 — the hardware specifics in sensors.py / tuxthrottle_kbd.py /
hotkey_listener.py are now read from the model profile, with the 5515 values
as the fallback. These tests pin both directions: a profile field is used
when present, and its absence falls back to the reference value.
"""
import importlib

import sensors


def _reset_cache():
    sensors._MODEL_CACHE = None


def teardown_function():
    _reset_cache()


# --- profile field present -> used ---------------------------------------- #

def test_accessors_read_from_profile(monkeypatch):
    fake = {
        "id": "acme-x1",
        "cpu": {"hwmon": "coretemp"},
        "fans": {
            "hwmon": "acme_ec",
            "pwm_hwmon": "acme_pwm",
            "pwm_floor": 90,
            "count": 3,
            "platform_profile_path": "/sys/acme/profile",
        },
    }
    monkeypatch.setattr(sensors, "model_profile", lambda: fake)
    assert sensors._cpu_temp_hwmon() == "coretemp"
    assert sensors._fan_hwmon() == "acme_ec"
    assert sensors._fan_pwm_hwmon() == "acme_pwm"
    assert sensors._pwm_floor() == 90
    assert sensors._fan_indices() == (1, 2, 3)
    assert sensors._platform_profile_path() == "/sys/acme/profile"


# --- profile field absent -> 5515 fallback ------------------------------- #

def test_accessors_fall_back_when_profile_empty(monkeypatch):
    monkeypatch.setattr(sensors, "model_profile", lambda: {"id": "bare"})
    assert sensors._cpu_temp_hwmon() == "k10temp"
    assert sensors._fan_hwmon() == "alienware_wmi"
    assert sensors._fan_pwm_hwmon() == "dell_smm"
    assert sensors._pwm_floor() == sensors.PWM_FLOOR == 77
    assert sensors._fan_indices() == (1, 2)
    assert sensors._platform_profile_path() == "/sys/firmware/acpi/platform_profile"


def test_fan_indices_derives_count_from_rpm_inputs(monkeypatch):
    monkeypatch.setattr(sensors, "model_profile",
                        lambda: {"fans": {"rpm_inputs": ["fan1_input"]}})
    assert sensors._fan_indices() == (1,)


def test_reference_profile_still_resolves_to_5515_values(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(sensors, "_dmi",
                        lambda k: "Dell G15 5515" if k == "product_name" else "")
    assert sensors.model_id() == "g15-5515"
    assert sensors._fan_hwmon() == "alienware_wmi"
    assert sensors._pwm_floor() == 77
    assert sensors._fan_indices() == (1, 2)


# --- tuxthrottle_kbd routes its device name/USB id through the profile --- #

def test_kbd_reads_device_from_profile(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(
        sensors, "model_profile",
        lambda: {"keyboard": {"openrgb_device": "Acme LED", "usb": "1a2b:3c4d"}})
    import tuxthrottle_kbd
    kbd = importlib.reload(tuxthrottle_kbd)
    assert kbd.DEVICE == "Acme LED"
    assert kbd._USB_VID == "1a2b"
    assert "3c4d" in kbd._USB_PIDS
    # restore the real module for other tests
    _reset_cache()
    importlib.reload(kbd)
