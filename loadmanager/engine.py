"""Monitoring and reversible actions. Never terminates processes or deletes caches."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import statistics
import threading
import time
from collections import Counter, deque
from pathlib import Path

import psutil

from . import winapi

# Win32 priority classes. psutil only exposes the constants on Windows.
IDLE, BELOW_NORMAL, NORMAL, ABOVE_NORMAL = 64, 16384, 32, 32768
# Trimming a working set only helps when RAM is short; otherwise it just causes page faults.
TRIM_AT_MEMORY_PERCENT = 70
MAX_SELECTIONS, MAX_TARGETS = 40, 400

# What Load Manager may touch. Names alone are insufficient; owner, executable path,
# PID birth time, and ancestry are checked again immediately before each action.
#   heavy: apps that really hold CPU and RAM during work. Windows only, chosen as a whole
#          group so tabs and helper processes that appear later are covered too.
#   light: launchers that idle near 0% CPU. Chosen per process; suspended off Windows.
# Claude is trim-only: lowering its priority would make every render tool it starts
# inherit the lower class.
APPS = {
    "chrome": {"title": "Google Chrome", "kind": "heavy", "names": {"chrome.exe"},
               "actions": ("priority", "eco", "trim")},
    "claude": {"title": "Claude", "kind": "heavy", "names": {"claude.exe"},
               "actions": ("trim",)},
    "telegram": {"title": "Telegram", "kind": "heavy", "names": {"telegram.exe"},
                 "actions": ("priority", "eco", "trim")},
    "gdrive": {"title": "Google Drive (sync)", "kind": "heavy",
               "names": {"googledrivefs.exe", "googledrivesync.exe"},
               "actions": ("priority", "eco", "trim")},
    "spotify": {"title": "Spotify", "kind": "light", "names": {"spotify", "spotify.exe"},
                "actions": ("priority",)},
    "steam": {"title": "Steam", "kind": "light", "names": {"steam", "steam.exe"},
              "actions": ("priority",)},
    "epic": {"title": "Epic Games Launcher", "kind": "light",
             "names": {"epicgameslauncher", "epicgameslauncher.exe"}, "actions": ("priority",)},
    "discord": {"title": "Discord", "kind": "light", "names": {"discord", "discord.exe"},
                "actions": ("priority",)},
    "slack": {"title": "Slack", "kind": "light", "names": {"slack", "slack.exe"},
              "actions": ("priority",)},
}
_APP_BY_NAME = {name: key for key, spec in APPS.items() for name in spec["names"]}
BACKGROUND_APPS = {n for spec in APPS.values() if spec["kind"] == "light" for n in spec["names"]}
WORK_APPS = {
    "code", "code.exe", "claude", "claude.exe", "codex", "codex.exe",
    "chatgpt", "chatgpt.exe", "ffmpeg", "ffmpeg.exe", "python", "python3",
    "python.exe", "pythonw.exe", "node", "node.exe", "premiere pro",
    "adobe premiere pro.exe", "resolve", "resolve.exe", "final cut pro",
    "chrome", "chrome.exe", "firefox", "firefox.exe", "safari", "msedge.exe",
}
RENDER_APPS = {"ffmpeg", "ffmpeg.exe", "resolve", "resolve.exe",
               "adobe premiere pro.exe", "premiere pro", "final cut pro"}


def classify(name):
    return _APP_BY_NAME.get(name.lower())


def heavy_keys():
    return [key for key, spec in APPS.items() if spec["kind"] == "heavy"]


class PolicyError(ValueError):
    pass


def state_directory() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return base / "LoadManager"
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support/LoadManager"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "loadmanager"


class FileLock:
    """OS-owned lock: released even after a crash. One byte for Windows."""
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def acquire(self, blocking=True):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = open(self.path, "a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError:
            self.file.close()
            self.file = None
            raise
        return self

    def release(self):
        if self.file:
            self.file.close()
            self.file = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def identity(process):
    return {"pid": process.pid, "created": process.create_time(),
            "name": process.name(), "exe": process.exe(), "owner": process.username()}


def same_process(process, item):
    return (process.pid == item["pid"] and
            abs(process.create_time() - item["created"]) < 0.001 and
            process.exe() == item["exe"] and
            process.name() == item["name"] and process.username() == item["owner"])


class Policy:
    def __init__(self, provider=psutil, own_pid=None, system=None):
        self.provider = provider
        self.own_pid = own_pid or os.getpid()
        self.system = system or platform.system()
        self.monitor_only = False
        if provider is psutil and platform.system() == "Linux":
            # Some hosted sandboxes expose a host /proc with namespace-local
            # syscall PIDs. Never send signals using that mismatched PID table.
            try:
                proc_pid = int(os.readlink("/proc/self"))
                if proc_pid != os.getpid():
                    self.own_pid = proc_pid
                    self.monitor_only = True
            except (OSError, ValueError):
                self.monitor_only = True
        self.owner = provider.Process(self.own_pid).username()

    def related(self):
        """PIDs of this app's ancestors and descendants; never action targets."""
        try:
            own = self.provider.Process(self.own_pid)
            return {p.pid for p in own.parents()} | {p.pid for p in own.children(recursive=True)}
        except (psutil.Error, OSError):
            return set()

    def app_key(self, process):
        key = classify(process.name())
        if key is None or (APPS[key]["kind"] == "heavy" and self.system != "Windows"):
            return None
        return key

    def allowed(self, process, suspending=None, related=None):
        """`suspending` enables the launcher-contains-work check; it only matters when
        the action stops a whole process tree. Priority changes do not touch children."""
        if suspending is None:
            suspending = self.system != "Windows"
        try:
            if self.monitor_only:
                return False
            if process.pid <= 1 or process.pid == self.own_pid:
                return False
            if self.app_key(process) is None or process.username() != self.owner:
                return False
            if not process.exe():
                return False
            if process.pid in (self.related() if related is None else related):
                return False
            # Never pause a launcher that contains a work process in its tree.
            if suspending and any(p.name().lower() in WORK_APPS for p in process.children(recursive=True)):
                return False
            return True
        except (psutil.Error, OSError):
            return False

    def boostable(self, process):
        """Render tools a running session may lift above Normal priority."""
        try:
            if self.monitor_only or self.system != "Windows":
                return False
            if process.pid <= 1 or process.pid == self.own_pid:
                return False
            if process.name().lower() not in RENDER_APPS or process.username() != self.owner:
                return False
            return bool(process.exe())
        except (psutil.Error, OSError):
            return False


