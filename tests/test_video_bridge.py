"""The calls the Video page makes through the window bridge: answers shaped like the page expects, errors as text,
the file dialogs, and the closing guard. tests/video_contract.json holds the same shapes for the page's own tests."""
import enum
import json
import sys
import types
from pathlib import Path

import pytest

from loadmanager import app as app_module
from loadmanager.app import Bridge
from loadmanager.engine import Monitor
from test_video_jobs import Box, make

CONTRACT = json.loads(Path(__file__).with_name("video_contract.json").read_text(encoding="utf-8"))


class Stub:
    def get(self):
        return {}

    def poll(self):
        return None


class Dialog(enum.IntEnum):                      # what pywebview calls FileDialog; the tests do not need pywebview itself
    OPEN, FOLDER, SAVE = 10, 20, 30


@pytest.fixture(autouse=True)
def fake_webview(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(FileDialog=Dialog))


@pytest.fixture
def box(tmp_path, monkeypatch):
    return Box(tmp_path, monkeypatch)


@pytest.fixture
def api(box):
    return Bridge(box.session, Stub(), Stub())


def same_shape(real, expected, where="response"):
    """Same keys everywhere (values and None-versus-value differences are fine)."""
    if isinstance(real, dict) and isinstance(expected, dict):
        assert set(real) == set(expected), f"{where}: {sorted(set(real) ^ set(expected))}"
        for key in real:
            same_shape(real[key], expected[key], f"{where}.{key}")
    elif isinstance(real, list) and isinstance(expected, list) and real and expected:
        same_shape(real[0], expected[0], where + "[0]")


def finish(box):
    assert box.until(lambda: not box.jobs.running)


# ---------- the shapes the page relies on ----------

def test_the_answers_have_the_shapes_in_the_contract(box, api, tmp_path):
    same_shape(api.video_state(), CONTRACT["state_idle"], "state_idle")
    src = make(tmp_path / "cloudish" / "a.mov", 4000)
    assert api.video_start("fetch", {"sources": [str(src)], "project": "demo"})["ok"]
    finish(box)
    same_shape(api.video_state(), CONTRACT["state_job"], "state_job")
    assert api.video_start("clean", {"project": "demo", "src": True})["ok"]
    finish(box)
    same_shape(api.video_state(), CONTRACT["state_clean"], "state_clean")
    assert api.video_start("doctor", {})["ok"]
    finish(box)
    same_shape(api.video_state(), CONTRACT["state_doctor"], "state_doctor")
    same_shape(api.video_overview(), CONTRACT["overview"], "overview")
    same_shape(api.video_kit(), CONTRACT["kit"], "kit")
    same_shape(api.video_configure({}), CONTRACT["settings"], "settings")
    same_shape(api.video_inspect(str(src)), CONTRACT["inspect"], "inspect")
    same_shape(api.video_suggest(str(box.work / "demo" / "src" / "a.mov"), "fast", "h264"), CONTRACT["suggest"], "suggest")
    same_shape(box.jobs.brief(), CONTRACT["brief"], "brief")


def test_the_snapshot_carries_a_short_video_summary(box):
    class Quick(Monitor):
        def __init__(self, session):
            self.session = session
            import threading
            self.lock = threading.Lock()
            self.snapshot = None

    result = Quick(box.session).get()
    assert set(result["video"]) == set(CONTRACT["brief"]) and result["video"]["running"] is False


def test_every_answer_says_ok_and_every_failure_says_why(box, api, tmp_path):
    assert api.video_state()["ok"] is True
    bad = api.video_start("fetch", {"sources": ["relative"]})
    assert bad["ok"] is False and "full path" in bad["error"]
    assert api.video_start("nonsense", {})["ok"] is False and api.video_start("doctor", None)["ok"] is True
    finish(box)
    assert api.video_configure({"hw": "gpu9000"})["ok"] is False
    assert api.video_inspect(str(tmp_path / "gone"))["ok"] is False
    assert api.video_suggest("relative.mov", "fast", "h264")["ok"] is False
    assert api.video_reveal("elsewhere")["ok"] is False


def test_cancel_answers_whether_there_was_anything_to_cancel(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="20")
    api = Bridge(box.session, Stub(), Stub())
    assert api.video_cancel() == {"ok": True, "cancelled": False}
    clip = box.clip()
    assert api.video_start("encode", {"input": str(clip), "output": str(box.work / "o.mp4"), "sw": True})["ok"]
    assert api.video_start("doctor", {})["ok"] is False, "a second job is refused with a reason"
    assert api.video_cancel() == {"ok": True, "cancelled": True}
    finish(box)
    assert api.video_state()["job"]["status"] == "cancelled"


def test_the_state_call_leaves_out_lines_the_page_already_has(box, api, tmp_path):
    api.video_start("setup", {})
    finish(box)
    job = api.video_state()["job"]
    assert job["lines"] and api.video_state([job["id"], job["lines_total"]])["job"]["lines"] is None


