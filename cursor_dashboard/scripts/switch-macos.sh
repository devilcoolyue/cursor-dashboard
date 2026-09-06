set -euo pipefail
umask 077
if [ "$(date +%s)" -ge __EXPIRES_AT__ ]; then
  echo 'Token expired. Generate a new command.' >&2
  exit 1
fi
CURSOR_APP='/Applications/Cursor.app'
if [ ! -d "$CURSOR_APP" ]; then CURSOR_APP="$HOME/Applications/Cursor.app"; fi
CURSOR_EXE="$CURSOR_APP/Contents/MacOS/Cursor"
APP_ROOT="$CURSOR_APP/Contents/Resources/app"
CURSOR_DB="$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
if [ ! -x "$CURSOR_EXE" ] || [ ! -f "$CURSOR_DB" ] || [ ! -f "$APP_ROOT/bin/cursor" ]; then
  echo 'Cursor installation or database not found. Install and open Cursor first.' >&2
  exit 1
fi
ELECTRON_RUN_AS_NODE=1 "$CURSOR_EXE" -e 'require(process.argv[1])' "$APP_ROOT/node_modules/@vscode/sqlite3"
if pgrep -x Cursor >/dev/null; then
  echo 'Closing Cursor. Save your work and accept any save prompts.'
  osascript -e 'tell application "Cursor" to quit'
fi
for attempt in {1..30}; do
  if ! pgrep -x Cursor >/dev/null; then break; fi
  sleep 1
done
if pgrep -x Cursor >/dev/null; then
  echo 'Cursor is still running. Close it manually and run the command again.' >&2
  exit 1
fi
ELECTRON_RUN_AS_NODE=1 "$CURSOR_EXE" - "$APP_ROOT" "$CURSOR_DB" <<'CURSOR_PANEL_JS'
__ENGINE__
CURSOR_PANEL_JS
env -u ELECTRON_RUN_AS_NODE -u VSCODE_IPC_HOOK_CLI /bin/bash "$APP_ROOT/bin/cursor"
echo 'Cursor reopened. Check the account shown in Cursor.'
