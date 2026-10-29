"""Quiet mode (power plan switching with a guaranteed way back) and the spike recorder."""
import json
import os
import time

import psutil
import pytest

from loadmanager import engine, winapi
from loadmanager.engine import Monitor, PolicyError, Session
from loadmanager.power import Powercfg
from loadmanager.recorder import ProcessTable, SpikeRecorder, psutil_table
from test_engine import Provider
from test_windows_actions import FakeNative, add, chrome_tabs

TURBO = "6fecc5ae-f350-48a5-b669-b472cb895ccf"
SILENT = "64a64f24-65b9-4b56-befd-5ec1eaced9b3"
BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
PERFORMANCE = "27fa6203-3987-4dcc-918d-748559d549ec"
NORMAL, IDLE = engine.NORMAL, engine.IDLE


class FakeCfg:
    """A stand-in for powercfg with the laptop's four plans."""
    def __init__(self, current=TURBO, names=None):
        names = names or {TURBO: "Turbo", SILENT: "Silent", BALANCED: "Balanced", PERFORMANCE: "Performance"}
        self.plans = [{"guid": g, "name": n} for g, n in names.items()]
        self.current, self.calls = current, []
        self.fail_activate = self.fail_read = False

    def schemes(self):
        if self.fail_read:
            raise OSError("powercfg list failed")
        return [{**p, "active": p["guid"] == self.current} for p in self.plans]

    def active(self):
        if self.fail_read:
            raise OSError("powercfg failed")
        return next({"guid": p["guid"], "name": p["name"]} for p in self.plans if p["guid"] == self.current)

    def activate(self, guid):
        self.calls.append(guid)
        if self.fail_activate:
            raise OSError("access denied")
        self.current = guid


@pytest.fixture
def lab(tmp_path):
    provider, cfg = Provider(), FakeCfg()
    session = Session(tmp_path, provider, "Windows", native=FakeNative(provider), powercfg=cfg)
    session.guardian_ready = True
    session.memory_percent = lambda: 50
    return session, provider, cfg


def journal_power(session):
    return json.loads(session.path.read_text())["power"]


# --- powercfg wrapper -------------------------------------------------------------------------------

class Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


ENGLISH = f"""Existing Power Schemes (* Active)
-----------------------------------
Power Scheme GUID: {PERFORMANCE}  (Performance)
Power Scheme GUID: {BALANCED}  (Balanced)
Power Scheme GUID: {TURBO}  (Turbo) *
"""
RUSSIAN = f"""Существующие схемы управления питанием (* активная)
Схема управления питанием, GUID: {BALANCED}  (Сбалансированная) *
Схема управления питанием, GUID: {SILENT}  (Тихий режим)
"""


def test_plans_are_read_by_id_in_any_display_language():
    english = Powercfg(lambda args: Result(ENGLISH)).schemes()
    assert [(s["name"], s["active"]) for s in english] == [("Performance", False), ("Balanced", False), ("Turbo", True)]
    russian = Powercfg(lambda args: Result(RUSSIAN))
    assert [s["name"] for s in russian.schemes()] == ["Сбалансированная", "Тихий режим"]
    assert russian.active() == {"guid": BALANCED, "name": "Сбалансированная"}


def test_switching_runs_powercfg_without_a_shell_and_only_with_a_real_id():
    calls = []
    cfg = Powercfg(lambda args: (calls.append(args), Result())[1])
    cfg.activate(SILENT)
    assert calls == [["powercfg", "/setactive", SILENT]]
    for bad in ("", "silent", f"{SILENT}; calc", "../../x", None):
        with pytest.raises(OSError):
            cfg.activate(bad)
    assert len(calls) == 1


def test_powercfg_failures_become_oserrors():
    with pytest.raises(OSError, match="returned 1"):
        Powercfg(lambda args: Result("", 1)).schemes()
    with pytest.raises(OSError, match="FileNotFoundError"):
        Powercfg(lambda args: (_ for _ in ()).throw(FileNotFoundError())).active()
    with pytest.raises(OSError):
        Powercfg(lambda args: Result("nothing useful")).active()


# --- quiet mode ---------------------------------------------------------------------------------------

