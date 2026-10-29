"""Auto-start on render and the timed real command. Time is passed in, and the command runner is
replaced where a test is about the sequence rather than the process; a few tests run real commands."""
import sys
import threading
import time

import psutil
import pytest

from loadmanager import engine
from loadmanager.automation import compare_runs
from loadmanager.engine import PolicyError, Session
from test_engine import Provider
from test_windows_actions import FakeNative, add, chrome_tabs

NORMAL = engine.NORMAL


@pytest.fixture
def win(tmp_path):
    provider = Provider()
    native = FakeNative(provider)
    session = Session(tmp_path, provider, "Windows", native=native)
    session.guardian_ready = True
    session.memory_percent = lambda: 50
    return session, provider, native


def busy(cpu=30.0, name="ffmpeg.exe", created=990.0):
    return [{"name": name, "cpu": cpu, "created": created}]


def drive(auto, steps, cores=24):
    """steps: (time, processes) pairs."""
    for now, processes in steps:
        auto.tick(processes, cores, now=now)


# --- auto-start -----------------------------------------------------------------------------------

def test_disabled_autopilot_never_changes_anything_but_still_logs_renders(win):
    s, provider, _ = win
    chrome_tabs(provider)
    drive(s.autopilot, [(1000, busy()), (1002, busy()), (1010, busy()), (1040, [])])
    assert s.entries == [] and s.autopilot.state()["state"] == "off"
    job, = s.autopilot.state()["jobs"]
    assert job["seconds"] == 20.0 and job["auto_session"] is False and job["tools"] == ["ffmpeg.exe"]


def test_a_render_starts_a_session_and_the_end_of_it_stops_the_session(win):
    s, provider, _ = win
    tabs = chrome_tabs(provider)
    a = s.autopilot
    a.configure(enabled=True)
    drive(a, [(1000, busy())])
    assert a.state()["state"] == "pending" and not s.entries
    drive(a, [(1002, busy())])
    assert not s.entries                               # only 2 s: still short of START_AFTER
    drive(a, [(1004, busy())])
    assert s.entries and a.state()["state"] == "active" and a.state()["owned"]
    assert all(t.nice() == engine.IDLE for t in tabs)
    drive(a, [(1006, busy()), (1008, [])])
    assert s.entries and a.state()["state"] == "ending"
    drive(a, [(1030, [])])                             # 24 s without a render, grace is 20 s
    assert s.entries == [] and a.state()["state"] == "waiting"
    assert all(t.nice() == NORMAL for t in tabs)
    job, = a.state()["jobs"]
    assert job["auto_session"] is True and job["seconds"] == 16.0


def test_a_tool_that_barely_uses_the_cpu_is_not_a_render(win):
    s, provider, _ = win
    chrome_tabs(provider)
    s.autopilot.configure(enabled=True)
    drive(s.autopilot, [(1000, busy(cpu=1.0)), (1010, busy(cpu=1.0, name="Adobe Premiere Pro.exe")),
                        (1020, busy(cpu=90.0, name="node.exe"))])
    assert s.entries == [] and s.autopilot.state()["state"] == "waiting"


def test_the_threshold_is_half_a_core(win):
    s, provider, _ = win
    chrome_tabs(provider)
    a = s.autopilot
    a.configure(enabled=True)
    drive(a, [(1000, busy(cpu=11.0)), (1004, busy(cpu=11.0))], cores=4)    # half a core of 4 is 12.5%
    assert not s.entries
    drive(a, [(1006, busy(cpu=13.0)), (1010, busy(cpu=13.0))], cores=4)
    assert s.entries


def test_a_session_started_by_hand_is_left_alone(win):
    s, provider, _ = win
    chrome_tabs(provider)
    s.apply([{"group": "chrome"}], "focus")
    a = s.autopilot
    a.configure(enabled=True)
    drive(a, [(1000, busy()), (1010, busy()), (1100, [])])
    assert s.entries and a.state()["state"] == "manual" and not a.state()["owned"]


