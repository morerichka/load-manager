import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from loadmanager.engine import FileLock, Monitor, PolicyError, Session, identity


class Process:
    def __init__(self, pid, name="spotify", owner="me", created=100., exe=None):
        self.pid, self._name, self._owner, self._created = pid, name, owner, created
        self._exe = exe or "/apps/"+name
        self._status, self._nice = psutil.STATUS_RUNNING, 32
        self._children, self._parents = [], []
        self.suspend_error = self.resume_error = self.nice_error = False
        self.suspend_calls = self.resume_calls = 0
        self.rss = 400 * 1024**2
    def name(self): return self._name
    def username(self): return self._owner
    def create_time(self): return self._created
    def exe(self): return self._exe
    def parents(self): return self._parents
    def children(self, recursive=False): return self._children
    def status(self): return self._status
    def nice(self, value=None):
        if value is not None:
            if self.nice_error: raise psutil.AccessDenied(self.pid)
            self._nice = value
        return self._nice
    def memory_info(self): return type("Mem", (), {"rss": self.rss})()
    def suspend(self):
        if self.suspend_error: raise psutil.AccessDenied(self.pid)
        self.suspend_calls += 1
        self._status = psutil.STATUS_STOPPED
    def resume(self):
        if self.resume_error: raise psutil.AccessDenied(self.pid)
        self.resume_calls += 1
        self._status = psutil.STATUS_RUNNING


class Provider:
    def __init__(self):
        self.processes = {os.getpid(): Process(os.getpid(), "loadmanager"), 300001: Process(300001)}
    def Process(self, pid):
        if pid not in self.processes: raise psutil.NoSuchProcess(pid)
        return self.processes[pid]
    def process_iter(self): return list(self.processes.values())


@pytest.fixture
def setup(tmp_path):
    provider=Provider()
    session=Session(tmp_path, provider, "Darwin")
    session.guardian_ready=True
    p=provider.Process(300001)
    return session, provider, p


def test_suspend_and_restore_preserves_identity(setup):
    s, _, p=setup
    result=s.apply([identity(p)], "focus")
    assert result["applied"] == 1 and p.status() == psutil.STATUS_STOPPED
    assert json.loads(s.path.read_text())["entries"][0]["created"] == p.create_time()
    assert s.restore() == {"restored":1,"remaining":0}
    assert p.status() == psutil.STATUS_RUNNING and not s.entries


@pytest.mark.parametrize("field,value",[("_created",200.),("_exe","/elsewhere/spotify"),("_owner","other"),("_name","other")])
def test_identity_change_rejected_before_action(setup,field,value):
    s,_,p=setup
    selection=identity(p)
    setattr(p,field,value)
    with pytest.raises(PolicyError): s.apply([selection],"focus")
    assert p.suspend_calls == 0


@pytest.mark.parametrize("name",["python3","code","chrome","ffmpeg","kernel_task","unknown"])
def test_work_and_unknown_processes_protected(setup,name):
    s,_,p=setup
    p._name=name
    with pytest.raises(PolicyError): s.apply([identity(p)],"render")


def test_foreign_owner_protected(setup):
    s,_,p=setup;p._owner="other"
    with pytest.raises(PolicyError): s.apply([identity(p)],"focus")


def test_own_process_and_descendants_protected(setup):
    s,provider,p=setup
    provider.Process(os.getpid())._children=[p]
    with pytest.raises(PolicyError): s.apply([identity(p)],"focus")


def test_launcher_containing_work_is_protected(setup):
    s,_,p=setup;p._children=[Process(300002,"ffmpeg")]
    with pytest.raises(PolicyError): s.apply([identity(p)],"focus")


def test_preexisting_pause_is_not_owned(setup):
    s,_,p=setup;p._status=psutil.STATUS_STOPPED
    with pytest.raises(PolicyError): s.apply([identity(p)],"focus")
    s.restore()
    assert p.resume_calls == 0


def test_duplicate_selection_applied_once(setup):
    s,_,p=setup
    assert s.apply([identity(p),identity(p)],"focus")["applied"] == 1
    assert p.suspend_calls == 1


def test_guardian_required(setup):
    s,_,p=setup;s.guardian_ready=False
    with pytest.raises(PolicyError): s.apply([identity(p)],"focus")


def test_partial_failure_rolls_back(setup):
    s,provider,p=setup
    second=Process(300002,"discord");second.suspend_error=True
    provider.processes[second.pid]=second
    with pytest.raises(psutil.AccessDenied): s.apply([identity(p),identity(second)],"render")
    assert p.status() == psutil.STATUS_RUNNING and not s.entries


def test_failed_restore_persisted_and_retryable(setup):
    s,_,p=setup;s.apply([identity(p)],"focus");p.resume_error=True
    assert s.restore()["remaining"] == 1
    assert len(json.loads(s.path.read_text())["entries"]) == 1
    p.resume_error=False
    assert s.restore()["remaining"] == 0