def test_quiet_mode_switches_the_plan_and_the_journal_knows_the_way_back(lab):
    s, _, cfg = lab
    state = s.quiet.enter()
    assert cfg.current == SILENT and state["active"] and state["state"] == "quiet"
    assert journal_power(s)["before"] == TURBO and journal_power(s)["after"] == SILENT
    assert state["original"] == {"guid": TURBO, "name": "Turbo"}
    after = s.quiet.leave()
    assert cfg.current == TURBO and not after["active"] and journal_power(s) is None
    assert any("restored" in e["text"] for e in s.events)


def test_the_quiet_plan_is_picked_by_name_unless_you_choose_one(lab):
    s, _, cfg = lab
    assert s.quiet.state()["quiet_target"] is None            # nothing read yet
    s.quiet.refresh(force=True)
    assert s.quiet.state()["quiet_target"]["name"] == "Silent"
    cfg.plans = [p for p in cfg.plans if p["guid"] != SILENT]
    s.quiet.refresh(force=True)
    assert s.quiet.state()["quiet_target"]["name"] == "Balanced"
    s.quiet.configure(scheme=PERFORMANCE)
    assert s.quiet.enter()["original"]["name"] == "Turbo" and cfg.current == PERFORMANCE


def test_nothing_to_switch_to_is_an_error_not_a_silent_no_op(lab):
    s, _, cfg = lab
    cfg.plans = [p for p in cfg.plans if p["guid"] in (TURBO, PERFORMANCE)]
    with pytest.raises(PolicyError, match="No quiet power plan"):
        s.quiet.enter()
    cfg.plans.append({"guid": SILENT, "name": "Silent"})
    cfg.current = SILENT
    with pytest.raises(PolicyError, match="already the active"):
        s.quiet.enter()
    assert cfg.calls == [] and journal_power(s) is None if s.path.exists() else True


def test_a_failed_switch_leaves_no_trace(lab):
    s, _, cfg = lab
    cfg.fail_activate = True
    with pytest.raises(PolicyError, match="Could not switch"):
        s.quiet.enter()
    assert not s.quiet.state()["active"] and journal_power(s) is None and cfg.current == TURBO


def test_a_crash_in_the_middle_of_the_switch_leaves_the_way_back_on_disk(lab):
    s, provider, cfg = lab

    def die(guid):
        raise RuntimeError("the process was killed here")
    cfg.activate = die
    with pytest.raises(RuntimeError):
        s.quiet.enter()
    assert journal_power(s)["before"] == TURBO and journal_power(s)["now"] == TURBO
    del cfg.activate
    guardian = Session(s.directory, provider, "Windows", s.path, powercfg=cfg)
    assert guardian.restore(from_disk=True)["remaining"] == 0 and cfg.current == TURBO and journal_power(guardian) is None


def test_a_failed_return_keeps_the_entry_so_it_can_be_retried(lab):
    s, _, cfg = lab
    s.quiet.enter()
    cfg.fail_activate = True
    with pytest.raises(PolicyError, match="Could not restore"):
        s.quiet.leave()
    assert s.quiet.state()["active"] and journal_power(s)["before"] == TURBO
    cfg.fail_activate = False
    s.quiet.leave()
    assert cfg.current == TURBO and journal_power(s) is None


def test_a_plan_chosen_by_someone_else_is_never_overridden_on_leave(lab):
    s, _, cfg = lab
    s.quiet.enter()
    cfg.current = BALANCED                                    # Fn+F5, or the laptop's own software
    cfg.calls.clear()
    state = s.quiet.leave()
    assert cfg.current == BALANCED and cfg.calls == [] and not state["active"]
    assert "changed by something else" in state["note"]


def test_the_monitor_tick_releases_quiet_mode_when_the_plan_is_changed_elsewhere(lab):
    s, _, cfg = lab
    s.quiet.enter()
    t = time.time()
    s.quiet.tick(now=t + 2)
    assert s.quiet.state()["active"]                          # too soon to check again
    cfg.current = BALANCED
    cfg.calls.clear()
    s.quiet.tick(now=t + 11)
    state = s.quiet.state()
    assert not state["active"] and "released" in state["note"] and cfg.calls == [] and cfg.current == BALANCED
    assert journal_power(s) is None


def test_an_original_plan_that_no_longer_exists_is_not_chased(lab):
    s, _, cfg = lab
    s.quiet.enter()
    cfg.plans = [p for p in cfg.plans if p["guid"] != TURBO]
    cfg.calls.clear()
    state = s.quiet.leave()
    assert cfg.calls == [] and not state["active"] and "no longer exists" in state["note"]