class Session:
    def __init__(self, directory=None, provider=psutil, system=None, journal=None, native=None, powercfg="auto", wake="auto"):
        self.provider = provider
        self._system = system or platform.system()
        self.policy = Policy(provider, system=self._system)
        self.native = native if native is not None else (
            winapi if self._system == "Windows" and winapi.available() else None)
        self.memory_percent = lambda: psutil.virtual_memory().percent
        self.directory = Path(directory or state_directory())
        own = provider.Process(self.policy.own_pid)
        self.path = Path(journal) if journal else self.directory / f"session-{own.pid}-{int(own.create_time()*1000)}.json"
        self.lock_path = self.path.with_suffix(".lock")
        self.entries = []
        self.inherit = {}
        self.mutex = threading.RLock()
        self.events = deque(maxlen=100)
        self.profile = None
        self.guardian_ready = False
        self.guardian_check = lambda: True      # launch() replaces it with a liveness check
        self.started = None
        self.stats = {}
        self.benchmark = Benchmark(self)
        self.settings_path = self.directory / "settings.json"
        self.history_path = self.directory / "history.json"
        self.store_lock = threading.Lock()
        from .automation import Autopilot, TimedRun
        self.autopilot = Autopilot(self)
        self.timed = TimedRun(self)
        from .power import Powercfg, PowerGuard
        from .recorder import SpikeRecorder
        if powercfg == "auto":
            powercfg = Powercfg() if self._system == "Windows" and Powercfg.available() else None
        if wake == "auto":                       # only with the real powercfg: test doubles never hold a real request
            wake = winapi.WakeRequest() if isinstance(powercfg, Powercfg) and winapi.available() else None
        self.quiet = PowerGuard(self, powercfg, wake)
        self.recorder = SpikeRecorder(self)
        from .jobs import VideoJobs
        self.video = VideoJobs(self)
        self._render_held = False

    @property
    def system(self):
        return self._system

    @system.setter
    def system(self, value):
        self._system = value
        self.policy.system = value

    def log(self, text, kind="info"):
        self.events.appendleft({"time": time.time(), "text": text, "kind": kind})

    def _save(self):
        atomic_json(self.path, {"version": 1, "entries": self.entries, "inherit": self.inherit,
                                "power": self.quiet.entry})

    def work_in_progress(self):
        """Something is being worked on: a session, a timed run, a video job, a render tool, or a busy period."""
        return (bool(self.entries) or self.timed.running or self.video.running or self.autopilot.job is not None
                or self.recorder.current is not None)

    def guardian_ok(self):
        return self.guardian_ready and bool(self.guardian_check())

    def update_settings(self, section, values):
        """Small user preferences (auto-start, last timed command). Written only when they change."""
        self._update_store(self.settings_path, section, lambda old: {**old, **values}, {})

    def update_history(self, key, items):
        self._update_store(self.history_path, key, lambda old: list(items), [])

    def _update_store(self, path, key, change, default):
        with self.store_lock:
            data = read_json(path, {})
            if not isinstance(data, dict):
                data = {}
            old = data.get(key, default)
            data[key] = change(old if isinstance(old, type(default)) else default)
            try:
                atomic_json(path, data)
            except OSError as exc:
                self.log(f"Could not save {path.name}: {type(exc).__name__}", "error")

    def _expand(self, selections):
        """Selections are explicit PIDs or {"group": key}. A group is re-enumerated now, so
        browser tabs that opened or closed since the list was drawn are handled."""
        if not isinstance(selections, list) or not 1 <= len(selections) <= MAX_SELECTIONS:
            raise PolicyError("Select between 1 and 40 apps or processes")
        expanded, seen = [], set()
        for item in selections:
            if not isinstance(item, dict):
                raise PolicyError("Invalid process")
            if "group" in item:
                spec = APPS.get(item["group"]) if isinstance(item["group"], str) else None
                if not spec or spec["kind"] != "heavy":
                    raise PolicyError("Unknown app group")
                if self.system != "Windows":
                    raise PolicyError("App groups are available on Windows only")
                for p in self.provider.process_iter():
                    try:
                        if classify(p.name()) != item["group"] or p.username() != self.policy.owner:
                            continue
                        found = identity(p)
                    except (psutil.Error, OSError):
                        continue
                    if found["pid"] not in seen:
                        seen.add(found["pid"])
                        expanded.append((found, True))
                continue
            if type(item.get("pid")) is not int:
                raise PolicyError("Invalid process")
            if item["pid"] not in seen:
                seen.add(item["pid"])
                expanded.append((item, False))
        if len(expanded) > MAX_TARGETS:
            raise PolicyError("Too many processes selected")
        return expanded

    def _windows_actions(self, process, spec, profile, pressure):
        actions = []
        if "priority" in spec["actions"]:
            before = int(process.nice())
            after = IDLE if profile == "render" else BELOW_NORMAL
            # Never raise a process that was already less important.
            if before not in (IDLE, after):
                actions.append({"action": "priority", "before": before, "after": after})
        if "eco" in spec["actions"] and self.native:
            try:
                state = self.native.get_eco(process.pid)
            except OSError:
                state = None
            if state is not None and not (state[0] & 1 and state[1] & 1):
                actions.append({"action": "eco", "before": list(state),
                                "after": [state[0] | 1, state[1] | 1]})
        if "trim" in spec["actions"] and pressure:
            actions.append({"action": "trim"})
        return actions

    def plan(self, selections, profile):
        if profile not in {"focus", "render", "auto"}:
            raise PolicyError("Unknown profile")
        expanded = self._expand(selections)
        related = self.policy.related()
        windows = self.system == "Windows"
        pressure = bool(self.native) and self.memory_percent() >= TRIM_AT_MEMORY_PERCENT
        targets = []
        for item, soft in expanded:
            try:
                p = self.provider.Process(item["pid"])
                if not same_process(p, item) or not self.policy.allowed(p, not windows, related):
                    if soft:
                        continue
                    raise PolicyError("The process changed or is protected. Refresh the list.")
                if p.status() in {psutil.STATUS_STOPPED, getattr(psutil, "STATUS_TRACING_STOP", "tracing-stop")}:
                    if soft:
                        continue
                    raise PolicyError("The process was already suspended by another program")
                key = classify(p.name())
                base = {**identity(p), "app": key, "_soft": soft}
                actions = (self._windows_actions(p, APPS[key], profile, pressure)
                           if windows else [{"action": "suspend"}])
                targets.extend({**base, **action} for action in actions)
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                if soft:
                    continue
                raise PolicyError("The process is not accessible. Refresh the list.") from exc
        if not targets:
            raise PolicyError("Nothing to change for the selected processes")
        return targets

    def preview(self, selections, profile):
        """What apply() would do, without doing it."""
        targets = self.plan(selections, profile)
        apps = {}
        for t in targets:
            entry = apps.setdefault(t["app"], {"app": t["app"], "title": APPS[t["app"]]["title"],
                                               "pids": set(), "actions": Counter()})
            entry["pids"].add(t["pid"])
            entry["actions"][t["action"]] += 1
        return {"profile": profile, "apps": [
            {"app": e["app"], "title": e["title"], "processes": len(e["pids"]), "actions": dict(e["actions"])}
            for e in apps.values()]}

    def _perform(self, process, item):
        action = item["action"]
        if action == "priority":
            process.nice(item["after"])
        elif action == "suspend":
            process.suspend()
        elif action == "eco":
            self.native.set_eco(item["pid"], *item["after"])
        else:
            raise PolicyError("Unknown action")

    def _trim(self, process):
        before = process.memory_info().rss
        self.native.trim(process.pid)
        return max(0, before - process.memory_info().rss)

    def apply(self, selections, profile):
        with self.mutex, FileLock(self.lock_path):
            if self.entries:
                raise PolicyError("End the current session first")
            if not self.guardian_ok():
                raise PolicyError("The recovery guardian is not ready yet")
            targets = self.plan(selections, profile)
            per_process = {}
            for t in targets:
                per_process.setdefault((t["pid"], t["created"]), []).append(t)
            changed, counts, per_app, skipped, freed = set(), Counter(), {}, 0, 0
            windows, related = self.system == "Windows", self.policy.related()
            try:
                for actions in per_process.values():
                    first, soft = actions[0], actions[0]["_soft"]
                    try:
                        p = self.provider.Process(first["pid"])
                        if not same_process(p, first) or not self.policy.allowed(p, not windows, related):
                            raise PolicyError("The process changed before it could be modified")
                        if p.status() == psutil.STATUS_STOPPED:
                            raise PolicyError("The process was suspended by another program")
                    except (psutil.NoSuchProcess, psutil.AccessDenied, PolicyError):
                        # Browser helpers come and go; one gone mid-apply must not cancel the rest.
                        if not soft:
                            raise
                        skipped += 1
                        continue
                    journaled = [{k: v for k, v in a.items() if k != "_soft"}
                                 for a in actions if a["action"] != "trim"]
                    # Write intent before touching the OS, including before crash windows.
                    self.entries.extend(journaled)
                    if journaled:
                        self._save()
                    done = []
                    for entry in journaled:
                        try:
                            self._perform(p, entry)
                            done.append(entry["action"])
                        except (psutil.Error, OSError):
                            if not soft:
                                raise
                            self.entries.remove(entry)
                            self._save()
                    if any(a["action"] == "trim" for a in actions):
                        try:
                            freed += self._trim(p)
                            done.append("trim")
                        except (psutil.Error, OSError):
                            if not soft:
                                raise
                    if done:
                        changed.add(first["pid"])
                        for action in done:
                            counts[action] += 1
                            per_app.setdefault(first["app"], Counter())[action] += 1
                    else:
                        skipped += 1
            except Exception:
                self._restore_locked(include_power=False)
                raise
            if not changed:
                raise PolicyError("None of the processes could be changed. Refresh the list.")
            if self.entries:
                self.profile = profile
                self.started = time.time()
                self._remember_inherited()
                self._save()
                if profile == "render" and not self._render_held:
                    self._render_held = True
                    self.quiet._render_begin_locked()
            freed_mb = round(freed / 1024**2, 1)
            self.stats = {"processes": len(changed), "actions": dict(counts), "freed_mb": freed_mb,
                          "skipped": skipped, "boosted": 0, "profile": profile}
            self._log_applied(per_app, freed_mb)
            return {"applied": len(changed), "profile": profile, "actions": dict(counts),
                    "freed_mb": freed_mb, "skipped": skipped, "session": bool(self.entries)}

    def _log_applied(self, per_app, freed_mb):
        words = {"priority": "priority lowered", "eco": "efficiency mode", "suspend": "suspended",
                 "trim": "memory trimmed"}
        for key, counter in per_app.items():
            parts = ", ".join(f"{words[a]}: {n}" for a, n in counter.items())
            self.log(f"{APPS[key]['title']}: {parts}", "action")
        if freed_mb:
            self.log(f"RAM freed: {freed_mb} MB", "success")

    def _remember_inherited(self):
        """New browser/sync helpers inherit the lowered class from their parent. Remember the
        class so restore can raise them back; otherwise a tab opened mid-session stays slow."""
        by_app = {}
        for e in self.entries:
            if e["action"] == "priority" and APPS.get(e.get("app"), {}).get("kind") == "heavy":
                by_app.setdefault(e["app"], []).append(e)
        for key, items in by_app.items():
            before = Counter(e["before"] for e in items).most_common(1)[0][0]
            self.inherit[key] = {"before": before, "after": items[0]["after"], "since": time.time()}

    def watch(self):
        """Called every monitor tick. During a render session, lift render tools above Normal.
        A tool started by a lowered parent (for example from Claude) inherits the lowered class."""
        with self.mutex:
            if self.profile != "render" or self.system != "Windows" or self.policy.monitor_only:
                return 0
            self._prune_dead()
            tracked = {(e["pid"], e["created"]) for e in self.entries}
            found = []
            for p in self.provider.process_iter():
                try:
                    if p.name().lower() not in RENDER_APPS or not self.policy.boostable(p):
                        continue
                    if (p.pid, p.create_time()) in tracked or int(p.nice()) not in (IDLE, BELOW_NORMAL, NORMAL):
                        continue
                    found.append((p, {**identity(p), "app": "render", "action": "priority",
                                      "before": int(p.nice()), "after": ABOVE_NORMAL}))
                except (psutil.Error, OSError):
                    continue
            if not found:
                return 0
            boosted = 0
            with FileLock(self.lock_path):
                for p, entry in found:
                    self.entries.append(entry)
                    self._save()
                    try:
                        p.nice(ABOVE_NORMAL)
                        boosted += 1
                        self.log(f"{entry['name']}: priority raised for the render", "action")
                    except (psutil.Error, OSError):
                        self.entries.remove(entry)
                        self._save()
            self.stats["boosted"] = self.stats.get("boosted", 0) + boosted
            return boosted

    def _prune_dead(self):
        alive = []
        for e in self.entries:
            try:
                self.provider.Process(e["pid"])
                alive.append(e)
            except psutil.NoSuchProcess:
                continue
            except (psutil.Error, OSError):
                alive.append(e)
        if len(alive) != len(self.entries):
            self.entries = alive
            self._save()

    def _restore_inherited(self, handled):
        for key, info in self.inherit.items():
            for p in self.provider.process_iter():
                try:
                    if classify(p.name()) != key or p.pid in handled or p.username() != self.policy.owner:
                        continue
                    if p.create_time() >= info["since"] - 1 and int(p.nice()) == info["after"]:
                        p.nice(info["before"])
                except (psutil.Error, OSError):
                    continue

    def _restore_locked(self, include_power=True):
        if self.policy.monitor_only and self.entries:
            raise PolicyError("Monitoring only: this environment exposes an isolated process table")
        remaining, restored, per_app = [], set(), Counter()
        handled = {item["pid"] for item in self.entries}
        for item in self.entries:
            try:
                p = self.provider.Process(item["pid"])
                if not same_process(p, item):
                    self.log(f"PID {item['pid']}: different process, skipped", "info")
                    continue
                if item["action"] == "priority":
                    if int(p.nice()) != item["after"]:
                        self.log(f"{item['name']}: priority was changed by something else, left as is")
                        continue
                    p.nice(item["before"])
                elif item["action"] == "suspend":
                    if p.status() == psutil.STATUS_STOPPED:
                        p.resume()
                elif item["action"] == "eco":
                    if not self.native:
                        raise PolicyError("Efficiency mode is not available on this platform")
                    state = self.native.get_eco(item["pid"])
                    if state is None or not (state[0] & 1 and state[1] & 1):
                        self.log(f"{item['name']}: efficiency mode was changed by something else, left as is")
                        continue
                    self.native.set_eco(item["pid"], *item["before"])
                else:
                    raise PolicyError("Unknown action in the journal")
                restored.add(item["pid"])
                per_app[item.get("app") or item["name"]] += 1
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, OSError, PolicyError) as exc:
                remaining.append(item)
                self.log(f"{item['name']}: could not restore ({type(exc).__name__})", "error")
        for key, n in per_app.items():
            title = APPS[key]["title"] if key in APPS else "Render tools" if key == "render" else key
            self.log(f"{title}: actions restored: {n}", "success")
        self.entries = remaining
        if not remaining:
            try:
                self._restore_inherited(handled)
            finally:
                self.inherit = {}
        power_left = 0
        if include_power and self.quiet.entry:
            try:
                self.quiet._revert_locked()
            except (PolicyError, OSError) as exc:
                power_left = 1
                self.log(f"Power plan: {exc}", "error")
        self._save()
        if not remaining:
            self.profile = None
            self.started = None
            if self._render_held:
                self._render_held = False
                self.quiet._render_end_locked()
        return {"restored": len(restored), "remaining": len(remaining) + power_left}

    def restore(self, from_disk=False, include_power=True):
        """Puts everything back. include_power=False leaves quiet mode on (ending a session by hand)."""
        with self.mutex, FileLock(self.lock_path):
            if from_disk and self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("version") != 1:
                    raise PolicyError("Unknown journal version")
                self.entries = data["entries"]
                self.inherit = data.get("inherit", {})
                power = data.get("power")
                self.quiet.entry = power if self.quiet.valid_entry(power) else None
            return self._restore_locked(include_power)


