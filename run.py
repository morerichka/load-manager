"""PyInstaller entry point; also supports `python run.py`."""
import sys
from loadmanager.cli import main
if __name__ == "__main__":
    sys.exit(main())