def test_plan_choices_are_validated_remembered_and_locked_while_quiet(lab, tmp_path):
    s, provider, cfg = lab
    for bad in ({"scheme": "not-a-plan"}, {"render_scheme": 5}, {"scheme": "00000000-0000-0000-0000-000000000000"}):
        with pytest.raises(PolicyError):
            s.quiet.configure(**bad)
    s.quiet.configure(scheme=BALANCED.upper(), render_scheme=PERFORMANCE, auto_enter=True)
    again = Session(tmp_path, provider, "Windows", powercfg=FakeCfg()).quiet.state()
    assert again["scheme"] == BALANCED and again["render_scheme"] == PERFORMANCE and again["auto_enter"]
    s.quiet.configure(scheme="")
    assert s.quiet.state()["scheme"] is None
    s.quiet.enter()
    with pytest.raises(PolicyError, match="Leave quiet mode"):
        s.quiet.configure(scheme=BALANCED)


def test_quiet_mode_is_unavailable_without_power_plans(tmp_path):
    s = Session(tmp_path, Provider(), "Darwin")
    assert not s.quiet.available and not s.quiet.state()["available"]
    with pytest.raises(PolicyError, match="Windows power plans"):
        s.quiet.enter()
    s.quiet.render_begin()
    s.quiet.render_end()
    s.quiet.tick()


# --- renders ask for the original (or chosen) plan -------------------------------------------------

def test_a_render_session_runs_on_the_original_plan_and_quiet_mode_returns_after_it(lab):
    s, provider, cfg = lab
    chrome_tabs(provider)
    s.quiet.enter()
    assert cfg.current == SILENT
    s.apply([{"group": "chrome"}], "render")
    assert cfg.current == TURBO and s.quiet.state()["state"] == "render"
    s.restore(include_power=False)
    assert cfg.current == SILENT and s.quiet.state()["active"] and s.quiet.state()["state"] == "quiet"
    s.quiet.leave()
    assert cfg.current == TURBO


def test_a_focus_session_stays_quiet(lab):
    s, provider, cfg = lab
    chrome_tabs(provider)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "focus")
    assert cfg.current == SILENT
    s.restore(include_power=False)
    assert cfg.current == SILENT


def test_a_chosen_render_plan_is_used_for_the_render(lab):
    s, provider, cfg = lab
    chrome_tabs(provider)
    s.quiet.configure(render_scheme=PERFORMANCE)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "render")
    assert cfg.current == PERFORMANCE
    s.restore(include_power=False)
    assert cfg.current == SILENT


def test_entering_quiet_mode_in_the_middle_of_a_render_waits_for_it(lab):
    s, provider, cfg = lab
    chrome_tabs(provider)
    s.apply([{"group": "chrome"}], "render")
    s.quiet.enter()
    assert cfg.current == TURBO and s.quiet.state()["state"] == "render"
    s.restore(include_power=False)
    assert cfg.current == SILENT


def test_a_timed_ab_runs_both_phases_on_the_same_power_plan(lab):
    s, provider, cfg = lab
    chrome_tabs(provider)
    s.quiet.enter()
    seen = []
    s.timed._execute = lambda argv, cwd: (seen.append(cfg.current), (1.0, 0, None))[1]
    s.timed.start("render --fast", mode="ab")
    deadline = time.time() + 20
    while s.timed.running and time.time() < deadline:
        time.sleep(0.02)
    assert seen == [TURBO, TURBO] and cfg.current == SILENT


def test_auto_start_follows_the_same_rule(lab):
    s, provider, cfg = lab
    chrome_tabs(provider)
    s.quiet.enter()
    s.autopilot.configure(enabled=True)
    busy = [{"name": "ffmpeg.exe", "cpu": 30.0, "created": 990.0}]
    for now in (1000, 1004):
        s.autopilot.tick(busy, 24, now=now)
    assert cfg.current == TURBO
    s.autopilot.tick([], 24, now=1030)
    assert cfg.current == SILENT and s.quiet.state()["active"]


def test_a_failed_apply_does_not_end_quiet_mode(lab):
    s, provider, cfg = lab
    a, b = chrome_tabs(provider, 2)
    b.nice_error = True
    s.quiet.enter()
    with pytest.raises(psutil.AccessDenied):
        s.apply([engine.identity(a), engine.identity(b)], "render")
    assert cfg.current == SILENT and s.quiet.state()["active"]


