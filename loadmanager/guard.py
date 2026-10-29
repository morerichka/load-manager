"""Claude Code PreToolUse hook: stops media tools from running directly on a cloud-synced folder.

Blocks ffmpeg / ffprobe / ffplay / HandBrakeCLI / mediainfo and `lm encode|run` when they point at Google Drive,
Dropbox, OneDrive or iCloud Drive (or are started from a working directory inside one). Exit code 2 blocks the
command and stderr goes back to Claude so it can change course.

It must never get in the way by accident, so every doubt means "allow": unreadable input, unfamiliar JSON, an
unexpected error all end in exit code 0. The escape hatch for a deliberate case is LM_ALLOW_DRIVE=1 in front of
the command. Standard library only, so it starts fast and works on a fresh Python."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from .cloud import mentions_cloud

_TOOLS = r"(?:ffmpeg|ffprobe|ffplay|HandBrakeCLI|mediainfo)"
# a media tool anywhere in a segment (used when the segment names a cloud path); .exe is covered by the boundary
LOOSE = re.compile(r"(?:^|[^A-Za-z0-9_.-])" + _TOOLS + r"(?:[^A-Za-z0-9_-]|$)"
                   r"|(?:^|[^A-Za-z0-9_.-])(?:lm|vk)(?:\.cmd|\.exe)?\s+(?:encode|run)(?:\s|$)", re.I)
# a media tool as the command word (used when only the working directory is in the cloud)
STRICT = re.compile(r"^\s*(?:(?:do|then|else|time|exec|nice|sudo|caffeinate|start|[A-Za-z_][A-Za-z0-9_]*=\S*)\s+)*"
                    r"[\"']?(?:\S*[\\/])?" + _TOOLS + r"(?:\.exe)?[\"']?(?:\s|$)", re.I)
# flags that only ask the tool about itself and never open a media file
QUERY = re.compile(r"\s(?:-version|-buildconf|-encoders|-decoders|-codecs|-formats|-hwaccels|-filters|-protocols"
                   r"|-pix_fmts|-h|-help|--help)(?:\s|$)")
SPLIT = re.compile(r"&&|\|\||[;|&\n]")
MOVES = re.compile(r"(?:^|[\s;&|(])(?:cd|pushd|Set-Location|sl)(?:\s|$)", re.I)


def launcher():
    """How to call the lm helper in the message: the installed launcher if there is one."""
    home = Path(os.environ.get("CLAUDE_DIR") or Path.home() / ".claude") / "loadmanager"
    for name in ("lm", "lm.cmd"):
        if (home / name).exists():
            return str(home / name)
    return f'"{sys.executable}" -m loadmanager'


def reason(command, cwd=""):
    """None to allow, "path" or "cwd" when the command should be blocked."""
    if not command or re.search(r"LM_ALLOW_DRIVE=1|VK_ALLOW_DRIVE=1", command):
        return None
    cwd_in_cloud = bool(cwd) and mentions_cloud(cwd) and not MOVES.search(command)
    # judge each part of a compound command on its own, so that
    # "cp <Drive file> ~/VideoWork/src/ && ffmpeg -i ~/VideoWork/src/clip.mov ..." passes
    for segment in SPLIT.split(command):
        if mentions_cloud(segment) and LOOSE.search(segment):
            return "path"
        if cwd_in_cloud and STRICT.search(segment) and not QUERY.search(segment):
            return "cwd"
    return None


def message(why):
    lm = launcher()
    where = ("(the working directory is inside Google Drive / Dropbox / OneDrive / iCloud)." if why == "cwd"
             else "(Google Drive / Dropbox / OneDrive / iCloud Drive).")
    return "\n".join([
        "Blocked by Load Manager: this command runs a media tool directly on a cloud-synced folder", where,
        "Files there can be on-demand placeholders: the tool stalls or pulls the whole file down first,",
        "and writing into the folder starts an upload while the render is still running.", "",
        "Do this instead:",
        f"  1. {lm} fetch \"<file or folder in the cloud>\"     (copies it into ~/VideoWork/<project>/src)",
        "  2. run ffmpeg / lm encode on the local copy",
        f"  3. {lm} publish <result> \"<cloud folder>\"     (only if the user asked for the upload)", "",
        "If the user said it is fine to work in place (a small file), repeat the command with LM_ALLOW_DRIVE=1 in front.",
    ])


def main(stdin=None):
    try:
        data = json.loads((stdin or sys.stdin).read())
        tool_input = data.get("tool_input") or {}
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str):
            return 0
        why = reason(command, data.get("cwd") if isinstance(data.get("cwd"), str) else "")
    except Exception:
        return 0
    if why is None:
        return 0
    try:
        print(message(why), file=sys.stderr)
    except Exception:
        pass
    return 2


if __name__ == "__main__":
    sys.exit(main())
