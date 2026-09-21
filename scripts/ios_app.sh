#!/usr/bin/env bash
# ============================================================
# iOS App 构建/安装/启动（M5 第二步）
# ============================================================
# 为什么不用 .xcodeproj：手写 pbxproj 又长又脆，而这一步要证明的是
# 「SwiftUI + SharedCore.framework 能在模拟器上跑起来」——用 swiftc 直编 + 手工 .app 包
# 完全够用，而且全部可脚本化、可复现。等要接入真实签名/发布时再上 Xcode 工程。
#
# 用法：
#   bash scripts/ios_app.sh build     # 产出 .tooling/tmp/Sekb.app
#   bash scripts/ios_app.sh run       # build + 装到已启动的模拟器 + 启动（打印自检输出）
#   bash scripts/ios_app.sh boot      # 启动一个 iPhone 模拟器（默认 iPhone 17 Pro / iOS 26.4）
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
export TMPDIR="$ROOT/.tooling/tmp"
export CLANG_MODULE_CACHE_PATH="$TMPDIR/modulecache"
mkdir -p "$TMPDIR/modulecache"

APP_NAME="Sekb"
BUNDLE_ID="com.sekb.ondevice.ios"
BUILD_DIR="$TMPDIR/ios-app"
APP_BUNDLE="$BUILD_DIR/$APP_NAME.app"
# 模拟器架构：Apple Silicon 上的 iOS 模拟器就是 arm64
TARGET="arm64-apple-ios17.0-simulator"
FW_IOS="$ROOT/apps/shared/build/bin/iosSimulatorArm64/debugFramework"

[ -n "${JAVA_HOME:-}" ] || for cand in "/Applications/Android Studio.app/Contents/jbr/Contents/Home"; do
    [ -x "$cand/bin/java" ] && export JAVA_HOME="$cand" && break
done
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$ROOT/.tooling/gradle-home}"
export ANDROID_USER_HOME="$ROOT/.tooling/android-home"
export KONAN_DATA_DIR="${KONAN_DATA_DIR:-$ROOT/.tooling/konan}"

ACTION="${1:-run}"

boot_sim() {
    local dev="${1:-}"
    if [ -z "$dev" ]; then
        dev="$(xcrun simctl list devices available | grep -m1 "iPhone 17 Pro (" | sed -E 's/.*\(([0-9A-F-]{36})\).*/\1/')"
    fi
    [ -n "$dev" ] || { echo "找不到可用的 iPhone 模拟器" >&2; exit 1; }
    xcrun simctl boot "$dev" 2>/dev/null || true
    xcrun simctl bootstatus "$dev" -b >/dev/null 2>&1 || true
    echo "$dev"
}

build_app() {
    # 1) 先产出 iOS 模拟器的 framework（native target 默认关闭 → 显式打开）
    ( cd "$ROOT/apps/android" && ./gradlew --no-daemon -PsekbNativeTargets=true \
        :shared:linkDebugFrameworkIosSimulatorArm64 --console=plain -q ) || return 1
    [ -d "$FW_IOS/SharedCore.framework" ] || { echo "缺少 framework：$FW_IOS" >&2; return 1; }

    # 2) 编译 SwiftUI 源码（静态 framework：直接 -F 指目录 + -framework）
    rm -rf "$APP_BUNDLE"; mkdir -p "$APP_BUNDLE"
    # `-parse-as-library` 是必须的：单文件 swiftc 会按"脚本模式"编译，
    # 于是 `@main` 报 "'main' attribute cannot be used in a module that contains top-level code"（实测）
    xcrun swiftc -O -parse-as-library -sdk "$(xcrun --sdk iphonesimulator --show-sdk-path)" \
        -target "$TARGET" -module-cache-path "$TMPDIR/modulecache" \
        -F "$FW_IOS" -framework SharedCore \
        -o "$APP_BUNDLE/$APP_NAME" \
        $ROOT/apps/ios/App/*.swift || return 1
    cp "$ROOT/apps/ios/App/Info.plist" "$APP_BUNDLE/Info.plist"
    # 自检用的样本文档（与 Android 侧共用同一批 test resources，避免"两端各造一份样本"）
    for f in "$ROOT"/apps/android/app/src/test/resources/*.pdf; do
        [ -f "$f" ] && cp "$f" "$APP_BUNDLE/"
    done
    echo "✅ 已产出 $APP_BUNDLE"
}

case "$ACTION" in
    boot) boot_sim ;;
    build) build_app ;;
    run)
        build_app || exit 1
        DEV="$(boot_sim)" || exit 1
        echo "模拟器：$DEV"
        # 必须先 terminate：`simctl launch` 对**已在运行**的 App 只是切到前台，
        # `onAppear` 不会再触发 → 无人值守时抓不到自检输出（实测踩过）。
        xcrun simctl terminate "$DEV" "$BUNDLE_ID" >/dev/null 2>&1 || true
        xcrun simctl uninstall "$DEV" "$BUNDLE_ID" >/dev/null 2>&1 || true
        xcrun simctl install "$DEV" "$APP_BUNDLE" || exit 1
        # 不用 `--console-pty`：受限环境里分配 pty 会失败（实测 "Unable to open pty: Error 1"）。
        # 改为正常启动 + 从模拟器日志里抓应用输出（print → os_log），这在无人值守下更稳。
        xcrun simctl launch "$DEV" "$BUNDLE_ID" >/dev/null || exit 1
        # 自检里含真实网络调用（宿主 Ollama 冷加载可能十几秒）→ 等久一点再抓日志
        sleep 45
        xcrun simctl spawn "$DEV" log show --last 90s --style compact \
            --predicate 'process == "Sekb"' 2>/dev/null | grep -E "SEKB_IOS_SELFTEST" || {
                echo "（未从日志抓到自检输出；把模拟器界面切到前台可看到 List 中的结果）"
                exit 1
            }
        ;;
    *) echo "用法：bash scripts/ios_app.sh [boot|build|run]" >&2; exit 2 ;;
esac
