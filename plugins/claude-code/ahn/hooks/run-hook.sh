#!/bin/sh
# Run ahn_lookup.py with Python 3.7 or newer. Claude Code runs hook commands
# through sh (Git Bash on Windows). Without a usable Python this prints nothing
# and exits 0, so a missing or too-old interpreter never affects the tool call.
case "$0" in
  */*) here="${0%/*}" ;;
  *) here=. ;;
esac
script="$here/ahn_lookup.py"
check='import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)'

# Exec the interpreter given as arguments if it exists and is Python 3.7+.
# The check reads /dev/null, so the hook payload on stdin stays for the hook.
# Both starts are isolated (-I) and skip site (-S): the hook is stdlib-only, and loading
# site-packages .pth files costs seconds per run on some machines.
try_python() {
  command -v "$1" >/dev/null 2>&1 || return 1
  "$@" -I -S -c "$check" </dev/null >/dev/null 2>&1 || return 1
  exec "$@" -I -S "$script"
}

if [ "${OS:-}" = "Windows_NT" ]; then
  # "python3" can be the Microsoft Store alias, which is not Python; the check skips it.
  try_python py -3
  try_python python3
  try_python python
else
  try_python python3
  try_python python
  try_python py -3
fi
exit 0
