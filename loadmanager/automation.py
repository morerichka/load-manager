"""Hands-off helpers built on Session: start a session when a render begins, and time a real
command with and without one. Nothing here terminates another program's processes; cancelling
a timed run only stops the command that run itself started."""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time

import psutil

from .engine import APPS, RENDER_APPS, PolicyError, heavy_keys, read_json


class Autopilot:
    """Starts the Render profile when a render tool is busy, ends it when the render is over.

    Only a session started here is ended here. A session the user started by hand is never
    touched, and one the user ended during a render is not started again until that render stops."""
    START_AFTER = 3.0      # seconds a render must keep running before the session starts
    GRACE = 20.0           # seconds without a render before the session ends (bridges gaps between ffmpeg calls)
    RETRY_AFTER = 60.0     # back-off after a failed start
    MIN_JOB = 3.0          # shorter runs are not logged
    KEEP = 30

    def __init__(self, session):
        self.session = session
        self.lock = threading.RLock()
        saved = read_json(session.settings_path, {}).get("auto", {})
        self.enabled = bool(saved.get("enabled", False))
        groups = saved.get("groups")
        known = heavy_keys()
        self.groups = [g for g in groups if g in known] if isinstance(groups, list) else list(known)
        self.groups = self.groups or list(known)
        grace = saved.get("grace")
        self.grace = float(grace) if isinstance(grace, (int, float)) and not isinstance(grace, bool) and 5 <= grace <= 600 else self.GRACE
        jobs = read_json(session.history_path, {}).get("render_jobs", [])
        self.jobs = jobs if isinstance(jobs, list) else []
        self.state_name = "off" if not self.enabled else "waiting"
        self.owned = False
        self.hold = False
        self.paused = False
        self.render_since = None
        self.last_render = None
        self.retry_at = 0.0
        self.error = None
        self.job = None

    # -- configuration ------------------------------------------------------------------------

    def configure(self, enabled=None, groups=None, grace=None):
        with self.lock:
            if groups is not None:
                known = heavy_keys()
                if not isinstance(groups, list) or not groups or any(g not in known for g in groups):
                    raise PolicyError("Select at least one known heavy app")
                self.groups = list(dict.fromkeys(groups))
            if grace is not None:
                if isinstance(grace, bool) or not isinstance(grace, (int, float)) or not 5 <= grace <= 600:
                    raise PolicyError("The grace period must be between 5 and 600 seconds")
                self.grace = float(grace)
            if enabled is not None:
                enabled = bool(enabled)
                if enabled != self.enabled:
                    self.session.log("Auto-start on render " + ("enabled" if enabled else "disabled"), "info")
                if not enabled and self.owned and self.session.entries:
                    self.session.log("The current session stays until you end it", "info")
                self.enabled = enabled
                if not enabled:
                    self.owned = False
                self.state_name = "waiting" if enabled else "off"
            self.session.update_settings("auto", {"enabled": self.enabled, "groups": self.groups, "grace": self.grace})
            return self.state()

    def pause(self):
        with self.lock:
            self.paused = True

    def resume(self):
        with self.lock:
            self.paused = False
            self.render_since = None

    def state(self):
        with self.lock:
            return {"enabled": self.enabled, "state": self.state_name, "groups": list(self.groups),
                    "available_groups": [{"key": k, "title": APPS[k]["title"]} for k in heavy_keys()],
                    "grace": self.grace, "start_after": self.START_AFTER, "error": self.error,
                    "owned": self.owned, "paused": self.paused, "jobs": list(reversed(self.jobs[-10:]))}

    # -- the loop -----------------------------------------------------------------------------

    def tick(self, processes, cores=1, now=None):
        """`processes` is the monitor's process list (name, cpu, created). A render tool counts as
        busy when it uses at least half a core."""
        now = time.time() if now is None else now
        with self.lock:
            if self.paused:
                return
            threshold = 50.0 / max(1, cores)
            busy = [p for p in processes if p["name"].lower() in RENDER_APPS and p["cpu"] >= threshold]
            self._track_job(busy, now)
            if not self.enabled:
                self.state_name = "off"
                return
            live = bool(self.session.entries)
            if self.owned and not live:                      # the user ended our session by hand
                self.owned, self.hold = False, bool(busy)
            if busy:
                self.last_render = now
                if self.render_since is None:
                    self.render_since = now
                if live:
                    self.state_name = "active" if self.owned else "manual"
                elif self.hold:
                    self.state_name = "held"
                elif now < self.retry_at:
                    self.state_name = "retry"
                elif now - self.render_since >= self.START_AFTER:
                    self._start(now)
                else:
                    self.state_name = "pending"
                return
            self.render_since = None
            self.hold = False
            if self.owned and live:
                if now - (self.last_render if self.last_render is not None else now) >= self.grace:
                    self._stop()
                else:
                    self.state_name = "ending"
            else:
                self.state_name = "manual" if live else "waiting"

    def _start(self, now):
        try:
            result = self.session.apply([{"group": g} for g in self.groups], "render")
        except (PolicyError, psutil.Error, OSError) as exc:
            self.error, self.retry_at, self.state_name = str(exc), now + self.RETRY_AFTER, "retry"
            self.session.log(f"Auto-start skipped: {exc}", "error")
            return
        self.error = None
        self.owned = bool(result["session"])
        self.hold = not self.owned                           # a trim-only result must not repeat every tick
        self.state_name = "active" if self.owned else "held"
        if self.job is not None:
            self.job["auto_session"] = True
        self.session.log("Auto-start: render detected, session started", "action")

    def _stop(self):
        try:
            result = self.session.restore(include_power=False)
        except Exception as exc:                             # retried on the next tick
            self.error, self.state_name = str(exc), "ending"
            self.session.log(f"Auto-stop failed: {exc}", "error")
            return
        if result["remaining"]:
            self.error, self.state_name = "Some processes could not be restored; retrying", "ending"
            return
        self.owned, self.error, self.state_name = False, None, "waiting"
        self.session.log("Auto-stop: render finished, session ended", "success")

    def _track_job(self, busy, now):
        if busy:
            if self.job is None:
                self.job = {"started": min(p["created"] for p in busy), "tools": set(), "auto_session": False}
            self.job["tools"].update(p["name"] for p in busy)
            self.job["last"] = now
        elif self.job is not None and now - self.job["last"] >= self.grace:
            job, self.job = self.job, None
            seconds = job["last"] - job["started"]
            if seconds >= self.MIN_JOB:
                self.jobs.append({"started": round(job["started"]), "seconds": round(seconds, 1),
                                  "tools": sorted(job["tools"]), "auto_session": job["auto_session"]})
                del self.jobs[:-self.KEEP]
                self.session.update_history("render_jobs", self.jobs)


