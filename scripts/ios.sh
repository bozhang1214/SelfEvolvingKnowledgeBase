#!/usr/bin/env bash
# ============================================================
# iOS / Mac 端侧构建入口（M5 起）
# ============================================================
# 目前提供两个动作：
#   smoke    产出 macosArm64 的 SharedCore.framework，并编译运行 Swift 冒烟（Host 上真跑共享逻辑）
#   framework 只产出 iOS 模拟器 + macOS 的 framework
#
# 三个必须知道的环境事实（都踩过）：
#   1. **DEVELOPER_DIR 必须指向 Xcode**：本机 `xcode-select` 指向 CommandLineTools，
#      不设它会报 "An error occurred during an xcrun execution / xcrun xcodebuild -version"
#      （表面像 klib 缓存错误，实则是 Xcode 不可用）；
#   2. KOTLIN/NATIVE 的工具链与缓存全部落在仓库内 `.tooling/konan`（工作区外会被沙箱拒）；
#   3. native target 默认关闭，这里统一带上 `-PsekbNativeTargets=true`。
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT/apps/android"

[ -n "${JAVA_HOME:-}" ] || for cand in "/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
                                        "/Applications/DevEco-Studio.app/Contents/jbr/Contents/Home"; do
    [ -x "$cand/bin/java" ] && export JAVA_HOME="$cand" && break
done
[ -n "${JAVA_HOME:-}" ] || { echo "找不到 JDK（需要 17+）" >&2; exit 1; }
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$ROOT/.tooling/gradle-home}"
export ANDROID_USER_HOME="$ROOT/.tooling/android-home"
export KONAN_DATA_DIR="${KONAN_DATA_DIR:-$ROOT/.tooling/konan}"
export TMPDIR="$ROOT/.tooling/tmp"
mkdir -p "$TMPDIR"

ACTION="${1:-smoke}"

gradle_frameworks() {
    ( cd "$APP_DIR" && ./gradlew --no-daemon -PsekbNativeTargets=true \
        :shared:linkDebugFrameworkIosSimulatorArm64 :shared:linkDebugFrameworkMacosArm64 --console=plain )
}

case "$ACTION" in
    framework)
        gradle_frameworks || exit 1
        echo "✅ framework 就位："
        ls -d "$ROOT"/apps/shared/build/bin/*/debugFramework/SharedCore.framework
        ;;
    smoke)
        gradle_frameworks || exit 1
        FW="$ROOT/apps/shared/build/bin/macosArm64/debugFramework"
        [ -d "$FW/SharedCore.framework" ] || { echo "缺少 macOS framework：$FW" >&2; exit 1; }
        BIN="$ROOT/.tooling/tmp/sekb-ios-smoke"
        # 静态 framework：直接 -F 指到目录、-framework SharedCore 即可
        # Swift/clang 也会往工作区外写 module cache（沙箱拒绝：Operation not permitted）→ 一并收进仓库
        MC="$TMPDIR/modulecache"; mkdir -p "$MC"
        export CLANG_MODULE_CACHE_PATH="$MC"
        xcrun swiftc -O -module-cache-path "$MC" -o "$BIN" "$ROOT/apps/ios/smoke/Smoke.swift" \
            -F "$FW" -framework SharedCore \
            -Xlinker -rpath -Xlinker "$FW" || exit 1
        "$BIN"
        ;;
    *)
        echo "用法：bash scripts/ios.sh [smoke|framework]" >&2; exit 2 ;;
esac
