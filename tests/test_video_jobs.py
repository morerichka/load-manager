"""The Video page's backend: form values to command lines, settings, and real jobs (child processes running the real
`lm` code against a stand-in ffmpeg), including progress, cancelling, closing the window and the failure paths."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loadmanager import jobs as jobs_module, video
from loadmanager.engine import PolicyError, Session
from loadmanager.jobs import VideoJobs, build, split_command

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).with_name("fake_ffmpeg.py")


def make(path, size=2000):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


class Box:
    """A session in a temporary folder whose jobs run `lm` against the stand-in ffmpeg."""

    def __init__(self, tmp_path, monkeypatch, **fake):
        for name in list(os.environ):
            if name.startswith(("LM_", "VK_", "FAKE_")):
                monkeypatch.delenv(name)
        monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / ".claude"))
        self.tmp, self.work = tmp_path, tmp_path / "VideoWork"
        self.session = Session(tmp_path / "state", powercfg=None, wake=None)
        self.jobs = self.session.video
        self.jobs.configure({"work": str(self.work), "min_free_gb": 0})
        self.jobs.extra_env = {"PYTHONPATH": str(ROOT), "LM_FFMPEG": f'"{sys.executable}" "{FAKE}"', "LM_NOTIFY": "0",
                               "LM_RETRY_DELAY": "0", "CLAUDE_DIR": str(tmp_path / ".claude"), **fake}
        self.powered, self.notified = [], []                # the computer is never really put to sleep by a test
        self.jobs.power = lambda action: self.powered.append(action)
        self.jobs.notify = lambda *a: self.notified.append(a)
        self.jobs.after_delay = 0.4

    def until(self, check, limit=60):
        end = time.time() + limit
        while time.time() < end:
            if check():
                return True
            time.sleep(0.05)
        return False

    def settle(self, limit=90):
        """Nothing runs and nothing will start by itself (the queue is empty or paused)."""
        assert self.until(lambda: not self.jobs.running and (not self.jobs.queue or self.jobs.paused), limit), "the queue did not settle"
        return self.jobs.state()

    def encode_params(self, clip, name, **more):
        return {"input": str(clip), "output": str(self.work / "out" / name), "sw": True, **more}

    def finish(self, limit=90):
        assert self.until(lambda: not self.jobs.running, limit), "the job did not end"
        return self.jobs.state()["job"]

    def run(self, kind, **params):
        self.jobs.start(kind, params)
        return self.finish()

    def clip(self, project="demo", name="clip.mov", size=5000):
        return make(self.work / project / "src" / name, size)


@pytest.fixture
def box(tmp_path, monkeypatch):
    return Box(tmp_path, monkeypatch)


# ---------- form values -> command line ----------

def argv(kind, **params):
    return build(kind, params)[0]


def test_every_kind_builds_the_command_the_cli_expects(tmp_path):
    clip, folder, dest = make(tmp_path / "a.mov"), (tmp_path / "f").resolve(), tmp_path / "dest"
    folder.mkdir()
    dest.mkdir()
    assert argv("status") == ["status"] and argv("setup") == ["setup"] and argv("doctor") == ["doctor"]
    assert argv("uninstall_claude") == ["uninstall-claude"]
    assert argv("install_claude")[:2] == ["install-claude", "--python"]
    assert argv("fetch", sources=[str(clip), str(folder)], project="wed", verify=True) == \
        ["fetch", "--project", "wed", "--verify", "--", str(clip), str(folder)]
    assert argv("fetch", sources=[str(clip)], dry_run=True) == ["fetch", "--dry-run", "--", str(clip)]
    assert argv("publish", items=[str(clip)], dest=str(dest), force=True) == ["publish", "--force", "--", str(clip), str(dest)]
    out = str(tmp_path / "o.mp4")
    assert argv("encode", input=str(clip), output=out, preset="final", codec="hevc", bitrate="8M", sw=True, low=True,
                force=True, no_hwdecode=True, extra='-t 30 -metadata title="a b"') == \
        ["encode", str(clip), out, "--preset", "final", "--codec", "hevc", "--bitrate", "8M", "--sw", "--no-hwdecode", "--low",
         "--force", "--", "-t", "30", "-metadata", "title=a b"]
    assert argv("encode", input=str(clip), output=out) == ["encode", str(clip), out, "--preset", "fast", "--codec", "h264"]
    assert argv("proxies", project="wed", preset="fast", low=True) == ["proxies", "wed", "--preset", "fast", "--low"]
    assert argv("proxies") == ["proxies", "--preset", "proxy"]
    assert argv("run", command="python -c pass", name="job", low=True, no_notify=True) == \
        ["run", "--name", "job", "--low", "--no-notify", "--", "python", "-c", "pass"]
    assert argv("clean", project="wed", src=True, proxy=True) == ["clean", "wed", "--src", "--proxy"]
    assert argv("clean", project="wed", proxy=True, yes=True) == ["clean", "wed", "--proxy", "--yes"]


def test_titles_say_what_will_happen(tmp_path):
    clip = make(tmp_path / "a.mov")
    out = str(tmp_path / "o.mp4")
    assert build("fetch", {"sources": [str(clip)]})[1] == "Copy a.mov from the cloud"
    assert build("fetch", {"sources": [str(clip), str(clip)], "dry_run": True})[1] == "Check the plan for a.mov and 1 more from the cloud"
    assert build("encode", {"input": str(clip), "output": out, "preset": "proxy"})[1] == "Encode a.mov (proxy)"
    assert build("clean", {"project": "w", "src": True})[1] == "Preview what to delete for w"
    assert build("clean", {"project": "w", "src": True, "yes": True})[1] == "Delete files for w"
    assert build("proxies", {})[1] == "Make proxies"
    assert build("encode", {"input": str(clip), "output": out})[2]["output"] == out


@pytest.mark.parametrize("kind,params,message", [
    ("nope", {}, "Unknown command"),
    ("fetch", {}, "choose at least one"),
    ("fetch", {"sources": ["relative/path"]}, "use the full path"),
    ("fetch", {"sources": [str(Path.home() / "no-such-thing-xyz")]}, "not found"),
    ("fetch", {"sources": "a\nb"}, "use the full path"),
    ("fetch", {"sources": [str(Path.home())], "project": "a/b"}, "invalid project name"),
    ("fetch", {"sources": [str(Path.home())], "project": "x" * 90}, "single line"),
    ("encode", {"input": "-rf", "output": "x"}, "use the full path"),
    ("encode", {"input": "", "output": "x"}, "choose a file"),
    ("publish", {"items": [str(Path.home())], "dest": str(Path.home() / "no-such-xyz")}, "does not exist"),
    ("clean", {"project": "w"}, "Choose what to clean"),
    ("clean", {"src": True}, "Project: this is required"),
    ("run", {"command": "   "}, "this is required"),
    ("run", {"command": 'echo "unterminated'}, "Could not read the command"),
    ("run", {"command": "x\ny"}, "single line"),
])
def test_wrong_form_values_are_explained_not_run(kind, params, message):
    with pytest.raises(PolicyError, match=message):
        build(kind, params)


def test_encode_values_are_checked(tmp_path):
    clip = make(tmp_path / "a.mov")
    ok = {"input": str(clip), "output": str(tmp_path / "o.mp4")}
    for bad, message in (({"preset": "ultra"}, "Preset"), ({"codec": "av1"}, "Codec"), ({"bitrate": "fast"}, "Bitrate"),
                         ({"bitrate": "1;rm"}, "Bitrate"), ({"output": str(clip)}, "overwrite the input"),
                         ({"extra": '-vf "x'}, "Extra ffmpeg arguments"), ({"input": str(tmp_path / "gone.mov")}, "does not exist"),
                         ({"input": str(tmp_path)}, "does not exist")):
        with pytest.raises(PolicyError, match=message):
            build("encode", {**ok, **bad})
    assert "--bitrate" not in argv("encode", **ok)


def test_a_path_can_never_look_like_an_option(tmp_path):
    clip = make(tmp_path / "-weird name.mov")
    argv_ = argv("fetch", sources=[str(clip)])
    assert argv_[-2] == "--" and argv_[-1] == str(clip) and not argv_[-1].startswith("-")
    assert os.path.isabs(argv_[-1])


def test_typed_commands_are_split_like_a_shell_would_but_never_run_by_one():
    assert split_command('python -c "print(1)"') == ["python", "-c", "print(1)"]
    assert split_command("  ffmpeg   -i a.mov  ") == ["ffmpeg", "-i", "a.mov"]
    if os.name == "nt":
        assert split_command(r'"C:\Program Files\x\y.exe" "a b"') == [r"C:\Program Files\x\y.exe", "a b"]
    with pytest.raises(PolicyError):
        split_command("")


def test_jobs_use_the_console_python_even_when_the_window_runs_on_pythonw(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("pythonw exists on Windows only")
    exe = tmp_path / "pythonw.exe"
    exe.write_bytes(b"")
    (tmp_path / "python.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(exe))
    assert jobs_module.console_python() == str(tmp_path / "python.exe")
    assert jobs_module.lm_command() == [str(tmp_path / "python.exe"), "-m", "loadmanager"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert jobs_module.lm_command() == [str(exe)]


# ---------- settings ----------

def test_settings_are_validated_saved_and_restored(box, tmp_path):
    ffm = make(tmp_path / "bin" / "ffmpeg.exe")
    view = box.jobs.configure({"work": str(tmp_path / "W2"), "hw": "nvenc", "min_free_gb": 12, "ffmpeg": str(ffm)})
    assert view["work_effective"] == str(tmp_path / "W2") and view["hw"] == "nvenc" and view["ffmpeg_found"] == str(ffm)
    assert VideoJobs(box.session).settings == box.jobs.settings                         # read back from settings.json
    assert json.loads((tmp_path / "state" / "settings.json").read_text())["video"]["hw"] == "nvenc"
    for bad, message in (({"work": "relative"}, "full path"), ({"work": str(tmp_path / "Dropbox" / "VW")}, "not inside Google Drive"),
                         ({"hw": "gpu9000"}, "Encoder"), ({"min_free_gb": "lots"}, "number of GB"), ({"min_free_gb": -1}, "number of GB"),
                         ({"ffmpeg": str(tmp_path / "missing.exe")}, "was not found"), ({"work": "a\nb"}, "path")):
        with pytest.raises(PolicyError, match=message):
            box.jobs.configure(bad)
    assert box.jobs.settings["hw"] == "nvenc", "a rejected change leaves the settings as they were"
    with pytest.raises(PolicyError):
        box.jobs.configure("nonsense")


def test_broken_saved_settings_fall_back_to_defaults(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "settings.json").write_text(json.dumps({"video": {"work": "relative", "hw": "x", "min_free_gb": "?", "ffmpeg": 5}}))
    (tmp_path / "state" / "history.json").write_text(json.dumps({"video_jobs": "nonsense"}))
    session = Session(tmp_path / "state", powercfg=None, wake=None)
    assert session.video.settings == jobs_module.DEFAULTS and session.video.history == []


def test_the_environment_of_a_job_comes_from_the_window_not_from_the_shell(box, monkeypatch, tmp_path):
    monkeypatch.setenv("LM_ALLOW_DRIVE", "1")
    monkeypatch.setenv("VK_WORK", "/elsewhere")
    box.jobs.extra_env = {}
    box.jobs.configure({"hw": "none", "min_free_gb": 7.5})
    env = box.jobs.environment({"LM_CANCEL_FILE": "c"})
    assert "LM_ALLOW_DRIVE" not in env and "VK_WORK" not in env
    assert env["LM_WORK"] == str(box.work) and env["LM_HW"] == "none" and env["LM_MIN_FREE_GB"] == "7.5"
    assert env["PYTHONIOENCODING"] == "utf-8" and env["LM_CANCEL_FILE"] == "c" and "LM_FFMPEG" not in env


def test_settings_cannot_change_while_a_job_runs(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="5")
    clip = box.clip()
    box.jobs.start("encode", {"input": str(clip), "output": str(box.work / "o.mp4"), "sw": True})
    try:
        with pytest.raises(PolicyError, match="Wait for the running job"):
            box.jobs.configure({"hw": "none"})
    finally:
        box.jobs.cancel()
        box.finish()


# ---------- real jobs ----------

def test_a_fetch_runs_and_says_what_it_did(box, tmp_path):
    src = make(tmp_path / "cloudish" / "a.mov", 4000)
    job = box.run("fetch", sources=[str(src)], project="demo")
    assert job["status"] == "done" and job["code"] == 0 and job["summary"] == "Copied 1, skipped 0, failed 0."
    assert (box.work / "demo" / "src" / "a.mov").stat().st_size == 4000
    assert job["command"].startswith("lm fetch --project demo --") and job["lines_total"] == len(job["lines"])
    assert box.jobs.state()["history"][0]["title"] == job["title"]


def test_a_dry_run_changes_nothing(box, tmp_path):
    src = make(tmp_path / "cloudish" / "a.mov")
    job = box.run("fetch", sources=[str(src)], project="demo", dry_run=True)
    assert job["status"] == "done" and "(dry run" in job["summary"] and not (box.work / "demo").exists()


def test_a_cloud_folder_is_refused_with_the_reason_in_the_output(box, tmp_path):
    clip = make(tmp_path / "Google Drive" / "My Drive" / "x.mov")
    job = box.run("encode", input=str(clip), output=str(tmp_path / "o.mp4"))
    assert job["status"] == "failed" and job["code"] == 4
    assert "refusing to work in place" in job["summary"] and any("lm fetch" in line for line in job["lines"])


def test_a_failing_encode_is_reported_as_failed_and_leaves_no_half_file(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_FAIL_ON="bad")
    clip = box.clip(name="bad.mov")
    out = box.work / "demo" / "out" / "bad.mp4"
    job = box.run("encode", input=str(clip), output=str(out), sw=True)
    assert job["status"] == "failed" and job["code"] == 1 and job["summary"] == "ffmpeg exited with 1"
    assert not out.exists()
    assert box.jobs.state()["history"][0]["status"] == "failed"


def test_an_encode_reports_progress_speed_and_the_result(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="4")
    clip = box.clip()
    out = box.work / "demo" / "out" / "clip-fast.mp4"
    box.jobs.start("encode", {"input": str(clip), "output": str(out), "sw": True})
    assert box.until(lambda: (box.jobs.state()["job"]["progress"] or 0) > 0.2)
    seen = box.jobs.state()["job"]
    assert seen["status"] == "running" and 0.2 < seen["progress"] < 1 and seen["speed"] == "2.0x"
    assert box.jobs.brief()["running"] and box.jobs.brief()["progress"] > 0.2 and box.jobs.running
    job = box.finish()
    assert job["status"] == "done" and job["progress"] == 1.0 and out.is_file() and job["output"] == str(out)
    assert job["summary"].startswith("Done: ") and box.jobs.brief()["running"] is False


def test_cancelling_an_encode_stops_it_and_removes_the_half_written_file(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    clip = box.clip()
    out = box.work / "demo" / "out" / "o.mp4"
    box.jobs.start("encode", {"input": str(clip), "output": str(out), "preset": "final"})
    assert box.until(lambda: out.exists())
    assert box.jobs.cancel() is True and box.jobs.cancel() is False, "a second cancel does nothing"
    assert box.jobs.state()["job"]["cancelling"] is True
    started = time.time()
    job = box.finish(20)
    assert time.time() - started < 15, "the stand-in would have run for 30 s"
    assert job["status"] == "cancelled" and job["code"] == 130 and job["summary"] == "Cancelled."
    assert not out.exists() and not list(box.session.directory.joinpath("video-jobs").glob("cancel-*"))


def test_cancelling_a_batch_stops_the_batch_and_removes_the_unfinished_proxy(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    for name in ("a.mov", "b.mov", "c.mov"):
        box.clip(name=name)
    box.jobs.start("proxies", {"project": "demo"})
    proxy = box.work / "demo" / "proxy"
    assert box.until(lambda: (proxy / "a.mp4").exists())
    job_lines = lambda: box.jobs.state()["job"]["lines"]
    assert box.until(lambda: any(l.startswith("[1/3]") for l in job_lines()))
    box.jobs.cancel()
    job = box.finish(20)
    assert job["status"] == "cancelled"
    assert not list(proxy.glob("*.mp4")), "the unfinished proxy was removed and the next clip never started"
    assert not any(l.startswith("[2/3]") for l in job["lines"])


def test_cancelling_a_copy_removes_its_part_file(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch)
    src = make(tmp_path / "cloudish" / "big.mov", 30 << 20)
    box.jobs.start("fetch", {"sources": [str(src)], "project": "demo"})
    box.jobs.cancel()
    job = box.finish(30)
    dst = box.work / "demo" / "src"
    assert job["status"] in ("cancelled", "done")                           # a very fast copy may have finished first
    assert not list(dst.glob("*.lmpart.tmp"))
    if job["status"] == "cancelled":
        assert not (dst / "big.mov").exists()


def test_a_job_that_ignores_the_cancel_is_stopped_for_good_after_the_grace_period(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch)
    box.jobs.grace = 0.6
    box.jobs.spawn = lambda cmd, **kw: subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **kw)
    box.jobs.start("doctor", {})
    time.sleep(0.5)
    box.jobs.cancel()
    started = time.time()
    job = box.finish(20)
    assert job["status"] == "cancelled" and time.time() - started < 10


def test_only_one_job_runs_at_a_time(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="5")
    clip = box.clip()
    box.jobs.start("encode", {"input": str(clip), "output": str(box.work / "o.mp4"), "sw": True})
    try:
        with pytest.raises(PolicyError, match="still running"):
            box.jobs.start("doctor", {})
    finally:
        box.jobs.cancel()
        box.finish()


def test_a_command_that_cannot_start_is_an_error_not_a_crash(box):
    def refuse(cmd, **kw):
        raise OSError("no such program")
    box.jobs.spawn = refuse
    with pytest.raises(PolicyError, match="Could not start the command: no such program"):
        box.jobs.start("doctor", {})
    assert not box.jobs.running and box.jobs.state()["job"] is None


def test_run_keeps_a_log_and_passes_the_exit_code_on(box):
    job = box.run("run", command=f'"{sys.executable}" -c "import sys; print(\'hello\'); sys.exit(3)"', name="probe")
    assert job["status"] == "failed" and job["code"] == 3 and "hello" in job["lines"]
    assert "probe" in (box.work / ".logs" / "runs.log").read_text()


def test_run_in_a_working_folder(box, tmp_path):
    here = tmp_path / "cwd"
    here.mkdir()
    job = box.run("run", command=f'"{sys.executable}" -c "import os; print(os.getcwd())"', cwd=str(here))
    assert job["status"] == "done" and any(line.lower() == str(here).lower() for line in job["lines"])


def test_the_doctor_gives_rows_the_page_can_show(box):
    job = box.run("doctor")
    tags = {r["tag"] for r in job["rows"]}
    assert job["status"] == "done" and tags <= {"ok", "warn", "info"} and any("ffmpeg version fake" in r["text"] for r in job["rows"])
    assert any(r["tag"] == "ok" and "libx264" in r["text"] for r in job["rows"])


def test_clean_previews_first_and_deletes_only_after_the_second_call(box, tmp_path):
    src = make(tmp_path / "cloudish" / "a.mov", 3000)
    box.run("fetch", sources=[str(src)], project="demo")
    preview = box.run("clean", project="demo", src=True)
    assert preview["result"] == {"clean": {"applied": False, "count": 1, "size": "2.9 KB"}}
    assert (box.work / "demo" / "src" / "a.mov").exists()
    done = box.run("clean", project="demo", src=True, yes=True)
    assert done["result"]["clean"]["applied"] is True and not (box.work / "demo" / "src" / "a.mov").exists()
    nothing = box.run("clean", project="demo", src=True)
    assert nothing["result"]["clean"]["count"] == 0


def test_publish_copies_back_and_never_replaces_a_different_file(box, tmp_path):
    render = make(box.work / "demo" / "out" / "final.mp4", 3000)
    dest = tmp_path / "Dropbox" / "Wedding"
    dest.mkdir(parents=True)
    make(dest / "final.mp4", 10)
    refused = box.run("publish", items=[str(render)], dest=str(dest))
    assert refused["status"] == "failed" and (dest / "final.mp4").stat().st_size == 10
    forced = box.run("publish", items=[str(render)], dest=str(dest), force=True)
    assert forced["status"] == "done" and (dest / "final.mp4").stat().st_size == 3000


def test_the_claude_kit_installs_and_goes_through_the_window(box, tmp_path):
    assert box.jobs.kit()["installed"] is False
    job = box.run("install_claude")
    assert job["status"] == "done", job["lines"]
    kit = box.jobs.kit()
    assert kit["installed"] and kit["hook"] and kit["skill"] and kit["launcher"] and kit["dir"] == str(tmp_path / ".claude")
    assert box.run("uninstall_claude")["status"] == "done"
    assert box.jobs.kit()["installed"] is False


def test_an_unreadable_settings_file_is_reported_by_the_kit_status(box, tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{ nope")
    kit = box.jobs.kit()
    assert kit["installed"] is False and "not valid JSON" in kit["error"]


def test_the_page_only_gets_new_lines_when_there_are_some(box, tmp_path):
    src = make(tmp_path / "cloudish" / "a.mov")
    box.run("fetch", sources=[str(src)], project="demo")
    job = box.jobs.state()["job"]
    again = box.jobs.state([job["id"], job["lines_total"]])["job"]
    assert again["lines"] is None and again["lines_total"] == job["lines_total"]
    assert box.jobs.state([job["id"], 1])["job"]["lines"] == job["lines"]


def test_the_history_is_kept_between_runs_of_the_app(box, tmp_path):
    box.run("setup")
    box.run("doctor")
    again = VideoJobs(box.session)
    assert [h["title"] for h in again.state()["history"]] == ["Health check", "Create the work folder"]
    assert all(set(h) >= {"id", "kind", "title", "command", "status", "code", "started", "ended", "summary"} for h in again.history)
    for _ in range(jobs_module.HISTORY + 3):
        box.jobs.history.append(dict(box.jobs.history[0]))
    box.run("setup")
    assert len(box.jobs.history) == jobs_module.HISTORY


def test_the_session_log_and_the_busy_check_know_about_jobs(box):
    box.run("setup")
    assert any("Create the work folder" in e["text"] for e in box.session.events)
    assert box.session.work_in_progress() is False


def test_a_running_job_counts_as_work_in_progress(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="5")
    clip = box.clip()
    box.jobs.start("encode", {"input": str(clip), "output": str(box.work / "o.mp4"), "sw": True})
    try:
        assert box.session.work_in_progress() is True
    finally:
        box.jobs.cancel()
        box.finish()


# ---------- closing the window ----------

def test_closing_with_nothing_running_is_always_allowed(box):
    assert box.jobs.close_guard() is True


def test_closing_during_a_job_asks_first_and_cancels_on_the_second_try(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    clip = box.clip()
    box.jobs.start("encode", {"input": str(clip), "output": str(box.work / "o.mp4"), "sw": True})
    assert box.until(lambda: box.jobs.state()["job"]["lines_total"] > 2)
    assert box.jobs.close_guard() is False and box.jobs.running and box.jobs.brief()["close_asked"] > 0
    assert any("Closing was held back" in e["text"] for e in box.session.events)
    assert box.jobs.close_guard() is True and not box.jobs.running
    assert box.jobs.state()["job"]["status"] == "cancelled"


def test_a_second_close_after_a_long_pause_asks_again(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FAKE_SLEEP="30")
    clip = box.clip()
    now = [1000.0]
    box.jobs.clock = lambda: now[0]
    box.jobs.start("encode", {"input": str(clip), "output": str(box.work / "o.mp4"), "sw": True})
    try:
        assert box.jobs.close_guard() is False
        now[0] += 60
        assert box.jobs.close_guard() is False and box.jobs.running
    finally:
        box.jobs.cancel()
        box.finish()


# ---------- what the forms ask about ----------

def test_the_overview_lists_projects_with_sizes_and_the_free_space(box):
    assert box.jobs.overview()["exists"] is False and box.jobs.overview()["projects"] == []
    make(box.work / "wed" / "src" / "a.mov", 3000)
    make(box.work / "wed" / "src" / "sub" / "b.mov", 1000)
    make(box.work / "wed" / "proxy" / "a.mp4", 100)
    make(box.work / ".logs" / "x.log", 5)
    make(box.work / "alpha" / "out" / "f.mp4", 7)
    view = box.jobs.overview()
    assert [p["name"] for p in view["projects"]] == ["alpha", "wed"]
    wed = view["projects"][1]
    assert wed["src"] == {"bytes": 4000, "files": 2} and wed["proxy"] == {"bytes": 100, "files": 1} and wed["out"] == {"bytes": 0, "files": 0}
    assert view["exists"] and view["free"] > 0 and view["work"] == str(box.work) and view["system"] == video.system()


def test_ffmpeg_is_found_from_the_settings_or_the_path(box, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module.shutil, "which", lambda name: None)
    assert box.jobs.overview()["ffmpeg"] is None
    monkeypatch.setattr(jobs_module.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert box.jobs.overview()["ffmpeg"] == "/usr/bin/ffmpeg"
    ffm = make(tmp_path / "mine" / "ffmpeg")
    box.jobs.configure({"ffmpeg": str(ffm)})
    assert box.jobs.overview()["ffmpeg"] == str(ffm)


def test_inspect_tells_what_a_path_is(box, tmp_path):
    cloud = make(tmp_path / "Google Drive" / "My Drive" / "Wedding 2026" / "a.mov", 123)
    info = box.jobs.inspect(str(cloud))
    assert info["cloud"] is True and info["dir"] is False and info["size"] == 123 and info["name"] == "a.mov" and info["project"] == "Wedding-2026"
    assert box.jobs.inspect(str(cloud.parent))["dir"] is True and box.jobs.inspect(str(cloud.parent))["size"] is None
    local = make(box.work / "p" / "src" / "a.mov")
    assert box.jobs.inspect(str(local))["inside_work"] is True and box.jobs.inspect(str(local))["cloud"] is False
    with pytest.raises(PolicyError, match="not found"):
        box.jobs.inspect(str(tmp_path / "nope"))
    with pytest.raises(PolicyError, match="full path"):
        box.jobs.inspect("a.mov")


def test_the_output_name_is_suggested_inside_the_project(box, tmp_path):
    inside = make(box.work / "wed" / "src" / "clip one.mov")
    assert box.jobs.suggest_output(str(inside), "fast") == str(box.work / "wed" / "out" / "clip one-fast.mp4")
    assert box.jobs.suggest_output(str(inside), "final", "hevc") == str(box.work / "wed" / "out" / "clip one-final-hevc.mp4")
    assert box.jobs.suggest_output(str(inside), "proxy", "hevc") == str(box.work / "wed" / "out" / "clip one-proxy.mp4")
    outside = make(tmp_path / "Holiday 2025" / "x.mov")
    assert box.jobs.suggest_output(str(outside), "proxy") == str(box.work / "Holiday-2025" / "out" / "x-proxy.mp4")
    with pytest.raises(PolicyError, match="Preset"):
        box.jobs.suggest_output(str(inside), "huge")
    with pytest.raises(PolicyError, match="does not exist"):
        box.jobs.suggest_output(str(tmp_path / "gone.mov"))


def test_reveal_shows_a_folder_or_selects_a_file_and_never_opens_one(box, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(jobs_module.subprocess, "Popen", lambda cmd, **kw: opened.append(cmd))
    clip = make(box.work / "wed" / "out" / "x.mp4")
    assert box.jobs.reveal("out", "wed") == str(clip.parent)
    assert box.jobs.reveal("project", "wed") == str(box.work / "wed")
    assert box.jobs.reveal("work") == str(box.work)
    assert box.jobs.reveal("proxy", "wed") == str(box.work / "wed"), "a folder that does not exist yet shows its parent"
    assert box.jobs.reveal("path", path=str(clip)) == str(clip)
    first, last = opened[0], opened[-1]
    if os.name == "nt":
        assert first == ["explorer", str(clip.parent)] and last == ["explorer", f"/select,{clip}"]
    for bad_kind, project in (("elsewhere", "wed"), ("src", "../x")):
        with pytest.raises(PolicyError):
            box.jobs.reveal(bad_kind, project)
    with pytest.raises(PolicyError, match="not found"):
        box.jobs.reveal("path", path=str(tmp_path / "gone"))


# ---------- what the command line gained for this ----------

def test_the_cancel_file_acts_like_ctrl_c(tmp_path):
    flag, hits = tmp_path / "cancel", []
    video.CANCELLED.clear()
    try:
        thread = video.start_cancel_watch(str(flag), interrupt=lambda: hits.append(1), interval=0.02)
        time.sleep(0.1)
        assert hits == [] and thread.is_alive()
        flag.write_text("x")
        thread.join(2)
        assert hits == [1] and video.CANCELLED.is_set()
    finally:
        video.CANCELLED.clear()


def test_waiting_for_a_program_can_be_interrupted_and_stops_the_program(monkeypatch):
    real, made, calls = subprocess.Popen, [], []

    class Interrupted:
        """Looks like a running program whose wait is interrupted on the second look, like Ctrl+C or a cancel would."""
        def __init__(self, proc):
            self.proc = proc

        def wait(self, timeout=None):
            calls.append(timeout)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired("x", timeout)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return self.proc.wait(timeout)

        def kill(self):
            self.proc.kill()

    def popen(cmd, **kw):
        made.append(real([sys.executable, "-c", "import time; time.sleep(60)"], **kw))
        return Interrupted(made[-1])
    monkeypatch.setattr(video.subprocess, "Popen", popen)
    with pytest.raises(KeyboardInterrupt):
        video._call(["x"], low=False)
    assert calls[0] == 0.5 and made[0].poll() is not None, "the program was stopped, not left running"


def test_an_encode_prints_the_length_of_the_input(box, tmp_path):
    clip = box.clip()
    job = box.run("encode", input=str(clip), output=str(box.work / "demo" / "out" / "o.mp4"), sw=True)
    assert "Input duration: 10.0 s" in job["lines"]


def test_a_stop_that_reached_ffmpeg_also_stops_the_batch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LM_WORK", str(tmp_path / "W"))
    monkeypatch.setenv("LM_FFMPEG", f'"{sys.executable}" "{FAKE}"')
    monkeypatch.setenv("LM_MIN_FREE_GB", "0")
    monkeypatch.setenv("LM_NOTIFY", "0")
    for name in ("a.mov", "b.mov"):
        make(tmp_path / "W" / "p" / "src" / name)
    seen = []

    def stopped(args, hw=None, batch=False, hw_known=False):
        seen.append(Path(args.input).name)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_bytes(b"half")
        video.CANCELLED.set()
        return 130
    monkeypatch.setattr(video, "encode_one", stopped)
    video._encoders.cache_clear()
    video._selftest.cache_clear()
    try:
        assert video.main(["proxies", "p"]) == 130
    finally:
        video.CANCELLED.clear()
    assert seen == ["a.mov"] and not list((tmp_path / "W" / "p" / "proxy").glob("*"))
    assert "interrupted" in capsys.readouterr().err


# ---------- reading what a job prints ----------

def feed(lines, count_lines=True):
    job = jobs_module.Job(1, "proxies", "t", ["proxies"], {}, 0.0)
    jobs = VideoJobs.__new__(VideoJobs)
    jobs.lock = __import__("threading").RLock()
    jobs.clock = lambda: 100.0
    for line in lines:
        jobs._line(job, line)
    return jobs, job


def test_the_progress_file_is_read_whichever_way_ffmpeg_writes_the_time(tmp_path):
    p = tmp_path / "p.progress"
    assert jobs_module.read_progress(str(tmp_path / "missing")) == (None, None)
    p.write_text("")
    assert jobs_module.read_progress(str(p)) == (None, None)
    p.write_text("frame=1\nout_time_us=2500000\nspeed=1.5x\nprogress=continue\nout_time_us=4000000\nspeed= 2.25x\n")
    assert jobs_module.read_progress(str(p)) == (4.0, "2.25x")
    p.write_text("out_time_ms=3000000\nspeed=N/A\n")
    assert jobs_module.read_progress(str(p)) == (3.0, None)
    p.write_text("out_time=00:01:02.500000\nout_time_us=N/A\n")
    assert jobs_module.read_progress(str(p)) == (62.5, None)
    p.write_text("x" * 20000 + "\nout_time_us=1000000\n")
    assert jobs_module.read_progress(str(p))[0] == 1.0, "only the end of a long file is read"


def test_a_batch_shows_the_item_it_is_on_plus_how_far_the_clip_has_got(tmp_path):
    p = tmp_path / "p.progress"
    p.write_text("out_time_us=5000000\nspeed=2.0x\n")
    jobs, job = feed(["Plan: x", "[2/4] clips/b.mov", "Input duration: 10.0 s", f"Progress file: {p} (out_time and speed are the lines to watch)"])
    assert (job.index, job.count, job.item, job.duration, job.progress_file) == (2, 4, "clips/b.mov", 10.0, str(p))
    assert jobs._overall(job) == pytest.approx((1 + 0.5) / 4)
    assert jobs._view(job)["speed"] == "2.0x"
    jobs._line(job, "[3/4] clips/c.mov")
    assert job.duration is None and job.progress_file is None and jobs._overall(job) == pytest.approx(2 / 4), "a new item starts again"


def test_a_copy_shows_how_far_the_current_file_has_got():
    jobs, job = feed(["[2/4] big.mov (1.0 GB)", "  512.0 MB / 1.0 GB (50%)"])
    assert jobs._overall(job) == pytest.approx(1.5 / 4)
    single, one = feed(["  512.0 MB / 1.0 GB (25%)"])
    assert single._overall(one) == 0.25
    other, idle = feed(["nothing yet"])
    assert other._overall(idle) is None
    jobs._line(job, "  1.0 GB / 1.0 GB (150%)")
    assert job.percent == 100, "never more than all"


def test_the_summary_prefers_the_cause_of_a_failure():
    jobs, job = feed(["Plan: 2 file(s), 1 KB -> x", "lm: warning: first thing", "lm: warning: second thing"])
    job.status, job.code = "failed", 1
    assert jobs._summary(job) == "Plan: 2 file(s), 1 KB -> x", "a summary line says more than a warning"
    job.summary = None
    assert jobs._summary(job) == "first thing", "the first warning is the cause, the later ones are consequences"
    jobs._line(job, "lm: not enough free space")
    assert jobs._summary(job) == "not enough free space"
    other, bare = feed([])
    bare.status, bare.code = "failed", 9
    assert other._summary(bare) == "The command ended with exit code 9."
    bare.status = "cancelled"
    assert other._summary(bare) == "Cancelled."
    bare.status, bare.summary = "done", "Copied 1, skipped 0, failed 0."
    assert other._summary(bare) == "Copied 1, skipped 0, failed 0."


def test_a_cancel_that_came_too_late_does_not_turn_a_success_into_a_cancellation(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch)
    box.jobs.spawn = lambda cmd, **kw: subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.8)"], **kw)
    box.jobs.start("doctor", {})
    assert box.jobs.cancel() is True
    assert box.finish()["status"] == "done"


def test_a_stopped_job_is_notified_as_stopped_not_failed(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setenv("LM_WORK", str(tmp_path))
    monkeypatch.setattr(video, "notify", lambda title, message: sent.append(message))

    def interrupted(cmd, low):
        raise KeyboardInterrupt
    monkeypatch.setattr(video, "_call", interrupted)
    assert video.run_wrapped("encode a.mov", True, ["x"], batch=False) == 130
    assert sent == ["encode a.mov was stopped"]
