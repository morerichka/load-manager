"""Quiet mode's second method (change only processor settings of the current plan: turbo boost, maximum state)
and keeping the laptop awake while work runs in quiet mode."""
import os
import time

import pytest

from loadmanager import winapi
from loadmanager.engine import PolicyError, Session
from loadmanager.power import BOOST, MAX_STATE, Powercfg, PowerGuard
from test_engine import Provider
from test_quiet_and_spikes import BALANCED, SILENT, TURBO, FakeCfg, journal_power
from test_windows_actions import FakeNative, chrome_tabs

SUB = "SUB_PROCESSOR"


class LimitCfg(FakeCfg):
    """FakeCfg that also keeps processor settings per plan: boost mode (2 = Aggressive) and maximum state."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.values = {(p["guid"], alias): {"ac": ac, "dc": dc}
                       for p in self.plans for alias, ac, dc in ((BOOST, 2, 2), (MAX_STATE, 100, 100))}
        self.set_calls, self.fail_set, self.fail_second_set, self.missing = [], False, False, set()

    def value(self, guid, sub, setting):
        if self.fail_read:
            raise OSError("powercfg query failed")
        if setting in self.missing or (guid, setting) not in self.values:
            return {"ac": None, "dc": None}
        return dict(self.values[(guid, setting)])

    def set_value(self, guid, sub, setting, ac=None, dc=None):
        self.set_calls.append((guid, setting, ac, dc))
        if self.fail_set or (self.fail_second_set and len(self.set_calls) == 2):
            raise OSError("access denied")
        for key, number in (("ac", ac), ("dc", dc)):
            if number is not None:
                self.values[(guid, setting)][key] = number

    def get(self, guid, setting):
        v = self.values[(guid, setting)]
        return v["ac"], v["dc"]


class FakeWake:
    def __init__(self):
        self.held, self.hold_calls, self.release_calls, self.fail = False, 0, 0, False

    def hold(self):
        if self.fail:
            raise OSError("power request refused")
        self.held = True
        self.hold_calls += 1

    def release(self):
        self.held = False
        self.release_calls += 1


@pytest.fixture
def lim(tmp_path):
    provider, cfg, wake = Provider(), LimitCfg(), FakeWake()
    session = Session(tmp_path, provider, "Windows", native=FakeNative(provider), powercfg=cfg, wake=wake)
    session.guardian_ready = True
    session.memory_percent = lambda: 50
    session.quiet.configure(mode="limit")
    return session, provider, cfg, wake


# --- turbo boost and maximum state ---------------------------------------------------------------------

def test_quiet_mode_can_turn_boost_off_and_leave_the_plan_alone(lim):
    s, _, cfg, _ = lim
    state = s.quiet.enter()
    assert cfg.current == TURBO and cfg.get(TURBO, BOOST) == (0, 0) and cfg.get(TURBO, MAX_STATE) == (100, 100)
    assert state["active"] and state["limits"] == [{"label": "Turbo boost", "before": "Aggressive", "now": "Off"}]
    assert state["original"] == {"guid": TURBO, "name": "Turbo"}
    entry = journal_power(s)
    assert entry["mode"] == "limit" and entry["scheme"] == TURBO and entry["changes"][0]["before_ac"] == 2
    after = s.quiet.leave()
    assert cfg.get(TURBO, BOOST) == (2, 2) and not after["active"] and journal_power(s) is None
    assert cfg.calls and all(call == TURBO for call in cfg.calls)             # only ever re-selected the same plan


def test_the_maximum_state_can_be_lowered_too(lim):
    s, _, cfg, _ = lim
    s.quiet.configure(cap=70)
    state = s.quiet.enter()
    assert cfg.get(TURBO, BOOST) == (0, 0) and cfg.get(TURBO, MAX_STATE) == (70, 70)
    assert [l["now"] for l in state["limits"]] == ["Off", "70%"]
    s.quiet.leave()
    assert cfg.get(TURBO, MAX_STATE) == (100, 100) and cfg.get(TURBO, BOOST) == (2, 2)


def test_only_the_maximum_state_when_boost_is_left_alone(lim):
    s, _, cfg, _ = lim
    s.quiet.configure(boost_off=False, cap=60)
    s.quiet.enter()
    assert cfg.get(TURBO, BOOST) == (2, 2) and cfg.get(TURBO, MAX_STATE) == (60, 60)


def test_a_lower_value_that_is_already_there_is_never_raised(lim):
    s, _, cfg, _ = lim
    cfg.values[(TURBO, MAX_STATE)] = {"ac": 100, "dc": 50}
    s.quiet.configure(cap=70)
    s.quiet.enter()
    assert cfg.get(TURBO, MAX_STATE) == (70, 50)
    s.quiet.leave()
    assert cfg.get(TURBO, MAX_STATE) == (100, 50)


def test_a_setting_that_is_already_low_is_not_touched_and_not_remembered(lim):
    s, _, cfg, _ = lim
    cfg.values[(TURBO, BOOST)] = {"ac": 0, "dc": 0}                            # the user already turned boost off
    s.quiet.configure(cap=70)
    state = s.quiet.enter()
    assert [l["label"] for l in state["limits"]] == ["Maximum processor state"]
    s.quiet.leave()
    assert cfg.get(TURBO, BOOST) == (0, 0)


def test_nothing_to_limit_is_an_error_not_a_silent_no_op(lim):
    s, _, cfg, _ = lim
    cfg.values[(TURBO, BOOST)] = {"ac": 0, "dc": 0}
    with pytest.raises(PolicyError, match="Nothing to limit"):
        s.quiet.enter()
    s.quiet.configure(boost_off=False, cap=100)
    with pytest.raises(PolicyError, match="turn turbo boost off"):
        s.quiet.enter()
    assert cfg.set_calls == []


def test_a_plan_that_lacks_a_setting_is_handled(lim):
    s, _, cfg, _ = lim
    s.quiet.configure(cap=70)
    cfg.missing = {BOOST}
    assert [l["label"] for l in s.quiet.enter()["limits"]] == ["Maximum processor state"]
    s.quiet.leave()
    cfg.missing = {BOOST, MAX_STATE}
    with pytest.raises(PolicyError, match="Nothing to limit"):
        s.quiet.enter()


def test_a_refused_change_leaves_no_trace(lim):
    s, _, cfg, _ = lim
    cfg.fail_set = True
    with pytest.raises(PolicyError, match="Could not change"):
        s.quiet.enter()
    assert not s.quiet.state()["active"] and journal_power(s) is None and cfg.get(TURBO, BOOST) == (2, 2)


def test_a_half_applied_change_is_undone(lim):
    s, _, cfg, _ = lim
    s.quiet.configure(cap=70)
    cfg.fail_second_set = True                                                  # boost is set, then the maximum state fails
    with pytest.raises(PolicyError):
        s.quiet.enter()
    assert cfg.get(TURBO, BOOST) == (2, 2) and cfg.get(TURBO, MAX_STATE) == (100, 100) and journal_power(s) is None


def test_a_crash_while_changing_leaves_the_way_back_on_disk(lim):
    s, provider, cfg, _ = lim

    def die(*args, **kwargs):
        raise RuntimeError("the process was killed here")
    cfg.set_value = die
    with pytest.raises(RuntimeError):
        s.quiet.enter()
    assert journal_power(s)["mode"] == "limit" and journal_power(s)["changes"][0]["before_ac"] == 2
    del cfg.set_value
    guardian = Session(s.directory, provider, "Windows", s.path, powercfg=cfg, wake=None)
    assert guardian.restore(from_disk=True)["remaining"] == 0
    assert cfg.get(TURBO, BOOST) == (2, 2) and journal_power(guardian) is None


def test_the_guardian_restores_the_settings_after_a_crash(lim):
    s, provider, cfg, _ = lim
    s.quiet.configure(cap=70)
    s.quiet.enter()
    guardian = Session(s.directory, provider, "Windows", s.path, powercfg=cfg, wake=None)
    assert guardian.restore(from_disk=True) == {"restored": 0, "remaining": 0}
    assert cfg.get(TURBO, BOOST) == (2, 2) and cfg.get(TURBO, MAX_STATE) == (100, 100)


def test_closing_the_app_restores_the_settings_but_ending_a_session_does_not(lim):
    s, provider, cfg, _ = lim
    chrome_tabs(provider, 1)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "focus")
    s.restore(include_power=False)
    assert cfg.get(TURBO, BOOST) == (0, 0) and s.quiet.state()["active"]
    assert s.restore() == {"restored": 0, "remaining": 0} and cfg.get(TURBO, BOOST) == (2, 2)


def test_one_setting_changed_by_someone_else_is_left_and_ours_is_still_restored(lim):
    s, _, cfg, _ = lim
    s.quiet.configure(cap=70)
    s.quiet.enter()
    cfg.values[(TURBO, BOOST)]["ac"] = 1                                        # the user turned boost back on
    state = s.quiet.leave()
    assert cfg.get(TURBO, BOOST)[0] == 1                                        # left as the user set it
    assert cfg.get(TURBO, MAX_STATE) == (100, 100)                              # ours was taken off
    assert not state["active"] and "Turbo boost" in state["note"] and "changed by something else" in state["note"]


def test_the_monitor_releases_quiet_mode_when_a_setting_is_changed_elsewhere(lim):
    s, _, cfg, _ = lim
    s.quiet.configure(cap=70)
    s.quiet.enter()
    cfg.values[(TURBO, MAX_STATE)]["ac"] = 90
    s.quiet.tick(now=time.time() + 11)
    state = s.quiet.state()
    assert not state["active"] and "released" in state["note"] and "Maximum processor state" in state["note"]
    assert cfg.get(TURBO, BOOST) == (2, 2) and cfg.get(TURBO, MAX_STATE)[0] == 90 and journal_power(s) is None


def test_switching_to_another_plan_takes_the_limits_off_the_old_one(lim):
    s, _, cfg, _ = lim
    s.quiet.enter()
    cfg.current = BALANCED                                                      # Fn+F5 or the vendor software
    s.quiet.tick(now=time.time() + 11)
    assert not s.quiet.state()["active"] and cfg.get(TURBO, BOOST) == (2, 2) and cfg.current == BALANCED
    assert "taken off" in s.quiet.state()["note"] and journal_power(s) is None


def test_a_deleted_plan_is_not_chased(lim):
    s, _, cfg, _ = lim
    s.quiet.enter()
    cfg.plans = [p for p in cfg.plans if p["guid"] != TURBO]
    cfg.set_calls.clear()
    state = s.quiet.leave()
    assert cfg.set_calls == [] and not state["active"] and "no longer exists" in state["note"]


def test_a_failed_return_keeps_the_entry_and_is_retried(lim):
    s, _, cfg, _ = lim
    s.quiet.enter()
    cfg.fail_set = True
    with pytest.raises(PolicyError, match="Could not restore"):
        s.quiet.leave()
    assert s.quiet.state()["active"] and s.restore()["remaining"] == 1
    cfg.fail_set = False
    assert s.restore()["remaining"] == 0 and cfg.get(TURBO, BOOST) == (2, 2)


def test_after_a_partly_failed_return_only_the_rest_is_retried(lim):
    s, _, cfg, _ = lim
    s.quiet.configure(cap=70)
    s.quiet.enter()
    cfg.set_calls.clear()
    cfg.fail_second_set = True                                                  # the first setting is restored, the second is refused
    with pytest.raises(PolicyError):
        s.quiet.leave()
    assert [l["label"] for l in s.quiet.state()["limits"]] == ["Maximum processor state"]
    assert cfg.get(TURBO, BOOST) == (2, 2)
    cfg.fail_second_set = False
    s.quiet.leave()
    assert cfg.get(TURBO, MAX_STATE) == (100, 100) and not s.quiet.state()["active"]


# --- renders ------------------------------------------------------------------------------------------

def test_a_render_runs_with_the_original_settings_and_the_limits_return_after_it(lim):
    s, provider, cfg, _ = lim
    chrome_tabs(provider, 1)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "render")
    assert cfg.get(TURBO, BOOST) == (2, 2) and s.quiet.state()["state"] == "render"
    s.restore(include_power=False)
    assert cfg.get(TURBO, BOOST) == (0, 0) and s.quiet.state()["state"] == "quiet"
    s.quiet.leave()
    assert cfg.get(TURBO, BOOST) == (2, 2)


def test_a_focus_session_keeps_the_limits(lim):
    s, provider, cfg, _ = lim
    chrome_tabs(provider, 1)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "focus")
    assert cfg.get(TURBO, BOOST) == (0, 0)


def test_entering_in_the_middle_of_a_render_waits_for_it(lim):
    s, provider, cfg, _ = lim
    chrome_tabs(provider, 1)
    s.apply([{"group": "chrome"}], "render")
    s.quiet.enter()
    assert cfg.get(TURBO, BOOST) == (2, 2) and s.quiet.state()["state"] == "render"
    s.restore(include_power=False)
    assert cfg.get(TURBO, BOOST) == (0, 0)


def test_a_timed_ab_runs_both_phases_with_the_same_settings(lim):
    s, provider, cfg, _ = lim
    chrome_tabs(provider, 1)
    s.quiet.enter()
    seen = []
    s.timed._execute = lambda argv, cwd: (seen.append(cfg.get(TURBO, BOOST)[0]), (1.0, 0, None))[1]
    s.timed.start("render --fast", mode="ab")
    deadline = time.time() + 20
    while s.timed.running and time.time() < deadline:
        time.sleep(0.02)
    assert seen == [2, 2] and cfg.get(TURBO, BOOST)[0] == 0


# --- settings and journal ------------------------------------------------------------------------------

@pytest.mark.parametrize("change", [{"mode": "cap"}, {"mode": "both"}, {"cap": 29}, {"cap": 101}, {"cap": "70"}, {"cap": True},
                                    {"cap": 70.5}])
def test_unusable_method_or_maximum_state_is_rejected(lim, change):
    s, _, _, _ = lim
    before = s.quiet.state()
    with pytest.raises(PolicyError):
        s.quiet.configure(**change)
    assert (s.quiet.state()["mode"], s.quiet.state()["cap"]) == (before["mode"], before["cap"])


def test_choices_are_remembered_and_locked_while_quiet(lim, tmp_path):
    s, provider, cfg, _ = lim
    s.quiet.configure(cap=65, boost_off=False, keep_awake=False)
    again = Session(tmp_path, provider, "Windows", powercfg=cfg, wake=None).quiet.state()
    assert (again["mode"], again["cap"], again["boost_off"], again["keep_awake"]) == ("limit", 65, False, False)
    s.quiet.enter()
    for change in ({"cap": 50}, {"mode": "plan"}, {"boost_off": True}):
        with pytest.raises(PolicyError, match="Leave quiet mode"):
            s.quiet.configure(**change)
    s.quiet.configure(keep_awake=True, auto_enter=True)                         # these can change any time
    assert s.quiet.state()["keep_awake"] and s.quiet.state()["auto_enter"]


def test_old_and_new_journal_entries_are_recognised():
    legacy = {"before": TURBO, "before_name": "Turbo", "after": SILENT, "after_name": "Silent", "now": SILENT, "since": 1.0}
    change = {"alias": BOOST, "label": "Turbo boost", "before_ac": 2, "before_dc": 2, "after": 0, "now": 0}
    limit = {"mode": "limit", "scheme": TURBO, "scheme_name": "Turbo", "changes": [change], "since": 1.0}
    assert PowerGuard.valid_entry(legacy) and PowerGuard.valid_entry({**legacy, "mode": "plan"}) and PowerGuard.valid_entry(limit)
    for bad in (None, "x", {}, {"mode": "limit"}, {**limit, "changes": "x"}, {**limit, "changes": [{"alias": BOOST}]},
                {"mode": "limit", "scheme": TURBO}, {"before": TURBO}):
        assert not PowerGuard.valid_entry(bad)


def test_the_plan_method_still_works_next_to_the_new_one(lim):
    s, _, cfg, _ = lim
    s.quiet.configure(mode="plan")
    s.quiet.enter()
    assert cfg.current == SILENT and cfg.get(TURBO, BOOST) == (2, 2)
    s.quiet.leave()
    assert cfg.current == TURBO


# --- powercfg values --------------------------------------------------------------------------------------

class Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


ENGLISH_VALUE = """
Power Scheme GUID: 6fecc5ae-f350-48a5-b669-b472cb895ccf  (Turbo)
  Subgroup GUID: 54533251-82be-4824-96c1-47b60b740d00  (Processor power management)
    Power Setting GUID: be337238-0d82-4146-a960-4f3749d470c7  (Processor performance boost mode)
      Possible Setting Index: 000
      Possible Setting Friendly Name: Disabled
      Possible Setting Index: 002
      Possible Setting Friendly Name: Aggressive
    Current AC Power Setting Index: 0x00000002
    Current DC Power Setting Index: 0x00000000
