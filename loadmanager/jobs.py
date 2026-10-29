"""Runs the lm video commands for the window: one job at a time, in a child process, with live output.

The window never re-implements a command. A job is `lm <command> ...` started as a child process, its settings passed
in environment variables, its output read line by line, and a cancel that works like Ctrl+C in a terminal (the child
watches a file, see video.start_cancel_watch). What the window shows is exactly what the command line would print."""
from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import psutil

from . import install_claude, video, winapi
from .cloud import mentions_cloud
from .engine import PolicyError, read_json

HW_CHOICES = ("auto", "none", "nvenc", "amf", "qsv", "videotoolbox")
KINDS = ("status", "setup", "doctor", "fetch", "encode", "proxies", "publish", "run", "clean", "install_claude",
         "uninstall_claude")
DEFAULTS = {"work": "", "hw": "auto", "min_free_gb": 5.0, "ffmpeg": ""}
QUEUEABLE = ("fetch", "encode", "proxies", "publish", "run")      # the rest answer at once or delete, so they never wait
AFTER_CHOICES = ("nothing", "sleep", "hibernate", "shutdown")
AFTER_DELAY = 60.0                              # seconds between "everything is done" and the computer sleeping or shutting down
QUEUE_LIMIT = 100
HISTORY = 12
LINES_KEPT = 2000
LINES_SENT = 150
GRACE = 12.0                                    # seconds a cancelled job gets to clean up before it is stopped for good

RE_ITEM = re.compile(r"^\[(\d+)/(\d+)\]\s*(.*)$")
RE_PERCENT = re.compile(r"\((\d{1,3})%\)\s*$")
RE_DURATION = re.compile(r"^Input duration: ([\d.]+) s")
RE_PROGRESS_FILE = re.compile(r"^Progress file: (.+?) \(out_time")
RE_SUMMARY = re.compile(r"^(Done: |Copied \d|Published \d|Proxies for |Work folder ready|Nothing to remove|Would remove|"
                        r"Removing \d|Nothing was removed|Plan: |\(dry run)")
RE_CLEAN = re.compile(r"^(Would remove|Removing) (\d+) file\(s\), (.+):$")
RE_ROW = re.compile(r"^\[(ok|warn|info)\]\s+(.*)$")
RE_BITRATE = re.compile(r"^\d{1,4}(\.\d{1,2})?[kKmM]?$")


# ---------- turning the form fields of the window into a command line ----------

def _text(p, key, limit=4000, required=False, label=None):
    value = p.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise PolicyError(f"{label or key}: this is required")
        return ""
    if not isinstance(value, str) or "\0" in value or "\n" in value or "\r" in value or len(value) > limit:
        raise PolicyError(f"{label or key}: this must be a single line of text")
    return value.strip()


def _flag(p, key):
    return bool(p.get(key))


def _choice(p, key, options, default, label=None):
    value = p.get(key) or default
    if value not in options:
        raise PolicyError(f"{label or key}: choose one of {', '.join(options)}")
    return value


def _path(value, label, kind="any", exists=True):
    """An absolute path (so that it can never look like an option). `kind` says what must exist: a file, a folder,
    either ("any"), or nothing yet ("new", for an output). With exists=False nothing has to exist (a queued item may
    wait for a file that an earlier item makes)."""
    if not isinstance(value, str) or not value.strip() or "\0" in value or "\n" in value or "\r" in value or len(value) > 1000:
        raise PolicyError(f"{label}: choose a file or folder")
    path = Path(os.path.expanduser(value.strip().strip('"')))
    if not path.is_absolute():
        raise PolicyError(f"{label}: use the full path, for example {Path.home() / 'clip.mov'}")
    path = Path(os.path.normpath(str(path)))
    if not exists:
        return str(path)
    if kind == "file" and not path.is_file():
        raise PolicyError(f"{label}: this file does not exist: {path}")
    if kind == "dir" and not path.is_dir():
        raise PolicyError(f"{label}: this folder does not exist: {path}")
    if kind == "any" and not path.exists():
        raise PolicyError(f"{label}: not found: {path}")
    return str(path)                                # kind "new": nothing has to exist yet


def _paths(p, key, label, kind="any", limit=200, exists=True):
    value = p.get(key)
    if isinstance(value, str):
        value = [line for line in value.splitlines() if line.strip()]
    if not isinstance(value, (list, tuple)) or not value:
        raise PolicyError(f"{label}: choose at least one file or folder")
    if len(value) > limit:
        raise PolicyError(f"{label}: at most {limit} items at a time")
    return [_path(v, label, kind, exists) for v in value]


def _project(p, key="project", required=False):
    name = _text(p, key, 80, required, "Project")
    if name:
        try:
            video.valid_project(name)
        except video.Fail as exc:
            raise PolicyError(str(exc)) from exc
    return name


