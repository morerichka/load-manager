import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    source=Path(__file__).resolve().parents[1]/"scripts/bootstrap.py"
    spec=importlib.util.spec_from_file_location("bootstrap_test",source)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root=tmp_path/"Load Manager with spaces & symbols"
    root.mkdir()
    (root/"pyproject.toml").write_text('[project]\nname="test"\n',encoding="utf-8")
    env=root/".loadmanager-venv"
    python=env/"bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(module,"ROOT",root)
    monkeypatch.setattr(module,"ENV",env)
    monkeypatch.setattr(module,"STAMP",env/".loadmanager-ready.json")
    monkeypatch.setattr(module,"environment_python",lambda:python)
    monkeypatch.setattr(module.platform,"system",lambda:"Darwin")
    return module


def test_ready_environment_does_not_download_again(bootstrap,monkeypatch):
    b=bootstrap
    b.STAMP.write_text(json.dumps(b.installation_signature()))
    calls=[]
    monkeypatch.setattr(b.subprocess,"run",lambda args,**kwargs:(calls.append(args) or SimpleNamespace(returncode=0)))
    b.prepare()
    assert len(calls)==1 and "pip" not in calls[0]


def test_first_install_uses_argument_list_and_commits_ready_stamp(bootstrap,monkeypatch):
    b=bootstrap;calls=[]
    monkeypatch.setattr(b.subprocess,"run",lambda args,**kwargs:(calls.append((args,kwargs)) or SimpleNamespace(returncode=0)))
    b.prepare()
    assert calls[0][0][-1]==str(b.ROOT)+"[desktop]"
    assert calls[0][1].get("shell",False) is False
    assert json.loads(b.STAMP.read_text())==b.installation_signature()


def test_failed_install_is_not_marked_ready(bootstrap,monkeypatch):
    b=bootstrap
    def failure(args,**kwargs):raise subprocess.CalledProcessError(1,args)
    monkeypatch.setattr(b.subprocess,"run",failure)
    with pytest.raises(subprocess.CalledProcessError):b.prepare()
    assert not b.STAMP.exists()


def test_changed_requirements_trigger_reinstall(bootstrap,monkeypatch):
    b=bootstrap
    b.STAMP.write_text(json.dumps(b.installation_signature()))
    (b.ROOT/"pyproject.toml").write_text('changed dependencies')
    calls=[]
    monkeypatch.setattr(b.subprocess,"run",lambda args,**kwargs:(calls.append(args) or SimpleNamespace(returncode=0)))
    b.prepare()
    assert "pip" in calls[0]


def test_unsupported_quick_launcher_does_not_install(bootstrap,monkeypatch):
    b=bootstrap
    monkeypatch.setattr(b.platform,"system",lambda:"Linux")
    with pytest.raises(RuntimeError,match="supports Windows and macOS"):b.prepare()


def test_the_claude_kit_install_does_not_need_the_window_library(bootstrap,monkeypatch):
    b=bootstrap;calls=[]
    monkeypatch.setattr(b.subprocess,"run",lambda args,**kwargs:(calls.append(args) or SimpleNamespace(returncode=0)))
    b.prepare(desktop=False)
    assert calls[0][-1]==str(b.ROOT) and "webview" not in " ".join(calls[1])
    assert json.loads(b.STAMP.read_text())["extras"]=="kit"


def test_a_desktop_install_already_satisfies_a_kit_request(bootstrap,monkeypatch):
    b=bootstrap;calls=[]
    b.STAMP.write_text(json.dumps(b.installation_signature(True)))
    monkeypatch.setattr(b.subprocess,"run",lambda args,**kwargs:(calls.append(args) or SimpleNamespace(returncode=0)))
    b.prepare(desktop=False)
    assert len(calls)==1 and "pip" not in calls[0]


def test_a_kit_install_is_upgraded_when_the_window_is_wanted(bootstrap,monkeypatch):
    b=bootstrap;calls=[]
    b.STAMP.write_text(json.dumps(b.installation_signature(False)))
    monkeypatch.setattr(b.subprocess,"run",lambda args,**kwargs:(calls.append(args) or SimpleNamespace(returncode=0)))
    b.prepare()
    assert "pip" in calls[0] and calls[0][-1].endswith("[desktop]")


def test_the_claude_kit_flag_installs_the_hook_with_the_private_python(bootstrap,monkeypatch):
    b=bootstrap;calls=[]
    python=b.environment_python()
    monkeypatch.setattr(b,"prepare",lambda desktop=True:python if desktop is False else pytest.fail("kit mode must not ask for the window library"))
    monkeypatch.setattr(b.subprocess,"run",lambda args,**kwargs:(calls.append(args) or SimpleNamespace(returncode=0)))
    monkeypatch.setattr("sys.argv",["bootstrap.py","--claude-kit"])
    assert b.main()==0
    assert calls==[[str(python),"-m","loadmanager","install-claude","--python",str(python)]]