def test_reused_pid_never_resumed(setup):
    s,provider,p=setup;s.apply([identity(p)],"focus")
    replacement=Process(p.pid,created=200.)
    replacement._status=psutil.STATUS_STOPPED
    provider.processes[p.pid]=replacement
    assert s.restore()["restored"] == 0
    assert replacement.resume_calls == 0


def test_exited_target_is_skipped(setup):
    s,provider,p=setup;s.apply([identity(p)],"focus")
    del provider.processes[p.pid]
    assert s.restore() == {"restored":0,"remaining":0}


def test_disk_recovery_after_restart(setup):
    s,provider,p=setup;s.apply([identity(p)],"focus")
    replacement=Session(s.directory,provider,"Darwin",s.path)
    assert replacement.restore(from_disk=True)["restored"] == 1
    assert p.status() == psutil.STATUS_RUNNING


@pytest.mark.parametrize("profile,expected",[("focus",16384),("render",64)])
def test_windows_priority_roundtrip(setup,monkeypatch,profile,expected):
    s,_,p=setup;s.system="Windows"
    monkeypatch.setattr(psutil,"BELOW_NORMAL_PRIORITY_CLASS",16384,raising=False)
    monkeypatch.setattr(psutil,"IDLE_PRIORITY_CLASS",64,raising=False)
    s.apply([identity(p)],profile)
    assert p.nice() == expected and p.suspend_calls == 0
    s.restore()
    assert p.nice() == 32


def test_windows_external_priority_change_preserved(setup,monkeypatch):
    s,_,p=setup;s.system="Windows"
    monkeypatch.setattr(psutil,"BELOW_NORMAL_PRIORITY_CLASS",16384,raising=False)
    monkeypatch.setattr(psutil,"IDLE_PRIORITY_CLASS",64,raising=False)
    s.apply([identity(p)],"focus");p.nice(128)
    s.restore()
    assert p.nice() == 128


@pytest.mark.parametrize("selections,profile",[([],"focus"),([{}],"focus"),([{"pid":True}],"focus"),([],"invalid")])
def test_invalid_requests_rejected(setup,selections,profile):
    s,_,_=setup
    with pytest.raises(PolicyError):s.apply(selections,profile)


def test_second_session_cannot_overwrite_original(setup):
    s,_,p=setup;s.apply([identity(p)],"focus")
    with pytest.raises(PolicyError):s.apply([identity(p)],"render")
    assert len(s.entries) == 1


def test_file_lock_excludes_other_process(tmp_path):
    path=tmp_path/"instance.lock"
    with FileLock(path):
        child=subprocess.run([sys.executable,"-c", "from loadmanager.engine import FileLock; import sys; FileLock(sys.argv[1]).acquire(False)",str(path)],capture_output=True)
        assert child.returncode != 0
    with FileLock(path): pass


def test_live_monitor_is_read_only(tmp_path):
    s=Session(tmp_path)
    m=Monitor(s)
    m.sample();time.sleep(.05)
    sample=m.sample()
    assert sample["demo"] is False and 0<=sample["cpu"]<=100
    assert sample["memory_total"]>0 and len(sample["history"])==2
    assert not s.path.exists()


@pytest.mark.skipif(os.name=="nt",reason="POSIX guardian integration; Windows recovery covered by unit tests")
def test_guardian_recovers_after_real_parent_crash(tmp_path):
    # Only a disposable process created by this test is ever suspended.
    if sys.platform.startswith("linux") and int(os.readlink("/proc/self")) != os.getpid():
        pytest.skip("The sandbox exposes a host /proc table inconsistent with child PIDs")
    target=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])
    guard=None
    try:
        p=psutil.Process(target.pid)
        if p.exe() != str(Path(sys.executable).resolve()) and "time.sleep(60)" not in " ".join(p.cmdline()):
            pytest.skip("The sandbox exposes a host /proc table inconsistent with child PIDs")
        journal=tmp_path/"session.json"
        journal.write_text(json.dumps({"version":1,"entries":[{**identity(p),"action":"suspend"}]}))
        parent=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])
        try:
            guard=subprocess.Popen([sys.executable,"-m","loadmanager","--guardian",str(parent.pid),str(psutil.Process(parent.pid).create_time()),str(journal)])
            p.suspend()
            parent.kill();parent.wait(timeout=5)
            guard.wait(timeout=10)
            assert p.status()!=psutil.STATUS_STOPPED
            assert json.loads(journal.read_text())["entries"]==[]
        finally:
            if parent.poll() is None:parent.kill();parent.wait()
    finally:
        try:psutil.Process(target.pid).resume()
        except psutil.Error:pass
        target.terminate();target.wait(timeout=5)
        if guard and guard.poll() is None:guard.terminate();guard.wait()