def test_ending_the_session_by_hand_is_respected_until_the_render_is_over(win):
    s, provider, _ = win
    chrome_tabs(provider)
    a = s.autopilot
    a.configure(enabled=True)
    drive(a, [(1000, busy()), (1004, busy())])
    assert s.entries
    s.restore()
    drive(a, [(1006, busy()), (1100, busy())])
    assert s.entries == [] and a.state()["state"] == "held"
    drive(a, [(1110, [])])                             # render over: re-armed
    drive(a, [(2000, busy(created=1990.0)), (2004, busy(created=1990.0))])
    assert s.entries and a.state()["state"] == "active"


def test_a_short_gap_between_two_ffmpeg_calls_does_not_restart_the_session(win):
    s, provider, native = win
    chrome_tabs(provider)
    a = s.autopilot
    a.configure(enabled=True)
    drive(a, [(1000, busy()), (1004, busy())])
    first = list(s.entries)
    drive(a, [(1008, []), (1012, []), (1014, busy()), (1016, busy())])
    assert s.entries == first and len(a.state()["jobs"]) == 0
    drive(a, [(1018, []), (1040, [])])
    assert s.entries == [] and len(a.state()["jobs"]) == 1


def test_a_failed_start_backs_off_instead_of_retrying_every_tick(win):
    s, provider, _ = win                              # no heavy app is running at all
    a = s.autopilot
    a.configure(enabled=True)
    calls = []
    real_apply = s.apply
    s.apply = lambda *args: (calls.append(1), real_apply(*args))[1]
    drive(a, [(1000, busy()), (1004, busy()), (1006, busy()), (1030, busy())])
    assert len(calls) == 1 and a.state()["state"] == "retry" and "Nothing to change" in a.state()["error"]
    drive(a, [(1070, busy())])                          # back-off (60 s) is over
    assert len(calls) == 2
    assert any("Auto-start skipped" in e["text"] for e in s.events)


def test_a_trim_only_result_does_not_repeat_every_tick(win):
    s, provider, native = win
    add(provider, 410000, "claude.exe")
    s.memory_percent = lambda: 90
    a = s.autopilot
    a.configure(enabled=True, groups=["claude"])
    drive(a, [(1000, busy()), (1004, busy()), (1006, busy()), (1008, busy()), (1010, busy())])
    assert len(native.trimmed) == 1 and a.state()["state"] == "held" and not s.entries


def test_turning_it_off_hands_a_running_session_back_to_the_user(win):
    s, provider, _ = win
    chrome_tabs(provider)
    a = s.autopilot
    a.configure(enabled=True)
    drive(a, [(1000, busy()), (1004, busy())])
    a.configure(enabled=False)
    drive(a, [(1100, [])])
    assert s.entries and not a.state()["owned"] and a.state()["state"] == "off"


def test_runs_shorter_than_three_seconds_are_not_logged(win):
    s, _, _ = win
    drive(s.autopilot, [(1000, busy(created=999.0)), (1030, [])])
    assert s.autopilot.state()["jobs"] == []


def test_a_paused_autopilot_ignores_ticks(win):
    s, provider, _ = win
    chrome_tabs(provider)
    a = s.autopilot
    a.configure(enabled=True)
    a.pause()
    drive(a, [(1000, busy()), (1004, busy()), (1008, busy())])
    assert s.entries == []
    a.resume()
    drive(a, [(1010, busy()), (1014, busy())])
    assert s.entries


def test_settings_and_render_log_survive_a_restart(win, tmp_path):
    s, provider, native = win
    a = s.autopilot
    a.configure(enabled=True, groups=["chrome", "telegram"], grace=30)
    drive(a, [(1000, busy()), (1040, [])])
    again = Session(tmp_path, provider, "Windows", native=native).autopilot.state()
    assert again["enabled"] and again["groups"] == ["chrome", "telegram"] and again["grace"] == 30.0
    assert again["jobs"][0]["seconds"] == 10.0


@pytest.mark.parametrize("change", [{"groups": []}, {"groups": ["bogus"]}, {"groups": "chrome"}, {"grace": 1},
                                    {"grace": 9999}, {"grace": True}, {"grace": "20"}])
def test_invalid_auto_settings_are_rejected_and_not_saved(win, change):
    s, _, _ = win
    with pytest.raises(PolicyError):
        s.autopilot.configure(**change)
    assert not s.settings_path.exists()


# --- timed command ------------------------------------------------------------------------------

def py(code):
    return f'"{sys.executable}" -c "{code}"'


def wait(timed, seconds=20):
    deadline = time.time() + seconds
    while timed.running and time.time() < deadline:
        time.sleep(0.02)
    assert not timed.running


