"""Installs the Claude part of Load Manager for the current user, on Windows and macOS alike:

  ~/.claude/skills/load-manager/SKILL.md      the skill that teaches Claude the video routine
  ~/.claude/loadmanager/lm (and lm.cmd)       a launcher for the `lm` helper
  ~/.claude/settings.json                     + one PreToolUse hook entry (the Drive guard); a backup is made first

Run it with `lm install-claude`, remove it with `lm uninstall-claude`. Nothing else is touched; the work folder
(~/VideoWork) is left exactly as it is. Standard library only."""
from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path

MARK = "loadmanager.guard"
MATCHER = "Bash|PowerShell"
SKILL_NAME = "load-manager"


class InstallError(Exception):
    pass


def claude_dir():
    return Path(os.environ.get("CLAUDE_DIR") or Path.home() / ".claude")


def posix(path):
    return str(path).replace("\\", "/") if os.name == "nt" else str(path)


def guard_command(python):
    return f'"{posix(python)}" -m {MARK}'


# ---------- settings.json: pure merge functions ----------

def _is_ours(entry):
    return isinstance(entry, dict) and any(isinstance(h, dict) and MARK in str(h.get("command", ""))
                                           for h in entry.get("hooks") or [])


def add_hook(settings, command):
    """Settings with exactly one Load Manager guard entry. Other hooks and keys are left alone."""
    if not isinstance(settings, dict):
        raise InstallError("settings.json does not hold a JSON object")
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError('"hooks" in settings.json is not an object')
    pre = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre, list):
        raise InstallError('"hooks.PreToolUse" in settings.json is not a list')
    pre[:] = [e for e in pre if not _is_ours(e)]
    pre.append({"matcher": MATCHER, "hooks": [{"type": "command", "command": command}]})
    return settings


def remove_hook(settings):
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    pre = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    if isinstance(pre, list):
        kept = [e for e in pre if not _is_ours(e)]
        if len(kept) != len(pre):
            if kept:
                hooks["PreToolUse"] = kept
            else:
                del hooks["PreToolUse"]
                if not hooks:
                    del settings["hooks"]
    return settings


def has_hook(settings):
    pre = ((settings or {}).get("hooks") or {}).get("PreToolUse") if isinstance(settings, dict) else None
    return isinstance(pre, list) and any(_is_ours(e) for e in pre)


def read_settings(path):
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError as exc:
        raise InstallError(f"{path} is not valid JSON ({exc}); it was left unchanged") from exc


def write_settings(path, settings):
    """Backup first, then replace the file in one step."""
    if path.exists():
        backup = path.with_name(f"{path.name}.lm-backup-{time.strftime('%Y%m%d-%H%M%S')}")
        backup.write_bytes(path.read_bytes())
        print(f"  backup: {backup}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".lm-tmp.{os.getpid()}")
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(str(tmp), str(path))


def snippet(command):
    return ('Add this to settings.json by hand (or ask Claude: "add this hook to my Claude Code settings"):\n\n'
            + json.dumps({"hooks": {"PreToolUse": [{"matcher": MATCHER, "hooks": [{"type": "command", "command": command}]}]}},
                         indent=2) + "\n\n(merge it into the existing \"hooks\" object if there is one)")


# ---------- files ----------

def shims(home, python):
    """The lm launcher: a shell script for macOS/Git Bash and a .cmd for Windows."""
    home.mkdir(parents=True, exist_ok=True)
    sh = home / "lm"
    sh.write_text(f'#!/bin/sh\nexec "{posix(python)}" -m loadmanager "$@"\n', encoding="utf-8", newline="\n")
    with contextlib.suppress(OSError):
        sh.chmod(0o755)
    cmd = home / "lm.cmd"
    cmd.write_bytes(f'@echo off\r\n"{python}" -m loadmanager %*\r\n'.encode("utf-8"))
    return sh if os.name != "nt" else cmd


def skill_text(launcher):
    template = (Path(__file__).parent / "skill" / "SKILL.md").read_text(encoding="utf-8")
    return template.replace("{{LM}}", posix(launcher))


def install(python=None):
    python = Path(python or sys.executable)
    base = claude_dir()
    home, skill_dir, settings_path = base / "loadmanager", base / "skills" / SKILL_NAME, base / "settings.json"
    print(f"Installing the Load Manager Claude kit into {base}")
    launcher = shims(home, python)
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_text(launcher), encoding="utf-8", newline="\n")
    print("  skill and launcher written")
    command = guard_command(python)
    try:
        write_settings(settings_path, add_hook(read_settings(settings_path), command))
        print("  guard hook registered in settings.json")
    except (InstallError, OSError) as exc:
        print(f"  could not update settings.json automatically: {exc}")
        print(snippet(command))
    print("\nDone. Next steps:\n  1. Restart Claude (new sessions pick up the skill and the hook).\n"
          f"  2. Check the machine:   {launcher} doctor\n"
          "Work folder: ~/VideoWork (created on first use). For encoding you also need ffmpeg "
          "(macOS: brew install ffmpeg; Windows: winget install ffmpeg).")
    return 0


def uninstall():
    base = claude_dir()
    settings_path = base / "settings.json"
    print(f"Removing the Load Manager Claude kit from {base}")
    try:
        settings = read_settings(settings_path)
        if has_hook(settings):
            write_settings(settings_path, remove_hook(settings))
            print("  guard hook removed from settings.json")
    except (InstallError, OSError) as exc:
        print(f"  could not update settings.json automatically: {exc}")
    for target in (base / "skills" / SKILL_NAME, base / "loadmanager"):
        if target.is_dir():
            for f in sorted(target.rglob("*"), reverse=True):
                with contextlib.suppress(OSError):
                    f.unlink() if f.is_file() or f.is_symlink() else f.rmdir()
            with contextlib.suppress(OSError):
                target.rmdir()
    print("  skill and launcher removed. The work folder was left as it is.")
    return 0


def main(argv):
    if argv and argv[0] == "uninstall-claude":
        return uninstall()
    python = None
    if "--python" in argv:
        python = argv[argv.index("--python") + 1]
    return install(python)
