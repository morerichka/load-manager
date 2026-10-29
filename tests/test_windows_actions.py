"""Windows heavy apps (Chrome, Claude, Telegram, Google Drive): groups, EcoQoS, memory trim,
inherited priority, render boost, and the measurement probe. Uses test doubles from test_engine."""
import json
import os
import platform
import time

import psutil
import pytest

from loadmanager import engine
from loadmanager.engine import APPS, Benchmark, Monitor, PolicyError, Session, identity
from test_engine import Process, Provider

IDLE, BELOW, NORMAL, ABOVE = engine.IDLE, engine.BELOW_NORMAL, engine.NORMAL, engine.ABOVE_NORMAL


class FakeNative:
    """Stands in for loadmanager.winapi: EcoQoS masks per pid and a working-set trim."""
    def __init__(self, provider):
        self.provider, self.eco, self.trimmed, self.set_calls = provider, {}, [], []

    def get_eco(self, pid):
        return self.eco.get(pid, (0, 0))

    def set_eco(self, pid, control, state):
        self.set_calls.append((pid, control, state))
        self.eco[pid] = (control, state)

    def trim(self, pid):
        self.trimmed.append(pid)
        self.provider.Process(pid).rss //= 4


@pytest.fixture
def win(tmp_path):
    provider = Provider()
    native = FakeNative(provider)
    session = Session(tmp_path, provider, "Windows", native=native)
    session.guardian_ready = True
    session.memory_percent = lambda: 50
    return session, provider, native


def add(provider, pid, name, **kwargs):
    provider.processes[pid] = Process(pid, name, **kwargs)
    return provider.processes[pid]


def chrome_tabs(provider, count=3, first=400000):
    return [add(provider, first + i, "chrome.exe", created=100. + i) for i in range(count)]


def test_group_expands_to_every_process_of_the_app_and_restores(win):
    s, provider, native = win
    tabs = chrome_tabs(provider)
    foreign = add(provider, 400100, "chrome.exe", owner="someone-else")
    result = s.apply([{"group": "chrome"}], "render")
    assert result["applied"] == 3 and result["actions"] == {"priority": 3, "eco": 3}
    assert all(t.nice() == IDLE and native.eco[t.pid] == (1, 1) for t in tabs)
    assert foreign.nice() == NORMAL and foreign.pid not in native.eco
    assert len(json.loads(s.path.read_text())["entries"]) == 6
    assert s.restore() == {"restored": 3, "remaining": 0}
    assert all(t.nice() == NORMAL and native.eco[t.pid] == (0, 0) for t in tabs)


def test_focus_is_milder_than_render(win):
    s, provider, _ = win
    tab, = chrome_tabs(provider, 1)
    s.apply([{"group": "chrome"}], "focus")
    assert tab.nice() == BELOW


@pytest.mark.parametrize("memory,trimmed", [(50, False), (69.9, False), (70, True), (92, True)])
def test_memory_is_trimmed_only_under_pressure(win, memory, trimmed):
    s, provider, native = win
    chrome_tabs(provider, 1)
    s.memory_percent = lambda: memory
    result = s.apply([{"group": "chrome"}], "render")
    assert bool(native.trimmed) is trimmed
    assert (result["freed_mb"] > 0) is trimmed
    # trimming is not journaled: there is nothing to put back
    assert {e["action"] for e in s.entries} == {"priority", "eco"}


def test_claude_is_trim_only_and_never_lowered(win):
    s, provider, native = win
    claude = add(provider, 410000, "claude.exe")
    with pytest.raises(PolicyError):                     # no memory pressure: nothing to do
        s.apply([{"group": "claude"}], "render")
    s.memory_percent = lambda: 90
    result = s.apply([{"group": "claude"}], "render")
    assert claude.nice() == NORMAL and claude.pid not in native.eco
    assert result["session"] is False and result["freed_mb"] > 0
    assert s.entries == [] and s.profile is None and s.stats["freed_mb"] == result["freed_mb"]


def test_group_member_that_vanishes_mid_apply_does_not_cancel_the_session(win):
    s, provider, _ = win
    tabs = chrome_tabs(provider)
    real_plan = s.plan

    def plan_then_close_a_tab(*args):
        targets = real_plan(*args)
        del provider.processes[tabs[1].pid]
        return targets
    s.plan = plan_then_close_a_tab
    result = s.apply([{"group": "chrome"}], "render")
    assert result["applied"] == 2 and result["skipped"] == 1
    assert tabs[0].nice() == IDLE and tabs[2].nice() == IDLE
    assert s.restore()["remaining"] == 0