# --- the way back after the app is gone ------------------------------------------------------------------

def test_closing_the_app_puts_everything_back_including_the_plan(lab):
    s, provider, cfg = lab
    chrome_tabs(provider)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "focus")
    assert s.restore() == {"restored": 3, "remaining": 0}
    assert cfg.current == TURBO and journal_power(s) is None


def test_ending_a_session_by_hand_leaves_quiet_mode_on(lab):
    s, provider, cfg = lab
    chrome_tabs(provider)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "focus")
    s.restore(include_power=False)
    assert cfg.current == SILENT and s.quiet.state()["active"]


def test_the_guardian_restores_the_plan_after_a_crash(lab, tmp_path):
    s, provider, cfg = lab
    s.quiet.enter()
    guardian = Session(s.directory, provider, "Windows", s.path, powercfg=cfg)
    assert guardian.restore(from_disk=True) == {"restored": 0, "remaining": 0}
    assert cfg.current == TURBO and journal_power(guardian) is None


def test_the_guardian_restores_the_plan_a_render_left_selected(lab):
    s, provider, cfg = lab
    chrome_tabs(provider)
    s.quiet.configure(render_scheme=PERFORMANCE)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "render")
    assert cfg.current == PERFORMANCE and journal_power(s)["now"] == PERFORMANCE
    guardian = Session(s.directory, provider, "Windows", s.path, powercfg=cfg)
    guardian.restore(from_disk=True)
    assert cfg.current == TURBO


def test_a_failed_plan_restore_counts_as_remaining_and_is_retried(lab):
    s, _, cfg = lab
    s.quiet.enter()
    cfg.fail_activate = True
    assert s.restore()["remaining"] == 1
    cfg.fail_activate = False
    assert s.restore() == {"restored": 0, "remaining": 0} and cfg.current == TURBO


@pytest.mark.parametrize("power", [None, "x", {}, {"before": TURBO}])
def test_a_damaged_power_entry_in_the_journal_is_ignored(lab, power):
    s, provider, cfg = lab
    s.path.write_text(json.dumps({"version": 1, "entries": [], "power": power}))
    guardian = Session(s.directory, provider, "Windows", s.path, powercfg=cfg)
    assert guardian.restore(from_disk=True) == {"restored": 0, "remaining": 0} and cfg.calls == []


def test_the_plan_list_is_cached_and_read_failures_are_reported(lab):
    s, _, cfg = lab
    s.quiet.refresh(force=True)
    cfg.fail_read = True
    s.quiet.refresh(now=time.time() + 1)                      # inside the cache window: no new read
    assert s.quiet.state()["error"] is None
    s.quiet.refresh(force=True)
    assert "failed" in s.quiet.state()["error"] and s.quiet.state()["schemes"]


# --- process table -----------------------------------------------------------------------------------------

def table(procs):
    return {pid: {"ppid": 1, "name": n, "created": 100.0, "cpu_seconds": cpu, "rss": 1 << 20}
            for pid, (n, cpu) in procs.items()}


def test_cpu_percent_is_the_change_in_cpu_time_over_the_machine_capacity():
    ticks = iter([0.0, 2.0, 4.0])
    reads = iter([table({1: ("a.exe", 10.0)}) | {7: {"ppid": 1, "name": "b.exe", "created": 5.0, "cpu_seconds": 3.0, "rss": 0}},
                  table({1: ("a.exe", 14.0)}) | {7: {"ppid": 1, "name": "b.exe", "created": 5.0, "cpu_seconds": 3.0, "rss": 0}},
                  table({1: ("a.exe", 14.0)}) | {7: {"ppid": 1, "name": "b.exe", "created": 9.0, "cpu_seconds": 50.0, "rss": 0}}])
    t = ProcessTable(reader=lambda: next(reads), clock=lambda: next(ticks))
    t.cores = 8
    first, _ = t.read()
    assert {r["cpu"] for r in first} == {0.0}                 # nothing to compare with yet, but everything is listed
    rows, parents = t.read()
    by = {r["pid"]: r for r in rows}
    assert by[1]["cpu"] == pytest.approx(4.0 / (2.0 * 8) * 100)     # 4 CPU-seconds in 2 s on 8 cores = 25%
    assert by[7]["cpu"] == 0.0 and parents[1] == (1, "a.exe", 100.0)
    again, _ = t.read()
    assert {r["pid"]: r["cpu"] for r in again}[7] == 0.0       # PID reused by another process: no bogus delta
    assert by[1]["rss"] == 1 << 20 and by[1]["created"] == 100.0


