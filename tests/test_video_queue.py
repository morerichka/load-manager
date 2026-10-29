"""The queue of the Video page and what happens when everything is finished (sleep, hibernate, shut down). Real jobs run
as child processes against a stand-in ffmpeg; the computer is never really switched off: the power call is replaced."""
import json
import time

import pytest

from loadmanager import jobs as jobs_module, video
from loadmanager.engine import PolicyError, Session
from loadmanager.jobs import VideoJobs
from test_video_jobs import Box, make


@pytest.fixture
def box(tmp_path, monkeypatch):
    return Box(tmp_path, monkeypatch)


# ---------- the queue ----------

def test_buttons_pressed_while_a_job_runs_wait_their_turn_and_run_in_order(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="0.8")
    clip = box.clip()
    first = box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    second = box.jobs.submit("encode", box.encode_params(clip, "b.mp4"))
    third = box.jobs.submit("encode", box.encode_params(clip, "c.mp4"))
    assert first["queued"] is False and second == {"queued": True, "id": 1, "position": 1} and third["position"] == 2
    assert [i["title"] for i in box.jobs.queue_view()["items"]] == ["Encode clip.mov (fast)"] * 2
    assert box.jobs.brief()["queued"] == 2
    state = box.settle()
    assert state["queue"]["items"] == [] and [h["status"] for h in state["history"]] == ["done"] * 3
    assert all((box.work / "out" / n).is_file() for n in ("a.mp4", "b.mp4", "c.mp4"))
    started = [h["started"] for h in state["history"]][::-1]
    assert started == sorted(started), "each started after the one before"


def test_an_item_may_wait_for_a_file_that_an_earlier_item_makes(box, tmp_path):
    src = make(tmp_path / "cloudish" / "clip.mov", 4000)
    box.jobs.submit("fetch", {"sources": [str(src)], "project": "demo"})
    later = box.work / "demo" / "src" / "clip.mov"
    assert not later.exists()
    queued = box.jobs.submit("encode", box.encode_params(later, "o.mp4"))
    assert queued["queued"] is True, "the file does not exist yet, and that is fine for a queued item"
    state = box.settle()
    assert [h["status"] for h in state["history"]] == ["done", "done"] and (box.work / "out" / "o.mp4").is_file()


def test_a_queued_form_that_is_wrong_is_refused_straight_away(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="2")
    clip = box.clip()
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    try:
        for bad, message in (({"preset": "ultra"}, "Preset"), ({"output": "relative.mp4"}, "full path")):
            with pytest.raises(PolicyError, match=message):
                box.jobs.submit("encode", {**box.encode_params(clip, "b.mp4"), **bad})
        assert box.jobs.queue_view()["items"] == []
    finally:
        box.jobs.cancel()
        box.settle()


def test_an_item_whose_file_never_appears_fails_when_its_turn_comes_and_stops_the_queue(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="0.5")
    clip = box.clip()
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    box.jobs.submit("encode", box.encode_params(box.work / "nothing.mov", "b.mp4"))
    box.jobs.submit("encode", box.encode_params(clip, "c.mp4"))
    state = box.settle()
    assert [h["status"] for h in state["history"]][::-1] == ["done", "failed"]
    failed = state["history"][0]
    assert "does not exist" in failed["summary"] and failed["code"] is None
    assert state["queue"]["paused"] is True and state["queue"]["reason"] == "failed" and len(state["queue"]["items"]) == 1
    box.jobs.queue_pause(False)
    assert box.settle()["history"][0]["status"] == "done" and (box.work / "out" / "c.mp4").is_file()


def test_keep_going_carries_on_past_a_job_that_cannot_start_or_fails(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="0.4", FAKE_FAIL_ON="bad")
    good, bad = box.clip(name="good.mov"), box.clip(name="bad.mov")
    box.jobs.queue_configure(keep_going=True)
    box.jobs.submit("encode", box.encode_params(bad, "x.mp4"))
    box.jobs.submit("encode", box.encode_params(box.work / "gone.mov", "y.mp4"))
    box.jobs.submit("encode", box.encode_params(good, "z.mp4"))
    state = box.settle()
    assert [h["status"] for h in state["history"]][::-1] == ["failed", "failed", "done"]
    assert state["queue"]["paused"] is False and (box.work / "out" / "z.mp4").is_file()
    assert VideoJobs(box.session).keep_going is True, "the choice is remembered"


