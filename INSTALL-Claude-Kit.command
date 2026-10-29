#!/bin/bash
set -u
cd -- "$(dirname -- "$0")" || exit 1
printf '\n  LOAD MANAGER - CLAUDE KIT\n  Installing the Claude hook and skill...\n\n'
if ! command -v python3 >/dev/null 2>&1; then
  printf 'Install Python 3.12 from https://www.python.org/downloads/macos/ (or: brew install python)\n'
  read -r -p 'Press Return to close. '
  exit 1
fi
python3 scripts/bootstrap.py --claude-kit
result=$?
if [ "$result" -ne 0 ]; then
  printf '\nCould not install. Read the message above and START-HERE.txt.\n'
else
  printf '\nDone. Restart Claude. Then ask it to run: lm doctor\n'
fi
read -r -p 'Press Return to close. '
exit "$result"