def test_pid_zero_and_shrinking_counters_are_harmless():
    ticks = iter([0.0, 2.0])
    reads = iter([{0: {"ppid": 0, "name": "System Idle Process", "created": 0.0, "cpu_seconds": 50.0, "rss": 0},
                   2: {"ppid": 1, "name": "x.exe", "created": 1.0, "cpu_seconds": 9.0, "rss": 0}},
                  {0: {"ppid": 0, "name": "System Idle Process", "created": 0.0, "cpu_seconds": 90.0, "rss": 0},
                   2: {"ppid": 1, "name": "x.exe", "created": 1.0, "cpu_seconds": 3.0, "rss": 0}}])
    t = ProcessTable(reader=lambda: next(reads), clock=lambda: next(ticks))
    t.read()
    rows, _ = t.read()
    assert [r["pid"] for r in rows] == [2] and rows[0]["cpu"] == 0.0


def test_the_portable_fallback_lists_this_process():
    mine = psutil_table()[os.getpid()]
    assert mine["name"] and mine["created"] > 0 and mine["rss"] > 0


@pytest.mark.skipif(os.name != "nt", reason="Windows system call")
def test_the_windows_bulk_table_matches_what_psutil_knows():
    t = winapi.process_table()
    me = t[os.getpid()]
    assert abs(me["created"] - psutil.Process().create_time()) < 1.0
    assert me["name"].lower().startswith("python") and me["rss"] > 1 << 20
    assert t[0]["name"] == "System Idle Process" and len(t) > 20
    assert me["ppid"] == psutil.Process().ppid()
    assert any(e["name"].lower() == "svchost.exe" for e in t.values())      # system services are visible


# --- spike recorder ---------------------------------------------------------------------------------------

@pytest.fixture
def rec(tmp_path):
    s = Session(tmp_path, Provider(), "Windows", powercfg=None)
    s.recorder.cores = 10
    return s, s.recorder


def parents_of(*nodes):
    """nodes: (pid, ppid, name, created)"""
    return {pid: (ppid, name, created) for pid, ppid, name, created in nodes}


PARENTS = parents_of((1, 0, "explorer.exe", 1.0), (10, 1, "claude.exe", 2.0), (11, 10, "cmd.exe", 3.0),
                     (12, 11, "node.exe", 4.0), (13, 12, "python.exe", 5.0), (50, 1, "python.exe", 6.0),
                     (60, 1, "ffmpeg.exe", 7.0), (70, 1, "chrome.exe", 8.0), (71, 70, "chrome.exe", 8.5),
                     (80, 1, "bdservicehost.exe", 9.0))


def busy_rows(**cpu):
    ppid = {pid: PARENTS[pid][0] for pid in PARENTS}
    return [{"pid": pid, "ppid": ppid[pid], "name": PARENTS[pid][1], "cpu": c} for pid, c in
            ((int(k[1:]), v) for k, v in cpu.items())]


def run(recorder, steps, start=1000.0, every=2.0):
    now = start
    for cpu, rows in steps:
        recorder.observe(cpu, rows, PARENTS, now=now)
        now += every
    return now


def test_a_sustained_busy_period_is_recorded_with_its_culprits(rec):
    s, r = rec
    rows = busy_rows(p13=30.0, p60=20.0, p80=5.0, p70=4.0, p71=6.0)
    end = run(r, [(65.0, rows)] * 10 + [(5.0, [])] * 6)
    assert r.state()["count"] == 1 and r.state()["current"] is None
    spike, = r.state()["spikes"]
    assert spike["seconds"] == pytest.approx(20.0) and spike["avg_cpu"] == 65.0 and spike["peak_cpu"] == 65.0
    names = [(c["name"], c["via"]) for c in spike["culprits"]]
    assert names == [("python.exe", "Claude"), ("ffmpeg.exe", None), ("Google Chrome", None), ("bdservicehost.exe", None)]
    first = spike["culprits"][0]
    assert first["cpu_seconds"] == pytest.approx(30.0 / 100 * 10 * 20.0) and first["share"] == pytest.approx(30 / 65 * 100, abs=0.2)
    chrome = spike["culprits"][2]
    assert chrome["processes"] == 2 and chrome["cpu_seconds"] == pytest.approx(10.0 / 100 * 10 * 20.0)
    assert sum(c["share"] for c in spike["culprits"]) == pytest.approx(100.0, abs=0.5)


