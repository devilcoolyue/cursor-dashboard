set -euo pipefail
umask 077
STEP_NUMBER=0
STEP_TITLE=''
STEP_ACTIVE=0
SPINNER_PID=''
QUIT_PID=''
ANIMATE=0
if [ -t 2 ] && [ "${TERM:-dumb}" != 'dumb' ]; then ANIMATE=1; fi

stop_spinner() {
  if [ -n "$SPINNER_PID" ]; then
    kill "$SPINNER_PID" 2>/dev/null || true
    wait "$SPINNER_PID" 2>/dev/null || true
    SPINNER_PID=''
  fi
  if [ "$ANIMATE" -eq 1 ] && [ "$STEP_ACTIVE" -eq 1 ]; then printf '\r\033[2K' >&2; fi
}
step_begin() {
  STEP_NUMBER=$1
  STEP_TITLE=$2
  STEP_ACTIVE=1
  if [ "$ANIMATE" -eq 1 ]; then
    (
      frames='|/-\\'
      frame=0
      started=$SECONDS
      while :; do
        printf '\r\033[2K  %s [%s/5] %s · 已等待 %s 秒' "${frames:frame%4:1}" "$STEP_NUMBER" "$STEP_TITLE" "$((SECONDS - started))" >&2
        frame=$((frame + 1))
        sleep 0.12
      done
    ) </dev/null &
    SPINNER_PID=$!
  else
    printf '  … [%s/5] %s\n' "$STEP_NUMBER" "$STEP_TITLE" >&2
  fi
}
step_done() {
  stop_spinner
  printf '  ✓ [%s/5] %s\n' "$STEP_NUMBER" "$STEP_TITLE" >&2
  STEP_ACTIVE=0
}
run_step() {
  local output
  # Keep native output from overwriting the animated line; replay errors intact.
  if output=$("$@" 2>&1); then
    step_done
    if [ -n "$output" ]; then printf '%s\n' "$output" >&2; fi
  else
    stop_spinner
    if [ -n "$output" ]; then printf '%s\n' "$output" >&2; fi
    return 1
  fi
}
cleanup_quit_request() {
  if [ -n "$QUIT_PID" ]; then
    # Only stop our AppleScript helper; never force-quit Cursor with unsaved work.
    kill -KILL "$QUIT_PID" 2>/dev/null || true
    wait "$QUIT_PID" 2>/dev/null || true
    QUIT_PID=''
  fi
}
finish() {
  result=$?
  stop_spinner
  cleanup_quit_request
  if [ "$result" -ne 0 ] && [ "$STEP_ACTIVE" -eq 1 ]; then
    printf '  ✗ [%s/5] %s：未完成，操作已停止。\n' "$STEP_NUMBER" "$STEP_TITLE" >&2
    if [ "$STEP_NUMBER" -eq 5 ]; then printf '账号已写入，请手动打开 Cursor 并核对当前账号。\n' >&2; fi
  fi
  exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf '\nCursor 账号切换\n检查安装 → 检查运行环境 → 退出 Cursor → 备份并写入 → 重新打开\n\n' >&2
printf '请先保存工作，并在 macOS 系统「终端」中运行此命令。\n\n' >&2
step_begin 1 '检查凭证与 Cursor 安装'
if [ "$(date +%s)" -ge __EXPIRES_AT__ ]; then
  stop_spinner
  echo '登录凭证已过期，请回到面板重新生成命令。' >&2
  exit 1
fi
CURSOR_APP='/Applications/Cursor.app'
if [ ! -d "$CURSOR_APP" ]; then CURSOR_APP="$HOME/Applications/Cursor.app"; fi
CURSOR_EXE="$CURSOR_APP/Contents/MacOS/Cursor"
APP_ROOT="$CURSOR_APP/Contents/Resources/app"
CURSOR_DB="$HOME/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
if [ ! -x "$CURSOR_EXE" ] || [ ! -f "$CURSOR_DB" ] || [ ! -f "$APP_ROOT/bin/cursor" ]; then
  stop_spinner
  echo '未找到 Cursor 安装或用户数据库，请先安装并打开一次 Cursor。' >&2
  exit 1
fi
step_done
step_begin 2 '检查 Cursor 运行环境与 SQLite'
run_step env ELECTRON_RUN_AS_NODE=1 "$CURSOR_EXE" -e 'require(process.argv[1]); console.log("运行环境：" + process.platform + " / " + process.arch)' "$APP_ROOT/node_modules/@vscode/sqlite3" </dev/null
printf '\n退出最多等待约 30 秒；请处理 Cursor 的保存确认及 macOS 自动化授权弹窗。\n' >&2
step_begin 3 '等待 Cursor 安全退出'
if pgrep -x Cursor >/dev/null; then
  # Apple Events and process exit share the same deadline.
  osascript -e 'tell application "Cursor" to quit' </dev/null &
  QUIT_PID=$!
  for attempt in {1..30}; do
    if ! pgrep -x Cursor >/dev/null; then break; fi
    if [ -n "$QUIT_PID" ] && ! kill -0 "$QUIT_PID" 2>/dev/null; then
      if wait "$QUIT_PID"; then
        QUIT_PID=''
      else
        QUIT_PID=''
        stop_spinner
        echo '退出请求失败或已取消。请保存文件，用 Cmd+Q 退出 Cursor，再到系统「终端」重新执行。' >&2
        exit 1
      fi
    fi
    sleep 1
  done
  cleanup_quit_request
fi
if pgrep -x Cursor >/dev/null; then
  stop_spinner
  echo '等待退出超过 30 秒，尚未修改账号。请保存文件，用 Cmd+Q 退出 Cursor，再到系统「终端」重新执行。' >&2
  exit 1
fi
step_done
step_begin 4 '备份本地数据并写入账号'
run_step env ELECTRON_RUN_AS_NODE=1 "$CURSOR_EXE" - "$APP_ROOT" "$CURSOR_DB" <<'CURSOR_PANEL_JS'
__ENGINE__
CURSOR_PANEL_JS
step_begin 5 '重新打开 Cursor'
run_step env -u ELECTRON_RUN_AS_NODE -u VSCODE_IPC_HOOK_CLI /bin/bash "$APP_ROOT/bin/cursor" </dev/null
printf '\n切换步骤已完成，请在 Cursor 中核对当前账号。\n' >&2
