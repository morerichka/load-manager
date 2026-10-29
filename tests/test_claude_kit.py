"""Installing the Claude hook and skill, the merged settings.json, and the hook as Claude Code would run it."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loadmanager import cli, install_claude as ic

ROOT = Path(__file__).resolve().parents[1]
PY = Path(sys.executable)


@pytest.fixture
def claude(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / ".claude"))
    return tmp_path / ".claude"


def command_for(python="/opt/py/bin/python"):
    return f'"{python}" -m loadmanager.guard'


# ---------- pure settings merging ----------

def test_the_hook_is_added_next_to_what_is_already_there():
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
    settings = {"model": "x", "hooks": {"PreToolUse": [other], "Stop": [{"hooks": []}]}, "permissions": {"allow": ["Read"]}}
    ic.add_hook(settings, command_for())
    pre = settings["hooks"]["PreToolUse"]
    assert pre[0] == other and len(pre) == 2 and pre[1]["matcher"] == "Bash|PowerShell"
    assert pre[1]["hooks"] == [{"type": "command", "command": command_for()}]
    assert settings["model"] == "x" and settings["permissions"] == {"allow": ["Read"]} and settings["hooks"]["Stop"] == [{"hooks": []}]


def test_adding_twice_leaves_one_entry_and_replaces_an_old_path():
    settings = ic.add_hook({}, command_for("/old/python"))
    ic.add_hook(settings, command_for("/new/python"))
    assert [h["hooks"][0]["command"] for h in settings["hooks"]["PreToolUse"]] == [command_for("/new/python")]


def test_removing_takes_only_ours_and_tidies_what_it_emptied():
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
    settings = ic.add_hook({"hooks": {"PreToolUse": [other]}}, command_for())
    assert ic.has_hook(settings)
    ic.remove_hook(settings)
    assert settings == {"hooks": {"PreToolUse": [other]}} and not ic.has_hook(settings)
    only_ours = ic.add_hook({"model": "x"}, command_for())
    assert ic.remove_hook(only_ours) == {"model": "x"}
    assert ic.remove_hook({}) == {} and ic.remove_hook({"hooks": {"PreToolUse": "weird"}}) == {"hooks": {"PreToolUse": "weird"}}


@pytest.mark.parametrize("settings", [[], "x", {"hooks": []}, {"hooks": {"PreToolUse": {}}}])
def test_settings_of_an_unexpected_shape_are_refused_not_overwritten(settings):
    with pytest.raises(ic.InstallError):
        ic.add_hook(settings, command_for())


def test_the_hook_command_uses_forward_slashes_for_the_shell_and_survives_json():
    command = ic.guard_command(Path("C:\\Users\\Me\\LoadManager\\.venv\\Scripts\\python.exe"))
    if os.name == "nt":
        assert command == '"C:/Users/Me/LoadManager/.venv/Scripts/python.exe" -m loadmanager.guard'
    assert json.loads(json.dumps({"c": command}))["c"] == command and command.endswith("-m loadmanager.guard")


# ---------- installing into a temporary Claude folder ----------

def test_install_writes_the_skill_the_launcher_and_the_hook(claude, capsys):
    assert ic.install(PY) == 0
    skill = (claude / "skills" / "load-manager" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: load-manager\ndescription:") and "{{LM}}" not in skill
    launcher = claude / "loadmanager" / ("lm.cmd" if os.name == "nt" else "lm")
    assert ic.posix(launcher) in skill and launcher.exists()
    sh = (claude / "loadmanager" / "lm").read_bytes()
    assert sh.startswith(b"#!/bin/sh\n") and b"\r" not in sh and b"-m loadmanager" in sh
    assert (claude / "loadmanager" / "lm.cmd").read_bytes().startswith(b"@echo off\r\n")
    settings = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    assert ic.has_hook(settings) and "Restart Claude" in capsys.readouterr().out
    if os.name != "nt":
        assert os.access(claude / "loadmanager" / "lm", os.X_OK)


def test_install_keeps_the_users_settings_and_makes_a_backup(claude):
    claude.mkdir()
    original = {"model": "opus", "hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "x"}]}]}}
    (claude / "settings.json").write_text(json.dumps(original), encoding="utf-8")
    ic.install(PY)
    merged = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    assert merged["model"] == "opus" and merged["hooks"]["PreToolUse"][0] == original["hooks"]["PreToolUse"][0] and ic.has_hook(merged)
    backups = list(claude.glob("settings.json.lm-backup-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text(encoding="utf-8")) == original
    assert not list(claude.glob("*.lm-tmp.*"))


def test_install_twice_is_harmless(claude):
    ic.install(PY)
    ic.install(PY)
    pre = json.loads((claude / "settings.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert len(pre) == 1


def test_a_settings_file_that_is_not_json_is_left_alone_and_the_snippet_is_shown(claude, capsys):
    claude.mkdir()
    (claude / "settings.json").write_text("{ this is not json", encoding="utf-8")
    assert ic.install(PY) == 0
    out = capsys.readouterr().out
    assert (claude / "settings.json").read_text(encoding="utf-8") == "{ this is not json"
    assert "could not update settings.json" in out and '"PreToolUse"' in out and "loadmanager.guard" in out
    assert (claude / "skills" / "load-manager" / "SKILL.md").exists()                # the rest still installed


def test_an_empty_settings_file_counts_as_empty_settings(claude):
    claude.mkdir()
    (claude / "settings.json").write_text("  \n", encoding="utf-8")
    ic.install(PY)
    assert ic.has_hook(json.loads((claude / "settings.json").read_text(encoding="utf-8")))


def test_uninstall_removes_exactly_what_install_added(claude, tmp_path):
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    work = tmp_path / "VideoWork"
    (work / "p").mkdir(parents=True)
    ic.install(PY)
    assert ic.uninstall() == 0
    assert json.loads((claude / "settings.json").read_text(encoding="utf-8")) == {"model": "opus"}
    assert not (claude / "skills" / "load-manager").exists() and not (claude / "loadmanager").exists()
    assert (work / "p").is_dir()                                                      # the work folder is never touched
    assert ic.uninstall() == 0                                                         # and a second run is harmless


def test_uninstalling_without_ever_installing_changes_nothing(claude):
    assert ic.uninstall() == 0
    assert not (claude / "settings.json").exists()


def test_the_command_line_entry_points(claude, capsys):
    assert cli.main(["install-claude", "--python", str(PY)], default_app=False) == 0
    assert ic.has_hook(json.loads((claude / "settings.json").read_text(encoding="utf-8")))
    assert cli.main(["uninstall-claude"], default_app=False) == 0
    assert not ic.has_hook(json.loads((claude / "settings.json").read_text(encoding="utf-8")))


# ---------- the installed hook, run the way Claude Code runs it ----------

def run_installed_hook(claude, payload):
    settings = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    env = dict(os.environ, PYTHONPATH=str(ROOT), CLAUDE_DIR=str(claude))
    return subprocess.run(command, shell=True, input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60)


def test_the_registered_command_really_blocks_and_really_allows(claude):
    ic.install(PY)
    drive = "G:\\My Drive\\Wedding\\clip.mov" if os.name == "nt" else "/Users/me/Library/CloudStorage/GoogleDrive-x/My Drive/clip.mov"
    blocked = run_installed_hook(claude, {"tool_name": "Bash", "tool_input": {"command": f'ffmpeg -i "{drive}" out.mp4'}, "cwd": str(claude)})
    assert blocked.returncode == 2 and "Blocked by Load Manager" in blocked.stderr and "fetch" in blocked.stderr
    allowed = run_installed_hook(claude, {"tool_name": "Bash", "tool_input": {"command": "ffmpeg -i local.mov out.mp4"}, "cwd": str(claude)})
    assert allowed.returncode == 0 and allowed.stderr == ""
    garbage = run_installed_hook(claude, "not a dict")
    assert garbage.returncode == 0


def test_the_hook_does_not_import_the_heavy_parts(claude):
    """It runs before every command, so it must start fast and need nothing but the standard library."""
    code = ("import sys, loadmanager.guard; heavy = [m for m in ('psutil', 'webview', 'loadmanager.engine', 'loadmanager.app') "
            "if m in sys.modules]; print(heavy)")
    out = subprocess.run([str(PY), "-c", code], capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)), timeout=60)
    assert out.stdout.strip() == "[]"


# ---------- the unified command line ----------

def test_lm_dispatches_to_the_right_part(monkeypatch, capsys, tmp_path):
    seen = []
    monkeypatch.setattr("loadmanager.video.main", lambda argv: seen.append(("video", argv)) or 0)
    assert cli.main(["fetch", "x"], default_app=False) == 0 and seen == [("video", ["fetch", "x"])]
    assert cli.main(["version"], default_app=False) == 0 and "Load Manager" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit_info:
        monkeypatch.setattr(sys, "argv", ["lm"])
        cli.lm_main()
    assert exit_info.value.code == 2 and "lm doctor" in capsys.readouterr().out


def test_lm_help_and_the_window_command(monkeypatch, capsys):
    assert cli.main(["--help"], default_app=False) == 0 and "install-claude" in capsys.readouterr().out
    launched = []
    monkeypatch.setattr("loadmanager.app.main", lambda: launched.append(list(sys.argv)))
    assert cli.main(["app", "--snapshot"], default_app=False) == 0 and launched[0][1:] == ["--snapshot"]
    assert cli.main(["--guardian", "1", "2", "j"]) == 0 and launched[1][1:] == launched[1][1:]         # flags reach the app unchanged


def test_the_spike_log_is_readable_from_the_command_line(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("loadmanager.engine.state_directory", lambda: tmp_path)
    assert cli.main(["spikes"]) == 0 and "No busy periods recorded yet" in capsys.readouterr().out
    (tmp_path / "history.json").write_text(json.dumps({"spikes": [
        {"started": 1_700_000_000, "seconds": 95.0, "avg_cpu": 61.2, "peak_cpu": 90.0,
         "culprits": [{"name": "ffmpeg.exe", "via": None, "share": 62.1}, {"name": "node.exe", "via": "Claude", "share": 20.0}]}]}))
    assert cli.main(["spikes"]) == 0
    out = capsys.readouterr().out
    assert "95 s" in out and "ffmpeg.exe 62.1%" in out and "node.exe via Claude 20.0%" in out


def test_python_dash_m_still_reaches_the_app_for_its_own_flags():
    out = subprocess.run([str(PY), "-m", "loadmanager", "--help"], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=str(ROOT)), timeout=60)
    assert out.returncode == 0 and "--snapshot" in out.stdout
