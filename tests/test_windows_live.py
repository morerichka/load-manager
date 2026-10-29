"""Opt-in check of the real Windows mechanisms. Not run by default:

    set LOADMANAGER_LIVE=1
    python -m pytest tests/test_windows_live.py -s

It saturates every CPU thread for about 40 seconds, so close other heavy work first.
Only disposable copies of cmd.exe with a test name are touched, never your own Chrome,
Telegram, Claude or Drive. The test registers its own catalogue entry for that name.
"""
import json
import os
import shutil
import sys
import subprocess
import time
from pathlib import Path

import psutil
import pytest

from loadmanager import engine, winapi

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows only"),
    pytest.mark.skipif(os.environ.get("LOADMANAGER_LIVE") != "1", reason="set LOADMANAGER_LIVE=1 to run"),
]
NL = chr(10)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    for p in psutil.process_iter(["name", "exe"]):
        # copies from an earlier test of this module may still be exiting; they live under a pytest temp folder
        if (p.info["name"] or "").lower() == "ffmpeg.exe" and "pytest-of-" not in (p.info["exe"] or "").lower():
            pytest.skip("a real ffmpeg.exe is running; the render checks would touch it")
    monkeypatch.setitem(engine.APPS, "e2etest", {"title": "E2E", "kind": "heavy", "names": {"loadmanager-e2e.exe"},
                                                 "actions": ("priority", "eco", "trim")})
    monkeypatch.setitem(engine._APP_BY_NAME, "loadmanager-e2e.exe", "e2etest")
    work = tmp_path / "live"
    work.mkdir()
    for name in ("loadmanager-e2e.exe", "ffmpeg.exe"):
        shutil.copy(r"C:\Windows\System32\cmd.exe", work / name)
    (work / "burn.cmd").write_text(NL.join(["@echo off", "for /L %%i in (1,1,999999999) do @rem", ""]))
    (work / "wait.cmd").write_text(NL.join(["@echo off", "ping -n 120 127.0.0.1 >nul", ""]))
    (work / "parent.cmd").write_text(NL.join(
        ["@echo off", "ping -n 14 127.0.0.1 >nul", f'"{work / "loadmanager-e2e.exe"}" /c "{work / "wait.cmd"}"', ""]))

    def ours():
        found = []
        for p in psutil.process_iter():
            try:
                if p.exe().lower().startswith(str(work).lower()):
                    found.append(p)
            except psutil.Error:
                pass
        return found

    def spawn(exe, script, prio=None):
        """Start detached, like a real app: it must not be a child of Load Manager."""
        known = {p.pid for p in ours()}
        command = ["cmd.exe", "/c", "start", "", "/B"] + ([prio] if prio else []) + [str(work / exe), "/c", str(work / script)]
        subprocess.run(command, creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            new = [p for p in ours() if p.pid not in known]
            if new:
                return new[0]
            time.sleep(0.1)
        raise AssertionError("could not start " + exe)

    yield spawn
    for p in ours():
        try:
            p.kill()
        except psutil.Error:
            pass


def probe(session):
    session.benchmark.start()
    while session.benchmark.running:
        time.sleep(0.2)
    assert session.benchmark.state()["error"] is None
    return session.benchmark.state()["results"][-1]["score"]


def test_background_load_is_given_back_to_the_foreground_job(sandbox, tmp_path):
    session = engine.Session(tmp_path / "state", psutil, "Windows")
    session.guardian_ready = True
    session.memory_percent = lambda: 99                       # also exercise the memory trim
    assert session.native is winapi

    quiet = probe(session)
    burners = [sandbox("loadmanager-e2e.exe", "burn.cmd") for _ in range(psutil.cpu_count())]
    parent = sandbox("loadmanager-e2e.exe", "parent.cmd")       # will start a child after the session begins
    time.sleep(1)
    loaded = probe(session)
    assert loaded < quiet * 0.8, "the burners did not saturate this machine; nothing to compare"

    pids = [b.pid for b in burners]
    eco_before = {pid: winapi.get_eco(pid) for pid in pids}
    result = session.apply([{"group": "e2etest"}], "render")
    assert result["applied"] >= len(pids) and result["freed_mb"] > 0
    assert all(psutil.Process(pid).nice() == engine.IDLE for pid in pids)
    assert all(winapi.get_eco(pid)[0] & 1 and winapi.get_eco(pid)[1] & 1 for pid in pids)

    time.sleep(14)                                            # the parent now starts its child
    child = [c for c in psutil.Process(parent.pid).children(recursive=True) if c.name().lower() == "loadmanager-e2e.exe"][0]
    assert child.nice() == engine.IDLE                        # inherited: restore must sweep it
    render = sandbox("ffmpeg.exe", "wait.cmd", prio="/BELOWNORMAL")
    assert session.watch() >= 1 and psutil.Process(render.pid).nice() == engine.ABOVE_NORMAL

    after = probe(session)
    print(f"quiet={quiet} loaded={loaded} with session={after} MiB/s  ({100 * (after / loaded - 1):+.0f}% vs loaded)")
    assert after > loaded * 1.2

    assert session.restore() == {"restored": len(pids) + 2, "remaining": 0}
    assert all(psutil.Process(pid).nice() == engine.NORMAL for pid in pids)
    assert all(winapi.get_eco(pid) == eco_before[pid] for pid in pids)
    assert psutil.Process(child.pid).nice() == engine.NORMAL
    assert psutil.Process(render.pid).nice() == engine.BELOW_NORMAL
    assert json.loads(session.path.read_text())["entries"] == []


def test_timed_ab_shows_the_real_time_saved_under_background_load(sandbox, tmp_path):
    """The same real command, run without and with a session, while 24 busy 'apps' compete."""
    session = engine.Session(tmp_path / "state", psutil, "Windows")
    session.guardian_ready = True
    session.memory_percent = lambda: 10                      # keep this test about CPU
    session.autopilot.configure(groups=["e2etest"])
    burners = [sandbox("loadmanager-e2e.exe", "burn.cmd") for _ in range(psutil.cpu_count())]
    time.sleep(1)
    work = ("import hashlib,threading,os;b=bytes(1<<20);"
            "f=lambda:[hashlib.sha256(b).digest() for _ in range(3000)];"
            "t=[threading.Thread(target=f) for _ in range(os.cpu_count())];"
            "[x.start() for x in t];[x.join() for x in t]")
    session.timed.start(f'"{sys.executable}" -c "{work}"', mode="ab")
    deadline = time.time() + 180
    while session.timed.running and time.time() < deadline:
        time.sleep(0.5)
    state = session.timed.state()
    assert state["error"] is None and not state["running"]
    comparison = state["comparisons"][0]
    print(f"without session {comparison['baseline']} s, with session {comparison['session']} s, "
          f"saved {comparison['saved_seconds']} s ({comparison['saved_percent']}%)")
    assert comparison["saved_percent"] > 10
    assert session.entries == [] and all(psutil.Process(b.pid).nice() == engine.NORMAL for b in burners)


def test_the_autopilot_follows_a_real_render_process(sandbox, tmp_path):
    """An ffmpeg.exe look-alike starts burning CPU: the session starts; it exits: the session ends."""
    session = engine.Session(tmp_path / "state", psutil, "Windows")
    session.guardian_ready = True
    session.memory_percent = lambda: 10
    auto = session.autopilot
    auto.configure(enabled=True, groups=["e2etest"], grace=5)
    apps = [sandbox("loadmanager-e2e.exe", "burn.cmd") for _ in range(4)]
    monitor = engine.Monitor(session)
    monitor.sample()

    def step():
        time.sleep(2)
        auto.tick(monitor.sample()["processes"], monitor.threads)

    for _ in range(3):
        step()
    assert session.entries == [], "busy apps alone are not a render"
    render = sandbox("ffmpeg.exe", "burn.cmd")
    deadline = time.time() + 60
    while not session.entries and time.time() < deadline:
        step()
    assert session.entries and auto.state()["state"] == "active"
    assert all(psutil.Process(a.pid).nice() == engine.IDLE for a in apps)
    render.kill()
    deadline = time.time() + 60
    while session.entries and time.time() < deadline:
        step()
    assert session.entries == [] and all(psutil.Process(a.pid).nice() == engine.NORMAL for a in apps)
    job = auto.state()["jobs"][0]
    assert job["auto_session"] is True and job["tools"] == ["ffmpeg.exe"]


def test_the_recorder_names_a_real_busy_process(sandbox, tmp_path):
    """A few busy look-alike apps at below-normal priority, with lowered levels so this stays gentle."""
    session = engine.Session(tmp_path / "state", psutil, "Windows", powercfg=None)
    session.recorder.configure(on=16, off=11)
    monitor = engine.Monitor(session)
    monitor.sample()
    busy = [sandbox("loadmanager-e2e.exe", "burn.cmd", prio="/BELOWNORMAL") for _ in range(4)]

    def step():
        time.sleep(2)
        snapshot = monitor.sample()
        session.recorder.observe(snapshot["cpu"], monitor.last_rows, monitor.last_parents)

    deadline = time.time() + 40
    while not session.recorder.state()["current"] and time.time() < deadline:
        step()
    assert session.recorder.state()["current"], "the busy period was never noticed"
    for _ in range(5):
        step()
    for b in busy:
        b.kill()
    deadline = time.time() + 40
    while session.recorder.state()["count"] == 0 and time.time() < deadline:
        step()
    spike = session.recorder.state()["spikes"][0]
    top = spike["culprits"][0]
    print(f"busy period {spike['seconds']} s, avg {spike['avg_cpu']}%; top: {top['name']} x{top['processes']} "
          f"{top['cpu_seconds']} core-s = {top['share']}%")
    assert top["name"] in ("E2E", "loadmanager-e2e.exe") and top["processes"] >= 3
    assert top["cpu_seconds"] > 20 and top["share"] > 40


@pytest.mark.skipif(os.environ.get("LOADMANAGER_LIVE_POWER") != "1",
                    reason="switches your real power plan for a few seconds: set LOADMANAGER_LIVE_POWER=1")
def test_quiet_mode_on_the_real_power_plans(tmp_path):
    """Enter, return, then a simulated crash and the guardian's recovery. Always ends on the original plan."""
    from loadmanager.power import Powercfg
    cfg = Powercfg()
    assert Powercfg.available()
    original = cfg.active()
    session = engine.Session(tmp_path / "state", psutil, "Windows", powercfg=cfg)
    session.quiet.refresh(force=True)
    target = session.quiet.state()["quiet_target"]
    if target is None or target["guid"] == original["guid"]:
        pytest.skip("no different quiet plan to switch to")
    try:
        session.quiet.enter()
        assert cfg.active()["guid"] == target["guid"]
        time.sleep(1)
        session.quiet.leave()
        assert cfg.active()["guid"] == original["guid"]
        session.quiet.enter()
        guardian = engine.Session(session.directory, psutil, "Windows", session.path, powercfg=cfg)
        guardian.restore(from_disk=True)
        assert cfg.active()["guid"] == original["guid"]
    finally:
        if cfg.active()["guid"] != original["guid"]:
            cfg.activate(original["guid"])


def _single_thread_speed(seconds=3.0):
    """MiB/s of one hashing thread: a light load that follows the clock speed of one core."""
    import hashlib
    buffer, done, end = bytes(1 << 20), 0, time.perf_counter() + seconds
    while time.perf_counter() < end:
        hashlib.sha256(buffer).digest()
        done += 1
    return done / seconds


def _busiest_core_performance(seconds=3):
    """Highest '% Processor Performance' of any logical processor (100 = base clock) while one thread is busy."""
    probe = subprocess.Popen(["typeperf", r"\Processor Information(*)\% Processor Performance", "-sc", str(seconds)],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    speed = _single_thread_speed(seconds + 0.5)
    out, _ = probe.communicate(timeout=30)
    peaks = []
    for line in out.splitlines():
        cells = [c.strip().strip('"') for c in line.split('","')]
        values = []
        for cell in cells[1:]:
            try:
                values.append(float(cell))
            except ValueError:
                pass
        if values and len(values) > 3:
            peaks.append(max(values))
    return speed, (sum(peaks) / len(peaks) if peaks else None)


@pytest.mark.skipif(os.environ.get("LOADMANAGER_LIVE_POWER") != "1",
                    reason="changes a power plan setting for a few seconds: set LOADMANAGER_LIVE_POWER=1")
def test_the_limit_method_on_a_temporary_copy_of_the_active_plan(tmp_path):
    """Works on a COPY of the plan in use (made and deleted here), so the real plans are never edited.
    Turns turbo boost off, measures one busy thread, holds and clears a real sleep-prevention request, and
    checks the guardian's recovery. Always ends on the original plan."""
    import re
    from loadmanager.power import BOOST, Powercfg, SUB_PROCESSOR
    cfg = Powercfg()
    assert Powercfg.available()
    original = cfg.active()
    made = subprocess.run(["powercfg", "/duplicatescheme", original["guid"]], capture_output=True, text=True, errors="replace")
    found = re.search(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", made.stdout)
    assert made.returncode == 0 and found, "could not make a temporary copy of the plan"
    copy = found.group(0).lower()
    try:
        cfg.activate(copy)
        time.sleep(1)
        if cfg.active()["guid"] != copy:
            pytest.skip("something switched the plan back")
        boost_before = cfg.value(copy, SUB_PROCESSOR, BOOST)
        if boost_before["ac"] in (None, 0):
            pytest.skip("this plan has no turbo boost to turn off")
        original_boost = cfg.value(original["guid"], SUB_PROCESSOR, BOOST)
        session = engine.Session(tmp_path / "state", psutil, "Windows", powercfg=cfg)
        assert session.quiet.wake is not None                       # the real power request
        session.quiet.configure(mode="limit", boost_off=True, cap=100)
        speed_before, perf_before = _busiest_core_performance()
        state = session.quiet.enter()
        assert cfg.value(copy, SUB_PROCESSOR, BOOST) == {"ac": 0, "dc": 0}
        assert [l["now"] for l in state["limits"]] == ["Off"]
        assert cfg.value(original["guid"], SUB_PROCESSOR, BOOST) == original_boost         # the real plan was not edited
        assert cfg.active()["guid"] == copy
        time.sleep(1)
        speed_during, perf_during = _busiest_core_performance()
        print(f"one busy thread: {speed_before:.0f} -> {speed_during:.0f} MiB/s; "
              f"busiest core {perf_before:.0f} -> {perf_during:.0f} % of base clock")
        if perf_before and perf_before > 150:
            assert perf_during < perf_before * 0.7, "turning boost off did not lower the clock"
        session.timed.running = True                                  # pretend work is running
        session.quiet.tick(now=time.time())
        assert session.quiet.wake.held and session.quiet.state()["awake"]
        session.timed.running = False
        session.quiet.leave()
        assert not session.quiet.wake.held
        assert cfg.value(copy, SUB_PROCESSOR, BOOST) == boost_before
        session.quiet.enter()                                          # a crash now: the guardian puts it back
        guardian = engine.Session(session.directory, psutil, "Windows", session.path, powercfg=cfg)
        guardian.restore(from_disk=True)
        assert cfg.value(copy, SUB_PROCESSOR, BOOST) == boost_before
    finally:
        cfg.activate(original["guid"])
        subprocess.run(["powercfg", "/delete", copy], capture_output=True)
        assert cfg.active()["guid"] == original["guid"]
