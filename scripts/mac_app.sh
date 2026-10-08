#!/usr/bin/env bash
# ============================================================
# M8 Mac 端：复用 iOS 的 SwiftUI 源码，编出一个能跑的 macOS .app
# ============================================================
# 用法：
#   bash scripts/mac_app.sh build     # 产出 .tooling/tmp/mac-app/Sekb.app
#   bash scripts/mac_app.sh run       # build + 直接跑二进制并抓自检输出
#   bash scripts/mac_app.sh ui        # build + 逐页**离屏渲染** PNG 到 apps/mac/docs/screenshots/
#
# ## 关于 `ui`（macOS 的视觉验证）
#
# 本机 `screencapture -x` 实测失败（`could not create image from display`：屏幕录制权限被拒），
# 所以这一端不用屏幕截图，改用 `ImageRenderer` **离屏渲染**（`SEKB_UI_PROBE=1`，见
# `apps/ios/App/MacUiProbe.swift`）：不需要任何系统权限，渲染的是同一份 `AppShell` 视图树。
# 它的局限（写进文档，不冒充截图）：不含窗口标题栏/圆角，也不是真实窗口的像素。
#
# 逐页状态由两个只读环境变量摆位：`SEKB_INITIAL_SCREEN` / `SEKB_DEMO_SEED`
# （与 `scripts/ios_app.sh ui` 同一套开关，两端同一份源码、同一套验证方法）。
# ============================================================
# ## 设计要点（都是与 iOS 侧的**有意差异**，不是遗漏）
#
# 1. **源码直接复用 `apps/ios/App/*.swift`，不复制一份。**
#    实测这些文件**没有任何 UIKit 依赖**（`grep -rn 'import UIKit\|UIApplication'` 为空），
#    用到的 PDFKit / Foundation / Security / SwiftUI 在 macOS 上都有。
#    复制一份就等于"两端各改各的"，正是 RFC 反对的。
#
# 2. **不需要 `simctl`、也不需要抓统一日志**：macOS 上直接 exec `Contents/MacOS/Sekb`，
#    stdout 就是我们的。iOS 那边要 `simctl launch` + `log show --predicate` 是因为
#    模拟器里 `print` 不进统一日志（踩过），macOS 没这个问题 —— **这条比 iOS 干净**。
#
# 3. **做 ad-hoc 签名（`codesign -s -`）**，与 iOS 侧"故意不签名"相反：
#    iOS 侧不签是因为"未签名→Keychain -34018"与"ad-hoc 签名→SpringBoard 拒绝启动"互斥；
#    macOS 没有 SpringBoard 那道门，ad-hoc 签名后 Keychain 才能用 →
#    **iOS 上记为 SKIP 的 Keychain 往返，在 macOS 上应当能真验**（M8 的额外价值）。
#
# 4. ORT 用 `macos-arm64_x86_64` 切片（同一个 xcframework，与 iOS 共用下载产物）。
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
export TMPDIR="$ROOT/.tooling/tmp"
export CLANG_MODULE_CACHE_PATH="$TMPDIR/modulecache"
mkdir -p "$TMPDIR/modulecache"

APP_NAME="Sekb"
BUNDLE_ID="com.sekb.ondevice.mac"
BUILD_DIR="$TMPDIR/mac-app"
APP_BUNDLE="$BUILD_DIR/$APP_NAME.app"
TARGET="arm64-apple-macos14.0"

FW_MAC="$ROOT/apps/shared/build/bin/macosArm64/debugFramework"
ORT_MAC="$ROOT/.tooling/ort-ios/extracted/onnxruntime.xcframework/macos-arm64_x86_64"
MODEL_ROOT="$ROOT/.tooling/models"

[ -n "${JAVA_HOME:-}" ] || for cand in "/Applications/Android Studio.app/Contents/jbr/Contents/Home"; do
    [ -x "$cand/bin/java" ] && export JAVA_HOME="$cand" && break
done
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$ROOT/.tooling/gradle-home}"
export ANDROID_USER_HOME="$ROOT/.tooling/android-home"
export KONAN_DATA_DIR="${KONAN_DATA_DIR:-$ROOT/.tooling/konan}"