def test_a_short_burst_is_not_a_busy_period(rec):
    s, r = rec
    run(r, [(80.0, busy_rows(p60=50.0))] * 2 + [(5.0, [])] * 8)          # 4 s: below the 6 s needed
    assert r.state()["count"] == 0 and r.state()["current"] is None
    run(r, [(80.0, busy_rows(p60=50.0))] * 4 + [(5.0, [])] * 8, start=2000.0)    # 8 s: long enough
    assert r.state()["count"] == 1 and r.state()["spikes"][0]["seconds"] == pytest.approx(8.0)


def test_a_period_only_shows_up_once_the_load_has_lasted_six_seconds(rec):
    s, r = rec
    now = run(r, [(80.0, busy_rows(p60=50.0))] * 2)                       # 4 s of load
    assert r.state()["current"] is None
    run(r, [(80.0, busy_rows(p60=50.0))] * 2, start=now)                  # 8 s
    assert r.state()["current"] is not None


def test_a_confirmed_period_shorter_than_the_minimum_is_dropped(rec):
    s, r = rec
    run(r, [(80.0, busy_rows(p60=50.0))] * 3 + [(5.0, [])] * 8)         # confirmed at 6 s, lasted 6 s: under the 8 s minimum
    assert r.state()["count"] == 0 and r.state()["current"] is None


def test_dips_between_the_two_thresholds_do_not_split_a_period(rec):
    s, r = rec
    steps = [(60.0, busy_rows(p60=40.0))] * 5 + [(30.0, busy_rows(p60=20.0))] * 8 + [(60.0, busy_rows(p60=40.0))] * 4 + [(5.0, [])] * 8
    run(r, steps)                                                            # 16 s at 30%: between 25 and 40
    assert r.state()["count"] == 1


def test_a_quiet_gap_shorter_than_ten_seconds_does_not_end_the_period(rec):
    s, r = rec
    steps = [(60.0, busy_rows(p60=40.0))] * 5 + [(10.0, [])] * 3 + [(60.0, busy_rows(p60=40.0))] * 5 + [(5.0, [])] * 8
    run(r, steps)
    assert r.state()["count"] == 1 and r.state()["spikes"][0]["seconds"] >= 26


def test_the_current_period_is_visible_while_it_is_happening(rec):
    s, r = rec
    run(r, [(70.0, busy_rows(p13=40.0, p60=15.0))] * 6)
    current = r.state()["current"]
    assert current["seconds"] >= 10 and current["culprits"][0]["name"] == "python.exe"


def test_work_started_by_claude_or_by_load_manager_is_labelled(rec):
    assert SpikeRecorder._via(13, PARENTS) == "Claude"          # claude -> cmd -> node -> python
    assert SpikeRecorder._via(12, PARENTS) == "Claude"
    assert SpikeRecorder._via(10, PARENTS) is None              # Claude itself is not "via" Claude
    assert SpikeRecorder._via(50, PARENTS) is None
    me = os.getpid()
    tree = parents_of((1, 0, "explorer.exe", 1.0), (me, 1, "python.exe", 2.0), (900, me, "ffmpeg.exe", 3.0))
    assert SpikeRecorder._via(me, tree) == "Load Manager" and SpikeRecorder._via(900, tree) == "Load Manager"


def test_stale_parent_ids_and_loops_are_not_followed(rec):
    reused = parents_of((10, 1, "claude.exe", 50.0), (13, 10, "python.exe", 5.0))     # parent younger than child
    assert SpikeRecorder._via(13, reused) is None
    loop = parents_of((20, 21, "a.exe", 1.0), (21, 20, "b.exe", 1.0))
    assert SpikeRecorder._via(20, loop) is None
    assert SpikeRecorder._via(999, PARENTS) is None


def test_a_disabled_recorder_records_nothing_and_forgets_a_running_period(rec):
    s, r = rec
    run(r, [(70.0, busy_rows(p60=40.0))] * 6)
    r.configure(enabled=False)
    run(r, [(70.0, busy_rows(p60=40.0))] * 10 + [(5.0, [])] * 8, start=2000.0)
    assert r.state()["count"] == 0 and r.state()["current"] is None and not r.state()["enabled"]


