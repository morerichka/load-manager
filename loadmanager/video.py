"""Helpers for working with big video files without fighting cloud drives, on Windows and macOS.

  doctor    read-only health report and a short hardware-encoder self-test
  setup     create the local work folder (excluded from Time Machine on a Mac)
  fetch     copy files or folders out of Google Drive & co. into the work folder
  encode    ffmpeg with sane presets, using the hardware encoder the machine has
  proxies   an editing proxy of every clip in a project (resumable)
  run       run any long command with the computer kept awake, notify when it ends
  publish   copy results back into a cloud folder (never deletes, never overwrites by default)
  status    what is in the work folder and how much disk is left
  clean     free disk space; only removes copies whose cloud original is still there, unchanged

This is the shell kit "Mac Video Kit" rewritten in Python so that one tool covers both systems and can be tested
properly. Standard library only. Hardware encoders: VideoToolbox on a Mac; NVENC, AMF or QuickSync on Windows."""
from __future__ import annotations

import _thread
import argparse
import contextlib
import functools
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .cloud import mentions_cloud

GIB = 1024 ** 3
PART = ".lmpart.tmp"
JUNK = {".ds_store", "thumbs.db", "desktop.ini"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".mts", ".m2ts", ".mpg", ".mpeg", ".webm"}
MEDIA_TOOLS = {"ffmpeg", "ffprobe", "ffplay", "handbrakecli", "mediainfo"}
HW_ORDER = {"Darwin": ["videotoolbox"], "Windows": ["nvenc", "amf", "qsv"], "Linux": ["nvenc", "qsv"]}
HW_FLAGS = {  # codec -> flags, "{b}" is the bitrate
    "videotoolbox": {"h264": ["-c:v", "h264_videotoolbox", "-b:v", "{b}", "-profile:v", "high"],
                     "hevc": ["-c:v", "hevc_videotoolbox", "-b:v", "{b}", "-tag:v", "hvc1"]},
    "nvenc": {"h264": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-b:v", "{b}", "-profile:v", "high"],
              "hevc": ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-b:v", "{b}", "-tag:v", "hvc1"]},
    "amf": {"h264": ["-c:v", "h264_amf", "-usage", "transcoding", "-quality", "balanced", "-b:v", "{b}"],
            "hevc": ["-c:v", "hevc_amf", "-usage", "transcoding", "-quality", "balanced", "-b:v", "{b}", "-tag:v", "hvc1"]},
    "qsv": {"h264": ["-c:v", "h264_qsv", "-b:v", "{b}", "-profile:v", "high"],
            "hevc": ["-c:v", "hevc_qsv", "-b:v", "{b}", "-tag:v", "hvc1"]},
}


class Fail(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


# The window cancels a job by creating the file named in LM_CANCEL_FILE. That acts like Ctrl+C in a terminal: the
# copy loops clean up their part files, a running ffmpeg is stopped, an unfinished proxy is removed.
CANCELLED = threading.Event()


def start_cancel_watch(path, interrupt=_thread.interrupt_main, interval=0.2):
    def watch():
        while not CANCELLED.is_set():
            if os.path.exists(path):
                CANCELLED.set()
                interrupt()
                return
            time.sleep(interval)
    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def say(text=""):
    print(text, flush=True)


def warn(text):
    print(f"lm: warning: {text}", file=sys.stderr, flush=True)


def system():
    return platform.system()


# ---------- settings from the environment (LM_*, with the old VK_* names still accepted) ----------

def env(name, default=None):
    return os.environ.get("LM_" + name, os.environ.get("VK_" + name, default))


def work_dir():
    return Path(env("WORK") or Path.home() / "VideoWork")


def min_free_gb():
    try:
        return float(env("MIN_FREE_GB", "5"))
    except ValueError:
        return 5.0


def ffmpeg_cmd():
    """LM_FFMPEG may hold a path or a whole command (a path with spaces needs quotes)."""
    raw = env("FFMPEG") or "ffmpeg"
    return [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t
            for t in shlex.split(raw, posix=(os.name != "nt"))] or ["ffmpeg"]


# ---------- small helpers ----------

def human(n):
    n, units, i = float(n), ["B", "KB", "MB", "GB", "TB"], 0
    while n >= 1024 and i < 4:
        n /= 1024
        i += 1
    return f"{int(n)} {units[i]}" if i == 0 else f"{n:.1f} {units[i]}"


def fsize(path):
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def abs_path(path):
    return Path(path).resolve()


def free_bytes(path):
    d = Path(path)
    while not d.exists() and d != d.parent:
        d = d.parent
    try:
        return shutil.disk_usage(str(d)).free
    except OSError:
        return 0


def tree_size(path):
    total = 0
    for root, _dirs, files in os.walk(str(path)):
        for name in files:
            total += fsize(Path(root) / name)
    return total


def guard_cloud(path):
    """Refuse a cloud-synced path unless the user chose that on purpose."""
    if env("ALLOW_DRIVE") == "1":
        return
    if mentions_cloud(str(path)):
        raise Fail(f"refusing to work in place on a cloud-synced folder:\n    {path}\n"
                   "Files there can be placeholders that download on demand, and writing there uploads while you render.\n"
                   f"Copy it first:   lm fetch \"<file or folder>\"      then work on the copy in {work_dir()}\n"
                   "(If you really mean it, set LM_ALLOW_DRIVE=1.)", 4)


def valid_project(name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name or ""):
        raise Fail(f"invalid project name '{name}' (letters, digits, dot, dash and underscore only)")
    return name


def project_from(path):
    p = Path(path)
    base = p.name if p.is_dir() else p.parent.name
    base = re.sub(r"[^A-Za-z0-9._]+", "-", base).strip("-")
    return base or "project"


def is_junk(name):
    low = name.lower()
    return (low in JUNK or low.endswith(PART) or low.endswith(".vkpart.tmp") or low.startswith(".tmp.drivedownload"))


def list_files(directory):
    found = []
    for root, dirs, files in os.walk(str(directory)):
        dirs.sort()
        for name in sorted(files):
            if not is_junk(name):
                found.append(Path(root) / name)
    return found


def count_files(directory):
    return len(list_files(directory)) if Path(directory).is_dir() else 0


def ensure_work():
    work = work_dir()
    guard_cloud(work)
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise Fail(f"cannot create {work}: {exc}")
    if system() == "Darwin" and shutil.which("tmutil"):
        probe = subprocess.run(["tmutil", "isexcluded", str(work)], capture_output=True, text=True, errors="replace")
        if "Excluded" not in (probe.stdout or ""):
            if subprocess.run(["tmutil", "addexclusion", str(work)], capture_output=True).returncode == 0:
                say(f"Excluded {work} from Time Machine.")
            else:
                warn(f"could not exclude {work} from Time Machine (add it under System Settings > General > Time Machine > Options)")
    return work


# ---------- copy with a part file, a size check, retries and a journal ----------

def copy_file(src, dst, refuse_diff=False, step=8 << 20):
    """Write dst+PART first and rename when the size matches. Returns copied, skipped, failed or refused."""
    src, dst = Path(src), Path(dst)
    size = fsize(src)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return "failed"
    if dst.is_file():
        if fsize(dst) == size:
            say("  already there, skipped")
            return "skipped"
        if refuse_diff:
            warn(f"a different file already exists at {dst} (use --force to replace it)")
            return "refused"
    part = Path(str(dst) + PART)
    t0 = last = time.time()
    done = 0
    try:
        with open(src, "rb") as read, open(part, "wb") as write:
            while True:
                block = read.read(step)
                if not block:
                    break
                write.write(block)
                done += len(block)
                if size >= 50 << 20:
                    if sys.stderr.isatty():
                        print(f"\r  {human(done)} / {human(size)} ({done * 100 // max(size, 1)}%)   ", end="", file=sys.stderr, flush=True)
                    elif time.time() - last >= 15:
                        print(f"  {human(done)} / {human(size)} ({done * 100 // max(size, 1)}%)", file=sys.stderr, flush=True)
                        last = time.time()
        if sys.stderr.isatty() and size >= 50 << 20:
            print("\r" + " " * 60 + "\r", end="", file=sys.stderr, flush=True)
        if fsize(part) != size:
            raise OSError("size mismatch after copy")
        os.replace(str(part), str(dst))
        st = src.stat()
        with contextlib.suppress(OSError):
            os.utime(str(dst), (st.st_atime, st.st_mtime))
    except KeyboardInterrupt:
        with contextlib.suppress(OSError):
            part.unlink()
        raise
    except OSError as exc:
        with contextlib.suppress(OSError):
            part.unlink()
        warn(f"copy failed: {src} ({exc})")
        return "failed"
    say(f"  done in {int(time.time() - t0)} s")
    return "copied"


def copy_retry(src, dst, refuse_diff=False):
    attempts, delay = int(float(env("RETRIES", "3"))), float(env("RETRY_DELAY", "5"))
    for attempt in range(1, max(attempts, 1) + 1):
        result = copy_file(src, dst, refuse_diff)
        if result != "failed" or attempt >= attempts:
            return result
        warn(f"retrying in {delay:g}s (attempt {attempt + 1} of {attempts})")
        time.sleep(delay)
    return "failed"


# Each fetched file is recorded as: size TAB path-relative-to-project TAB original path.
# `lm clean` uses it to prove the original is still there before it removes a local copy.
def manifest_path(project):
    return work_dir() / project / ".lm-manifest.tsv"


def manifest_add(project, size, local, original):
    rel = Path(local).resolve().relative_to((work_dir() / project).resolve()).as_posix()
    with open(manifest_path(project), "a", encoding="utf-8", newline="\n") as out:
        out.write(f"{size}\t{rel}\t{original}\n")


def manifest_latest(project):
    path, latest = manifest_path(project), {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                latest[parts[1]] = (int(parts[0]) if parts[0].isdigit() else -1, parts[1], parts[2])
    return [latest[k] for k in sorted(latest)]


# ---------- fetch / publish ----------

def cmd_fetch(args):
    sources = [abs_path(s) for s in args.sources]
    for given, path in zip(args.sources, sources):
        if not path.exists():
            raise Fail(f"fetch: not found: {given}")
    project = valid_project(args.project or project_from(sources[0]))
    base = work_dir() / project / "src"
    pairs = []
    for s in sources:
        if s.is_dir():
            pairs += [(f, base / s.name / f.relative_to(s)) for f in list_files(s)]
        else:
            pairs.append((s, base / s.name))
    if not pairs:
        raise Fail("fetch: nothing to copy")
    total = sum(fsize(f) for f, _ in pairs)
    need, free = total + int(min_free_gb() * GIB), free_bytes(work_dir())
    say(f"Plan: {len(pairs)} file(s), {human(total)} -> {base}")
    say(f"Free on that disk: {human(free)} (keeping at least {min_free_gb():g} GB spare)")
    if free < need:
        raise Fail(f"not enough free space: need {human(need)}, have {human(free)}. Free some space or lower LM_MIN_FREE_GB.")
    if args.dry_run:
        say("(dry run, nothing copied)")
        return 0
    ensure_work()
    for sub in ("src", "proxy", "out"):
        (work_dir() / project / sub).mkdir(parents=True, exist_ok=True)
    say("Reading from the cloud can take a while if the files are not downloaded yet.")
    ok = skipped = failed = 0
    try:
        for i, (f, t) in enumerate(pairs, 1):
            say(f"[{i}/{len(pairs)}] {f.name} ({human(fsize(f))})")
            result = copy_retry(f, t)
            if result == "copied":
                ok += 1
                if args.verify:
                    if f.read_bytes() == t.read_bytes():
                        say("  verified")
                    else:
                        warn(f"content differs from source: {t}")
                        failed += 1
                        continue
                manifest_add(project, fsize(t), t, f)
            elif result == "skipped":
                skipped += 1
                manifest_add(project, fsize(t), t, f)
            else:
                failed += 1
    except KeyboardInterrupt:
        print("\nlm: interrupted", file=sys.stderr)
        return 130
    say(f"Copied {ok}, skipped {skipped}, failed {failed}.")
    say(f"Local copy: {base}")
    say(f"Put renders in: {work_dir() / project / 'out'}   (proxies: {work_dir() / project / 'proxy'})")
    return 0 if failed == 0 else 1


def cmd_publish(args):
    *items, dest = args.items
    if not items:
        raise Fail("publish: usage: lm publish [--force] FILE_OR_DIR... DEST_DIR")
    dest = Path(dest)
    if not dest.is_dir():
        raise Fail(f"publish: destination folder does not exist: {dest}")
    dest = dest.resolve()
    copied = skipped = failed = 0
    try:
        for item in items:
            p = Path(item)
            if not p.exists():
                warn(f"not found: {item}")
                failed += 1
                continue
            pairs = ([(f, dest / p.resolve().name / f.relative_to(p.resolve())) for f in list_files(p.resolve())]
                     if p.is_dir() else [(p.resolve(), dest / p.name)])
            for f, t in pairs:
                say(f"{t.relative_to(dest).as_posix()} ({human(fsize(f))})")
                result = copy_retry(f, t, refuse_diff=not args.force)
                copied += result == "copied"
                skipped += result == "skipped"
                failed += result in ("failed", "refused")
    except KeyboardInterrupt:
        print("\nlm: interrupted", file=sys.stderr)
        return 130
    say(f"Published {copied}, skipped {skipped}, failed {failed}. Local files were left untouched.")
    if mentions_cloud(str(dest)):
        say("The cloud client now uploads in the background; check its icon for progress.")
    return 0 if failed == 0 else 1


# ---------- keeping the computer awake, notifying, running ----------

def notify(title, message):
    if env("NOTIFY") == "0":
        return
    clean = lambda s: re.sub(r"[\"'\\`$]", "", str(s))[:200]
    title, message = clean(title), clean(message)
    try:
        if system() == "Darwin" and shutil.which("osascript"):
            subprocess.run(["osascript", "-e", f'display notification "{message}" with title "{title}"'],
                           capture_output=True, timeout=10)
        elif system() == "Windows":
            script = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                      "$n=New-Object System.Windows.Forms.NotifyIcon;$n.Icon=[System.Drawing.SystemIcons]::Information;"
                      f"$n.Visible=$true;$n.ShowBalloonTip(6000,'{title}','{message}','Info');Start-Sleep -Seconds 7;$n.Dispose()")
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


@contextlib.contextmanager
def awake():
    """Keep the computer from going to sleep on its own while the block runs."""
    helper = wake = None
    if system() == "Darwin" and shutil.which("caffeinate"):
        helper = subprocess.Popen(["caffeinate", "-ims", "-w", str(os.getpid())], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif system() == "Windows":
        try:
            from .winapi import WakeRequest
            wake = WakeRequest("Load Manager: a long job is running")
            wake.hold()
        except OSError:
            wake = None
            warn("could not ask Windows to stay awake; the computer may sleep during a long job")
    else:
        warn("no sleep prevention available here; the computer may sleep during a long job")
    try:
        yield
    finally:
        if helper is not None:
            helper.terminate()
        if wake is not None:
            wake.close()


def spawn_options(low):
    """Lower the priority of a background job so the machine stays usable while it runs."""
    if not low:
        return {}
    if os.name == "nt":
        return {"creationflags": 0x00004000}                     # BELOW_NORMAL_PRIORITY_CLASS
    return {"preexec_fn": lambda: os.nice(10)}


def _call(cmd, low):
    """subprocess.call, except that the wait can be interrupted at any moment (Ctrl+C, or a cancel from the window)."""
    proc = subprocess.Popen(cmd, **spawn_options(low))
    try:
        while True:
            try:
                return proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
    except BaseException:
        proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(10)
        raise


def run_wrapped(name, want_notify, cmd, batch=False, low=False):
    """Awake while it runs, a log line and a notification at the end. Returns the exit code."""
    t0 = time.time()
    try:
        if batch:
            code = _call(cmd, low)
        else:
            with awake():
                code = _call(cmd, low)
    except FileNotFoundError:
        warn(f"command not found: {cmd[0]}")
        code = 127
    except KeyboardInterrupt:
        code = 130
    secs = int(time.time() - t0)
    with contextlib.suppress(OSError):
        logs = work_dir() / ".logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "runs.log", "a", encoding="utf-8", newline="\n") as log:
            log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{name}\texit={code}\t{secs}s\n")
    if want_notify and not batch:
        notify("Load Manager", f"{name} finished in {secs}s" if code == 0 else
               f"{name} was stopped" if code == 130 else f"{name} FAILED (exit {code})")
    return code


def cmd_run(args):
    command = list(args.cmdline)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise Fail("run: nothing to run. Usage: lm run [--name LABEL] [--low] -- COMMAND [ARGS...]")
    for a in command:
        guard_cloud(a)
    if Path(command[0]).name.lower().rsplit(".", 1)[0] in MEDIA_TOOLS:
        guard_cloud(os.getcwd())
    return run_wrapped(args.name or Path(command[0]).name, not args.no_notify, command, low=args.low)


# ---------- encode ----------

@functools.lru_cache(maxsize=8)
def _encoders(cmd):
    try:
        out = subprocess.run(list(cmd) + ["-hide_banner", "-encoders"], capture_output=True, text=True,
                             errors="replace", timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[VAS][A-Z.]{5}", parts[0]):
            names.add(parts[1])
    return frozenset(names)


def have_encoder(name):
    return name in _encoders(tuple(ffmpeg_cmd()))


@functools.lru_cache(maxsize=16)
def _selftest(cmd, encoder):
    try:
        return subprocess.run(list(cmd) + ["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                           "testsrc2=duration=1:size=1280x720:rate=30", "-pix_fmt", "yuv420p",
                                           "-c:v", encoder, "-b:v", "4M", "-f", "null", "-"],
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def hardware_backend():
    """The hardware encoder to use: LM_HW (none, videotoolbox, nvenc, amf, qsv) or the first that works."""
    forced = (env("HW") or "auto").lower()
    if forced == "none":
        return None
    cmd = tuple(ffmpeg_cmd())
    for backend in ([forced] if forced in HW_FLAGS else HW_ORDER.get(system(), [])):
        encoder = f"h264_{backend}"
        if encoder not in _encoders(cmd):
            continue
        if backend == "videotoolbox" or _selftest(cmd, encoder):     # the Mac's encoder is always there when listed
            return backend
    return None


def plan_encode(preset, codec, bitrate, hw, encoders, hwdecode, force, abs_in, abs_out, extra=(), progress=None,
                interactive=False, ffmpeg=("ffmpeg",)):
    """Build the ffmpeg command line. Pure: no files are touched. Returns (command, encoder, is_hardware, warnings)."""
    warnings = []
    pre = ["-hwaccel", "videotoolbox"] if hw == "videotoolbox" and hwdecode and preset != "final" else []
    flags = (lambda c, b: [b if t == "{b}" else t for t in HW_FLAGS[hw][c]]) if hw else None
    vf, aopts = None, ["-c:a", "aac", "-b:a", {"proxy": "128k", "fast": "192k", "final": "256k"}[preset]]
    if preset == "proxy":
        if codec == "hevc":
            warnings.append("--codec is ignored by the proxy preset (proxies are always H.264, which every editor reads)")
        vf = "scale=-2:'min(720,ih)'"
        vopts = flags("h264", bitrate or "6M") if hw else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    elif preset == "fast":
        if codec == "hevc":
            if hw and f"hevc_{hw}" in encoders:
                vopts = flags("hevc", bitrate or "12M")
            elif "libx265" in encoders:
                vopts = ["-c:v", "libx265", "-preset", "medium", "-crf", "22", "-tag:v", "hvc1"]
            else:
                raise Fail("encode: this ffmpeg has neither a hardware HEVC encoder nor libx265 (a full ffmpeg build has libx265)")
        else:
            vopts = flags("h264", bitrate or "20M") if hw else ["-c:v", "libx264", "-preset", "fast", "-crf", "19"]
    else:
        if codec == "hevc":
            if "libx265" not in encoders:
                raise Fail("encode: this ffmpeg has no libx265 (a full ffmpeg build has it)")
            vopts = ["-c:v", "libx265", "-preset", "slow", "-crf", "20", "-tag:v", "hvc1"]
        else:
            vopts = ["-c:v", "libx264", "-preset", "slow", "-crf", "17"]
        if bitrate:
            warnings.append("--bitrate is ignored by the final preset (it uses CRF)")
    cmd = list(ffmpeg) + ["-hide_banner", "-nostdin", "-y" if force else "-n"] + pre + ["-i", str(abs_in)]
    if vf:
        cmd += ["-vf", vf]
    cmd += vopts + ["-pix_fmt", "yuv420p"] + aopts + ["-sn", "-dn"]
    if str(abs_out).lower().endswith((".mp4", ".mov", ".m4v")):
        cmd += ["-movflags", "+faststart"]
    cmd += (["-loglevel", "warning", "-stats"] if interactive else ["-loglevel", "warning", "-nostats", "-progress", str(progress)])
    cmd += list(extra) + [str(abs_out)]
    encoder = vopts[1]
    return cmd, encoder, any(k in encoder for k in HW_FLAGS), warnings


def encode_one(args, hw=None, batch=False, hw_known=False):
    """Encode a single file. Raises Fail on bad arguments; returns ffmpeg's exit code."""
    if not Path(args.input).is_file():
        raise Fail(f"encode: input not found: {args.input}")
    abs_in, abs_out = abs_path(args.input), abs_path(args.output)
    guard_cloud(abs_in)
    guard_cloud(abs_out)
    ffmpeg = ffmpeg_cmd()
    if shutil.which(ffmpeg[0]) is None and not Path(ffmpeg[0]).exists():
        raise Fail("encode: ffmpeg not found. Install it (macOS: brew install ffmpeg; Windows: winget install ffmpeg)")
    if abs_out.exists() and not args.force:
        raise Fail(f"encode: output exists: {abs_out} (use --force to overwrite)")
    insize = fsize(abs_in)
    need = (insize // 4 if args.preset == "proxy" else insize) + int(min_free_gb() * GIB)
    free = free_bytes(abs_out.parent)
    if free < need:
        raise Fail(f"encode: not enough free space for the output: need about {human(need)}, have {human(free)}")
    if not args.dry_run:
        abs_out.parent.mkdir(parents=True, exist_ok=True)
    if not hw_known:
        hw = None if args.sw else hardware_backend()
    interactive = sys.stderr.isatty()
    progress = None
    if not interactive:
        logs = work_dir() / ".logs"
        with contextlib.suppress(OSError):
            logs.mkdir(parents=True, exist_ok=True)
        progress = Path(os.environ.get("LM_PROGRESS") or logs / time.strftime("encode-%Y%m%d-%H%M%S.progress"))
        with contextlib.suppress(OSError):
            progress.write_text("")
    cmd, encoder, hardware, warnings = plan_encode(
        args.preset, args.codec, args.bitrate, hw, _encoders(tuple(ffmpeg)), not args.no_hwdecode, args.force,
        abs_in, abs_out, args.extra, progress, interactive, ffmpeg)
    for w in warnings:
        warn(w)
    info = ""
    try:                                    # 10-bit / HDR sources (log footage!) lose their depth in these 8-bit presets
        info = subprocess.run(ffmpeg + ["-hide_banner", "-i", str(abs_in)], capture_output=True, text=True,
                              errors="replace", timeout=60).stderr
        video = next((line for line in info.splitlines() if "Video:" in line), "")
        if re.search(r"10(le|be)|12(le|be)|p010", video):
            warn("the input looks 10-bit/HDR; these presets write 8-bit SDR. Tell the user before continuing.")
    except (OSError, subprocess.SubprocessError):
        pass
    length = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if length:
        say(f"Input duration: {int(length.group(1)) * 3600 + int(length.group(2)) * 60 + float(length.group(3)):.1f} s")
    say(f"Encoder: {encoder} ({'hardware' if hardware else 'software'})   preset: {args.preset}")
    say("Command: " + " ".join(shlex.quote(c) if os.name != "nt" else subprocess.list2cmdline([c]) for c in cmd))
    if progress:
        say(f"Progress file: {progress} (out_time and speed are the lines to watch)")
    if args.dry_run:
        say("(dry run, nothing encoded)")
        return 0
    code = run_wrapped(f"encode {abs_out.name}", True, cmd, batch=batch, low=args.low)
    if code == 0 and abs_out.is_file():
        say(f"Done: {abs_out} ({human(fsize(abs_out))}, source was {human(insize)})")
    else:
        warn(f"ffmpeg exited with {code}")
        # a half-written file must not be mistaken for a finished one (proxies skips existing outputs);
        # with --force an older file may have been there first, so that case is only reported
        if abs_out.is_file():
            if not args.force:
                with contextlib.suppress(OSError):
                    abs_out.unlink()
                warn(f"removed the incomplete output {abs_out}")
            else:
                warn(f"{abs_out} may be incomplete")
    return code


def cmd_encode(args):
    if args.preset not in ("proxy", "fast", "final"):
        raise Fail(f"encode: unknown preset '{args.preset}' (proxy, fast or final)")
    if args.codec not in ("h264", "hevc"):
        raise Fail(f"encode: unknown codec '{args.codec}' (h264 or hevc)")
    return encode_one(args)


# ---------- proxies / status / clean ----------

def cmd_proxies(args):
    work = work_dir()
    project = args.project
    if not project:
        names = sorted(p.name for p in work.iterdir() if (p / "src").is_dir()) if work.is_dir() else []
        if len(names) != 1:
            raise Fail(f"proxies: say which project (found: {', '.join(names) or 'none'})")
        project = names[0]
    valid_project(project)
    pdir = work / project
    src = pdir / "src"
    if not src.is_dir():
        raise Fail(f"proxies: {src} does not exist (run lm fetch first)")
    if args.preset not in ("proxy", "fast"):
        raise Fail("proxies: preset must be proxy or fast")
    clips = sorted(f for f in list_files(src) if f.suffix.lower() in VIDEO_EXT and not f.name.startswith("._"))
    if not clips:
        raise Fail(f"proxies: no video files under {src}")
    logs = work / ".logs"
    logs.mkdir(parents=True, exist_ok=True)
    hw = None if args.sw else hardware_backend()            # decided once for the whole batch
    made = skipped = failed = 0
    seen, current, t0 = set(), None, time.time()
    try:
        with awake():
            for i, f in enumerate(clips, 1):
                rel = f.relative_to(src)
                out = pdir / "proxy" / rel.with_suffix(".mp4")
                if out in seen:
                    warn(f"two clips map to the same proxy name, skipped: {rel.as_posix()}")
                    failed += 1
                    continue
                seen.add(out)
                if out.exists() and out.stat().st_size > 0 and not args.force:
                    skipped += 1
                    continue
                if out.exists() and out.stat().st_size == 0:
                    out.unlink()
                say(f"[{i}/{len(clips)}] {rel.as_posix()}")
                (logs / f"proxies-{project}.status").write_text(f"{i}/{len(clips)} {rel.as_posix()}\n")
                current = out
                one = argparse.Namespace(input=str(f), output=str(out), preset=args.preset, codec="h264", bitrate=None,
                                         sw=args.sw, no_hwdecode=args.no_hwdecode, force=args.force, dry_run=False,
                                         extra=[], low=args.low)
                os.environ["LM_PROGRESS"] = str(logs / f"proxies-{project}.progress")
                try:
                    code = encode_one(one, hw=hw, batch=True, hw_known=True)        # one bad clip must not end the batch
                except Fail as exc:
                    warn(str(exc))
                    code = 1
                if CANCELLED.is_set():                                               # the stop reached ffmpeg, not this loop
                    raise KeyboardInterrupt
                made += code == 0
                failed += code != 0
                current = None
    except KeyboardInterrupt:
        if current is not None:
            with contextlib.suppress(OSError):
                current.unlink()
        print("\nlm: interrupted (the unfinished proxy was removed; run the same command again to continue)", file=sys.stderr)
        return 130
    finally:
        os.environ.pop("LM_PROGRESS", None)
    say(f"Proxies for {project}: {made} made, {skipped} already existed, {failed} failed ({int(time.time() - t0)}s). "
        f"Folder: {pdir / 'proxy'}")
    notify("Load Manager", f"Proxies for {project}: {made} made, {failed} failed")
    return 0 if failed == 0 else 1


def cmd_status(_args):
    work = work_dir()
    say(f"Work folder: {work}")
    say(f"Free on that disk: {human(free_bytes(work))}")
    if not work.is_dir():
        say("(it does not exist yet; lm fetch or lm setup creates it)")
        return 0
    projects = sorted(p for p in work.iterdir() if p.is_dir() and not p.name.startswith("."))
    for p in projects:
        say()
        say(p.name)
        for d in ("src", "proxy", "out"):
            say(f"  {d:<6} {human(tree_size(p / d)):>10}  {count_files(p / d)} file(s)")
        say(f"  originals on record: {len(manifest_latest(p.name))} (safe cleanup of those copies: lm clean {p.name} --src)")
    if not projects:
        say("No projects yet.")
    return 0


def cmd_clean(args):
    project = valid_project(args.project)
    pdir = work_dir() / project
    if not pdir.is_dir():
        raise Fail(f"clean: no such project: {project} (see: lm status)")
    if not (args.src or args.proxy):
        raise Fail("clean: say what to clean: --src and/or --proxy")
    candidates, tracked = [], 0
    if args.src:
        for size, rel, orig in manifest_latest(project):
            if not rel.startswith("src/") or ".." in rel:
                continue
            local = pdir / rel
            if local.is_symlink() or not local.is_file():
                continue
            tracked += 1
            if not Path(orig).is_file():
                say(f"keep    {rel}   (the original is no longer at {orig})")
            elif fsize(orig) != size:
                say(f"keep    {rel}   (the original has a different size now)")
            elif fsize(local) != size:
                say(f"keep    {rel}   (the local copy was changed after the fetch)")
            else:
                candidates.append((local, size))
        untracked = count_files(pdir / "src") - tracked
        if untracked > 0:
            say(f"keep    {untracked} file(s) in src/ with no record of an original (not fetched by lm)")
    if args.proxy and (pdir / "proxy").is_dir():
        for f in list_files(pdir / "proxy"):
            if not f.is_symlink():
                candidates.append((f, fsize(f)))
    if not candidates:
        say("Nothing to remove.")
        return 0
    total = sum(s for _, s in candidates)
    say(f"{'Removing' if args.yes else 'Would remove'} {len(candidates)} file(s), {human(total)}:")
    for f, _ in candidates[:20]:
        say(f"  {f.relative_to(pdir).as_posix()}")
    if len(candidates) > 20:
        say(f"  ... and {len(candidates) - 20} more")
    if not args.yes:
        say("Nothing was removed. Add --yes to do it. (out/ is never touched.)")
        return 0
    removed = 0
    for f, _ in candidates:
        with contextlib.suppress(OSError):
            f.unlink()
            removed += 1
    for sub in ("src", "proxy"):
        base = pdir / sub
        for root, _dirs, _files in os.walk(str(base), topdown=False):
            if Path(root) != base:
                with contextlib.suppress(OSError):
                    os.rmdir(root)                              # only succeeds when the folder is empty
    if args.src and manifest_path(project).is_file():
        kept = [m for m in manifest_latest(project) if (pdir / m[1]).is_file()]
        text = "".join(f"{s}\t{r}\t{o}\n" for s, r, o in kept)
        tmp = manifest_path(project).with_suffix(".new")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(str(tmp), str(manifest_path(project)))
    say(f"Removed {removed} file(s). Free on that disk now: {human(free_bytes(pdir))}")
    return 0


# ---------- setup / doctor ----------

def cmd_setup(_args):
    say(f"Work folder ready: {ensure_work()}")
    if system() == "Darwin":
        say("Tip: also add it under System Settings > Spotlight > Search Privacy so Spotlight does not index every render.")
    return 0


def row(tag, text):
    say(f"{tag:<9} {text}")


def _run_text(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def cloud_places():
    home, found = Path.home(), []
    candidates = list((home / "Library" / "CloudStorage").glob("*")) if (home / "Library" / "CloudStorage").is_dir() else []
    candidates += [home / "Google Drive", home / "Library" / "Mobile Documents", home / "iCloudDrive", home / "Dropbox"]
    candidates += sorted(home.glob("OneDrive*"))
    if system() == "Windows":
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            for name in ("My Drive", "Мой диск", "Мій диск"):
                candidates.append(Path(f"{letter}:/{name}"))
    for c in candidates:
        with contextlib.suppress(OSError):
            if c.exists():
                found.append(c)
    return found


def cmd_doctor(_args):
    say(f"Load Manager {__version__} - video workflow report (read-only)")
    say()
    sysname, work = system(), work_dir()
    if sysname == "Darwin":
        os_version = _run_text(["sw_vers", "-productVersion"]).strip() or platform.mac_ver()[0]
        cpu = _run_text(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or "cpu unknown"
        row("[info]", f"macOS {os_version}, {platform.machine()}, {cpu}")
        batt = _run_text(["pmset", "-g", "batt"]).splitlines()
        if batt:
            row("[info]", "power: " + re.sub(r"^Now drawing from |'", "", batt[0]))
        therm = [l for l in _run_text(["pmset", "-g", "therm"]).splitlines() if "CPU_Speed_Limit" in l]
        row("[warn]", f"thermal throttling reported: {therm[0].strip()}") if therm else row("[ok]", "no thermal throttling reported by pmset")
    else:
        release = platform.release()
        if sysname == "Windows" and release == "10" and int((platform.version().split(".") + ["0", "0", "0"])[2] or 0) >= 22000:
            release = "11"                                            # platform still says 10 on Windows 11
        row("[info]", f"{sysname} {release}, {platform.machine()}, {platform.processor() or 'cpu unknown'}")
        if sysname == "Windows":
            plan = re.search(r"\((.*?)\)\s*$", _run_text(["powercfg", "/getactivescheme"]).strip())
            if plan:
                row("[info]", f"power plan: {plan.group(1)} (the Power page of the Load Manager app can make it quieter)")
        with contextlib.suppress(Exception):
            import psutil
            b = psutil.sensors_battery()
            if b is not None:
                row("[info]", f"power: {b.percent:.0f}% {'plugged in' if b.power_plugged else 'on battery'}")
    say()
    free = free_bytes(work)
    row("[info]", f"free disk space at {work}: {human(free)}")
    if free < 50 * GIB:
        row("[warn]", "under 50 GB free: big renders may fail halfway")
    if work.is_dir():
        row("[info]", f"work folder holds {human(tree_size(work))}")
        if sysname == "Darwin" and shutil.which("tmutil"):
            excluded = "Excluded" in _run_text(["tmutil", "isexcluded", str(work)])
            row("[ok]", "work folder is excluded from Time Machine") if excluded else row("[warn]", "work folder is NOT excluded from Time Machine (run: lm setup)")
    else:
        row("[info]", "work folder does not exist yet (lm setup or lm fetch creates it)")
    if sysname == "Darwin" and shutil.which("tmutil"):
        n = _run_text(["tmutil", "listlocalsnapshots", "/"]).count("com.apple")
        row("[info]", f"local Time Machine snapshots: {n} (they can hold back many GB of 'purgeable' space)")
    say()
    places = cloud_places()
    for c in places:
        row("[info]", f"cloud folder: {c}")
    if not places:
        row("[info]", "no cloud-synced folders found in the usual places")
    with contextlib.suppress(Exception):
        import psutil
        for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
            name = (p.info.get("name") or "")
            if re.search(r"google ?drive|drivefs|fileprovider|dropbox|onedrive", name, re.I):
                row("[info]", f"running: {name}  mem {human(p.info['memory_info'].rss)}")
    say()
    ffmpeg = ffmpeg_cmd()
    if shutil.which(ffmpeg[0]) is None and not Path(ffmpeg[0]).exists():
        row("[warn]", "ffmpeg not found (macOS: brew install ffmpeg; Windows: winget install ffmpeg)")
    else:
        row("[ok]", (_run_text(ffmpeg + ["-version"]).splitlines() or ["ffmpeg"])[0])
        encoders, any_hw = _encoders(tuple(ffmpeg)), False
        for backend in HW_ORDER.get(sysname, []):
            for codec in ("h264", "hevc"):
                enc = f"{codec}_{backend}"
                if enc not in encoders:
                    continue
                any_hw = True
                ok = _selftest(tuple(ffmpeg), enc)
                row("[ok]" if ok else "[warn]", f"self-test: {enc} {'encoded 1 s of 720p' if ok else 'FAILED (no such hardware, or its driver)'}")
        if not any_hw:
            row("[warn]", "this ffmpeg has no hardware encoder for this machine; lm encode uses software x264/x265")
        for soft in ("libx264", "libx265"):
            row("[ok]" if soft in encoders else "[info]", f"{soft} {'available' if soft in encoders else 'not in this build'}")
    if sysname == "Darwin":
        row("[ok]", "caffeinate is available") if shutil.which("caffeinate") else row("[warn]", "caffeinate not found (lm run cannot keep the Mac awake)")
    elif sysname == "Windows":
        try:
            from .winapi import WakeRequest
            request = WakeRequest("Load Manager doctor")
            request.hold()
            request.close()
            row("[ok]", "Windows accepts a stay-awake request (lm run and lm proxies use it)")
        except OSError:
            row("[warn]", "Windows refused a stay-awake request; the computer may sleep during long jobs")
    say()
    say(f"Rule of thumb: copy out of the cloud folder (lm fetch), work in {work}, copy results back (lm publish).")
    return 0


# ---------- command line ----------

def build_parser():
    p = argparse.ArgumentParser(prog="lm", description=f"Load Manager {__version__} - big-video helpers (Windows and macOS)")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    sub.add_parser("doctor", help="health report and hardware-encoder self-test (changes nothing)")
    sub.add_parser("setup", help="create the local work folder")
    f = sub.add_parser("fetch", help="copy from Google Drive / Dropbox / OneDrive / iCloud into the work folder")
    f.add_argument("--project")
    f.add_argument("--verify", action="store_true")
    f.add_argument("--dry-run", action="store_true")
    f.add_argument("sources", nargs="+")
    e = sub.add_parser("encode", help="ffmpeg with sane presets (hardware encoder when it exists)")
    e.add_argument("input")
    e.add_argument("output")
    e.add_argument("--preset", default="fast")
    e.add_argument("--codec", default="h264")
    e.add_argument("--bitrate")
    e.add_argument("--sw", action="store_true", help="force the software encoder")
    e.add_argument("--no-hwdecode", action="store_true", help="macOS: do not use hardware decoding")
    e.add_argument("--low", action="store_true", help="run at lower priority so the computer stays usable")
    e.add_argument("--force", action="store_true")
    e.add_argument("--dry-run", action="store_true")
    px = sub.add_parser("proxies", help="a proxy of every clip in a project (resumable)")
    px.add_argument("project", nargs="?")
    px.add_argument("--preset", default="proxy")
    px.add_argument("--sw", action="store_true")
    px.add_argument("--no-hwdecode", action="store_true")
    px.add_argument("--low", action="store_true")
    px.add_argument("--force", action="store_true")
    r = sub.add_parser("run", help="keep the computer awake while a command runs, notify when it ends")
    r.add_argument("--name")
    r.add_argument("--no-notify", action="store_true")
    r.add_argument("--low", action="store_true")
    r.add_argument("cmdline", nargs=argparse.REMAINDER, metavar="COMMAND")
    pub = sub.add_parser("publish", help="copy results into a cloud folder (never replaces without --force)")
    pub.add_argument("--force", action="store_true")
    pub.add_argument("items", nargs="+")
    sub.add_parser("status", help="projects in the work folder and free disk space")
    c = sub.add_parser("clean", help="free disk space (lists first unless --yes)")
    c.add_argument("project")
    c.add_argument("--src", action="store_true")
    c.add_argument("--proxy", action="store_true")
    c.add_argument("--yes", action="store_true")
    return p


HANDLERS = {"doctor": cmd_doctor, "setup": cmd_setup, "fetch": cmd_fetch, "encode": cmd_encode, "proxies": cmd_proxies,
            "run": cmd_run, "publish": cmd_publish, "status": cmd_status, "clean": cmd_clean}
COMMANDS = set(HANDLERS)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if argv and argv[0] == "encode" and "--" in argv:                 # lm encode IN OUT ... -- extra ffmpeg args
        cut = argv.index("--")
        argv, extra = argv[:cut], argv[cut + 1:]
    args = build_parser().parse_args(argv)
    if not args.command:
        build_parser().print_help()
        return 2
    args.extra = extra
    if env("CANCEL_FILE"):
        start_cancel_watch(env("CANCEL_FILE"))
    try:
        return HANDLERS[args.command](args) or 0
    except Fail as exc:
        print(f"lm: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print("\nlm: interrupted", file=sys.stderr)
        return 130