"""
RUSSIAN_VALUE = ENGLISH_VALUE.replace("Current AC Power Setting Index", "Текущий индекс настройки питания от сети").replace(
    "Current DC Power Setting Index", "Текущий индекс настройки питания от батареи")


def test_a_setting_is_read_with_the_hidden_query_by_position_in_any_language():
    calls = []
    cfg = Powercfg(lambda a: (calls.append(a), Result(ENGLISH_VALUE))[1])
    assert cfg.value(TURBO, SUB, BOOST) == {"ac": 2, "dc": 0}
    assert calls == [["powercfg", "/qh", TURBO, SUB, BOOST]]                    # /qh: turbo boost is a hidden setting
    assert Powercfg(lambda a: Result(RUSSIAN_VALUE)).value(TURBO, SUB, BOOST) == {"ac": 2, "dc": 0}
    assert Powercfg(lambda a: Result("nothing here")).value(TURBO, SUB, BOOST) == {"ac": None, "dc": None}


def test_setting_a_value_runs_powercfg_directly_and_only_with_clean_arguments():
    calls = []
    cfg = Powercfg(lambda a: (calls.append(a), Result())[1])
    cfg.set_value(TURBO, SUB, BOOST, ac=0, dc=1)
    cfg.set_value(TURBO, SUB, MAX_STATE, ac=80)
    assert calls == [["powercfg", "/setacvalueindex", TURBO, SUB, BOOST, "0"],
                     ["powercfg", "/setdcvalueindex", TURBO, SUB, BOOST, "1"],
                     ["powercfg", "/setacvalueindex", TURBO, SUB, MAX_STATE, "80"]]
    calls.clear()
    for args in (("x", SUB, BOOST), (TURBO, "sub; calc", BOOST), (TURBO, SUB, "max value"), (TURBO, "", BOOST), (TURBO, SUB, None)):
        with pytest.raises(OSError, match="Invalid power setting"):
            cfg.set_value(*args, ac=1)
    for number in (-1, 100001, "70", 7.5, True):
        with pytest.raises(OSError, match="Invalid power setting value"):
            cfg.set_value(TURBO, SUB, BOOST, ac=number)
    assert calls == []


# --- keeping the laptop awake while work runs --------------------------------------------------------------

def test_work_keeps_the_laptop_awake_only_in_quiet_mode(lim):
    s, provider, _, wake = lim
    chrome_tabs(provider, 1)
    s.apply([{"group": "chrome"}], "focus")
    s.quiet.tick(now=1000.0)
    assert not wake.held                                       # work, but not in quiet mode
    s.restore(include_power=False)
    s.quiet.enter()
    s.quiet.tick(now=1001.0)
    assert not wake.held                                       # quiet mode, but nothing is running
    s.apply([{"group": "chrome"}], "focus")
    s.quiet.tick(now=1002.0)
    assert wake.held and s.quiet.state()["awake"] and wake.hold_calls == 1
    s.quiet.tick(now=1004.0)
    assert wake.hold_calls == 1                                # one request, not one per tick


@pytest.mark.parametrize("method", ["limit", "plan"])
def test_both_methods_keep_the_laptop_awake(lim, method):
    s, provider, _, wake = lim
    chrome_tabs(provider, 1)
    s.quiet.configure(mode=method)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "focus")
    s.quiet.tick(now=1000.0)
    assert wake.held


@pytest.mark.parametrize("signal", ["timed", "job", "spike", "session"])
def test_every_sign_of_work_counts(lim, signal):
    s, provider, _, wake = lim
    chrome_tabs(provider, 1)
    s.quiet.enter()
    if signal == "timed":
        s.timed.running = True
    elif signal == "job":
        s.autopilot.job = {"started": 1.0, "tools": set(), "auto_session": False, "last": 1.0}
    elif signal == "spike":
        s.recorder.current = object()
    else:
        s.apply([{"group": "chrome"}], "focus")
    assert s.work_in_progress()
    s.quiet.tick(now=1000.0)
    assert wake.held


def test_the_laptop_may_sleep_again_a_minute_after_the_work_ends(lim):
    s, provider, _, wake = lim
    chrome_tabs(provider, 1)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "focus")
    s.quiet.tick(now=1000.0)
    s.restore(include_power=False)
    s.quiet.tick(now=1030.0)
    assert wake.held                                           # inside the grace period
    s.quiet.tick(now=1061.0)
    assert not wake.held and wake.release_calls == 1
    assert any("may sleep again" in e["text"] for e in s.events)


def test_leaving_quiet_mode_lets_the_laptop_sleep_at_once(lim):
    s, provider, _, wake = lim
    chrome_tabs(provider, 1)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "focus")
    s.quiet.tick(now=1000.0)
    s.quiet.leave()
    assert not wake.held


def test_closing_the_app_releases_the_request(lim):
    s, provider, _, wake = lim
    chrome_tabs(provider, 1)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "focus")
    s.quiet.tick(now=1000.0)
    s.restore()
    assert not wake.held


def test_an_outside_change_ends_quiet_mode_and_the_request(lim):
    s, provider, cfg, wake = lim
    chrome_tabs(provider, 1)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "focus")
    s.quiet.tick(now=time.time())
    assert wake.held
    cfg.values[(TURBO, BOOST)]["ac"] = 1
    s.quiet.tick(now=time.time() + 11)
    assert not s.quiet.state()["active"] and not wake.held


def test_the_setting_can_be_turned_off_and_takes_effect_at_the_next_tick(lim):
    s, provider, _, wake = lim
    chrome_tabs(provider, 1)
    s.quiet.configure(keep_awake=False)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "focus")
    s.quiet.tick(now=1000.0)
    assert not wake.held
    s.quiet.configure(keep_awake=True)
    s.quiet.tick(now=1002.0)
    assert wake.held
    s.quiet.configure(keep_awake=False)
    s.quiet.tick(now=1004.0)
    assert not wake.held


def test_a_refused_request_is_reported_and_retried(lim):
    s, provider, _, wake = lim
    chrome_tabs(provider, 1)
    s.quiet.enter()
    s.apply([{"group": "chrome"}], "focus")
    wake.fail = True
    s.quiet.tick(now=1000.0)
    assert not wake.held and "Could not keep the laptop awake" in s.quiet.state()["error"]
    wake.fail = False
    s.quiet.tick(now=1002.0)
    assert wake.held and s.quiet.state()["error"] is None


def test_test_doubles_never_hold_a_real_request(tmp_path):
    s = Session(tmp_path, Provider(), "Windows", powercfg=LimitCfg())
    assert s.quiet.wake is None and s.quiet.state()["awake"] is False


@pytest.mark.skipif(os.name != "nt", reason="Windows power requests")
def test_the_real_power_request_can_be_held_and_cleared():
    request = winapi.WakeRequest("Load Manager test")
    request.hold()
    assert request.held and request.handle
    request.hold()                                             # idempotent
    request.release()
    assert not request.held
    request.hold()
    request.close()
    assert not request.held and request.handle is None


def test_a_lower_value_on_mains_is_kept_when_only_the_battery_value_needs_lowering(lim):
    s, _, cfg, _ = lim
    cfg.values[(TURBO, MAX_STATE)] = {"ac": 60, "dc": 100}
    s.quiet.configure(boost_off=False, cap=70)
    s.quiet.enter()
    assert cfg.get(TURBO, MAX_STATE) == (60, 70)
    s.quiet.leave()
    assert cfg.get(TURBO, MAX_STATE) == (60, 100)
