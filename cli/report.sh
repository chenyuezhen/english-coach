#!/bin/bash
# English Coach 一键上报
# 选中英文 → 右键「服务 → English Coach」或按快捷键 → 补一句中文意图 → 结果自动进剪贴板

set -uo pipefail

API="${ENGLISH_COACH_API:-}"
[ -z "$API" ] && [ -f "$HOME/.english-coach/api" ] && API=$(tr -d ' \n\r' < "$HOME/.english-coach/api")
API="${API:-http://127.0.0.1:8080}"
KEY="${ENGLISH_COACH_KEY:-}"
[ -z "$KEY" ] && [ -f "$HOME/.english-coach/key" ] && KEY=$(tr -d ' \n\r' < "$HOME/.english-coach/key")

if [ -z "$KEY" ]; then
  osascript -e 'display dialog "还没配置 API Key。\n\n在终端运行：\nmkdir -p ~/.english-coach\necho 你的KEY > ~/.english-coach/key\n\n（网页「成员」页点 API Key 可复制）" buttons {"好"} default button 1 with title "English Coach"'
  exit 1
fi

# 选中文本：服务传入的 stdin/参数 → 否则读剪贴板
SELECTED="${1:-}"
if [ -z "$SELECTED" ] && [ ! -t 0 ]; then
  SELECTED=$(cat 2>/dev/null || true)
fi
if [ -z "$SELECTED" ]; then
  SELECTED=$(pbpaste 2>/dev/null || true)
fi

APP_NAME="${ENGLISH_COACH_APP:-}"
if [ -z "$APP_NAME" ]; then
  APP_NAME=$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null || true)
fi

INTENT=$(osascript <<'AS'
set r to display dialog "你想表达什么？（可留空）" default answer "" with title "English Coach" buttons {"取消","提交"} default button 2
if button returned of r is "取消" then return "__CANCEL__"
return text returned of r
AS
)
[ "$INTENT" = "__CANCEL__" ] && exit 0

if [ -z "$INTENT" ] && [ -z "$SELECTED" ]; then
  osascript -e 'display dialog "没选中文字，也没写意图。" buttons {"好"} default button 1 with title "English Coach"'
  exit 0
fi

BODY=$(SELECTED="$SELECTED" INTENT="$INTENT" APP_NAME="$APP_NAME" python3 -c '
import json,os
print(json.dumps({
  "selected": os.environ.get("SELECTED",""),
  "intent_zh": os.environ.get("INTENT",""),
  "app": os.environ.get("APP_NAME",""),
}, ensure_ascii=False))
')

RESP=$(curl -sS -m 120 -X POST "$API/api/report" \
  -H "Content-Type: application/json" -H "X-API-Key: $KEY" -d "$BODY" 2>&1)

MSG=$(printf '%s' "$RESP" | python3 -c '
import json,sys
raw=sys.stdin.read()
try:
    d=json.loads(raw)
except Exception:
    print("提交失败：\n"+raw[:400]); sys.exit()
if "error" in d:
    print("提交失败："+str(d["error"])); sys.exit()
lv=d.get("levels",{})
best=lv.get("manager") or lv.get("formal_email") or lv.get("peer") or ""
print("【{}】\n\n{}\n\n可以直接粘：\n{}".format(d.get("topic",""), d.get("why_wrong",""), best))
' 2>&1)

# 最佳表达写进剪贴板
printf '%s' "$RESP" | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin); lv=d.get("levels",{})
    print(lv.get("manager") or lv.get("formal_email") or lv.get("peer") or "", end="")
except Exception: pass
' 2>/dev/null | pbcopy || true

MSG_ESC=$(printf '%s' "$MSG" | sed 's/\\/\\\\/g; s/"/\\"/g')
osascript -e "display dialog \"$MSG_ESC\" buttons {\"好\"} default button 1 with title \"English Coach\"" >/dev/null 2>&1 || true