def test_group_member_that_refuses_is_skipped_not_fatal(win):
    s, provider, native = win
    tabs = chrome_tabs(provider)
    tabs[1].nice_error = True
    result = s.apply([{"group": "chrome"}], "render")
    assert result["applied"] == 3                        # the refusing tab still got EcoQoS
    assert {e["pid"] for e in s.entries if e["action"] == "priority"} == {tabs[0].pid, tabs[2].pid}
    assert s.restore() == {"restored": 3, "remaining": 0}
    assert native.eco[tabs[1].pid] == (0, 0)


def test_explicit_selection_still_rolls_everything_back(win):
    s, provider, native = win
    a, b = chrome_tabs(provider, 2)
    b.nice_error = True
    with pytest.raises(psutil.AccessDenied):
        s.apply([identity(a), identity(b)], "render")
    assert a.nice() == NORMAL and native.eco.get(a.pid, (0, 0)) == (0, 0) and not s.entries


def test_existing_eco_state_is_never_taken_over(win):
    s, provider, native = win
    tab, = chrome_tabs(provider, 1)
    native.eco[tab.pid] = (1, 1)                         # the user already enabled Efficiency mode
    s.apply([{"group": "chrome"}], "render")
    assert [e["action"] for e in s.entries] == ["priority"]
    s.restore()
    assert native.eco[tab.pid] == (1, 1) and native.set_calls == []


def test_eco_changed_externally_is_left_alone_on_restore(win):
    s, provider, native = win
    tab, = chrome_tabs(provider, 1)
    s.apply([{"group": "chrome"}], "render")
    native.eco[tab.pid] = (0, 0)
    native.set_calls.clear()
    s.restore()
    assert native.set_calls == []


def test_tab_opened_during_the_session_is_not_left_slow(win):
    s, provider, _ = win
    old = chrome_tabs(provider, 2)
    s.apply([{"group": "chrome"}], "render")
    assert json.loads(s.path.read_text())["inherit"]["chrome"]["before"] == NORMAL
    new = add(provider, 400500, "chrome.exe", created=time.time() + 60)   # inherited Idle from its parent
    new._nice = IDLE
    unrelated = add(provider, 400501, "chrome.exe", created=time.time() + 61)
    unrelated._nice = 128                                                 # someone chose High on purpose
    s.restore()
    assert new.nice() == NORMAL and unrelated.nice() == 128
    assert all(t.nice() == NORMAL for t in old)
    assert s.inherit == {}


def test_recovery_after_a_crash_reads_inherit_and_eco_from_disk(win):
    s, provider, native = win
    tabs = chrome_tabs(provider, 2)
    s.apply([{"group": "chrome"}], "render")
    guardian = Session(s.directory, provider, "Windows", s.path, native=native)
    assert guardian.restore(from_disk=True) == {"restored": 2, "remaining": 0}
    assert all(t.nice() == NORMAL and native.eco[t.pid] == (0, 0) for t in tabs)


def test_heavy_apps_are_protected_off_windows(tmp_path):
    provider = Provider()
    tab, = chrome_tabs(provider, 1)
    s = Session(tmp_path, provider, "Darwin")
    s.guardian_ready = True
    with pytest.raises(PolicyError):
        s.apply([identity(tab)], "render")
    with pytest.raises(PolicyError):
        s.apply([{"group": "chrome"}], "render")
    assert tab.suspend_calls == 0


def test_light_apps_get_priority_only_even_under_memory_pressure(win):
    s, provider, native = win
    s.memory_percent = lambda: 95
    s.apply([identity(provider.Process(300001))], "render")
    assert [e["action"] for e in s.entries] == ["priority"] and native.trimmed == [] and native.eco == {}


@pytest.mark.parametrize("selection", [{"group": "nope"}, {"group": "spotify"}, {"group": 5}, {"group": None}])
def test_only_known_heavy_groups_can_be_selected(win, selection):
    s, _, _ = win
    with pytest.raises(PolicyError):
        s.apply([selection], "focus")


def test_selection_size_is_capped(win):
    s, provider, _ = win
    chrome_tabs(provider, engine.MAX_TARGETS + 1)
    with pytest.raises(PolicyError, match="Too many"):
        s.apply([{"group": "chrome"}], "render")


def test_preview_describes_the_plan_and_changes_nothing(win):
    s, provider, native = win
    tabs = chrome_tabs(provider)
    plan = s.preview([{"group": "chrome"}], "render")
    assert plan["apps"] == [{"app": "chrome", "title": "Google Chrome", "processes": 3,
                             "actions": {"priority": 3, "eco": 3}}]
    assert all(t.nice() == NORMAL for t in tabs) and not s.entries and not native.set_calls


