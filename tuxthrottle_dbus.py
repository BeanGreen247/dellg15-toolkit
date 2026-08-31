#!/usr/bin/env python3
"""Optional D-Bus system-service front end for tuxthrottled.

Mirrors the newline-JSON control socket (`tuxthrottle_control`) onto
`org.tuxthrottle.Daemon1` on the **system** bus so desktop clients can reach
the single-writer daemon the standard way. Needs `dbus-python` + PyGObject;
when either is missing `serve_in_thread()` returns `None` and the daemon runs
socket-only — no behaviour change.

Access is gated by the D-Bus policy file
(`/usr/share/dbus-1/system.d/org.tuxthrottle.Daemon1.conf`, installed by the
`DbusPolkitIntegration` tweak): root owns the name, active local users may
call. Every method body delegates to the exact same dispatch dict the socket
uses (`tuxthrottle_powerd._build_dispatch`), returning a JSON string
`{"ok": bool, "result"|"error": ...}`.
"""
from __future__ import annotations

import json
import threading

BUS_NAME = "org.tuxthrottle.Daemon1"
OBJ_PATH = "/org/tuxthrottle/Daemon1"
IFACE = "org.tuxthrottle.Daemon1"

try:
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    import dbus
    _HAVE_DBUS = True
except Exception:  # noqa: BLE001  (ImportError, or a broken gi typelib)
    _HAVE_DBUS = False


def available() -> bool:
    return _HAVE_DBUS


if _HAVE_DBUS:

    class _Service(dbus.service.Object):
        def __init__(self, bus_name, dispatch: dict):
            super().__init__(bus_name, OBJ_PATH)
            self._d = dispatch

        def _call(self, method: str, params: dict) -> str:
            fn = self._d.get(method)
            if fn is None:
                return json.dumps({"ok": False, "error": f"unknown method {method!r}"})
            try:
                return json.dumps({"ok": True, "result": fn(params)})
            except Exception as exc:  # noqa: BLE001
                return json.dumps({"ok": False, "error": str(exc)})

        @dbus.service.method(IFACE, in_signature="", out_signature="s")
        def Status(self):
            return self._call("status", {})

        @dbus.service.method(IFACE, in_signature="", out_signature="s")
        def Reload(self):
            return self._call("reload", {})

        @dbus.service.method(IFACE, in_signature="s", out_signature="s")
        def ApplyProfile(self, name):
            return self._call("apply_profile", {"name": str(name)})

        @dbus.service.method(IFACE, in_signature="s", out_signature="s")
        def Snapshot(self, label):
            return self._call("snapshot", {"label": str(label) or "manual"})

        @dbus.service.method(IFACE, in_signature="s", out_signature="s")
        def Rollback(self, target):
            return self._call("rollback", {"target": str(target) or "last"})

        @dbus.service.method(IFACE, in_signature="s", out_signature="s")
        def Call(self, params_json):
            """Escape hatch: params_json = {"method": "...", ...extra params}."""
            try:
                p = json.loads(params_json or "{}")
            except ValueError as exc:
                return json.dumps({"ok": False, "error": f"bad JSON: {exc}"})
            if not isinstance(p, dict):
                return json.dumps({"ok": False, "error": "params must be an object"})
            method = str(p.pop("method", ""))
            return self._call(method, p)


def serve_in_thread(dispatch: dict, log=print):
    """Own `BUS_NAME` on the system bus and service it from a daemon thread.

    Returns a `stop()` callable, or `None` when D-Bus/GLib is unavailable or
    the bus can't be claimed (not root, no system bus, name already owned)."""
    if not _HAVE_DBUS:
        return None
    try:
        DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        name = dbus.service.BusName(BUS_NAME, bus, do_not_queue=True)
        _Service(name, dispatch)
    except Exception as exc:  # noqa: BLE001
        log(f"d-bus service unavailable: {exc}")
        return None
    loop = GLib.MainLoop()
    threading.Thread(target=loop.run, name="tuxthrottle-dbus", daemon=True).start()
    log(f"d-bus service -> {BUS_NAME}")

    def stop():
        try:
            loop.quit()
        except Exception:  # noqa: BLE001
            pass

    return stop


# ------------------------------------------------------------------ client --- #

def call(method: str, params: dict | None = None):
    """Client side: invoke a method on a running tuxthrottled over the system
    bus. Returns the decoded response dict, or `None` if D-Bus is unavailable
    or the service isn't running."""
    if not _HAVE_DBUS:
        return None
    try:
        bus = dbus.SystemBus()
        obj = bus.get_object(BUS_NAME, OBJ_PATH)
        iface = dbus.Interface(obj, IFACE)
        p = dict(params or {})
        if method == "status":
            raw = iface.Status()
        elif method == "reload":
            raw = iface.Reload()
        elif method == "apply_profile":
            raw = iface.ApplyProfile(str(p.get("name", "")))
        elif method == "snapshot":
            raw = iface.Snapshot(str(p.get("label", "manual")))
        elif method == "rollback":
            raw = iface.Rollback(str(p.get("target", "last")))
        else:
            raw = iface.Call(json.dumps({"method": method, **p}))
        return json.loads(raw)
    except Exception:  # noqa: BLE001  (DBusException when the name is unowned)
        return None


def available_live() -> bool:
    """True only if a tuxthrottled is actually answering on the bus right now."""
    return bool(call("status"))
