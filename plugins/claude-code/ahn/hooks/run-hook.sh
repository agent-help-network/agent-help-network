#!/bin/sh
# Run ahn_lookup.py with an available Python 3. Claude Code runs hook commands
# through sh (Git Bash on Windows). Without a usable Python this prints nothing
# and exits 0, so a missing interpreter never affects the tool call.
script="$(dirname "$0")/ahn_lookup.py"
if command -v py >/dev/null 2>&1; then
  exec py -3 "$script"
fi
# On Windows "python3" can be the Microsoft Store alias, which is not Python.
if command -v python3 >/dev/null 2>&1 && python3 -c "" </dev/null >/dev/null 2>&1; then
  exec python3 "$script"
fi
if command -v python >/dev/null 2>&1 && python -c "import sys; sys.exit(sys.version_info[0] != 3)" </dev/null >/dev/null 2>&1; then
  exec python "$script"
fi
exit 0