def split_command(text):
    """A command typed into a box, as an argument list (it is started directly, never through a shell)."""
    try:
        parts = shlex.split(text, posix=(os.name != "nt"))
    except ValueError as exc:
        raise PolicyError(f"Could not read the command: {exc}") from exc
    if os.name == "nt":
        parts = [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t for t in parts]
    if not parts:
        raise PolicyError("Enter a command")
    return parts


def display(argv):
    return subprocess.list2cmdline(argv) if os.name == "nt" else " ".join(shlex.quote(a) for a in argv)


def build(kind, p, python=None, exists=True):
    """(arguments after `lm`, a title, details for the window). Raises PolicyError for anything that does not make sense.
    exists=False skips the "this file must exist" checks, for an item that will only run later."""
    if kind not in KINDS or not isinstance(p, dict):
        raise PolicyError("Unknown command")
    meta = {}
    if kind == "status":
        return ["status"], "Work folder status", meta
    if kind == "setup":
        return ["setup"], "Create the work folder", meta
    if kind == "doctor":
        return ["doctor"], "Health check", meta
    if kind == "install_claude":
        return ["install-claude", "--python", str(python or console_python())], "Install the Claude kit", meta
    if kind == "uninstall_claude":
        return ["uninstall-claude"], "Remove the Claude kit", meta
    if kind == "fetch":
        sources = _paths(p, "sources", "Copy from", exists=exists)
        project = _project(p)
        argv = ["fetch"] + (["--project", project] if project else []) + (["--verify"] if _flag(p, "verify") else []) \
            + (["--dry-run"] if _flag(p, "dry_run") else []) + ["--"] + sources
        what = Path(sources[0]).name + (f" and {len(sources) - 1} more" if len(sources) > 1 else "")
        meta["project"] = project
        return argv, ("Check the plan for " if _flag(p, "dry_run") else "Copy ") + what + " from the cloud", meta
    if kind == "publish":
        items = _paths(p, "items", "Copy these", exists=exists)
        dest = _path(p.get("dest"), "Copy to", "dir", exists)
        argv = ["publish"] + (["--force"] if _flag(p, "force") else []) + ["--"] + items + [dest]
        return argv, f"Copy {len(items)} item{'s' if len(items) != 1 else ''} to {Path(dest).name or dest}", meta
    if kind == "encode":
        source = _path(p.get("input"), "Input", "file", exists)
        target = _path(p.get("output"), "Output", "new")
        if os.path.normcase(source) == os.path.normcase(target):
            raise PolicyError("Output: this would overwrite the input file")
        preset = _choice(p, "preset", ("proxy", "fast", "final"), "fast", "Preset")
        codec = _choice(p, "codec", ("h264", "hevc"), "h264", "Codec")
        bitrate = _text(p, "bitrate", 12, False, "Bitrate")
        if bitrate and not RE_BITRATE.match(bitrate):
            raise PolicyError("Bitrate: write it like 8M or 6000k")
        extra = _text(p, "extra", 1000, False, "Extra ffmpeg arguments")
        argv = ["encode", source, target, "--preset", preset, "--codec", codec]
        if bitrate:
            argv += ["--bitrate", bitrate]
        for key, option in (("sw", "--sw"), ("no_hwdecode", "--no-hwdecode"), ("low", "--low"), ("force", "--force"),
                            ("dry_run", "--dry-run")):
            if _flag(p, key):
                argv.append(option)
        if extra:
            try:
                argv += ["--"] + shlex.split(extra)
            except ValueError as exc:
                raise PolicyError(f"Extra ffmpeg arguments: {exc}") from exc
        meta["output"] = target
        return argv, ("Check the command for " if _flag(p, "dry_run") else "Encode ") + f"{Path(source).name} ({preset})", meta
    if kind == "proxies":
        project = _project(p)
        preset = _choice(p, "preset", ("proxy", "fast"), "proxy", "Preset")
        argv = ["proxies"] + ([project] if project else []) + ["--preset", preset]
        for key, option in (("sw", "--sw"), ("no_hwdecode", "--no-hwdecode"), ("low", "--low"), ("force", "--force")):
            if _flag(p, key):
                argv.append(option)
        meta["project"] = project
        return argv, f"Make proxies{' for ' + project if project else ''}", meta
    if kind == "run":
        command = split_command(_text(p, "command", 4000, True, "Command"))
        name = _text(p, "name", 80, False, "Name")
        argv = ["run"] + (["--name", name] if name else []) + (["--low"] if _flag(p, "low") else []) \
            + (["--no-notify"] if _flag(p, "no_notify") else []) + ["--"] + command
        cwd = _path(p.get("cwd"), "Working folder", "dir", exists) if _text(p, "cwd", 1000) else ""
        meta["cwd"] = cwd
        return argv, f"Run {name or Path(command[0]).name}", meta
    if kind == "clean":
        project = _project(p, required=True)
        src, proxy = _flag(p, "src"), _flag(p, "proxy")
        if not (src or proxy):
            raise PolicyError("Choose what to clean: the copies from the cloud, the proxies, or both")
        argv = ["clean", project] + (["--src"] if src else []) + (["--proxy"] if proxy else []) \
            + (["--yes"] if _flag(p, "yes") else [])
        meta["project"] = project
        return argv, ("Delete files for " if _flag(p, "yes") else "Preview what to delete for ") + project, meta
    raise PolicyError("Unknown command")


