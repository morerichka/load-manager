"""Build on the target OS. No cross-compilation or signing credentials required."""
import platform
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
args=[sys.executable,"-m","PyInstaller","--noconfirm","--clean","--windowed",
      "--name","LoadManager","--collect-all","webview","--collect-all","loadmanager",
      "--add-data",f"{root/'loadmanager/ui'}:loadmanager/ui"]
if platform.system()=="Windows":
    args += ["--icon",str(root/"loadmanager/ui/app.ico")]
if platform.system()=="Darwin":
    args += ["--osx-bundle-identifier","app.loadmanager.desktop"]
args += [str(root/"run.py")]
subprocess.run(args,cwd=root,check=True)