def test_a_failed_job_pauses_the_queue_by_default(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="0.4", FAKE_FAIL_ON="bad")
    good, bad = box.clip(name="good.mov"), box.clip(name="bad.mov")
    box.jobs.submit("encode", box.encode_params(bad, "x.mp4"))
    box.jobs.submit("encode", box.encode_params(good, "z.mp4"))
    state = box.settle()
    assert state["history"][0]["status"] == "failed" and state["queue"]["paused"] and state["queue"]["reason"] == "failed"
    assert not (box.work / "out" / "z.mp4").exists()
    box.jobs.queue_pause(False)
    assert box.settle()["history"][0]["status"] == "done"


def test_cancelling_pauses_the_queue_and_keeps_what_is_left(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    clip = box.clip()
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    box.jobs.submit("encode", box.encode_params(clip, "b.mp4"))
    assert box.until(lambda: box.jobs.state()["job"]["lines_total"] > 2)
    box.jobs.cancel()
    state = box.settle(30)
    assert state["history"][0]["status"] == "cancelled" and state["queue"]["paused"] and state["queue"]["reason"] == "cancelled"
    assert len(state["queue"]["items"]) == 1 and not box.jobs.running
    box.jobs.extra_env["FAKE_SLEEP"] = "0"
    box.jobs.queue_pause(False)
    assert box.until(lambda: not box.jobs.running and not box.jobs.queue)
    assert box.jobs.state()["history"][0]["status"] == "done"


def test_pausing_while_idle_lets_a_plan_build_up_and_resume_starts_it(box):
    clip = box.clip()
    box.jobs.queue_pause(True)
    answer = box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    box.jobs.submit("encode", box.encode_params(clip, "b.mp4"))
    assert answer["queued"] is True and not box.jobs.running and box.jobs.queue_view()["reason"] == "you"
    assert box.jobs.brief()["paused"] is True
    box.jobs.queue_pause(False)
    state = box.settle()
    assert [h["status"] for h in state["history"]] == ["done", "done"] and state["queue"]["paused"] is False


def test_things_that_cannot_wait_run_at_once_or_are_refused_not_queued(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="2")
    clip = box.clip()
    box.jobs.queue_pause(True)
    assert box.jobs.submit("doctor", {})["queued"] is False, "nothing runs, so a health check may start although the queue is paused"
    box.finish()
    box.jobs.queue_pause(False)
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    try:
        with pytest.raises(PolicyError, match="still running"):
            box.jobs.submit("doctor", {})
        with pytest.raises(PolicyError, match="still running"):
            box.jobs.submit("clean", {"project": "demo", "src": True, "yes": True})
        assert box.jobs.queue_view()["items"] == []
    finally:
        box.jobs.cancel()
        box.settle()


def test_items_can_be_moved_removed_and_cleared(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    clip = box.clip()
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    ids = [box.jobs.submit("encode", box.encode_params(clip, f"{n}.mp4"))["id"] for n in "bcd"]
    try:
        order = lambda: [i["id"] for i in box.jobs.queue_view()["items"]]
        assert order() == ids
        box.jobs.queue_move(ids[2], -1)
        assert order() == [ids[0], ids[2], ids[1]]
        box.jobs.queue_move(ids[0], -1)
        box.jobs.queue_move(ids[1], 1)
        assert order() == [ids[0], ids[2], ids[1]], "moving past either end changes nothing"
        with pytest.raises(PolicyError, match="no longer"):
            box.jobs.queue_move(999, 1)
        box.jobs.queue_remove(ids[2])
        assert order() == [ids[0], ids[1]]
        assert json.loads((box.tmp / "state" / "history.json").read_text())["video_queue"][0]["kind"] == "encode"
        box.jobs.queue_clear()
        assert order() == [] and json.loads((box.tmp / "state" / "history.json").read_text())["video_queue"] == []
    finally:
        box.jobs.cancel()
        box.settle(30)


def test_the_queue_has_a_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "QUEUE_LIMIT", 2)
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    clip = box.clip()
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    box.jobs.submit("encode", box.encode_params(clip, "b.mp4"))
    box.jobs.submit("encode", box.encode_params(clip, "c.mp4"))
    try:
        with pytest.raises(PolicyError, match="queue is full"):
            box.jobs.submit("encode", box.encode_params(clip, "d.mp4"))
    finally:
        box.jobs.cancel()
        box.settle(30)


def test_a_waiting_queue_survives_a_restart_but_does_not_start_by_itself(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    clip = box.clip()
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    box.jobs.submit("encode", box.encode_params(clip, "b.mp4"))
    box.jobs.cancel()
    box.settle(30)
    again = VideoJobs(box.session)
    again.extra_env = dict(box.jobs.extra_env, FAKE_SLEEP="0")
    view = again.queue_view()
    assert len(view["items"]) == 1 and view["paused"] is True and view["reason"] == "restored" and not again.running
    again.queue_pause(False)
    assert box.until(lambda: not again.running and not again.queue)
    assert again.state()["history"][0]["status"] == "done" and (box.work / "out" / "b.mp4").is_file()


def test_a_damaged_saved_queue_is_ignored_item_by_item(tmp_path):
    (tmp_path / "state").mkdir()
    good = {"kind": "fetch", "params": {"sources": ["x"]}, "title": "t", "command": "lm fetch"}
    (tmp_path / "state" / "history.json").write_text(json.dumps({"video_queue": [
        good, {"kind": "clean", "params": {}, "title": "t", "command": "c"}, {"kind": "fetch", "params": "no", "title": "t", "command": "c"},
        {"kind": "fetch", "params": {}, "title": 5, "command": "c"}, "junk", None]}))
    jobs = Session(tmp_path / "state", powercfg=None, wake=None).video
    assert [i["kind"] for i in jobs.queue] == ["fetch"] and jobs.paused is True
    (tmp_path / "state" / "history.json").write_text(json.dumps({"video_queue": "nonsense"}))
    assert Session(tmp_path / "state", powercfg=None, wake=None).video.queue == []


def test_when_the_window_closes_nothing_new_starts(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    clip = box.clip()
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    box.jobs.submit("encode", box.encode_params(clip, "b.mp4"))
    assert box.until(lambda: box.jobs.state()["job"]["lines_total"] > 2)
    box.jobs.shutdown()
    assert not box.jobs.running and box.jobs.state()["history"][0]["status"] == "cancelled"
    assert box.jobs.closing and len(box.jobs.queue) == 1, "the second item did not start"


def test_pressing_a_button_during_the_hand_over_never_loses_or_doubles_an_item(tmp_path, monkeypatch):
    """The gap between one job ending and the next starting is closed: submissions in it are queued, not started twice."""
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="0.3")
    clip = box.clip()
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    answers = []
    for n in range(12):
        answers.append(box.jobs.submit("encode", box.encode_params(clip, f"q{n}.mp4")))
        time.sleep(0.07)
    state = box.settle(120)
    assert not box.jobs.queue and [h for h in state["history"] if h["status"] != "done"] == [], "nothing failed to start because of a gap"
    assert box.jobs.counter == 13, "every item started exactly once"
    made = sorted(p.name for p in (box.work / "out").glob("*.mp4"))
    assert made == sorted(["a.mp4"] + [f"q{n}.mp4" for n in range(12)]), "no item was lost"
    assert [a["queued"] for a in answers].count(False) <= 1, "at most one of them found the computer idle and started at once"


# ---------- what happens when everything is finished ----------

def wait_for_power(box, limit=20):
    assert box.until(lambda: box.powered, limit), "the computer was never told to switch off"


def test_the_computer_goes_to_sleep_after_the_last_job_with_a_warning_first(box):
    box.jobs.after_delay = 1.0
    box.jobs.queue_configure(after="sleep")
    box.run("setup")
    view = box.jobs.queue_view()
    assert view["countdown"]["action"] == "sleep" and 0 < view["countdown"]["seconds"] <= 1.0 and box.powered == []
    assert box.notified and "go to sleep in 1 seconds" in box.notified[0][1]
    assert any("will go to sleep" in e["text"] for e in box.session.events)
    wait_for_power(box)
    assert box.powered == ["sleep"] and box.jobs.queue_view()["after"] == "nothing" and box.jobs.queue_view()["countdown"] is None
    time.sleep(0.5)
    assert box.powered == ["sleep"], "once only"


@pytest.mark.parametrize("action", ["hibernate", "shutdown"])
def test_the_other_actions_work_the_same_way(box, action):
    box.jobs.queue_configure(after=action)
    box.run("setup")
    wait_for_power(box)
    assert box.powered == [action]


def test_it_waits_for_the_whole_queue_not_just_the_first_job(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="0.6")
    clip = box.clip()
    box.jobs.queue_configure(after="sleep")
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    box.jobs.submit("encode", box.encode_params(clip, "b.mp4"))
    assert box.until(lambda: len(box.jobs.state()["history"]) == 1 and box.jobs.running)
    assert box.powered == [] and box.jobs.queue_view()["countdown"] is None, "the second job is still to come"
    wait_for_power(box)
    assert [h["status"] for h in box.jobs.state()["history"]] == ["done", "done"]


def test_a_job_you_cancelled_never_puts_the_computer_to_sleep_and_the_choice_stays(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    clip = box.clip()
    box.jobs.queue_configure(after="sleep")
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    assert box.until(lambda: box.jobs.state()["job"]["lines_total"] > 2)
    box.jobs.cancel()
    box.settle(30)
    time.sleep(1.0)
    assert box.powered == [] and box.jobs.queue_view()["countdown"] is None and box.jobs.queue_view()["after"] == "sleep"


def test_a_failure_that_stops_the_queue_counts_as_finished(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="0.4", FAKE_FAIL_ON="bad")
    good, bad = box.clip(name="good.mov"), box.clip(name="bad.mov")
    box.jobs.queue_configure(after="shutdown")
    box.jobs.submit("encode", box.encode_params(bad, "x.mp4"))
    box.jobs.submit("encode", box.encode_params(good, "z.mp4"))
    wait_for_power(box)
    assert box.powered == ["shutdown"] and box.jobs.queue_view()["paused"] and len(box.jobs.queue) == 1


def test_a_cancelled_item_that_leaves_the_queue_paused_does_not_count_as_finished(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    clip = box.clip()
    box.jobs.queue_configure(after="sleep")
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    box.jobs.submit("encode", box.encode_params(clip, "b.mp4"))
    assert box.until(lambda: box.jobs.state()["job"]["lines_total"] > 2)
    box.jobs.cancel()
    box.settle(30)
    time.sleep(1.0)
    assert box.powered == [] and box.jobs.queue_view()["countdown"] is None


def test_starting_new_work_stops_the_countdown_but_keeps_the_choice(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="0.5")
    clip = box.clip()
    box.jobs.after_delay = 2.0
    box.jobs.queue_configure(after="sleep")
    box.run("setup")
    assert box.jobs.queue_view()["countdown"] is not None
    box.jobs.submit("encode", box.encode_params(clip, "a.mp4"))
    assert box.jobs.queue_view()["countdown"] is None and box.jobs.queue_view()["after"] == "sleep"
    box.finish()
    assert box.jobs.queue_view()["countdown"] is not None, "after the new job it counts down again"
    time.sleep(2.0 - 0.3)
    assert box.powered == [], "the first countdown was cancelled"
    wait_for_power(box)
    assert box.powered == ["sleep"]


def test_cancelling_the_countdown_forgets_the_action(box):
    box.jobs.after_delay = 1.0
    box.jobs.queue_configure(after="hibernate")
    box.run("setup")
    view = box.jobs.cancel_countdown()
    assert view["countdown"] is None and view["after"] == "nothing"
    time.sleep(1.4)
    assert box.powered == []
    assert any("countdown was cancelled" in e["text"] for e in box.session.events)
    assert box.jobs.cancel_countdown()["after"] == "nothing", "a second cancel is harmless"


def test_choosing_nothing_while_counting_down_cancels_it_too(box):
    box.jobs.after_delay = 1.0
    box.jobs.queue_configure(after="sleep")
    box.run("setup")
    assert box.jobs.queue_configure(after="nothing")["countdown"] is None
    time.sleep(1.4)
    assert box.powered == []


def test_a_refusal_by_the_computer_is_shown_not_raised(box):
    def refuse(action):
        raise PolicyError("Could not hibernate: Hibernation is not enabled.")
    box.jobs.power = refuse
    box.jobs.queue_configure(after="hibernate")
    box.run("setup")
    assert box.until(lambda: box.jobs.queue_view()["error"])
    view = box.jobs.queue_view()
    assert "Hibernation is not enabled" in view["error"] and view["after"] == "nothing"
    assert any("could not hibernate" in e["text"] for e in box.session.events)
    box.jobs.queue_configure(after="sleep")
    assert box.jobs.queue_view()["error"] is None, "choosing again clears the old message"


def test_arming_it_while_idle_does_nothing_until_a_job_finishes(box):
    box.jobs.queue_configure(after="sleep")
    time.sleep(0.8)
    assert box.powered == [] and box.jobs.queue_view()["countdown"] is None and box.jobs.brief()["after"] == "sleep"


def test_the_choices_depend_on_the_computer_and_are_checked(box, monkeypatch):
    with pytest.raises(PolicyError, match="cannot do that"):
        box.jobs.queue_configure(after="reboot")
    monkeypatch.setattr(video, "system", lambda: "Darwin")
    assert box.jobs.queue_view()["after_choices"] == ["nothing", "sleep", "shutdown"]
    with pytest.raises(PolicyError, match="cannot do that"):
        box.jobs.queue_configure(after="hibernate")
    monkeypatch.setattr(video, "system", lambda: "Windows")
    assert box.jobs.queue_view()["after_choices"] == ["nothing", "sleep", "hibernate", "shutdown"]


# ---------- the real power calls, with the system calls replaced ----------

class Ran:
    def __init__(self, code=0, err=""):
        self.calls, self.code, self.err = [], code, err

    def __call__(self, command, **kw):
        self.calls.append(command)
        return type("Done", (), {"returncode": self.code, "stderr": self.err, "stdout": ""})()


@pytest.mark.parametrize("system,action,expected", [
    ("Windows", "hibernate", ["shutdown", "/h"]),
    ("Windows", "shutdown", ["shutdown", "/s", "/t", "10"]),
    ("Darwin", "sleep", ["pmset", "sleepnow"]),
    ("Darwin", "shutdown", ["osascript", "-e", 'tell application "System Events" to shut down']),
    ("Linux", "sleep", ["systemctl", "suspend"]),
    ("Linux", "hibernate", ["systemctl", "hibernate"]),
    ("Linux", "shutdown", ["systemctl", "poweroff"]),
])
def test_each_action_is_the_right_system_command(system, action, expected):
    ran = Ran()
    jobs_module.run_power_action(action, system, ran)
    assert ran.calls == [expected]


def test_windows_sleep_uses_the_suspend_call_not_a_command():
    ran, slept = Ran(), []
    jobs_module.run_power_action("sleep", "Windows", ran, suspend=lambda: slept.append(1))
    assert slept == [1] and ran.calls == []

    def refuse():
        raise OSError("not allowed")
    with pytest.raises(PolicyError, match="Windows refused to sleep: not allowed"):
        jobs_module.run_power_action("sleep", "Windows", ran, suspend=refuse)


def test_a_command_that_fails_says_why():
    with pytest.raises(PolicyError, match="Could not hibernate: Hibernation has not been enabled"):
        jobs_module.run_power_action("hibernate", "Windows", Ran(1, "Hibernation has not been enabled.\nmore"))
    with pytest.raises(PolicyError, match="Could not sleep: exit code 5"):
        jobs_module.run_power_action("sleep", "Linux", Ran(5))

    def missing(command, **kw):
        raise FileNotFoundError("systemctl")
    with pytest.raises(PolicyError, match="Could not shutdown"):
        jobs_module.run_power_action("shutdown", "Linux", missing)


def test_actions_the_system_cannot_do_are_refused():
    for system, action in (("Darwin", "hibernate"), ("Windows", "nothing"), ("Windows", "reboot")):
        with pytest.raises(PolicyError, match="cannot"):
            jobs_module.run_power_action(action, system, Ran())


def test_a_test_can_never_put_the_real_computer_to_sleep(monkeypatch):
    touched = []                                    # even if the guard were broken, nothing real would be called here
    monkeypatch.setattr(jobs_module.winapi, "suspend", lambda: touched.append("suspend"))
    monkeypatch.setattr(jobs_module.subprocess, "run", lambda *a, **k: touched.append(a))
    for action in ("sleep", "shutdown", "hibernate"):
        with pytest.raises(PolicyError, match="Refusing"):
            jobs_module.run_power_action(action, "Windows")
    assert touched == []


def test_the_sleep_call_exists_on_every_system_and_is_not_called_here():
    from loadmanager import winapi
    assert callable(winapi.suspend)


# ---------- through the window bridge ----------

def bridge(box):
    from loadmanager.app import Bridge
    from test_video_bridge import Stub
    return Bridge(box.session, Stub(), Stub())


def test_the_bridge_answers_for_every_queue_call_with_the_queue_in_the_contract_shape(tmp_path, monkeypatch):
    from test_video_bridge import CONTRACT, same_shape
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="1.5")
    api, clip = bridge(box), box.clip()
    now = api.video_start("encode", box.encode_params(clip, "a.mp4"))
    queued = api.video_start("encode", box.encode_params(clip, "b.mp4"))
    same_shape(now, CONTRACT["submit_now"], "submit_now")
    same_shape(queued, CONTRACT["submit_queued"], "submit_queued")
    assert now["queued"] is False and queued["queued"] is True and queued["position"] == 1
    same_shape(api.video_state(), CONTRACT["state_queue"], "state_queue")
    paused = api.video_queue_pause(True)
    same_shape(paused, CONTRACT["queue_pause"], "queue_pause")
    assert paused["queue"]["paused"] is True and paused["queue"]["reason"] == "you"
    assert api.video_queue_pause(False)["queue"]["paused"] is False
    configured = api.video_queue_configure(True, "sleep")
    same_shape(configured, CONTRACT["queue_configure"], "queue_configure")
    assert configured["queue"]["keep_going"] is True and configured["queue"]["after"] == "sleep"
    item = api.video_state()["queue"]["items"]
    assert len(item) <= 1
    box.settle(60)
    box.until(lambda: box.jobs.queue_view()["countdown"], 20)
    same_shape(api.video_state(), CONTRACT["state_countdown"], "state_countdown")
    same_shape(api.video_cancel_countdown(), CONTRACT["countdown_cancel"], "countdown_cancel")
    assert box.powered == []


def test_the_bridge_moves_removes_and_clears_and_explains_mistakes(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    api, clip = bridge(box), box.clip()
    api.video_start("encode", box.encode_params(clip, "a.mp4"))
    ids = [api.video_start("encode", box.encode_params(clip, f"{n}.mp4"))["id"] for n in "bc"]
    try:
        moved = api.video_queue_move(ids[1], -1)
        assert moved["ok"] and [i["id"] for i in moved["queue"]["items"]] == [ids[1], ids[0]]
        assert api.video_queue_move(999, 1)["ok"] is False
        assert [i["id"] for i in api.video_queue_remove(ids[1])["queue"]["items"]] == [ids[0]]
        assert api.video_queue_clear()["queue"]["items"] == []
        bad = api.video_queue_configure(None, "reboot")
        assert bad["ok"] is False and "cannot do that" in bad["error"]
        assert api.video_start("encode", {"input": "x", "output": "y"})["ok"] is False
    finally:
        box.jobs.cancel()
        box.settle(30)
