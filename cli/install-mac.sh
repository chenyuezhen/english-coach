#!/bin/bash
# 安装 English Coach 到 macOS 右键「服务」菜单 + 可绑定快捷键
# 用法：bash cli/install-mac.sh <API_KEY>

set -euo pipefail

KEY="${1:-}"
if [ -z "$KEY" ]; then
  echo "用法: bash cli/install-mac.sh <API_KEY>"
  echo "API Key 在网页「成员」页点一下即可复制"
  exit 1
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$HOME/.english-coach"
WORKFLOW="$HOME/Library/Services/English Coach.workflow"

mkdir -p "$TARGET"
printf '%s' "$KEY" > "$TARGET/key"
chmod 600 "$TARGET/key"

cp "$DIR/report.sh" "$TARGET/report.sh"
chmod +x "$TARGET/report.sh"

# ---- 生成标准 Automator 服务包 ----
rm -rf "$WORKFLOW"
mkdir -p "$WORKFLOW/Contents"

cat > "$WORKFLOW/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>NSServices</key>
	<array>
		<dict>
			<key>NSBackgroundColorName</key>
			<string>background</string>
			<key>NSIconName</key>
			<string>NSActionTemplate</string>
			<key>NSMenuItem</key>
			<dict>
				<key>default</key>
				<string>English Coach 记一笔</string>
			</dict>
			<key>NSMessage</key>
			<string>runWorkflowAsService</string>
			<key>NSSendTypes</key>
			<array>
				<string>public.utf8-plain-text</string>
			</array>
		</dict>
	</array>
</dict>
</plist>
PLIST

cat > "$WORKFLOW/Contents/document.wflow" <<'WFLOW'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>AMApplicationBuild</key>
  <string>521</string>
  <key>AMApplicationVersion</key>
  <string>2.10</string>
  <key>AMDocumentVersion</key>
  <string>2</string>
  <key>actions</key>
  <array>
    <dict>
      <key>action</key>
      <dict>
        <key>AMAccepts</key>
        <dict>
          <key>Container</key>
          <string>List</string>
          <key>Optional</key>
          <false/>
          <key>Types</key>
          <array>
            <string>com.apple.cocoa.string</string>
          </array>
        </dict>
        <key>AMActionVersion</key>
        <string>2.0.3</string>
        <key>AMParameterProperties</key>
        <dict>
          <key>COMMAND_STRING</key>
          <dict/>
          <key>CheckedFix</key>
          <dict/>
          <key>inputMethod</key>
          <dict/>
          <key>shell</key>
          <dict/>
        </dict>
        <key>ActionBundlePath</key>
        <string>/System/Library/Automator/Run Shell Script.action</string>
        <key>ActionName</key>
        <string>Run Shell Script</string>
        <key>ActionParameters</key>
        <dict>
          <key>COMMAND_STRING</key>
          <string>input=$(cat)
export ENGLISH_COACH_APP="$1"
"$HOME/.english-coach/report.sh" "$input"</string>
          <key>CheckedFix</key>
          <false/>
          <key>inputMethod</key>
          <integer>0</integer>
          <key>shell</key>
          <string>/bin/bash</string>
        </dict>
        <key>BundleIdentifier</key>
        <string>com.apple.RunShellScript</string>
        <key>CFBundleVersion</key>
        <string>2.0.3</string>
        <key>CanShowSelectedItemsWhenRun</key>
        <false/>
        <key>CanShowWhenRun</key>
        <true/>
        <key>Class Name</key>
        <string>RunShellScriptAction</string>
        <key>InputUUID</key>
        <string>4B0F3A4C-9A11-4E33-9C55-1A1F0E9B2C01</string>
        <key>Keywords</key>
        <array/>
        <key>OutputUUID</key>
        <string>5C1E4B5D-AB22-4F44-AD66-2B2G1F0C3D02</string>
        <key>UnlocalizedApplications</key>
        <array/>
        <key>UUID</key>
        <string>6D2F5C6E-BC33-4A55-BE77-3C3H2A1D4E03</string>
        <key>arguments</key>
        <dict/>
        <key>isViewVisible</key>
        <integer>1</integer>
      </dict>
      <key>isViewVisible</key>
      <integer>1</integer>
    </dict>
  </array>
  <key>connectors</key>
  <dict/>
  <key>workflowMetaData</key>
  <dict>
    <key>serviceInputTypeIdentifier</key>
    <string>com.apple.Automator.text</string>
    <key>serviceOutputTypeIdentifier</key>
    <string>com.apple.Automator.nothing</string>
    <key>serviceProcessesInput</key>
    <integer>0</integer>
    <key>workflowTypeIdentifier</key>
    <string>com.apple.Automator.servicesMenu</string>
  </dict>
</dict>
</plist>
WFLOW

# 刷新服务缓存，让菜单立刻出现
/System/Library/CoreServices/pbs -update 2>/dev/null || true

echo ""
echo "已安装：English Coach"
echo ""
echo "现在可以这样用："
echo "  1) 选中一段英文"
echo "  2) 右键 → 服务 → English Coach"
echo ""
echo "绑定快捷键（推荐，比右键快很多）："
echo "  系统设置 → 键盘 → 键盘快捷键 → 服务 → 找到 English Coach → 点「无」→ 按 Ctrl+Option+E"
echo ""
echo "第一次运行如果被拦截："
echo "  系统设置 → 隐私与安全性 → 辅助功能，允许「English Coach」"
echo ""
echo "配置位置：$TARGET/key"
echo ""
