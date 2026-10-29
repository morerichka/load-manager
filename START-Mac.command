#!/bin/bash
set -u
cd -- "$(dirname -- "$0")" || exit 1
printf '\n  LOAD MANAGER\n  Preparing your workspace...\n\n'
if ! command -v python3 >/dev/null 2>&1; then
  printf 'Install Python 3.12 from https://www.python.org/downloads/macos/\n'
  read -r -p 'Press Return to close. '
  exit 1
fi
python3 scripts/bootstrap.py --launch
result=$?
if [ "$result" -ne 0 ]; then
  printf '\nCould not start. Read the message above and START-HERE.txt.\n'
  read -r -p 'Press Return to close. '
fi
exit "$result"
