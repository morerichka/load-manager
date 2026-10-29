"""Spike recorder: remembers busy periods and which programs were behind them.

Read-only. It looks at CPU use per process (system services included) every monitor tick, groups
the processes by program, and notes when the work was started by Claude (a descendant of a
claude.exe process) or by Load Manager itself. Process names only; command lines are never read."""
from __future__ import annotations

import os
import threading
import time

import psutil

from . import winapi
from .engine import APPS, PolicyError, classify, read_json


def psutil_table():
    """Fallback for other systems, where per-process calls are cheap."""
    table = {}
    for p in psutil.process_iter():
        try:
            with p.oneshot():
                t = p.cpu_times()
                table[p.pid] = {"ppid": p.ppid(), "name": p.name(), "created": p.create_time(),
                                "cpu_seconds": t.user + t.system, "rss": p.memory_info().rss}
        except psutil.Error:
            continue
    return table


class ProcessTable:
    """Turns two consecutive process tables into per-process CPU percent of the whole machine."""
    def __init__(self, reader=None, clock=time.monotonic):
        self.reader = reader or (winapi.process_table if winapi.available() else psutil_table)
        self.clock = clock
        self.cores = psutil.cpu_count() or 1
        self.previous, self.previous_time = {}, None

    def read(self):
        table, now = self.reader(), self.clock()
        elapsed = None if self.previous_time is None else now - self.previous_time
        rows, parents = [], {}
        for pid, e in table.items():
            parents[pid] = (e["ppid"], e["name"], e["created"])
            if not pid:
                continue
            cpu, before = 0.0, self.previous.get(pid)
            if elapsed and elapsed > 0 and before and before[0] == e["created"]:
                cpu = min(100.0, max(0.0, (e["cpu_seconds"] - before[1]) / (elapsed * self.cores) * 100))
            rows.append({"pid": pid, "ppid": e["ppid"], "name": e["name"], "cpu": cpu,
                         "created": e["created"], "rss": e.get("rss", 0)})
        self.previous = {pid: (e["created"], e["cpu_seconds"]) for pid, e in table.items()}
        self.previous_time = now
        return rows, parents


class _Acc:
    def __init__(self, started):
        self.started, self.last = started, started
        self.cpu_sum, self.samples, self.peak = 0.0, 0, 0.0
        self.total_seconds = 0.0                      # core-seconds the whole machine spent
        self.groups = {}

    def add(self, now, cpu, core_seconds, tick):
        self.last = now
        self.cpu_sum += cpu
        self.samples += 1
        self.peak = max(self.peak, cpu)
        self.total_seconds += core_seconds
        for key, g in tick.items():
            acc = self.groups.setdefault(key, {"name": g["name"], "via": g["via"], "seconds": 0.0, "peak": 0.0, "procs": 0})
            acc["seconds"] += g["cpu"] / 100 * g["cores"] * g["dt"]
            acc["peak"] = max(acc["peak"], g["cpu"])
            acc["procs"] = max(acc["procs"], len(g["pids"]))

    def culprits(self, limit):
        total = max(self.total_seconds, 1e-9)
        ordered = sorted(self.groups.values(), key=lambda g: -g["seconds"])
        return [{"name": g["name"], "via": g["via"], "processes": g["procs"], "cpu_seconds": round(g["seconds"], 1),
                 "share": round(min(100.0, g["seconds"] / total * 100), 1), "peak": round(g["peak"], 1)}
                for g in ordered[:limit]]

    def avg(self):
        return self.cpu_sum / self.samples if self.samples else 0.0