def test_the_work_folder_can_be_shown(box, api, monkeypatch):
    shown = []
    monkeypatch.setattr("loadmanager.jobs.subprocess.Popen", lambda cmd, **kw: shown.append(cmd))
    make(box.work / "p" / "out" / "x.mp4")
    assert api.video_reveal("work") == {"ok": True, "shown": str(box.work)} and len(shown) == 1


# ---------- file dialogs ----------

class FakeWindow:
    def __init__(self, result):
        self.result, self.calls = result, []

    def create_file_dialog(self, kind, **kw):
        self.calls.append((kind, kw))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_pick_without_a_window_says_to_type_the_path(api):
    assert api.pick("videos")["ok"] is False and "Type the path" in api.pick("videos")["error"]


def test_each_dialog_kind_asks_for_the_right_thing(api, tmp_path):
    kinds = Dialog
    window = api._window = FakeWindow(("C:\\a.mov", "C:\\b.mov"))
    assert api.pick("videos") == {"ok": True, "paths": ["C:\\a.mov", "C:\\b.mov"]}
    kind, kw = window.calls[-1]
    assert kind == kinds.OPEN and kw["allow_multiple"] is True and "*.mov" in kw["file_types"][0] and kw["directory"] == ""
    api.pick("files", str(tmp_path))
    kind, kw = window.calls[-1]
    assert kind == kinds.OPEN and kw["allow_multiple"] is True and kw["directory"] == str(tmp_path) and "file_types" not in kw
    window.result = [str(tmp_path)]
    assert api.pick("folder")["paths"] == [str(tmp_path)] and window.calls[-1][0] == kinds.FOLDER
    window.result = str(tmp_path / "out.mp4")
    out = api.pick("save", str(tmp_path / "out.mp4"))
    assert out["paths"] == [str(tmp_path / "out.mp4")]
    kind, kw = window.calls[-1]
    assert kind == kinds.SAVE and kw["save_filename"] == "out.mp4" and kw["directory"] == str(tmp_path)
    api.pick("program")
    kind, kw = window.calls[-1]
    assert kind == kinds.OPEN and "allow_multiple" not in kw


def test_a_cancelled_dialog_gives_no_paths_and_a_broken_one_an_error(api):
    window = api._window = FakeWindow(None)
    assert api.pick("folder") == {"ok": True, "paths": []}
    window.result = RuntimeError("no display")
    answer = api.pick("folder")
    assert answer["ok"] is False and "no display" in answer["error"]
    assert api.pick("teapot")["ok"] is False


def test_the_start_folder_may_be_a_file_a_folder_or_nonsense(api, tmp_path):
    window = api._window = FakeWindow(())
    clip = make(tmp_path / "a.mov")
    api.pick("files", str(clip))
    assert window.calls[-1][1]["directory"] == str(tmp_path)
    api.pick("files", str(tmp_path / "nope" / "x.mov"))
    assert window.calls[-1][1]["directory"] == ""


def test_the_window_attribute_is_private_so_the_bridge_does_not_expose_it(api):
    api._window = object()
    public = [name for name in dir(api) if not name.startswith("_")]
    assert "window" not in public and {"video_start", "video_state", "pick", "video_close_guard"} <= set(public)


# ---------- closing ----------

def test_the_window_closing_hook_follows_the_guard(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    api = Bridge(box.session, Stub(), Stub())
    assert api.video_close_guard() is True
    clip = box.clip()
    api.video_start("encode", {"input": str(clip), "output": str(box.work / "o.mp4"), "sw": True})
    assert box.until(lambda: box.jobs.state()["job"]["lines_total"] > 2)
    assert api.video_close_guard() is False
    assert api.video_close_guard() is True and not box.jobs.running


class Api:
    """What the closing hook talks to."""
    def __init__(self, allow=True, restored=True):
        self.allow, self.restored, self.calls = allow, restored, []

    def video_close_guard(self):
        self.calls.append("guard")
        return self.allow

    def timed_cancel(self):
        self.calls.append("timed")

    def restore(self, include_power=False):
        self.calls.append(("restore", include_power))
        return {"ok": self.restored}


def test_the_closing_hook_holds_the_window_back_for_a_job_or_a_failed_restore():
    held = Api(allow=False)
    assert app_module.closing_handler(held)() is False and held.calls == ["guard"], "nothing is restored while the close is held back"
    fine = Api()
    assert app_module.closing_handler(fine)() is None and fine.calls == ["guard", "timed", ("restore", True)]
    failed = Api(restored=False)
    assert app_module.closing_handler(failed)() is False


def test_the_packaged_entry_point_answers_lm_commands(tmp_path):
    import subprocess
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, str(root / "run.py"), "version"], capture_output=True, text=True, timeout=60,
                         env={**__import__("os").environ, "PYTHONPATH": str(root)})
    assert out.returncode == 0 and out.stdout.startswith("Load Manager 0.")
