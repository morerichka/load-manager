"""Native webview shell and a separate crash-recovery guardian."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
from .engine import FileLock, Monitor, PolicyError, Session, state_directory


class Bridge:
    def __init__(self, session, monitor, guardian):
        self._session, self._monitor, self._guardian = session, monitor, guardian

    def snapshot(self):
        return self._monitor.get()

    def _resolve(self, profile):
        return self._monitor.get().get("recommendation", "focus") if profile == "auto" else profile

    def preview(self, selections, profile):
        try:
            return {"ok": True, **self._session.preview(selections, self._resolve(profile))}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def apply(self, selections, profile):
        try:
            if self._guardian.poll() is not None:
                raise RuntimeError("The recovery guardian is not running. Restart Load Manager.")
            return {"ok": True, **self._session.apply(selections, self._resolve(profile))}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def benchmark(self):
        return {"ok": self._session.benchmark.start()}

    def auto_configure(self, enabled=None, groups=None, grace=None):
        try:
            return {"ok": True, "auto": self._session.autopilot.configure(enabled, groups, grace)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def timed_start(self, command, cwd="", mode="ab", order="baseline_first"):
        try:
            self._session.timed.start(command, cwd, mode, order)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def quiet_enter(self):
        try:
            return {"ok": True, "quiet": self._session.quiet.enter()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def quiet_leave(self):
        try:
            return {"ok": True, "quiet": self._session.quiet.leave()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def quiet_configure(self, scheme=None, render_scheme=None, auto_enter=None, mode=None, cap=None, keep_awake=None,
                        boost_off=None):
        try:
            return {"ok": True, "quiet": self._session.quiet.configure(scheme, render_scheme, auto_enter, mode, cap,
                                                                        keep_awake, boost_off)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def recorder_configure(self, enabled=None, on=None, off=None):
        try:
            return {"ok": True, "recorder": self._session.recorder.configure(enabled, on, off)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def recorder_clear(self):
        return {"ok": True, "recorder": self._session.recorder.clear()}

    def timed_cancel(self):
        self._session.timed.cancel()
        return {"ok": True}

    # -- the Video page: the lm commands as forms -------------------------------------------------

    def _video(self, call):
        try:
            return {"ok": True, **call()}
        except Exception as exc:
            return {"ok": False, "error": str(exc) or type(exc).__name__}

    def video_state(self, known=None):
        return self._video(lambda: self._session.video.state(known))

    def video_start(self, kind, params=None):
        return self._video(lambda: self._session.video.submit(kind, params))

    def video_queue_remove(self, item_id):
        return self._video(lambda: {"queue": self._session.video.queue_remove(item_id)})

    def video_queue_move(self, item_id, delta):
        return self._video(lambda: {"queue": self._session.video.queue_move(item_id, delta)})

    def video_queue_clear(self):
        return self._video(lambda: {"queue": self._session.video.queue_clear()})

    def video_queue_pause(self, paused):
        return self._video(lambda: {"queue": self._session.video.queue_pause(paused)})

    def video_queue_configure(self, keep_going=None, after=None):
        return self._video(lambda: {"queue": self._session.video.queue_configure(keep_going, after)})

    def video_cancel_countdown(self):
        return self._video(lambda: {"queue": self._session.video.cancel_countdown()})

    def video_cancel(self):
        return {"ok": True, "cancelled": self._session.video.cancel()}

    def video_overview(self):
        return self._video(self._session.video.overview)

    def video_kit(self):
        return self._video(self._session.video.kit)

    def video_configure(self, patch):
        return self._video(lambda: {"settings": self._session.video.configure(patch)})

    def video_inspect(self, path):
        return self._video(lambda: self._session.video.inspect(path))

    def video_suggest(self, path, preset="fast", codec="h264"):
        return self._video(lambda: {"output": self._session.video.suggest_output(path, preset, codec)})

    def video_reveal(self, kind, project="", path=""):
        return self._video(lambda: {"shown": self._session.video.reveal(kind, project, path)})

    def pick(self, kind, start=""):
        """Native file and folder dialogs for the Video page. Gives back the chosen paths (none when cancelled)."""
        window = getattr(self, "_window", None)
        if window is None:
            return {"ok": False, "error": "The file dialog is not available here. Type the path instead."}
        try:
            import webview
            kinds = webview.FileDialog
            video_types = ("Video files (*.mp4;*.mov;*.m4v;*.mxf;*.avi;*.mkv;*.mts;*.m2ts;*.mpg;*.mpeg;*.webm)", "All files (*.*)")
            where = Path(start) if isinstance(start, str) and start else None
            folder = str(where if where and where.is_dir() else where.parent if where and where.parent.is_dir() else "")
            if kind == "videos":
                result = window.create_file_dialog(kinds.OPEN, directory=folder, allow_multiple=True, file_types=video_types)
            elif kind == "files":
                result = window.create_file_dialog(kinds.OPEN, directory=folder, allow_multiple=True)
            elif kind == "folder":
                result = window.create_file_dialog(kinds.FOLDER, directory=folder)
            elif kind == "save":
                result = window.create_file_dialog(kinds.SAVE, directory=folder, save_filename=where.name if where else "",
                                                   file_types=("MP4 video (*.mp4)", "QuickTime movie (*.mov)"))
            elif kind == "program":
                types = ("Programs (*.exe)", "All files (*.*)") if os.name == "nt" else ()
                result = window.create_file_dialog(kinds.OPEN, directory=folder, file_types=types)
            else:
                return {"ok": False, "error": "Unknown dialog"}
        except Exception as exc:
            return {"ok": False, "error": f"Could not open the dialog: {exc}"}
        paths = [result] if isinstance(result, str) else [str(p) for p in result or []]
        return {"ok": True, "paths": paths}

    def video_close_guard(self):
        """True when the window may close. A running video job asks for a second attempt within 15 seconds."""
        return self._session.video.close_guard()

    def restore(self, include_power=False):
        """The End session button leaves quiet mode on; closing the window restores everything."""
        try:
            result = self._session.restore(include_power=include_power)
            return {"ok": result["remaining"] == 0, **result,
                    "error": "Not every process was restored. Try again." if result["remaining"] else None}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def bundled_html(native=True):
    ui = Path(__file__).parent / "ui"
    html = (ui / "index.html").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="styles.css">',
                        '<style>'+ (ui / "styles.css").read_text(encoding="utf-8")+'</style>')
    html = html.replace('<script src="app.js" defer></script>',
                        '<script>'+ (ui / "app.js").read_text(encoding="utf-8")+'</script>')
    if native:
        html = html.replace('data-native="false"', 'data-native="true"')
    return html


def guardian_main(parent_pid, parent_created, journal):
    path = Path(journal)
    path.with_suffix(".ready").write_text("ready", encoding="utf-8")
    while True:
        try:
            p = psutil.Process(parent_pid)
            if abs(p.create_time()-parent_created) > 0.001 or p.status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(1)
    session = Session(journal=path)
    try:
        # Retry temporary access errors; a persistent journal also survives reboot.
        for _ in range(5):
            if not session.restore(from_disk=True)["remaining"]:
                break
            time.sleep(1)
    finally:
        path.with_suffix(".ready").unlink(missing_ok=True)


def recover_previous(directory):
    errors = []
    for journal in directory.glob("session-*.json"):
        try:
            _, pid, created = journal.stem.split("-")
            try:
                p = psutil.Process(int(pid))
                alive = int(p.create_time()*1000) == int(created) and p.status() != psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                alive = False
            if alive:
                continue
            recovery = Session(journal=journal)
            result = recovery.restore(from_disk=True)
            if result["remaining"]:
                errors.append(journal.name)
            else:
                journal.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, psutil.Error):
            errors.append(journal.name)
    return errors


def closing_handler(api):
    """What happens when the window is closed: False keeps it open."""
    def on_closing():
        if not api.video_close_guard():
            return False                  # a video job is running: the first close only asks, the second cancels it
        api.timed_cancel()
        result = api.restore(include_power=True)
        if not result.get("ok"):
            # Keep the window alive so the user can retry and read the failure.
            return False
    return on_closing


def launch():
    import webview
    directory = state_directory()
    instance = FileLock(directory / "instance.lock")
    try:
        instance.acquire(blocking=False)
    except OSError:
        raise SystemExit("Load Manager is already running.")
    session = Session(directory)
    if session.policy.monitor_only:
        instance.release()
        raise SystemExit("This environment exposes a different PID namespace. Use --snapshot for read-only monitoring.")
    errors = recover_previous(directory)
    if errors:
        session.log("A previous session is still partly unrestored. New actions are blocked.", "error")
    command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "loadmanager"]
    command += ["--guardian", str(os.getpid()), str(psutil.Process().create_time()), str(session.path)]
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    guardian = subprocess.Popen(command, **kwargs)
    for _ in range(50):
        if session.path.with_suffix(".ready").exists():
            session.guardian_ready = not errors
            break
        if guardian.poll() is not None:
            break
        time.sleep(0.1)
    session.guardian_check = lambda: guardian.poll() is None
    if session.quiet.available and session.quiet.auto_enter:
        try:
            session.quiet.enter()
        except PolicyError as exc:
            session.log(f"Quiet mode was not entered: {exc}")
    monitor = Monitor(session)
    thread = threading.Thread(target=monitor.run, daemon=True)
    thread.start()
    session.log("Monitoring started. No system settings were changed.")
    api = Bridge(session, monitor, guardian)
    window = webview.create_window("Load Manager", html=bundled_html(), js_api=api,
                                   width=1280, height=850, min_size=(900, 650),
                                   background_color="#111414", text_select=True)

    api._window = window

    window.events.closing += closing_handler(api)
    try:
        webview.start(debug=False, private_mode=True)
    finally:
        monitor.stop_event.set()
        session.video.shutdown()
        session.restore()
        instance.release()


def main():
    parser = argparse.ArgumentParser(description="Load Manager desktop")
    parser.add_argument("--guardian", nargs=3, metavar=("PID", "CREATED", "JOURNAL"), help=argparse.SUPPRESS)
    parser.add_argument("--snapshot", action="store_true", help="Print real local metrics without starting a UI")
    parser.add_argument("--benchmark", action="store_true", help="Run the CPU-contention probe once and print the score")
    parser.add_argument("--export-demo", metavar="PATH", help="Export a standalone, clearly labeled UI demo")
    args = parser.parse_args()
    if args.snapshot or args.benchmark:
        sys.stdout.reconfigure(encoding="utf-8")
    if args.guardian:
        guardian_main(int(args.guardian[0]), float(args.guardian[1]), args.guardian[2])
    elif args.export_demo:
        Path(args.export_demo).write_text(bundled_html(native=False), encoding="utf-8")
    elif args.benchmark:
        probe = Session().benchmark
        probe.start()
        while probe.running:
            time.sleep(0.2)
        print(json.dumps(probe.state(), ensure_ascii=False, indent=2))
    elif args.snapshot:
        m = Monitor(Session())
        m.sample()
        time.sleep(0.3)
        print(json.dumps(m.sample(), ensure_ascii=False, indent=2))
    else:
        launch()


if __name__ == "__main__":
    main()
