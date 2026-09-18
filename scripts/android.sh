#!/usr/bin/env bash
# ============================================================
# Android 构建入口（**所有构建状态都放在仓库内**）
# ============================================================
# 为什么需要这一层：Gradle 默认把缓存写到 ~/.gradle（本机 1.4G）。在受限沙箱
# （DSH 的 workspace-write 等）里，写仓库外的文件会被拒绝，于是"每次构建都要授权"。
# 本脚本把 GRADLE_USER_HOME 指到仓库内的 .tooling/gradle-home，构建就只写仓库内。
#
# 用法：
#   bash scripts/android.sh test                 # 单元测试（无需模拟器）
#   bash scripts/android.sh assemble             # 打 debug APK
#   bash scripts/android.sh install              # 装到已连接的模拟器/真机
#   bash scripts/android.sh test assemble        # 可串联
#   SEKB_SEKB_URL=http://10.0.2.2:8010 bash scripts/android.sh assemble
#                                                # 云端地址在构建期覆盖（联调用本地后端）
#
# 依赖：JDK 17+（优先用 Android Studio 自带 JBR）、Android SDK。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT/apps/android"

# 1) JDK：优先 Android Studio 自带的 JBR（本机没有独立 java 时这是唯一可用的）
if [ -z "${JAVA_HOME:-}" ]; then
    for cand in "/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
                "/Applications/DevEco-Studio.app/Contents/jbr/Contents/Home"; do
        [ -x "$cand/bin/java" ] && export JAVA_HOME="$cand" && break
    done
fi
[ -n "${JAVA_HOME:-}" ] || { echo "找不到 JDK：请设置 JAVA_HOME（需要 17+）" >&2; exit 1; }

# 2) 构建状态全部落在仓库内（关键：避免工作区外写权限）
#    - GRADLE_USER_HOME：依赖缓存 / wrapper / daemon
#    - ANDROID_USER_HOME：AGP 要在这里生成 **debug.keystore**（否则 assembleDebug 会因为
#      写不了 ~/.android 直接失败——实测报 "Unable to create debug keystore ... not writable"）
#    - ANDROID_AVD_HOME：模拟器 AVD（可选搬进来；不搬则启动模拟器仍需授权）
# 已显式设置时不覆盖（CI 上会指向被 actions/cache 缓存的那个目录）
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$ROOT/.tooling/gradle-home}"
export ANDROID_USER_HOME="$ROOT/.tooling/android-home"
# ⚠️ 不要再设 ANDROID_PREFS_ROOT（哪怕设成同一个路径）：AGP 9 会因此直接崩在
#    "AndroidLocationsBuildService ... AndroidDirectoryCreator" 上（实测）。
export ANDROID_AVD_HOME="$ROOT/.tooling/android-avd"
mkdir -p "$GRADLE_USER_HOME" "$ANDROID_USER_HOME" "$ANDROID_AVD_HOME"

# 3) Android SDK：优先 apps/android/local.properties 里的 sdk.dir
SDK="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"
if [ -z "$SDK" ] && [ -f "$APP_DIR/local.properties" ]; then
    SDK="$(sed -n 's/^sdk\.dir=//p' "$APP_DIR/local.properties" | head -1)"
fi
[ -z "$SDK" ] && [ -d "$HOME/Library/Android/sdk" ] && SDK="$HOME/Library/Android/sdk"
export ANDROID_SDK_ROOT="$SDK" ANDROID_HOME="$SDK"

# 4) iOS 相关的未来用途：Xcode 的 developer 目录（本机 xcode-select 指向 CommandLineTools）
#    iOS 构建（apps/ios）会用 DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
[ -d /Applications/Xcode.app/Contents/Developer ] && \
    export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"

cd "$APP_DIR"

# compileSdk 次版本：本机 1（android-36.1），只有 android-36 的环境传 0
MINOR_FLAG="-PsekbCompileSdkMinor=${SEKB_COMPILE_SDK_MINOR:-1}"

STATUS=0
for task in "$@"; do
    case "$task" in
        test)      ./gradlew :app:testDebugUnitTest --console=plain $MINOR_FLAG ;;
        assemble)  ./gradlew :app:assembleDebug --console=plain $MINOR_FLAG \
                       ${SEKB_SEKB_URL:+-PsekbBaseUrl="$SEKB_SEKB_URL"} ;;
        install)   ./gradlew :app:installDebug --console=plain $MINOR_FLAG ;;
        *)         ./gradlew "$task" --console=plain $MINOR_FLAG ;;
    esac || STATUS=$?
done
exit $STATUS