def compare_runs(results, limit=3):
    """Latest successful run without a session against the latest with one, per command."""
    commands = []
    for r in reversed(results):
        if r["command"] not in commands:
            commands.append(r["command"])
    out = []
    for command in commands[:limit]:
        latest = {}
        for r in reversed(results):
            if r["command"] == command and r["exit_code"] == 0 and r["mode"] not in latest:
                latest[r["mode"]] = r
        base, sess = latest.get("baseline"), latest.get("session")
        item = {"command": command, "baseline": base and base["seconds"], "session": sess and sess["seconds"],
                "saved_seconds": None, "saved_percent": None}
        if base and sess and base["seconds"] > 0:
            item["saved_seconds"] = round(base["seconds"] - sess["seconds"], 1)
            item["saved_percent"] = round((base["seconds"] - sess["seconds"]) / base["seconds"] * 100, 1)
        out.append(item)
    return out


class TimedRun:
    """Runs one command you typed, without a session, with one, or both, and times it.

    The command is started directly (no shell), only from an explicit request, and stays your own
    child process. Only the command's own run time is measured; applying and restoring are not."""
    KEEP = 20
    MODES = {"baseline", "session", "ab"}
    ORDERS = {"baseline_first", "session_first"}

    def __init__(self, session):
        self.session = session
        self.lock = threading.Lock()
        self.running = False
        self.mode = self.phase = self.phase_started = self.error = None
        self.cancelled = False
        self.proc = None
        results = read_json(session.history_path, {}).get("timed_runs", [])
        self.results = results if isinstance(results, list) else []
        saved = read_json(session.settings_path, {}).get("timed", {})
        self.command = saved.get("command", "") if isinstance(saved.get("command"), str) else ""
        self.cwd = saved.get("cwd", "") if isinstance(saved.get("cwd"), str) else ""
        self.order = saved.get("order") if saved.get("order") in self.ORDERS else "baseline_first"

    def state(self):
        with self.lock:
            return {"running": self.running, "mode": self.mode, "phase": self.phase,
                    "phase_started": self.phase_started, "now": time.time(), "error": self.error,
                    "command": self.command, "cwd": self.cwd, "order": self.order,
                    "results": list(reversed(self.results[-10:])), "comparisons": compare_runs(self.results)}

    # -- request ------------------------------------------------------------------------------

    @staticmethod
    def _argv(command):
        if os.name == "nt":
            if not shlex.split(command, posix=False):
                raise PolicyError("Enter a command")
            return command                                   # CreateProcess parses it; no shell involved
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise PolicyError(f"Could not parse the command: {exc}") from exc
        if not argv:
            raise PolicyError("Enter a command")
        return argv

    def _check_session_possible(self):
        if self.session.entries:
            return                                           # an existing session is used as it is
        if not self.session.guardian_ok():
            raise PolicyError("The recovery guardian is not ready yet")
        # raises when none of the selected heavy apps is running: there would be nothing to compare
        self.session.plan([{"group": g} for g in self.session.autopilot.groups], "render")

    def start(self, command, cwd="", mode="ab", order="baseline_first"):
        if mode not in self.MODES:
            raise PolicyError("Unknown mode")
        if order not in self.ORDERS:
            raise PolicyError("Unknown order")
        command = command.strip() if isinstance(command, str) else ""
        if not command or len(command) > 4000 or "\n" in command or "\r" in command or "\0" in command:
            raise PolicyError("Enter a single-line command")
        cwd = cwd.strip() if isinstance(cwd, str) else ""
        if cwd and not os.path.isdir(cwd):
            raise PolicyError("The working folder does not exist")
        argv = self._argv(command)
        if mode != "session" and self.session.entries:
            raise PolicyError("End the current session before timing a run without one")
        if mode != "baseline":
            self._check_session_possible()
        with self.lock:
            if self.running:
                raise PolicyError("A timed run is already in progress")
            if self.session.benchmark.running:
                raise PolicyError("Wait for the CPU benchmark to finish")
            self.running, self.error, self.cancelled, self.mode = True, None, False, mode
            self.command, self.cwd, self.order = command, cwd, order
        self.session.update_settings("timed", {"command": command, "cwd": cwd, "order": order})
        threading.Thread(target=self._run, args=(command, argv, cwd or None, mode, order), daemon=True).start()

    def cancel(self):
        with self.lock:
            self.cancelled = True
            proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                root = psutil.Process(proc.pid)
                for child in root.children(recursive=True):
                    child.kill()
                root.kill()
            except psutil.Error:
                pass

    # -- the run ------------------------------------------------------------------------------

    def _run(self, command, argv, cwd, mode, order):
        phases = {"baseline": ["baseline"], "session": ["session"],
                  "ab": ["baseline", "session"] if order == "baseline_first" else ["session", "baseline"]}[mode]
        self.session.autopilot.pause()                       # a render must not start a session mid-baseline
        self.session.quiet.render_begin()                    # both phases run on the same (render) power plan
        try:
            for phase in phases:
                if self.cancelled:
                    break
                result = self._one(phase, command, argv, cwd)
                if self.cancelled:
                    self.error = "Cancelled"
                    break
                self._record(result)
                if result["exit_code"] != 0:
                    self.error = result["error"] or f"The command exited with code {result['exit_code']}"
                    break
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
        finally:
            self.session.quiet.render_end()
            self.session.autopilot.resume()
            with self.lock:
                self.running, self.phase, self.phase_started, self.proc = False, None, None, None

    def _one(self, phase, command, argv, cwd):
        owned, freed = False, 0.0
        self.phase, self.phase_started = phase, None
        try:
            if phase == "session" and not self.session.entries:
                applied = self.session.apply([{"group": g} for g in self.session.autopilot.groups], "render")
                owned, freed = bool(applied["session"]), applied["freed_mb"]
            seconds, code, error = self._execute(argv, cwd)
        finally:
            if owned:
                self.session.restore(include_power=False)
        return {"id": int(time.time() * 1000), "command": command, "cwd": cwd or "", "mode": phase,
                "seconds": round(seconds, 1), "exit_code": code, "error": error, "freed_mb": freed,
                "started": round(time.time() - seconds)}

    def _execute(self, argv, cwd):
        """Run the command and return (seconds, exit code, error text)."""
        log_path = self.session.directory / "timed-last.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            sink = open(log_path, "wb")
        except OSError:
            sink = subprocess.DEVNULL
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.phase_started = time.time()
        began = time.perf_counter()
        try:
            try:
                proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=sink,
                                        stderr=subprocess.STDOUT, creationflags=flags)
            except OSError as exc:
                return 0.0, None, f"Could not start the command: {exc.strerror or type(exc).__name__}"
            self.proc = proc
            if self.cancelled:                               # cancelled while the command was starting
                self.cancel()
            code = proc.wait()
        finally:
            if sink is not subprocess.DEVNULL:
                sink.close()
        seconds = time.perf_counter() - began
        error = None
        if code != 0 and not self.cancelled:
            try:
                tail = log_path.read_bytes()[-300:].decode("utf-8", "replace").strip()
            except OSError:
                tail = ""
            error = f"The command exited with code {code}" + (f": {tail}" if tail else "")
        return seconds, code, error

    def _record(self, result):
        label = "without a session" if result["mode"] == "baseline" else "with a session"
        self.session.log(f"Timed run {label}: {result['seconds']} s (exit code {result['exit_code']})",
                         "success" if result["exit_code"] == 0 else "error")
        self.results.append(result)
        del self.results[:-self.KEEP]
        self.session.update_history("timed_runs", self.results)
