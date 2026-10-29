"""Console-free desktop entry point with an error log and a visible error dialog."""
import ctypes
import os
import runpy
import sys
import traceback
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
log_path = root / "loadmanager-startup.log"


def report(message):
    text = f"Load Manager could not start.\n\n{message}\n\nDetails: {log_path}"
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, text, "Load Manager", 0x10)
    else:
        try:
            import tkinter
            from tkinter import messagebox
            window = tkinter.Tk()
            window.withdraw()
            messagebox.showerror("Load Manager", text)
            window.destroy()
        except Exception:
            print(text, file=sys.stderr)


with open(log_path, "a", encoding="utf-8", buffering=1) as log:
    sys.stdout = sys.stderr = log
    try:
        runpy.run_module("loadmanager", run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            print(str(exc))
            report(str(exc))
            raise
    except Exception as exc:
        traceback.print_exc()
        report(str(exc))
        raise SystemExit(1)