def console_python():
    """The interpreter that jobs run on: python.exe even when the window itself runs on pythonw.exe (it has no console)."""
    exe = Path(sys.executable)
    if os.name == "nt" and exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def lm_command():
    return [sys.executable] if getattr(sys, "frozen", False) else [console_python(), "-m", "loadmanager"]


def available_after(system=None):
    """What the computer can be told to do when everything is finished."""
    return ("nothing", "sleep", "shutdown") if (system or video.system()) == "Darwin" else AFTER_CHOICES


def run_power_action(action, system=None, run=None, suspend=None):
    """Sleep, hibernate or shut the computer down. Raises PolicyError (with the reason) when it cannot."""
    system = system or video.system()
    if action not in AFTER_CHOICES[1:] or action not in available_after(system):
        raise PolicyError(f"This computer cannot {action}")
    if run is None and suspend is None and "PYTEST_CURRENT_TEST" in os.environ:
        raise PolicyError("Refusing to put the computer to sleep or shut it down from a test")
    run = run or subprocess.run
    if system == "Windows":
        if action == "sleep":
            try:
                (suspend or winapi.suspend)()
            except OSError as exc:
                raise PolicyError(f"Windows refused to sleep: {exc}") from exc
            return
        command = ["shutdown", "/h"] if action == "hibernate" else ["shutdown", "/s", "/t", "10"]
    elif system == "Darwin":
        command = ["pmset", "sleepnow"] if action == "sleep" else ["osascript", "-e", 'tell application "System Events" to shut down']
    else:
        command = ["systemctl", {"sleep": "suspend", "hibernate": "hibernate", "shutdown": "poweroff"}[action]]
    try:
        done = run(command, capture_output=True, text=True, errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PolicyError(f"Could not {action}: {exc}") from exc
    if done.returncode != 0:
        detail = ((done.stderr or done.stdout or "").strip().splitlines() or [f"exit code {done.returncode}"])[0]
        raise PolicyError(f"Could not {action}: {detail}")


def kill_tree(pid):
    with contextlib.suppress(psutil.Error):
        root = psutil.Process(pid)
        for child in root.children(recursive=True):
            with contextlib.suppress(psutil.Error):
                child.kill()
        root.kill()


# ---------- reading what a job prints ----------

def read_progress(path):
    """(seconds of video done, speed text) from the tail of ffmpeg's -progress file."""
    try:
        with open(path, "rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 4096))
            tail = stream.read().decode("utf-8", "replace")
    except OSError:
        return None, None
    done = None
    stamps = re.findall(r"out_time_(?:us|ms)=(\d+)", tail)         # ffmpeg writes microseconds under both names
    if stamps:
        done = int(stamps[-1]) / 1e6
    else:
        clock = re.findall(r"out_time=(\d+):(\d+):(\d+(?:\.\d+)?)", tail)
        if clock:
            h, m, s = clock[-1]
            done = int(h) * 3600 + int(m) * 60 + float(s)
    speeds = re.findall(r"speed=\s*([\d.]+)x", tail)
    return done, (speeds[-1] + "x" if speeds else None)


class Job:
    def __init__(self, number, kind, title, argv, meta, now):
        self.id, self.kind, self.title, self.meta, self.started = number, kind, title, meta, now
        self.command = "lm " + display(argv)
        self.ended = self.code = None
        self.status = "running"
        self.cancelling = False
        self.lines = deque(maxlen=LINES_KEPT)
        self.total = 0
        self.index = self.count = None
        self.item = ""
        self.percent = self.duration = self.progress_file = None
        self.summary = self.error = self.warning = None
        self.rows = []
        self.proc = None
        self.cancel_file = None


class VideoJobs:
    def __init__(self, session, clock=time.time, spawn=subprocess.Popen, grace=GRACE, extra_env=None):
        self.session, self.clock, self.spawn, self.grace = session, clock, spawn, grace
        self.extra_env = dict(extra_env or {})            # applied last; the tests use it to point jobs at a stand-in ffmpeg
        self.lock = threading.RLock()
        self.job = None
        self.counter = 0
        self.close_asked = 0.0
        self.closing = False
        self.queue, self.queue_counter = [], 0              # items waiting for their turn: {id, kind, params, title, command}
        self.paused, self.pause_reason = False, None
        self.after, self.after_error = "nothing", None      # one-shot: what to do when everything is finished
        self.after_delay, self.countdown, self.countdown_token = AFTER_DELAY, None, 0
        self.power, self.notify = run_power_action, video.notify          # replaced by the tests
        stored = read_json(session.settings_path, {})
        saved = stored.get("video", {}) if isinstance(stored, dict) else {}
        self.settings = self._clean_settings(saved if isinstance(saved, dict) else {}, lenient=True)
        options = stored.get("video_queue", {}) if isinstance(stored, dict) else {}
        self.keep_going = bool(options.get("keep_going")) if isinstance(options, dict) else False
        stored = read_json(session.history_path, {})
        history = stored.get("video_jobs", []) if isinstance(stored, dict) else []
        self.history = [h for h in history if isinstance(h, dict)][-HISTORY:] if isinstance(history, list) else []
        waiting = stored.get("video_queue", []) if isinstance(stored, dict) else []
        for item in waiting if isinstance(waiting, list) else []:
            if isinstance(item, dict) and item.get("kind") in QUEUEABLE and isinstance(item.get("params"), dict) \
                    and isinstance(item.get("title"), str) and isinstance(item.get("command"), str):
                self.queue_counter += 1
                self.queue.append({"id": self.queue_counter, "kind": item["kind"], "params": item["params"],
                                   "title": item["title"][:200], "command": item["command"][:2000]})
        if self.queue:
            self.paused, self.pause_reason = True, "restored"       # never starts by itself after the app was closed

    # -- settings --------------------------------------------------------------------------------

    @staticmethod
    def _clean_settings(values, lenient=False):
        out = dict(DEFAULTS)
        for key in ("work", "ffmpeg"):
            value = values.get(key, DEFAULTS[key])
            if not isinstance(value, str) or "\0" in value or "\n" in value:
                if lenient:
                    continue
                raise PolicyError(f"{key}: this must be a path")
            value = value.strip().strip('"')
            if value and not Path(os.path.expanduser(value)).is_absolute():
                if lenient:
                    continue
                raise PolicyError("Use the full path of the " + ("work folder" if key == "work" else "ffmpeg program"))
            out[key] = os.path.normpath(os.path.expanduser(value)) if value else ""
        if out["work"] and mentions_cloud(out["work"]) and not lenient:
            raise PolicyError("The work folder must be on this computer, not inside Google Drive, Dropbox, OneDrive or iCloud")
        if out["ffmpeg"] and not lenient and not Path(out["ffmpeg"]).is_file():
            raise PolicyError(f"ffmpeg was not found at {out['ffmpeg']}")
        hw = values.get("hw", DEFAULTS["hw"])
        if hw in HW_CHOICES:
            out["hw"] = hw
        elif not lenient:
            raise PolicyError("Encoder: choose one of " + ", ".join(HW_CHOICES))
        try:
            gb = float(values.get("min_free_gb", DEFAULTS["min_free_gb"]))
            if not 0 <= gb <= 100000:
                raise ValueError
            out["min_free_gb"] = gb
        except (TypeError, ValueError):
            if not lenient:
                raise PolicyError("Spare space: a number of GB from 0 up") from None
        return out

    def configure(self, patch):
        if not isinstance(patch, dict):
            raise PolicyError("Nothing to change")
        with self.lock:
            if self.running:
                raise PolicyError("Wait for the running job to finish before changing these settings")
            new = self._clean_settings({**self.settings, **{k: v for k, v in patch.items() if k in DEFAULTS}})
            self.settings = new
        self.session.update_settings("video", new)
        return self.settings_view()

    def work_path(self):
        return Path(self.settings["work"]) if self.settings["work"] else Path.home() / "VideoWork"

    def ffmpeg_path(self):
        if self.settings["ffmpeg"]:
            return self.settings["ffmpeg"] if Path(self.settings["ffmpeg"]).is_file() else None
        return shutil.which("ffmpeg")

    def settings_view(self):
        return {**self.settings, "work_effective": str(self.work_path()), "ffmpeg_found": self.ffmpeg_path(),
                "hw_choices": list(HW_CHOICES), "platform": video.system()}

    def environment(self, extra=None):
        env = dict(os.environ)
        for name in [n for n in env if n.startswith(("LM_", "VK_"))]:
            del env[name]                           # the window's settings decide, not whatever the app was started with
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", LM_WORK=str(self.work_path()), LM_HW=self.settings["hw"],
                   LM_MIN_FREE_GB=f"{self.settings['min_free_gb']:g}")
        if self.settings["ffmpeg"]:
            env["LM_FFMPEG"] = f'"{self.settings["ffmpeg"]}"'
        env.update(self.extra_env)
        env.update(extra or {})
        return env

    # -- what the window shows -------------------------------------------------------------------

    @property
    def running(self):
        with self.lock:
            return self.job is not None and self.job.status == "running"

    def brief(self):
        with self.lock:
            job = self.job
            return {"running": self.running, "title": job.title if job else None,
                    "status": job.status if job else None, "progress": self._overall(job) if job else None,
                    "close_asked": self.close_asked, "queued": len(self.queue), "paused": self.paused,
                    "after": self.after}

    def state(self, known=None):
        """`known` is [job id, line count] of what the window already has: the lines are left out when nothing is new."""
        with self.lock:
            job = self.job
            view = self._view(job, with_lines=not (job and known == [job.id, job.total])) if job else None
            return {"running": self.running, "job": view, "history": list(reversed(self.history)),
                    "settings": self.settings_view(), "queue": self.queue_view(), "now": self.clock()}

    def queue_view(self):
        with self.lock:
            countdown = None
            if self.countdown:
                countdown = {"action": self.countdown["action"], "seconds": max(0.0, self.countdown["until"] - self.clock())}
            return {"items": [{"id": i["id"], "kind": i["kind"], "title": i["title"], "command": i["command"]} for i in self.queue],
                    "paused": self.paused, "reason": self.pause_reason, "keep_going": self.keep_going, "after": self.after,
                    "after_choices": list(available_after()), "countdown": countdown, "error": self.after_error}

    def _overall(self, job):
        frac = None
        if job.duration and job.progress_file and job.status == "running":
            done, _ = read_progress(job.progress_file)
            frac = None if done is None else max(0.0, min(1.0, done / job.duration))
        elif job.percent is not None:
            frac = job.percent / 100
        if job.status == "done":
            return 1.0
        if job.count:
            return min(1.0, ((job.index or 1) - 1 + (frac or 0)) / job.count)
        return frac

    def _view(self, job, with_lines=True):
        speed = None
        if job.progress_file and job.duration and job.status == "running":
            speed = read_progress(job.progress_file)[1]
        progress = self._overall(job)
        elapsed = (job.ended or self.clock()) - job.started
        eta = (elapsed / progress - elapsed) if job.status == "running" and progress and progress > 0.03 and elapsed > 5 else None
        view = {"id": job.id, "kind": job.kind, "title": job.title, "command": job.command, "status": job.status,
                "code": job.code, "started": job.started, "ended": job.ended, "cancelling": job.cancelling,
                "progress": progress, "speed": speed, "eta": eta, "index": job.index, "count": job.count, "item": job.item,
                "summary": self._summary(job),
                "rows": list(job.rows), "output": job.meta.get("output"), "project": job.meta.get("project"),
                "lines_total": job.total, "result": self._result(job)}
        view["lines"] = list(job.lines)[-LINES_SENT:] if with_lines else None
        return view

    @staticmethod
    def _summary(job):
        if job.status == "cancelled":
            return "Cancelled."
        if job.status == "failed":
            return job.error or job.summary or job.warning or f"The command ended with exit code {job.code}."
        return job.summary

    @staticmethod
    def _result(job):
        for text in reversed(job.lines):
            m = RE_CLEAN.match(text)
            if m:
                return {"clean": {"applied": m.group(1) == "Removing", "count": int(m.group(2)), "size": m.group(3)}}
            if text.startswith("Nothing to remove"):
                return {"clean": {"applied": False, "count": 0, "size": "0 B"}}
        return None

    # -- starting and stopping -------------------------------------------------------------------

    def start(self, kind, params):
        argv, title, meta = build(kind, params or {})
        with self.lock:
            if self.running:
                raise PolicyError("Another video job is still running. Wait for it or cancel it first.")
            self.counter += 1
            job = Job(self.counter, kind, title, argv, meta, self.clock())
            directory = self.session.directory / "video-jobs"
            directory.mkdir(parents=True, exist_ok=True)
            job.cancel_file = directory / f"cancel-{os.getpid()}-{job.id}"
            job.cancel_file.unlink(missing_ok=True)
            cwd = meta.get("cwd") or (str(self.work_path()) if self.work_path().is_dir() else str(Path.home()))
            flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
            try:
                job.proc = self.spawn(lm_command() + argv, env=self.environment({"LM_CANCEL_FILE": str(job.cancel_file)}),
                                      cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **flags)
            except OSError as exc:
                raise PolicyError(f"Could not start the command: {exc}") from exc
            self.job = job
            self.after_error = None
            if self.countdown:
                self.countdown = None                           # new work: the computer stays on; the action stays armed
                self.session.log("Video: the countdown was stopped because a new job started")
        self.session.log(f"Video: started \"{title}\"")
        threading.Thread(target=self._pump, args=(job,), daemon=True).start()
        return job.id

    def cancel(self):
        with self.lock:
            job = self.job
            if job is None or job.status != "running" or job.cancelling:
                return False
            job.cancelling = True
        with contextlib.suppress(OSError):
            job.cancel_file.write_text("cancel", encoding="utf-8")
        threading.Thread(target=self._stop_for_good, args=(job,), daemon=True).start()
        return True

    def _stop_for_good(self, job):
        deadline = time.time() + self.grace
        while time.time() < deadline:
            if job.proc.poll() is not None:
                return
            time.sleep(0.2)
        kill_tree(job.proc.pid)

    def wait(self, timeout=30.0):
        """For the window closing: True once nothing is running."""
        end = time.time() + timeout
        while self.running and time.time() < end:
            time.sleep(0.1)
        return not self.running

    def close_guard(self):
        """True when the window may close. A running job holds the first attempt back; a second one within 15 s cancels it."""
        if not self.running:
            return True
        now = self.clock()
        if now - self.close_asked < 15:
            self.cancel()
            self.wait(self.grace + 5)
            return True
        self.close_asked = now
        self.session.log("Closing was held back: a video job is still running. Close the window again within 15 seconds "
                         "to cancel it.", "error")
        return False

    # -- reading the output ----------------------------------------------------------------------

    def _pump(self, job):
        try:
            for raw in iter(job.proc.stdout.readline, b""):
                for text in raw.decode("utf-8", "replace").replace("\r", "\n").split("\n"):
                    if text.strip():
                        self._line(job, text.rstrip())
        except (OSError, ValueError):
            pass
        finally:
            code = job.proc.wait()
            self._finish(job, code)

    def _line(self, job, text):
        text = text[:400]
        with self.lock:
            job.lines.append(text)
            job.total += 1
            m = RE_ITEM.match(text)
            if m:
                job.index, job.count, job.item = int(m.group(1)), int(m.group(2)), m.group(3)
                job.percent = job.duration = job.progress_file = None
                return
            m = RE_DURATION.match(text)
            if m:
                job.duration = float(m.group(1)) or None
                return
            m = RE_PROGRESS_FILE.match(text)
            if m:
                job.progress_file = m.group(1)
                return
            m = RE_PERCENT.search(text)
            if m:
                job.percent = min(100, int(m.group(1)))
            if RE_SUMMARY.match(text):
                job.summary = text
            elif text.startswith("lm: warning: "):
                job.warning = job.warning or text[13:]          # the first one is the cause, the rest are consequences
            elif text.startswith("lm: "):
                job.error = text[4:]
            m = RE_ROW.match(text)
            if m:
                job.rows.append({"tag": m.group(1), "text": m.group(2)})

    def _finish(self, job, code):
        with self.lock:
            job.code, job.ended = code, self.clock()
            job.status = "cancelled" if job.cancelling and code != 0 else "done" if code == 0 else "failed"
            if job.status == "done":
                job.percent = 100
            job.progress_file = None
            item = {"id": job.id, "kind": job.kind, "title": job.title, "command": job.command, "status": job.status,
                    "code": code, "started": job.started, "ended": job.ended,
                    "summary": self._summary(job) or ""}
            self.history = (self.history + [item])[-HISTORY:]
            history = list(self.history)
        with contextlib.suppress(OSError):
            job.cancel_file.unlink()
        self.session.update_history("video_jobs", history)
        self.session.log(f"Video: \"{job.title}\" {'finished' if job.status == 'done' else job.status}"
                         f"{'' if job.status == 'done' else f' (exit {code})'}", "success" if job.status == "done" else "error")
        self._after_job(job)

    # -- the queue -------------------------------------------------------------------------------

    def submit(self, kind, params):
        """What the buttons call: starts it now when nothing is going on, otherwise puts it in the queue."""
        params = params or {}
        if kind not in QUEUEABLE:
            return {"queued": False, "id": self.start(kind, params)}        # refused with a reason while a job runs
        with self.lock:
            if not (self.running or self.queue or self.paused):
                return {"queued": False, "id": self.start(kind, params)}
            argv, title, _meta = build(kind, params, exists=False)         # the form must be right now; files may come later
            if len(self.queue) >= QUEUE_LIMIT:
                raise PolicyError(f"The queue is full ({QUEUE_LIMIT} items)")
            self.queue_counter += 1
            self.queue.append({"id": self.queue_counter, "kind": kind, "params": dict(params), "title": title,
                               "command": "lm " + display(argv)})
            position = len(self.queue)
            self._save_queue()
        self.session.log(f"Video: queued \"{title}\" (number {position})")
        return {"queued": True, "id": self.queue_counter, "position": position}

    def _save_queue(self):
        items = [{"kind": i["kind"], "params": i["params"], "title": i["title"], "command": i["command"]} for i in self.queue]
        self.session.update_history("video_queue", items)

    def _advance(self):
        """Start the next waiting item when nothing runs and the queue is not paused. An item that cannot start (a file
        that never appeared, say) is recorded as failed, and stops the queue unless it keeps going."""
        while True:
            with self.lock:                                 # taking the item and starting it is one step: no gap in between
                if self.closing or self.running or self.paused or not self.queue:
                    return
                item = self.queue.pop(0)
                self._save_queue()
                try:
                    self.start(item["kind"], item["params"])
                    return
                except PolicyError as exc:
                    failure = str(exc)
            self._record_unstarted(item, failure)
            if not self.keep_going:
                with self.lock:
                    if self.queue:
                        self.paused, self.pause_reason = True, "failed"
                return

    def _record_unstarted(self, item, message):
        with self.lock:
            self.counter += 1
            now = self.clock()
            self.history = (self.history + [{"id": self.counter, "kind": item["kind"], "title": item["title"], "command": item["command"],
                                             "status": "failed", "code": None, "started": now, "ended": now, "summary": message}])[-HISTORY:]
            history = list(self.history)
        self.session.update_history("video_jobs", history)
        self.session.log(f"Video: \"{item['title']}\" could not start: {message}", "error")

    def _after_job(self, job):
        with self.lock:
            if job.status == "cancelled":
                if self.queue:
                    self.paused, self.pause_reason = True, "cancelled"      # you stopped it: nothing carries on by itself
            elif job.status == "failed" and self.queue and not self.keep_going:
                self.paused, self.pause_reason = True, "failed"
        if job.status != "cancelled":
            self._advance()
            self._arm_countdown()

    def _settled(self):
        """Nothing is running and nothing will start by itself: the queue is empty, or it stopped because a job failed."""
        return not self.running and (not self.queue or (self.paused and self.pause_reason == "failed"))

    def _arm_countdown(self):
        with self.lock:
            if self.closing or self.after == "nothing" or self.countdown or not self._settled():
                return
            self.countdown_token += 1
            token, action = self.countdown_token, self.after
            self.countdown = {"action": action, "until": self.clock() + self.after_delay, "token": token}
        words = {"sleep": "go to sleep", "hibernate": "hibernate", "shutdown": "shut down"}[action]
        self.session.log(f"Video: everything is finished; the computer will {words} in {self.after_delay:g} seconds unless you cancel")
        self.notify("Load Manager", f"All done. The computer will {words} in {self.after_delay:g} seconds. Open Load Manager to cancel.")
        timer = threading.Timer(self.after_delay, self._fire, args=(token,))
        timer.daemon = True
        timer.start()

    def _fire(self, token):
        with self.lock:
            if not self.countdown or self.countdown["token"] != token:
                return
            action, self.countdown = self.countdown["action"], None
            if not self._settled():
                return
            self.after = "nothing"                                          # one shot: never a surprise the next time
        self.session.log(f"Video: {action} now")
        try:
            self.power(action)
        except Exception as exc:                                            # reported, never raised into a timer thread
            with self.lock:
                self.after_error = str(exc) or type(exc).__name__
            self.session.log(f"Video: could not {action}: {self.after_error}", "error")

    def queue_remove(self, item_id):
        with self.lock:
            self.queue = [i for i in self.queue if i["id"] != item_id]
            if not self.queue and self.pause_reason != "you":
                self.paused, self.pause_reason = False, None
            self._save_queue()
        return self.queue_view()

    def queue_move(self, item_id, delta):
        with self.lock:
            index = next((n for n, i in enumerate(self.queue) if i["id"] == item_id), None)
            if index is None:
                raise PolicyError("That item is no longer in the queue")
            target = index + (-1 if delta < 0 else 1)
            if 0 <= target < len(self.queue):
                self.queue[index], self.queue[target] = self.queue[target], self.queue[index]
                self._save_queue()
        return self.queue_view()

    def queue_clear(self):
        with self.lock:
            self.queue = []
            if self.pause_reason != "you":
                self.paused, self.pause_reason = False, None
            self._save_queue()
        return self.queue_view()

    def queue_pause(self, paused):
        with self.lock:
            self.paused, self.pause_reason = bool(paused), "you" if paused else None
        if not paused:
            self._advance()
            self._arm_countdown()
        return self.queue_view()

    def queue_configure(self, keep_going=None, after=None):
        with self.lock:
            if after is not None:
                if after not in available_after():
                    raise PolicyError("This computer cannot do that: choose " + ", ".join(available_after()))
                self.after, self.after_error = after, None
                if after == "nothing":
                    self.countdown = None
            if keep_going is not None:
                self.keep_going = bool(keep_going)
        if keep_going is not None:
            self.session.update_settings("video_queue", {"keep_going": self.keep_going})
        return self.queue_view()

    def cancel_countdown(self):
        """The countdown before sleeping or shutting down: stop it and forget the action."""
        with self.lock:
            had = self.countdown is not None
            self.countdown, self.after = None, "nothing"
        if had:
            self.session.log("Video: the countdown was cancelled; the computer stays on")
        return self.queue_view()

    def shutdown(self):
        """The window is closing for good: nothing new starts, a running job is cancelled."""
        with self.lock:
            self.closing, self.countdown = True, None
        self.cancel()
        self.wait(self.grace + 5)

    # -- things the forms ask about --------------------------------------------------------------

    def overview(self):
        """The work folder, its projects and the free disk space (what `lm status` prints), read straight from disk."""
        work = self.work_path()
        projects = []
        if work.is_dir():
            with contextlib.suppress(OSError):
                for entry in sorted(work.iterdir(), key=lambda e: e.name.lower()):
                    if entry.is_dir() and not entry.name.startswith(".") and len(projects) < 200:
                        parts = {sub: {"bytes": video.tree_size(entry / sub), "files": video.count_files(entry / sub)}
                                 for sub in ("src", "proxy", "out")}
                        projects.append({"name": entry.name, **parts})
        return {"work": str(work), "exists": work.is_dir(), "free": video.free_bytes(work), "projects": projects,
                "ffmpeg": self.ffmpeg_path(), "system": video.system(), "time": self.clock()}

    @staticmethod
    def kit():
        base = install_claude.claude_dir()
        error, hook = None, False
        try:
            hook = install_claude.has_hook(install_claude.read_settings(base / "settings.json"))
        except (install_claude.InstallError, OSError) as exc:
            error = str(exc)
        launcher = base / "loadmanager" / ("lm.cmd" if os.name == "nt" else "lm")
        skill = (base / "skills" / install_claude.SKILL_NAME / "SKILL.md").is_file()
        return {"dir": str(base), "hook": hook, "skill": skill, "launcher": launcher.is_file(),
                "installed": hook and skill and launcher.is_file(), "error": error}

    def inspect(self, value):
        path = Path(_path(value, "Path", "any"))
        is_dir = path.is_dir()
        return {"path": str(path), "name": path.name or str(path), "dir": is_dir, "cloud": mentions_cloud(str(path)),
                "size": None if is_dir else video.fsize(path), "project": video.project_from(path),
                "inside_work": self._inside_work(path)}

    def _inside_work(self, path):
        with contextlib.suppress(OSError, ValueError):
            path.resolve().relative_to(self.work_path().resolve())
            return True
        return False

    def suggest_output(self, value, preset="fast", codec="h264"):
        source = Path(_path(value, "Input", "file"))
        if preset not in ("proxy", "fast", "final"):
            raise PolicyError("Preset: choose proxy, fast or final")
        work = self.work_path()
        project = video.project_from(source)
        with contextlib.suppress(OSError, ValueError):
            parts = source.resolve().relative_to(work.resolve()).parts
            if len(parts) >= 3 and parts[1] in ("src", "proxy", "out"):
                project = parts[0]
        suffix = {"proxy": "-proxy", "fast": "-fast", "final": "-final"}[preset] + ("-hevc" if codec == "hevc" and preset != "proxy" else "")
        return str(work / project / "out" / f"{source.stem}{suffix}.mp4")

    def reveal(self, kind, project="", path=""):
        """Show a folder (or a file, selected) in Explorer / Finder. A file is never opened, only shown."""
        work = self.work_path()
        if kind == "path":
            target = Path(_path(path, "Path", "any"))
        else:
            if kind not in ("work", "project", "src", "proxy", "out"):
                raise PolicyError("Unknown folder")
            target = work
            if kind != "work":
                target = work / _project({"project": project}, required=True) / ("" if kind == "project" else kind)
        while not target.exists() and target != target.parent:
            target = target.parent
        if not target.exists():
            raise PolicyError("That folder does not exist yet")
        if os.name == "nt":
            command = ["explorer", f"/select,{target}"] if target.is_file() else ["explorer", str(target)]
        elif video.system() == "Darwin":
            command = ["open", "-R", str(target)] if target.is_file() else ["open", str(target)]
        else:
            command = ["xdg-open", str(target.parent if target.is_file() else target)]
        try:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise PolicyError(f"Could not open the folder: {exc}") from exc
        return str(target)