def fake_runner(session, durations, log):
    def execute(argv, cwd):
        log.append(bool(session.entries))
        return durations[len(log) - 1], 0, None
    return execute


def test_a_real_command_is_timed_and_remembered(win, tmp_path):
    s, _, _ = win
    s.timed.start(py("import time; time.sleep(0.3)"), mode="baseline")
    wait(s.timed)
    result, = s.timed.state()["results"]
    assert result["mode"] == "baseline" and result["exit_code"] == 0 and result["seconds"] >= 0.3
    assert s.entries == []
    again = Session(tmp_path, s.provider, "Windows", native=s.native).timed.state()
    assert again["results"][0]["id"] == result["id"] and again["command"].endswith('time.sleep(0.3)"')


def test_ab_applies_a_session_only_around_the_second_run(win):
    s, provider, _ = win
    tabs = chrome_tabs(provider)
    seen = []
    s.timed._execute = fake_runner(s, [10.0, 7.5], seen)
    s.timed.start("render --fast", mode="ab")
    wait(s.timed)
    assert seen == [False, True] and s.entries == [] and all(t.nice() == NORMAL for t in tabs)
    assert [r["mode"] for r in reversed(s.timed.state()["results"])] == ["baseline", "session"]
    comparison, = s.timed.state()["comparisons"]
    assert comparison["saved_seconds"] == 2.5 and comparison["saved_percent"] == 25.0


def test_the_order_can_be_swapped_to_check_for_cache_effects(win):
    s, provider, _ = win
    chrome_tabs(provider)
    seen = []
    s.timed._execute = fake_runner(s, [6.0, 9.0], seen)
    s.timed.start("render --fast", mode="ab", order="session_first")
    wait(s.timed)
    assert seen == [True, False]
    assert [r["mode"] for r in reversed(s.timed.state()["results"])] == ["session", "baseline"]


def test_a_failing_command_stops_the_sequence_and_reports_why(win):
    s, provider, _ = win
    chrome_tabs(provider)
    seen = []
    s.timed._execute = lambda argv, cwd: (seen.append(1), (0.5, 3, "The command exited with code 3: boom"))[1]
    s.timed.start("render --fast", mode="ab")
    wait(s.timed)
    assert len(seen) == 1 and "code 3" in s.timed.state()["error"] and s.entries == []
    assert s.timed.state()["comparisons"][0]["saved_percent"] is None     # a failed run is never compared
    assert not s.autopilot.paused


def test_the_session_is_always_ended_even_if_the_runner_crashes(win):
    s, provider, _ = win
    tabs = chrome_tabs(provider)

    def crash(argv, cwd):
        assert s.entries
        raise RuntimeError("disk vanished")
    s.timed._execute = crash
    s.timed.start("render --fast", mode="session")
    wait(s.timed)
    assert "disk vanished" in s.timed.state()["error"]
    assert s.entries == [] and all(t.nice() == NORMAL for t in tabs) and not s.autopilot.paused


def test_a_session_that_is_already_running_is_used_and_kept(win):
    s, provider, _ = win
    chrome_tabs(provider)
    s.apply([{"group": "chrome"}], "focus")
    before, seen = list(s.entries), []
    s.timed._execute = fake_runner(s, [5.0], seen)
    s.timed.start("render --fast", mode="session")
    wait(s.timed)
    assert seen == [True] and s.entries == before


def test_the_command_runs_where_it_is_told_to_and_its_exit_code_and_output_are_reported(win, tmp_path):
    s, _, _ = win
    folder = tmp_path / "work dir"
    folder.mkdir()
    s.timed.start(py("import os; print(os.getcwd())"), cwd=str(folder), mode="baseline")
    wait(s.timed)
    assert "work dir" in (s.directory / "timed-last.log").read_text()
    s.timed.start(py("print('boom'); import sys; sys.exit(4)"), mode="baseline")
    wait(s.timed)
    assert "code 4" in s.timed.state()["error"] and "boom" in s.timed.state()["error"]
    assert s.timed.state()["results"][0]["exit_code"] == 4