build_app() {
    echo "── 1/3 产出 macosArm64 的 SharedCore.framework ──"
    # `SEKB_SKIP_FRAMEWORK_BUILD=1`：复用已有 framework（理由与注意项同 ios_app.sh）
    if [ "${SEKB_SKIP_FRAMEWORK_BUILD:-0}" = "1" ]; then
        echo "   ⚠️ 跳过共享层构建，复用：$FW_MAC/SharedCore.framework（$(stat -f '%Sm' "$FW_MAC/SharedCore.framework" 2>/dev/null)）"
    else
        ( cd "$ROOT/apps/android" && ./gradlew --no-daemon -PsekbNativeTargets=true \
            :shared:linkDebugFrameworkMacosArm64 --console=plain -q ) || return 1
    fi
    [ -d "$FW_MAC/SharedCore.framework" ] || { echo "❌ 缺少 framework：$FW_MAC" >&2; return 1; }
    [ -d "$ORT_MAC/onnxruntime.framework" ] || {
        echo "❌ 缺少 ONNX Runtime macOS 切片：$ORT_MAC" >&2
        echo "   获取方式见 apps/ios/README.md 的「来源订正」（download.onnxruntime.ai）" >&2
        return 1
    }

    echo "── 2/3 编译 SwiftUI（源码直接复用 apps/ios/App/*.swift）──"
    rm -rf "$APP_BUNDLE"
    mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"
    xcrun swiftc -O -parse-as-library -sdk "$(xcrun --sdk macosx --show-sdk-path)" \
        -target "$TARGET" -module-cache-path "$TMPDIR/modulecache" \
        -F "$FW_MAC" -framework SharedCore \
        -F "$ORT_MAC" -framework onnxruntime -lc++ \
        -import-objc-header "$ROOT/apps/ios/App/SekbOrtBridge.h" \
        -o "$APP_BUNDLE/Contents/MacOS/$APP_NAME" \
        $ROOT/apps/ios/App/*.swift || return 1

    echo "── 3/3 组装 .app（macOS 是 Contents/ 布局，与 iOS 的扁平布局不同）──"
    cat > "$APP_BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
    <key>CFBundleName</key><string>${APP_NAME}</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>0.1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

    # 模型与样本文档放 Contents/Resources（`Bundle.main.resourceURL` 在 macOS 指向这里）
    for v in bge-small-zh-v1.5 bge-small-zh-v1.5-int8; do
        if [ -f "$MODEL_ROOT/$v/model.onnx" ] && [ -f "$MODEL_ROOT/$v/vocab.txt" ]; then
            mkdir -p "$APP_BUNDLE/Contents/Resources/models/$v"
            cp "$MODEL_ROOT/$v/model.onnx" "$MODEL_ROOT/$v/vocab.txt" \
               "$APP_BUNDLE/Contents/Resources/models/$v/"
            echo "   ✅ 模型 ${v}"
        fi
    done
    for f in "$ROOT"/apps/android/app/src/test/resources/*.pdf; do
        [ -f "$f" ] && cp "$f" "$APP_BUNDLE/Contents/Resources/"
    done

    # ad-hoc 签名：macOS 上没有"签名就起不来"的问题，签了 Keychain 才可用
    codesign --force --sign - --timestamp=none "$APP_BUNDLE" 2>&1 | tail -2
    echo "✅ 已产出 ${APP_BUNDLE}（$(du -sh "$APP_BUNDLE" | cut -f1)）"
}

run_app() {
    build_app || return 1
    echo
    echo "── 运行并抓自检（stdout 直出，不需要日志系统）──"
    # 自检跑完应用自己退出（见 SekbApp.init 的 SEKB_SELFTEST_ONLY）→ 不需要 timeout。
    # ⚠️ macOS **没有 GNU `timeout`**（实测 `timeout: command not found`），所以"自己退"才是正解；
    # 若应用异常不退出，这里用纯 bash 的看门狗兜底，避免无人值守时挂死。
    local out_file="$TMPDIR/mac-selftest.log"
    : > "$out_file"
    # 数据目录显式落在仓库内：macOS 的 `~/Library/Application Support` 在本机文件策略下
    # 是 `Operation not permitted`（实测），不指路就会退到临时目录 → 会话不持久。
    SEKB_SELFTEST_ONLY=1 \
    SEKB_DATA_DIR="${SEKB_MAC_DATA_DIR:-$TMPDIR/mac-app-data}" \
        "$APP_BUNDLE/Contents/MacOS/$APP_NAME" >"$out_file" 2>&1 &
    local pid=$! waited=0 limit="${SEKB_MAC_WAIT:-240}"
    while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt "$limit" ]; do
        sleep 1; waited=$((waited + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "⚠️ 超过 ${limit}s 仍未退出，强制结束" >&2
        kill -TERM "$pid" 2>/dev/null; sleep 1; kill -KILL "$pid" 2>/dev/null
    fi
    wait "$pid" 2>/dev/null || true
    local out; out="$(cat "$out_file")"
    printf '%s\n' "$out" | grep -E 'SEKB_IOS_SELFTEST|SEKB_MAC' || true
    # 前缀按平台区分（iOS `SEKB_IOS_SELFTEST` / macOS `SEKB_MAC_SELFTEST`）；
    # 两个都认，这样旧日志与新日志都能判"自检跑完了没有"。
    printf '%s\n' "$out" | grep -qE 'SEKB_(MAC|IOS)_SELFTEST PASS=' || {
        echo "❌ 没抓到自检总结行（应用可能没起来或中途失败）" >&2
        printf '%s\n' "$out" | tail -20 >&2
        return 1
    }
}

ui_app() {
    build_app || return 1
    local out_dir="$ROOT/apps/mac/docs/screenshots"
    mkdir -p "$out_dir"
    local empty_data="$TMPDIR/mac-ui-empty" demo_data="$TMPDIR/mac-ui-demo"
    # 空状态截图必须**真的是空的**：每次都从零开始
    rm -rf "$empty_data" "$demo_data"

    shoot() {  # $1=文件名 $2=页面 $3=数据目录 $4=种子(0/1)
        local name="$1" page="$2" data="$3" seed="$4"
        local log="$TMPDIR/mac-ui-$name.log"
        : > "$log"
        SEKB_UI_PROBE=1 \
        SEKB_INITIAL_SCREEN="$page" \
        SEKB_DEMO_SEED="$seed" \
        SEKB_DATA_DIR="$data" \
        SEKB_UI_PROBE_OUT="$out_dir/$name.png" \
        SEKB_UI_PROBE_WIDTH="${SEKB_UI_PROBE_WIDTH:-460}" \
        SEKB_UI_PROBE_HEIGHT="${SEKB_UI_PROBE_HEIGHT:-860}" \
            "$APP_BUNDLE/Contents/MacOS/$APP_NAME" >"$log" 2>&1 || true
        local line; line="$(grep -m1 'SEKB_MAC_SELFTEST_UI_PROBE\|SEKB_IOS_SELFTEST_UI_PROBE' "$log" || true)"
        if [ -z "$line" ]; then
            echo "❌ ${name}：探针没有输出（日志见 ${log}）" >&2
            tail -5 "$log" >&2
            return 1
        fi
        printf '%s\n' "$line"
        printf '%s\n' "$line" | grep -q 'ok=true' || return 1
    }

    # 1) 空状态：**关键回归点**——空状态不能把底部输入栏挤出屏幕
    #    （Android 真机上正是这个 bug：整屏只有空状态、用户没法输入）
    shoot "01-chat-empty" chat      "$empty_data" 0 || return 1
    # 2) 有数据的四页
    shoot "02-chat"       chat      "$demo_data"  1 || return 1
    shoot "03-history"    history   "$demo_data"  1 || return 1
    shoot "04-documents"  documents "$demo_data"  1 || return 1
    shoot "05-settings"   settings  "$demo_data"  1 || return 1
    echo "✅ 截图目录：$out_dir"
}

case "${1:-run}" in
    build) build_app ;;
    run)   run_app ;;
    ui)    ui_app ;;
    *) echo "用法：bash scripts/mac_app.sh [build|run|ui]" >&2; exit 2 ;;
esac
