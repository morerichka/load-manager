"""One-click setup. Uses only Python's standard library before installation."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".loadmanager-venv"
STAMP = ENV / ".loadmanager-ready.json"


def environment_python():
    return ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installation_signature(desktop=True):
    return {"project": str(ROOT), "requirements": hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest(),
            "python": list(sys.version_info[:2]), "platform": platform.system(), "machine": platform.machine(),
            "extras": "desktop" if desktop else "kit"}


def stamp_matches(stamp, expected):
    """The desktop install is a superset of the Claude-kit install, so it also satisfies a kit request."""
    if not isinstance(stamp, dict):
        return False
    if expected.get("extras") == "kit" and stamp.get("extras") == "desktop":
        stamp = dict(stamp, extras="kit")
    return stamp == expected


def prepare(desktop=True):
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required. Python 3.12 is recommended: https://www.python.org/downloads/")
    if platform.system() not in {"Windows", "Darwin"}:
        raise RuntimeError("This quick launcher supports Windows and macOS. For Linux, follow README.md.")
    python = environment_python()
    expected = installation_signature(desktop)
    check = "import psutil, webview, loadmanager" if desktop else "import psutil, loadmanager"
    if not python.exists():
        print("[1/3] Creating a private Python environment in this folder...", flush=True)
        venv.EnvBuilder(with_pip=True).create(ENV)
    ready = False
    try:
        ready = stamp_matches(json.loads(STAMP.read_text(encoding="utf-8")), expected)
    except (OSError, ValueError):
        pass
    if ready:
        ready = subprocess.run([str(python), "-c", check],
                               cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not ready:
        print("[2/3] Installing app dependencies. Internet is needed on this first run.", flush=True)
        print("This may take a few minutes. Nothing is installed system-wide.", flush=True)
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-e", str(ROOT) + ("[desktop]" if desktop else "")],
                       cwd=ROOT, check=True)
        subprocess.run([str(python), "-c", check], cwd=ROOT, check=True)
        STAMP.write_text(json.dumps(expected), encoding="utf-8")
    else:
        print("[1/3] Environment ready. [2/3] Dependencies already installed.", flush=True)
    return python


def create_shortcut(python):
    if os.name != "nt":
        raise RuntimeError("Desktop shortcut creation is available on Windows.")
    task_env = dict(os.environ)
    task_env.update(LOADMANAGER_ROOT=str(ROOT), LOADMANAGER_PYTHONW=str(python.with_name("pythonw.exe")))
    # Data goes through environment variables, never interpolated into PS code.
    script = r'''
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$path = Join-Path $desktop 'Load Manager.lnk'
if (Test-Path -LiteralPath $path) {
    $old = $shell.CreateShortcut($path)
    if ($old.TargetPath -ne $env:LOADMANAGER_PYTHONW) {
        $i = 2
        do { $path = Join-Path $desktop ("Load Manager (" + $i + ").lnk"); $i++ }
        while (Test-Path -LiteralPath $path)
    }
}
$shortcut = $shell.CreateShortcut($path)
$shortcut.TargetPath = $env:LOADMANAGER_PYTHONW
$shortcut.Arguments = '"' + (Join-Path $env:LOADMANAGER_ROOT 'scripts\launch.pyw') + '"'
$shortcut.WorkingDirectory = $env:LOADMANAGER_ROOT
$shortcut.IconLocation = (Join-Path $env:LOADMANAGER_ROOT 'loadmanager\ui\app.ico') + ',0'
$shortcut.Description = 'Load Manager - process priority and memory manager'
$shortcut.Save()
'''
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env=task_env, check=True)


def start_app(python):
    print("[3/3] Opening Load Manager...", flush=True)
    executable = python.with_name("pythonw.exe") if os.name == "nt" else python
    kwargs = {"cwd": ROOT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    with open(ROOT / "loadmanager-startup.log", "a", encoding="utf-8") as log:
        process = subprocess.Popen([str(executable), str(ROOT / "scripts/launch.pyw")], stdout=log, stderr=log, **kwargs)
    time.sleep(1)
    if process.poll() not in (None, 0):
        raise RuntimeError("The app could not open. See loadmanager-startup.log in this folder.")
    print("Use CREATE-Desktop-Shortcut.cmd for a desktop icon (Windows).", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Prepare and launch Load Manager")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--shortcut", action="store_true")
    parser.add_argument("--claude-kit", action="store_true", help="install the Claude hook and skill (no window needed)")
    args = parser.parse_args()
    try:
        if args.claude_kit:
            python = prepare(desktop=False)
            print("[3/3] Installing the Claude hook and skill...", flush=True)
            subprocess.run([str(python), "-m", "loadmanager", "install-claude", "--python", str(python)], cwd=ROOT, check=True)
            return 0
        python = prepare()
        if args.shortcut:
            create_shortcut(python)
        if args.launch or not args.shortcut:
            start_app(python)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print("\nLoad Manager: " + str(exc), file=sys.stderr, flush=True)
        print("If Python was just installed, close this window and try again. See START-HERE.txt.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
