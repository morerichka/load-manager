"""One entry point for everything: the video helpers, the Claude kit, the spike log and the app window.

  lm doctor | setup | fetch | encode | proxies | run | publish | status | clean     big-video helpers
  lm install-claude | uninstall-claude                                               the Claude hook and skill
  lm guard                                                                           the hook itself (reads stdin)
  lm spikes                                                                          recent busy periods and their causes
  lm app (or no arguments with `python -m loadmanager`)                              the desktop window
"""
from __future__ import annotations

import sys

from . import __version__

VIDEO = {"doctor", "setup", "fetch", "encode", "proxies", "run", "publish", "status", "clean"}


def spikes():
    """Recent busy periods from the spike log the app keeps (read-only)."""
    import time
    from .engine import read_json, state_directory
    log = read_json(state_directory() / "history.json", {}).get("spikes", [])
    if not log:
        print("No busy periods recorded yet. Open the Load Manager app and leave it running; "
              "it records them on the Spikes page.")
        return 0
    for s in reversed(log[-10:]):
        top = ", ".join(f"{c['name']}{' via ' + c['via'] if c.get('via') else ''} {c['share']}%" for c in s["culprits"][:3])
        print(f"{time.strftime('%d %b %H:%M', time.localtime(s['started']))}  {int(s['seconds'])} s  "
              f"avg {s['avg_cpu']}% peak {s['peak_cpu']}%  {top}")
    return 0


def main(argv=None, default_app=True):
    argv = list(sys.argv[1:] if argv is None else argv)
    first = argv[0] if argv else None
    if first in VIDEO:
        from . import video
        return video.main(argv)
    if first == "guard":
        from . import guard
        return guard.main()
    if first in ("install-claude", "uninstall-claude"):
        from . import install_claude
        return install_claude.main(argv)
    if first == "spikes":
        return spikes()
    if first in ("version", "--version"):
        print(f"Load Manager {__version__}")
        return 0
    if first in (None, "-h", "--help", "help") and not default_app:
        print(__doc__)
        return 0 if first else 2
    from . import app
    if first == "app":
        sys.argv = [sys.argv[0]] + argv[1:]
    app.main()
    return 0


def lm_main():
    """The `lm` command: with no arguments it shows help instead of opening the window."""
    sys.exit(main(default_app=False))