class Benchmark:
    """CPU-contention probe: a fixed multi-threaded hashing job at normal priority.

    It shows how much processor a normal job gets while background apps compete. Scores are
    comparable only on the same machine under similar load. It does not measure disk or GPU."""
    ROUNDS, ROUND_SECONDS, KEEP = 3, 2.0, 8

    def __init__(self, session):
        self.session = session
        self.lock = threading.Lock()
        self.running = False
        self.progress = 0.0
        self.error = None
        self.results = []

    def start(self):
        with self.lock:
            if self.running or self.session.timed.running:
                return False
            self.running, self.progress, self.error = True, 0.0, None
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _round(self, threads, buffer):
        deadline = time.perf_counter() + self.ROUND_SECONDS
        counts = [0] * threads

        def work(index):
            digest, n = hashlib.sha256, 0
            while time.perf_counter() < deadline:
                digest(buffer).digest()
                n += 1
            counts[index] = n
        began = time.perf_counter()
        workers = [threading.Thread(target=work, args=(i,)) for i in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        return sum(counts) * len(buffer) / 1024**2 / (time.perf_counter() - began)

    def _run(self):
        try:
            threads = psutil.cpu_count() or 1
            background = psutil.cpu_percent(interval=0.5)
            active, profile = bool(self.session.entries), self.session.profile
            buffer, scores = bytes(1 << 20), []
            for i in range(self.ROUNDS):
                scores.append(self._round(threads, buffer))
                self.progress = (i + 1) / self.ROUNDS
            with self.lock:
                self.results.append({"time": time.time(), "active": active, "profile": profile,
                                     "score": round(statistics.median(scores)),
                                     "background_cpu": round(background, 1), "threads": threads})
                del self.results[:-self.KEEP]
        except Exception as exc:
            self.error = type(exc).__name__
        finally:
            self.running = False

    def state(self):
        with self.lock:
            results = list(self.results)
            base = next((r for r in reversed(results) if not r["active"]), None)
            now = next((r for r in reversed(results) if r["active"]), None)
            delta = round((now["score"] / base["score"] - 1) * 100, 1) if base and now and base["score"] else None
            return {"running": self.running, "progress": round(self.progress, 2), "results": results,
                    "baseline": base, "with_session": now, "delta_percent": delta, "error": self.error}


class Monitor:
    def __init__(self, session):
        self.session = session
        self.history = deque(maxlen=60)
        self.snapshot = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.previous_io = None
        self.previous_time = time.monotonic()
        self.threads = psutil.cpu_count() or 1
        self.started = time.time()
        from .recorder import ProcessTable
        self.table = ProcessTable()
        self.last_rows, self.last_parents = [], {}
        self.owner_cache = {}         # (pid, created) -> belongs to this user
        self.process_objects = {}     # (pid, created) -> psutil.Process, only for apps in the catalogue
        self._tick_errors = {}

    def sample(self):
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory()
        now = time.monotonic()
        disk_io = psutil.disk_io_counters()
        disk_rate = None
        if disk_io and self.previous_io:
            elapsed = max(0.01, now-self.previous_time)
            disk_rate = max(0, (disk_io.read_bytes+disk_io.write_bytes-self.previous_io)/elapsed/1024**2)
        self.previous_io = disk_io.read_bytes+disk_io.write_bytes if disk_io else None
        self.previous_time = now
        disk = psutil.disk_usage(Path.home().anchor)
        try:
            battery = psutil.sensors_battery()
        except (OSError, NotImplementedError):
            battery = None
        # One system call lists every process with its CPU and memory. Per-process psutil calls are used
        # only for the few apps in the catalogue: asking about all ~400 processes cost ~2 s of CPU per tick.
        rows, parents = self.table.read()
        self.last_rows, self.last_parents = rows, parents
        processes, seen = [], set()
        managed = {p["pid"] for p in self.session.entries}
        policy, suspending = self.session.policy, self.session.system != "Windows"
        related = policy.related()
        for r in rows:
            key = (r["pid"], r["created"])
            seen.add(key)
            mine = self.owner_cache.get(key)
            if mine is None:
                try:
                    mine = psutil.Process(r["pid"]).username() == policy.owner
                except psutil.AccessDenied:
                    mine = False                 # system services and other users: never actionable
                except (psutil.Error, OSError):
                    continue
                self.owner_cache[key] = mine
            if not mine:
                continue
            app = classify(r["name"])
            item = {"pid": r["pid"], "created": r["created"], "name": r["name"], "exe": "", "owner": policy.owner,
                    "cpu": round(r["cpu"], 1), "memory": round(r.get("rss", 0)/1024**2, 1), "status": None,
                    "eligible": False, "managed": r["pid"] in managed, "app": app,
                    "kind": APPS[app]["kind"] if app else None}
            if app:
                try:
                    p = self.process_objects.get(key) or self.process_objects.setdefault(key, psutil.Process(r["pid"]))
                    item.update(identity(p))      # exact exe, owner and creation time for actions
                    item["eligible"] = policy.allowed(p, suspending, related)
                except (psutil.Error, OSError):
                    pass
            item["protected"] = not item["eligible"]
            processes.append(item)
        self.owner_cache = {k: v for k, v in self.owner_cache.items() if k in seen}
        self.process_objects = {k: v for k, v in self.process_objects.items() if k in seen}
        processes.sort(key=lambda p: (p["cpu"], p["memory"]), reverse=True)
        groups = []
        for key, spec in APPS.items():
            if spec["kind"] != "heavy":
                continue
            members = [p for p in processes if p["app"] == key]
            groups.append({"key": key, "title": spec["title"], "actions": list(spec["actions"]),
                           "available": self.session.system == "Windows", "count": len(members),
                           "eligible": sum(p["eligible"] for p in members),
                           "managed": sum(p["managed"] for p in members),
                           "cpu": round(sum(p["cpu"] for p in members), 1),
                           "memory": round(sum(p["memory"] for p in members), 1)})
        # Include every eligible target even when it is not among the top consumers.
        top = processes[:30]
        top_ids = {p["pid"] for p in top}
        top.extend(p for p in processes if p["pid"] not in top_ids and (p["eligible"] or p["managed"]))
        heavy = any(p["name"].lower() in RENDER_APPS and p["cpu"] > 5 for p in processes)
        self.history.append({"cpu": round(cpu, 1), "memory": round(mem.percent, 1)})
        snapshot = {
            "demo": False, "platform": platform.system(), "machine": platform.machine(),
            "cpu_name": platform.processor() or f"{self.threads} logical cores", "cores": self.threads,
            "cpu": round(cpu, 1), "memory_percent": round(mem.percent, 1),
            "memory_total": round(mem.total/1024**3, 1),
            "memory_used": round((mem.total-mem.available)/1024**3, 1),
            "memory_available": round(mem.available/1024**3, 1),
            "disk_rate": round(disk_rate, 1) if disk_rate is not None else None,
            "disk_free": round(disk.free/1024**3, 1), "disk_percent": disk.percent,
            "battery": {"percent": battery.percent, "plugged": battery.power_plugged} if battery else None,
            "processes": top, "groups": groups, "history": list(self.history), "timestamp": time.time(),
            "recommendation": "render" if heavy else "focus", "trim_threshold": TRIM_AT_MEMORY_PERCENT,
        }
        with self.lock:
            self.snapshot = snapshot
        return snapshot

    def run(self):
        psutil.cpu_percent()
        while not self.stop_event.is_set():
            try:
                snapshot = self.sample()
            except Exception as exc:        # one bad tick must never end monitoring
                snapshot = None
                self._tick_failed("sampling", exc)
            if snapshot:
                steps = (("recorder", lambda: self.session.recorder.observe(snapshot["cpu"], self.last_rows, self.last_parents)),
                         ("render boost", self.session.watch),
                         ("auto-start", lambda: self.session.autopilot.tick(snapshot["processes"], self.threads)),
                         ("power plan", self.session.quiet.tick))
                for name, step in steps:
                    try:
                        step()
                    except Exception as exc:
                        self._tick_failed(name, exc)
            self.stop_event.wait(2)

    def _tick_failed(self, name, exc):
        message = f"{name} failed: {type(exc).__name__}"
        if self._tick_errors.get(name) != message:       # one line per distinct failure, not one per tick
            self._tick_errors[name] = message
            self.session.log(message, "error")

    def get(self):
        with self.lock:
            result = dict(self.snapshot or {"loading": True})
        with self.session.mutex:
            result.update(active=bool(self.session.entries), profile=self.session.profile,
                          changed=len(self.session.entries), events=list(self.session.events),
                          session_started=self.session.started, guardian_ready=self.session.guardian_ready,
                          stats=dict(self.session.stats))
        result["benchmark"] = self.session.benchmark.state()
        result["auto"] = self.session.autopilot.state()
        result["timed"] = self.session.timed.state()
        result["quiet"] = self.session.quiet.state()
        result["recorder"] = self.session.recorder.state()
        result["video"] = self.session.video.brief()
        return result