def test_thresholds_are_validated_and_remembered(rec, tmp_path):
    s, r = rec
    for bad in ({"on": 5}, {"off": 5}, {"on": 99}, {"on": 30, "off": 30}, {"on": "40"}, {"off": True}):
        with pytest.raises(PolicyError):
            r.configure(**bad)
    assert not s.settings_path.exists()
    r.configure(on=50, off=20)
    again = Session(tmp_path, Provider(), "Windows", powercfg=None).recorder.state()
    assert (again["on"], again["off"], again["enabled"]) == (50.0, 20.0, True)


def test_nonsense_saved_thresholds_fall_back_to_the_defaults(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"recorder": {"on": 10, "off": 90, "enabled": False}}))
    r = Session(tmp_path, Provider(), "Windows", powercfg=None).recorder
    assert (r.on, r.off, r.enabled) == (40.0, 25.0, False)


def test_finished_periods_are_kept_on_disk_with_their_context(rec, tmp_path):
    s, r = rec
    s.quiet.cache["active"] = {"guid": TURBO, "name": "Turbo"}
    run(r, [(65.0, busy_rows(p60=40.0))] * 10 + [(5.0, [])] * 6)
    saved = json.loads(s.history_path.read_text())["spikes"]
    assert len(saved) == 1 and saved[0]["plan"] == "Turbo" and saved[0]["quiet_mode"] is False
    assert saved[0]["session"] is False and "on_ac" in saved[0]
    again = Session(tmp_path, Provider(), "Windows", powercfg=None).recorder.state()
    assert again["count"] == 1 and again["spikes"][0]["culprits"][0]["name"] == "ffmpeg.exe"


def test_the_report_adds_up_the_biggest_users_across_periods_and_can_be_cleared(rec):
    s, r = rec
    t = 1000.0
    for rows in (busy_rows(p60=45.0, p13=10.0), busy_rows(p60=10.0, p13=40.0), busy_rows(p80=20.0)):
        t = run(r, [(65.0, rows)] * 10 + [(5.0, [])] * 6, start=t) + 100
    top = r.state()["top"]
    assert [(x["name"], x["via"]) for x in top][:2] == [("ffmpeg.exe", None), ("python.exe", "Claude")]
    assert top[0]["spikes"] == 2 and r.state()["spikes"][0]["culprits"][0]["name"] == "bdservicehost.exe"   # newest first
    r.clear()
    assert r.state()["count"] == 0 and json.loads(s.history_path.read_text())["spikes"] == []


def test_only_the_most_recent_fifty_periods_are_kept(rec):
    s, r = rec
    t = 1000.0
    for _ in range(SpikeRecorder.KEEP + 5):
        t = run(r, [(65.0, busy_rows(p60=40.0))] * 6 + [(5.0, [])] * 6, start=t) + 50
    assert r.state()["count"] == SpikeRecorder.KEEP


# --- monitor integration ---------------------------------------------------------------------------------------

def test_the_snapshot_carries_quiet_mode_and_the_spike_report(tmp_path):
    m = Monitor(Session(tmp_path, powercfg=None))
    m.sample()
    time.sleep(.05)
    m.sample()
    full = m.get()
    assert {"available", "active", "state", "schemes", "quiet_target", "auto_enter", "note", "error"} <= set(full["quiet"])
    assert {"enabled", "on", "off", "current", "spikes", "top", "count"} <= set(full["recorder"])
    assert m.last_rows and m.last_parents[os.getpid()][1]


def test_one_monitor_tick_is_cheap(tmp_path):
    """The monitor must not be what warms the laptop: a tick used to cost ~2 s of CPU."""
    m = Monitor(Session(tmp_path, powercfg=None))
    m.sample()
    m.sample()
    started = time.process_time()
    for _ in range(3):
        m.sample()
    assert (time.process_time() - started) / 3 < 0.5


def test_a_failing_monitor_step_is_logged_once_not_every_tick(tmp_path):
    s = Session(tmp_path, powercfg=None)
    m = Monitor(s)
    for _ in range(5):
        m._tick_failed("power plan", OSError("boom"))
    m._tick_failed("recorder", ValueError("x"))
    assert [e["text"] for e in s.events if "failed" in e["text"]].count("power plan failed: OSError") == 1
    assert sum("failed" in e["text"] for e in s.events) == 2
