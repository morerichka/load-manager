"""Cloud-folder detection and the Claude Code hook that keeps media tools out of cloud folders."""
import io
import json

import pytest

from loadmanager import guard
from loadmanager.cloud import mentions_cloud


@pytest.mark.parametrize("path", [
    "/Users/me/Library/CloudStorage/GoogleDrive-me@gmail.com/My Drive/clip.mov",
    "/Users/me/Library/Mobile Documents/com~apple~CloudDocs/clip.mov",
    "/Volumes/GoogleDrive/My Drive/clip.mov",
    "/Users/me/Dropbox/Wedding/clip.mov",
    "C:\\Users\\me\\Google Drive\\Wedding\\clip.mov",
    "G:\\My Drive\\Wedding\\clip.mov",
    "G:\\Shared drives\\Studio\\clip.mov",
    "C:\\Users\\me\\OneDrive - Company\\clip.mov",
    "C:\\Users\\me\\iCloudDrive\\clip.mov",
    "G:\\Мой диск\\Свадьба\\clip.mov",
    "G:\\Мій диск\\Весілля\\clip.mov",
])
def test_cloud_folders_are_recognised_on_both_systems(path):
    assert mentions_cloud(path)


@pytest.mark.parametrize("path", ["", None, "/Users/me/VideoWork/wedding/src/clip.mov", "C:\\Users\\me\\Videos\\clip.mov",
                                  "/tmp/work/out.mp4", "D:\\Projects\\drivers\\x.mp4"])
def test_ordinary_folders_are_not(path):
    assert not mentions_cloud(path)


DRIVE = "/Users/me/Library/CloudStorage/GoogleDrive-me@x.com/My Drive/wedding/clip.mov"
WIN = "G:\\My Drive\\Wedding\\clip.mov"


@pytest.mark.parametrize("command", [
    f'ffmpeg -i "{DRIVE}" -c:v libx264 out.mp4',
    f'ffmpeg -i /tmp/clip.mov "{DRIVE}.mp4"',
    f'ffprobe "{DRIVE}"',
    f'/opt/homebrew/bin/ffmpeg -i "{DRIVE}" out.mp4',
    f'ffmpeg.exe -i "{WIN}" out.mp4',
    f'"C:\\tools\\ffmpeg.exe" -i "{WIN}" out.mp4',
    f'FFMPEG -i "{WIN}" out.mp4',
    f'HandBrakeCLI -i "{DRIVE}" -o out.mp4',
    f'mediainfo "{DRIVE}"',
    f'cd /tmp && ffmpeg -i "{DRIVE}" out.mp4',
    f'cat list.txt | ffmpeg -i "{DRIVE}" out.mp4',
    f'lm encode "{DRIVE}" out.mp4',
    f'vk run -- ffmpeg -i x.mov "{DRIVE}"',
    f'nice -n 10 ffmpeg -i "{DRIVE}" out.mp4',
])
def test_a_media_tool_pointed_at_a_cloud_path_is_blocked(command):
    assert guard.reason(command) == "path"


@pytest.mark.parametrize("command", [
    "ffmpeg -i ~/VideoWork/wedding/src/clip.mov out.mp4",
    f'cp "{DRIVE}" ~/VideoWork/wedding/src/ && ffmpeg -i ~/VideoWork/wedding/src/clip.mov out.mp4',
    f'ls "{DRIVE}"',
    f'cp "{DRIVE}" ~/VideoWork/src/',
    f'lm fetch "{DRIVE}"',
    f'lm publish out.mp4 "{DRIVE}"',
    f'rsync -a "{DRIVE}" ~/x/',
    f'LM_ALLOW_DRIVE=1 ffmpeg -i "{DRIVE}" out.mp4',
    f'VK_ALLOW_DRIVE=1 ffmpeg -i "{DRIVE}" out.mp4',
    "echo hello",
    "ffmpeg -version",
    "",
])
def test_everything_else_passes(command):
    assert guard.reason(command) is None


def test_a_media_tool_started_from_a_cloud_folder_is_blocked_unless_it_only_asks_questions_or_moves_away():
    assert guard.reason("ffmpeg -i clip.mov out.mp4", cwd=DRIVE) == "cwd"
    assert guard.reason("ffmpeg.exe -i clip.mov out.mp4", cwd="G:\\My Drive\\Wedding") == "cwd"
    assert guard.reason('time ffmpeg -i clip.mov out.mp4', cwd=DRIVE) == "cwd"
    assert guard.reason("ffmpeg -encoders", cwd=DRIVE) is None
    assert guard.reason("ffmpeg -version", cwd=DRIVE) is None
    assert guard.reason("cd ~/VideoWork/x && ffmpeg -i clip.mov out.mp4", cwd=DRIVE) is None
    assert guard.reason("ls", cwd=DRIVE) is None
    assert guard.reason("ffmpeg -i clip.mov out.mp4", cwd="/Users/me/VideoWork") is None


def run_hook(payload, monkeypatch, capsys):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    code = guard.main(io.StringIO(text))
    return code, capsys.readouterr().err


def test_the_hook_blocks_with_exit_code_2_and_tells_claude_what_to_do(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path))
    code, err = run_hook({"tool_name": "Bash", "tool_input": {"command": f'ffmpeg -i "{DRIVE}" o.mp4'}, "cwd": "/tmp"}, monkeypatch, capsys)
    assert code == 2
    assert "Blocked by Load Manager" in err and "fetch" in err and "publish" in err and "LM_ALLOW_DRIVE=1" in err


def test_the_message_names_the_installed_launcher(monkeypatch, capsys, tmp_path):
    (tmp_path / "loadmanager").mkdir()
    (tmp_path / "loadmanager" / "lm").write_text("#!/bin/sh\n")
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path))
    _, err = run_hook({"tool_input": {"command": f'ffmpeg -i "{DRIVE}" o.mp4'}}, monkeypatch, capsys)
    assert str(tmp_path / "loadmanager" / "lm") in err


def test_a_command_started_from_a_cloud_folder_gets_the_working_directory_message(monkeypatch, capsys):
    code, err = run_hook({"tool_input": {"command": "ffmpeg -i clip.mov o.mp4"}, "cwd": DRIVE}, monkeypatch, capsys)
    assert code == 2 and "working directory is inside" in err


@pytest.mark.parametrize("payload", ["", "not json", "[]", "null", "{}", '{"tool_input": "ffmpeg"}', '{"tool_input": {"command": 5}}',
                                     '{"tool_input": {"command": null}}', '{"tool_input": {}}'])
def test_anything_unreadable_is_allowed(payload, monkeypatch, capsys):
    assert run_hook(payload, monkeypatch, capsys) == (0, "")


def test_an_unexpected_error_never_blocks(monkeypatch, capsys):
    monkeypatch.setattr(guard, "reason", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert run_hook({"tool_input": {"command": "ffmpeg x"}}, monkeypatch, capsys)[0] == 0


def test_a_command_that_is_fine_is_silent(monkeypatch, capsys):
    assert run_hook({"tool_input": {"command": "ffmpeg -i /tmp/a.mov /tmp/b.mp4"}, "cwd": "/tmp"}, monkeypatch, capsys) == (0, "")