class SpikeRecorder:
    ON_AFTER = 6.0        # seconds above the threshold before a busy period counts
    OFF_AFTER = 10.0      # seconds below the lower threshold before it ends
    MIN_SECONDS = 8.0     # shorter ones are not kept
    KEEP = 50

    def __init__(self, session):
        self.session = session
        self.lock = threading.RLock()
        saved = read_json(session.settings_path, {}).get("recorder", {})
        self.enabled = bool(saved.get("enabled", True))
        on, off = saved.get("on"), saved.get("off")
        ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (on, off)) and 10 <= off < on <= 95
        self.on, self.off = (float(on), float(off)) if ok else (40.0, 25.0)
        spikes = read_json(session.history_path, {}).get("spikes", [])
        self.spikes = spikes if isinstance(spikes, list) else []
        self.current = self.rising = None
        self.cool_since = self.last_seen = None
        self.cores = psutil.cpu_count() or 1
        self.table = ProcessTable()

    def tick(self, cpu):
        """Called by the monitor: reads the process table (cheap) and updates the record."""
        if not self.enabled:
            return
        rows, parents = self.table.read()
        self.observe(cpu, rows, parents)

    def configure(self, enabled=None, on=None, off=None):
        with self.lock:
            new_on, new_off = self.on if on is None else on, self.off if off is None else off
            for value in (new_on, new_off):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise PolicyError("Thresholds must be numbers")
            if not 10 <= new_off < new_on <= 95:
                raise PolicyError("The end threshold must be at least 10% and lower than the start threshold (at most 95%)")
            self.on, self.off = float(new_on), float(new_off)
            if enabled is not None:
                self.enabled = bool(enabled)
                if not self.enabled:
                    self.current = self.rising = self.cool_since = None
            self.session.update_settings("recorder", {"enabled": self.enabled, "on": self.on, "off": self.off})
            return self.state()

    def clear(self):
        with self.lock:
            self.spikes = []
            self.session.update_history("spikes", [])
            return self.state()

    # -- the loop -----------------------------------------------------------------------------

    @staticmethod
    def _via(pid, parents):
        """"Load Manager", "Claude" or None: who started this process."""
        if pid == os.getpid():
            return "Load Manager"
        node = parents.get(pid)
        if node is None:
            return None
        own_name, current, seen = node[1].lower(), pid, {pid}
        for _ in range(12):
            ppid, _, created = parents[current]
            parent = parents.get(ppid)
            if parent is None or ppid in seen or parent[2] > created:      # gone, a loop, or a reused PID
                return None
            if ppid == os.getpid():
                return "Load Manager"
            if parent[1].lower() == "claude.exe" and own_name != "claude.exe":
                return "Claude"
            seen.add(ppid)
            current = ppid
        return None

    def _attribute(self, rows, parents, dt):
        groups = {}
        for r in rows:
            if r["cpu"] < 0.05:
                continue
            app = classify(r["name"])
            label = APPS[app]["title"] if app else r["name"]
            via = self._via(r["pid"], parents)
            g = groups.setdefault((label, via), {"name": label, "via": via, "cpu": 0.0, "pids": set(),
                                                  "cores": self.cores, "dt": dt})
            g["cpu"] += r["cpu"]
            g["pids"].add(r["pid"])
        return groups

    def observe(self, cpu, rows, parents, now=None):
        """cpu: whole-machine percent. rows: dicts with pid, ppid, name, cpu (percent of the machine).
        parents: pid -> (ppid, name, created)."""
        now = time.time() if now is None else now
        with self.lock:
            gap = None if self.last_seen is None else now - self.last_seen
            dt = gap if gap is not None and 0.0 < gap <= 6.0 else 2.0       # a stall or a sleep is not CPU time
            self.last_seen = now
            if not self.enabled:
                return
            hot = cpu >= (self.off if self.current else self.on)
            if hot:
                self.cool_since = None
                acc = self.current or self.rising
                if acc is None:
                    acc = self.rising = _Acc(now - dt)
                acc.add(now, cpu, cpu / 100 * self.cores * dt, self._attribute(rows, parents, dt))
                if self.current is None and now - acc.started >= self.ON_AFTER:
                    self.current, self.rising = acc, None
                return
            self.rising = None
            if self.current is not None:
                if self.cool_since is None:
                    self.cool_since = now
                elif now - self.cool_since >= self.OFF_AFTER:
                    self._finish()

    def _finish(self):
        acc = self.current
        self.current = self.cool_since = None
        seconds = acc.last - acc.started               # the last busy sample, not the first quiet one
        if seconds < self.MIN_SECONDS:
            return
        try:
            on_ac = psutil.sensors_battery()
            on_ac = None if on_ac is None else bool(on_ac.power_plugged)
        except (OSError, NotImplementedError, AttributeError):
            on_ac = None
        quiet = self.session.quiet.state()
        plan = (quiet["current"] or {}).get("name") if quiet["current"] else None
        self.spikes.append({"started": round(acc.started), "seconds": round(seconds, 1), "avg_cpu": round(acc.avg(), 1),
                            "peak_cpu": round(acc.peak, 1), "culprits": acc.culprits(6), "plan": plan,
                            "quiet_mode": quiet["active"], "on_ac": on_ac, "session": bool(self.session.entries)})
        del self.spikes[:-self.KEEP]
        self.session.update_history("spikes", self.spikes)

    # -- reporting ----------------------------------------------------------------------------

    def state(self):
        with self.lock:
            current = None
            if self.current is not None:
                a = self.current
                current = {"started": round(a.started), "seconds": round(a.last - a.started, 1), "avg_cpu": round(a.avg(), 1),
                           "peak_cpu": round(a.peak, 1), "culprits": a.culprits(5)}
            totals = {}
            for spike in self.spikes[-30:]:
                for c in spike["culprits"]:
                    t = totals.setdefault((c["name"], c["via"]), {"name": c["name"], "via": c["via"], "cpu_seconds": 0.0, "spikes": 0})
                    t["cpu_seconds"] += c["cpu_seconds"]
                    t["spikes"] += 1
            grand = sum(t["cpu_seconds"] for t in totals.values()) or 1.0
            top = [{**t, "cpu_seconds": round(t["cpu_seconds"], 1), "share": round(t["cpu_seconds"] / grand * 100, 1)}
                   for t in sorted(totals.values(), key=lambda t: -t["cpu_seconds"])[:6]]
            return {"enabled": self.enabled, "on": self.on, "off": self.off, "current": current,
                    "spikes": list(reversed(self.spikes[-10:])), "top": top, "count": len(self.spikes)}
