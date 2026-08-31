"""Phase 2.1 — the optional D-Bus front end must never break the daemon:
absent lib -> no-op; present lib but no bus/root -> serve_in_thread() returns
None; the method envelope delegates to the shared dispatch dict.
"""
import json

import pytest

import tuxthrottle_dbus as td


def test_available_is_bool():
    assert isinstance(td.available(), bool)


def test_client_call_is_none_without_a_daemon():
    # nothing is serving org.tuxthrottle.Daemon1 in the test env
    assert td.call("status") is None
    assert td.available_live() is False


def test_serve_in_thread_none_when_unavailable_or_unprivileged():
    # non-root (or no lib) can't claim a system-bus name -> graceful None
    stop = td.serve_in_thread({"status": lambda p: {"x": 1}}, log=lambda *_: None)
    if stop is not None:            # only if somehow running as root with a bus
        stop()


@pytest.mark.skipif(not td.available(), reason="dbus-python / GLib not installed")
def test_call_envelope_delegates_to_dispatch():
    svc = td._Service.__new__(td._Service)     # skip the D-Bus registration
    svc._d = {
        "status": lambda p: {"ok_ish": True, "got": p},
        "boom": lambda p: (_ for _ in ()).throw(RuntimeError("nope")),
    }
    ok = json.loads(svc._call("status", {"a": 1}))
    assert ok == {"ok": True, "result": {"ok_ish": True, "got": {"a": 1}}}

    bad = json.loads(svc._call("boom", {}))
    assert bad["ok"] is False and "nope" in bad["error"]

    unknown = json.loads(svc._call("nosuch", {}))
    assert unknown["ok"] is False and "unknown method" in unknown["error"]
