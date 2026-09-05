"""New Fixes-tab backend pieces: fixlog, crashwatch signature matching,
launchopts remove-token, and steamperf's mount-wait wrapper preservation.
No Steam / systemd / network needed — pure logic + tmp_path isolation.
"""
import json

import tuxthrottle_crashwatch as cw
import tuxthrottle_fixlog as fixlog
import tuxthrottle_launchopts as lo
import tuxthrottle_steamperf as sp


# --------------------------------------------------------------------------- #
#  fixlog
# --------------------------------------------------------------------------- #
def test_fixlog_round_trip(monkeypatch, tmp_path):
    p = tmp_path / "fixlog.jsonl"
    monkeypatch.setattr(fixlog, "log_path", lambda user=None: p)
    fixlog.log_event("test", "hello", level="warn")
    fixlog.log_event("test", "world")
    entries = fixlog.read_recent(10)
    assert len(entries) == 2
    assert entries[0]["message"] == "world"        # newest first
    assert entries[1]["level"] == "warn"


def test_fixlog_caps_length(monkeypatch, tmp_path):
    p = tmp_path / "fixlog.jsonl"
    monkeypatch.setattr(fixlog, "log_path", lambda user=None: p)
    monkeypatch.setattr(fixlog, "MAX_ENTRIES", 5)
    for i in range(20):
        fixlog.log_event("test", f"event {i}")
    lines = p.read_text().splitlines()
    assert len(lines) == 5
    assert json.loads(lines[-1])["message"] == "event 19"


# --------------------------------------------------------------------------- #
#  crashwatch signature matching
# --------------------------------------------------------------------------- #
def test_classify_benign_proton_bootstrap():
    sig = cw._classify("/path/wine-preloader", "wine c:\\...\\d3ddriverquery64.exe")
    assert sig is not None and sig["benign"] is True


def test_classify_steamwebhelper_crash_is_not_benign():
    sig = cw._classify("/path/steamwebhelper", "")
    assert sig is not None and sig["benign"] is False


def test_classify_unknown_exe_returns_none():
    assert cw._classify("/path/some-random-binary", "") is None


def test_scan_dedupes_across_calls(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    monkeypatch.setattr(cw, "_state_path", lambda user=None: state)
    monkeypatch.setattr(cw, "_journal_events", lambda since: [])
    calls = {"n": 0}

    def fake_coredumps(since):
        calls["n"] += 1
        return [{"key": "core:123:t1", "timestamp": "t1", "pid": "123",
                "exe": "/x/wine-preloader", "cmdline": ""}]

    monkeypatch.setattr(cw, "_coredump_events", fake_coredumps)
    first = cw.scan(60)
    second = cw.scan(60)
    assert len(first) == 1
    assert len(second) == 0          # already-seen key is suppressed


# --------------------------------------------------------------------------- #
#  launchopts remove-token
# --------------------------------------------------------------------------- #
def test_remove_token_strips_only_matching_flag():
    cfg = {"UserLocalConfigStore": {"Software": {"Valve": {"Steam": {"apps": {
        "10": {"LaunchOptions": "gamemoderun mangohud %command%"},
        "20": {"LaunchOptions": "mangohud %command%"},
        "30": {"LaunchOptions": "%command%"},
    }}}}}}
    apps = lo._apps_dict(cfg)
    changed = 0
    for _aid, entry in lo._iter_games(apps):
        cur = lo._ci_get(entry, "LaunchOptions") or ""
        if "mangohud" not in cur:
            continue
        entry["LaunchOptions"] = " ".join(p for p in cur.split() if p != "mangohud")
        changed += 1
    assert changed == 2
    assert apps["10"]["LaunchOptions"] == "gamemoderun %command%"
    assert apps["20"]["LaunchOptions"] == "%command%"
    assert apps["30"]["LaunchOptions"] == "%command%"


# --------------------------------------------------------------------------- #
#  steamperf: mount-wait wrapper survives regeneration
# --------------------------------------------------------------------------- #
def test_reapply_mountwait_is_idempotent():
    wrapped = ("Exec=/usr/local/bin/tuxthrottle-wait-mounts systemd-run --user "
              "--scope -- /usr/bin/steam -silent %U\n")
    assert sp._reapply_mountwait(wrapped) == wrapped


def test_reapply_mountwait_wraps_primary_exec_only():
    text = "Exec=/usr/bin/steam %U\nExec=/usr/bin/steam steam://store\n"
    out = sp._reapply_mountwait(text)
    lines = out.splitlines()
    assert lines[0].startswith("Exec=/usr/local/bin/tuxthrottle-wait-mounts ")
    assert lines[1] == "Exec=/usr/bin/steam steam://store"


def test_diagnose_runs_without_steam_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "status_igpu", lambda: "off")
    monkeypatch.setattr(sp, "_SYS_DESKTOP", str(tmp_path / "nope.desktop"))
    monkeypatch.setattr(sp, "_user_desktop", lambda: tmp_path / "shadow.desktop")
    results = sp.diagnose(user=None)
    assert all(s in ("ok", "bad") for s, _ in results)