def test_own_process_tree_is_never_a_group_member(win):
    s, provider, _ = win
    tabs = chrome_tabs(provider, 2)
    provider.Process(os.getpid())._children = [tabs[0]]
    assert s.apply([{"group": "chrome"}], "render")["applied"] == 1
    assert tabs[0].nice() == NORMAL and tabs[1].nice() == IDLE


# render boost -------------------------------------------------------------------------------

def render_session(win):
    s, provider, _ = win
    s.apply([identity(provider.Process(300001))], "render")
    return s, provider


def test_render_tool_is_lifted_while_the_session_runs_and_put_back_after(win):
    s, provider = render_session(win)
    ffmpeg = add(provider, 420000, "ffmpeg.exe")
    ffmpeg._nice = BELOW                                 # inherited from a lowered parent
    assert s.watch() == 1 and ffmpeg.nice() == ABOVE
    assert s.watch() == 0                                # already handled
    assert s.stats["boosted"] == 1
    s.restore()
    assert ffmpeg.nice() == BELOW


def test_boost_ignores_foreign_owner_non_render_tools_and_the_focus_profile(win):
    s, provider = render_session(win)
    foreign = add(provider, 420001, "ffmpeg.exe", owner="someone-else")
    other = add(provider, 420002, "node.exe")
    assert s.watch() == 0 and foreign.nice() == NORMAL and other.nice() == NORMAL
    s.restore()
    s.apply([identity(provider.Process(300001))], "focus")
    add(provider, 420003, "ffmpeg.exe")
    assert s.watch() == 0


def test_boost_is_off_before_a_session_and_off_windows(win, tmp_path):
    s, provider, _ = win
    add(provider, 420004, "ffmpeg.exe")
    assert s.watch() == 0
    mac = Session(tmp_path / "mac", provider, "Darwin")
    mac.profile = "render"
    assert mac.watch() == 0


def test_finished_render_tools_are_pruned_from_the_journal(win):
    s, provider = render_session(win)
    ffmpeg = add(provider, 420005, "ffmpeg.exe")
    s.watch()
    assert any(e["pid"] == ffmpeg.pid for e in s.entries)
    del provider.processes[ffmpeg.pid]
    s.watch()
    assert not any(e["pid"] == ffmpeg.pid for e in s.entries)
    assert not any(e["pid"] == ffmpeg.pid for e in json.loads(s.path.read_text())["entries"])


# measurement ---------------------------------------------------------------------------------

def wait_for(benchmark):
    deadline = time.time() + 20
    while benchmark.running and time.time() < deadline:
        time.sleep(0.02)
    assert not benchmark.running


def test_benchmark_compares_before_and_during_a_session(tmp_path, monkeypatch):
    monkeypatch.setattr(Benchmark, "ROUNDS", 2)
    monkeypatch.setattr(Benchmark, "ROUND_SECONDS", 0.05)
    s = Session(tmp_path)
    b = s.benchmark
    assert b.state()["delta_percent"] is None
    assert b.start() is True and b.start() is False      # one run at a time
    wait_for(b)
    first = b.state()
    assert first["error"] is None and first["baseline"]["score"] > 0 and first["with_session"] is None
    s.entries = [{"pid": 1}]                             # pretend a session is active
    b.start()
    wait_for(b)
    state = b.state()
    assert state["with_session"]["active"] is True and state["baseline"]["active"] is False
    expected = round((state["with_session"]["score"] / state["baseline"]["score"] - 1) * 100, 1)
    assert state["delta_percent"] == expected
    assert len(state["results"]) == 2


def test_benchmark_keeps_only_recent_results(tmp_path, monkeypatch):
    monkeypatch.setattr(Benchmark, "ROUNDS", 1)
    monkeypatch.setattr(Benchmark, "ROUND_SECONDS", 0.01)
    monkeypatch.setattr(Benchmark, "KEEP", 2)
    b = Session(tmp_path).benchmark
    for _ in range(3):
        b.start()
        wait_for(b)
    assert len(b.state()["results"]) == 2


def test_snapshot_exposes_heavy_groups_and_benchmark(tmp_path):
    m = Monitor(Session(tmp_path))
    m.sample()
    time.sleep(.05)
    snap = m.sample()
    assert [g["key"] for g in snap["groups"]] == ["chrome", "claude", "telegram", "gdrive"]
    for g in snap["groups"]:
        assert g["available"] == (platform.system() == "Windows")
        assert g["actions"] == list(APPS[g["key"]]["actions"])
        assert {"count", "eligible", "managed", "cpu", "memory", "title"} <= set(g)
    assert all("app" in p and "kind" in p for p in snap["processes"])
    full = m.get()
    assert {"running", "progress", "results", "baseline", "with_session", "delta_percent", "error"} <= set(full["benchmark"])
    assert "stats" in full and full["trim_threshold"] == engine.TRIM_AT_MEMORY_PERCENT
