#!/bin/bash
# 编译并安装 English Coach 菜单栏 App
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="/Applications/English Coach.app"

echo "编译中…"
cd "$DIR"

# 先生成图标（需要 Pillow）
if command -v python3 >/dev/null 2>&1; then
  python3 make_icon.py >/dev/null 2>&1 && echo "图标已生成" || echo "警告：图标生成失败，跳过"
fi

swiftc -O -o EnglishCoach main.swift -framework Cocoa -framework Carbon

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp EnglishCoach "$APP/Contents/MacOS/EnglishCoach"
chmod +x "$APP/Contents/MacOS/EnglishCoach"

# App 图标
if [ -f "$DIR/icons/AppIcon.icns" ]; then
  cp "$DIR/icons/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
fi
# 菜单栏图标
[ -f "$DIR/icons/menubarTemplate.png" ] && cp "$DIR/icons/menubarTemplate.png" "$APP/Contents/Resources/"
[ -f "$DIR/icons/menubarTemplate@2x.png" ] && cp "$DIR/icons/menubarTemplate@2x.png" "$APP/Contents/Resources/"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleExecutable</key>
	<string>EnglishCoach</string>
	<key>CFBundleIdentifier</key>
	<string>local.englishcoach.menubar</string>
	<key>CFBundleName</key>
	<string>English Coach</string>
	<key>CFBundleDisplayName</key>
	<string>English Coach</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>1.0</string>
	<key>CFBundleVersion</key>
	<string>1</string>
	<key>CFBundleIconFile</key>
	<string>AppIcon</string>
	<key>LSMinimumSystemVersion</key>
	<string>12.0</string>
	<key>NSAppTransportSecurity</key>
	<dict>
		<!-- 服务是自建的纯 HTTP 地址，需要放开 ATS -->
		<key>NSAllowsArbitraryLoads</key>
		<true/>
		<key>NSAllowsLocalNetworking</key>
		<true/>
	</dict>
	<key>LSUIElement</key>
	<true/>
	<key>NSHighResolutionCapable</key>
	<true/>
</dict>
</plist>
PLIST

# 移除隔离属性，避免「无法验证开发者」
xattr -cr "$APP" 2>/dev/null || true

# 签名：优先用稳定的开发者证书，否则退回 ad-hoc。
# 关键：ad-hoc 每次重建 CDHash 都会变，导致系统「辅助功能」授权失效，
# 表现为设置里开关是开的、但 App 里 AXIsProcessTrusted() 仍是 false。
SIGN_ID="${EC_SIGN_ID:-}"
if [ -z "$SIGN_ID" ]; then
  SIGN_ID=$(security find-identity -v -p codesigning 2>/dev/null \
    | grep -m1 "Apple Development" \
    | sed -E 's/.*"(.+)".*/\1/')
fi

if [ -n "$SIGN_ID" ]; then
  codesign --force --deep --sign "$SIGN_ID" --options runtime "$APP" 2>/dev/null \
    && echo "已用证书签名：$SIGN_ID"
else
  codesign --force --deep --sign - "$APP" 2>/dev/null || true
  echo "警告：未找到开发者证书，使用 ad-hoc 签名（每次重建需重新授权辅助功能）"
fi

# 确保配置目录存在
mkdir -p "$HOME/.english-coach"
# 第一个参数是 API Key，第二个参数是服务地址（都可选）
if [ ! -f "$HOME/.english-coach/key" ] && [ -n "${1:-}" ]; then
  printf '%s' "$1" > "$HOME/.english-coach/key"
  chmod 600 "$HOME/.english-coach/key"
fi
if [ -n "${2:-}" ]; then
  printf '%s' "$2" > "$HOME/.english-coach/api"
fi

open "$APP"

echo ""
echo "已安装并启动：$APP"
echo ""
echo "用法：任意 App 里选中英文 → 按 ⌃⌥E → 填中文意图 → 回车"
echo "     结果里的句子点一下就能复制"
echo ""
echo "菜单栏右上角有个「EC」图标，点开可以改 API Key。"
echo ""
echo "第一次用需要授权："
echo "  系统设置 → 隐私与安全性 → 辅助功能 → 打开「English Coach」"
echo "  （因为要模拟 ⌘C 读取你选中的文字）"
echo ""