def test_a_command_that_does_not_exist_is_reported_not_raised(win):
    s, _, _ = win
    s.timed.start("this-command-does-not-exist-xyz --flag", mode="baseline")
    wait(s.timed)
    assert "Could not start" in s.timed.state()["error"] and s.timed.state()["results"][0]["exit_code"] is None


def test_cancel_stops_only_the_started_command_and_restores(win):
    s, provider, _ = win
    tabs = chrome_tabs(provider)
    s.timed.start(py("import time; time.sleep(60)"), mode="session")
    deadline = time.time() + 10
    while (s.timed.proc is None or s.timed.proc.poll() is not None) and time.time() < deadline:
        time.sleep(0.02)
    pid = s.timed.proc.pid
    assert s.entries                                   # the session is up while the command runs
    s.timed.cancel()
    wait(s.timed, 10)
    assert s.timed.state()["error"] == "Cancelled" and s.timed.state()["results"] == []
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert s.entries == [] and all(t.nice() == NORMAL for t in tabs)


def test_the_autopilot_is_paused_for_the_whole_run(win):
    s, provider, _ = win
    chrome_tabs(provider)
    flags = []
    s.timed._execute = lambda argv, cwd: (flags.append(s.autopilot.paused), (1.0, 0, None))[1]
    s.timed.start("render --fast", mode="baseline")
    wait(s.timed)
    assert flags == [True] and not s.autopilot.paused


@pytest.mark.parametrize("command", ["", "   ", "a\nb", "a\0b", "x" * 4001, None, 5])
def test_unusable_commands_are_rejected(win, command):
    s, _, _ = win
    with pytest.raises(PolicyError):
        s.timed.start(command, mode="baseline")
    assert not s.timed.running


@pytest.mark.parametrize("kwargs", [{"mode": "both"}, {"order": "random"}, {"cwd": "Z:/does/not/exist/at/all"}])
def test_unusable_options_are_rejected(win, kwargs):
    s, _, _ = win
    with pytest.raises(PolicyError):
        s.timed.start("render --fast", **{"mode": "baseline", **kwargs})


def test_the_baseline_needs_a_session_free_machine(win):
    s, provider, _ = win
    chrome_tabs(provider)
    s.apply([{"group": "chrome"}], "focus")
    with pytest.raises(PolicyError, match="End the current session"):
        s.timed.start("render --fast", mode="baseline")


def test_a_session_comparison_needs_something_to_limit_and_a_guardian(win):
    s, provider, _ = win                               # none of the heavy apps is running
    with pytest.raises(PolicyError, match="Nothing to change"):
        s.timed.start("render --fast", mode="ab")
    chrome_tabs(provider)
    s.guardian_ready = False
    with pytest.raises(PolicyError, match="guardian"):
        s.timed.start("render --fast", mode="session")
    s.timed.start("render --fast", mode="baseline")    # a plain timing needs neither
    wait(s.timed)


def test_runs_do_not_overlap_the_benchmark_or_each_other(win):
    s, provider, _ = win
    chrome_tabs(provider)
    gate = threading.Event()
    s.timed._execute = lambda argv, cwd: (gate.wait(10), (1.0, 0, None))[1]
    s.timed.start("render --fast", mode="baseline")
    with pytest.raises(PolicyError, match="already in progress"):
        s.timed.start("render --fast", mode="baseline")
    assert s.benchmark.start() is False
    gate.set()
    wait(s.timed)
    s.benchmark.running = True
    with pytest.raises(PolicyError, match="benchmark"):
        s.timed.start("render --fast", mode="baseline")
    s.benchmark.running = False


def result(command, mode, seconds, code=0):
    return {"command": command, "mode": mode, "seconds": seconds, "exit_code": code}


def test_the_comparison_uses_the_latest_successful_run_of_each_kind():
    runs = [result("a", "baseline", 100.0), result("a", "session", 80.0),
            result("a", "baseline", 90.0), result("a", "session", 10.0, code=1),
            result("b", "baseline", 50.0), result("c", "baseline", 5.0), result("d", "baseline", 1.0)]
    out = compare_runs(runs)
    assert [c["command"] for c in out] == ["d", "c", "b"]               # newest commands first, at most three
    full = compare_runs(runs[:4], limit=3)[0]
    assert full["baseline"] == 90.0 and full["session"] == 80.0 and full["saved_percent"] == 11.1
    assert compare_runs(runs[4:5])[0]["saved_percent"] is None
