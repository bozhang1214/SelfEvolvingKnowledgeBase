#!/usr/bin/env bash
# ============================================================
# 启动 Android 模拟器（**所有状态都在仓库内**）
# ============================================================
# 为什么单独一个脚本：模拟器默认会往仓库外写三处，缺一处就直接崩或连不上，
# 三个坑都是实测踩出来的（见下）。跑通之后，构建 + 装包 + 自检全程不需要额外授权。
#
# 用法：
#   bash scripts/emulator.sh                 # 前台启动（Ctrl-C 关闭）
#   bash scripts/emulator.sh --background     # 后台启动后返回
#   AVD=别的名字 bash scripts/emulator.sh
#
# 依赖：Android SDK（emulator + platform-tools）、仓库内 .tooling/android-avd 有 AVD。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AVD="${AVD:-Medium_Phone_API_36.1}"

SDK="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"
[ -z "$SDK" ] && [ -f "$ROOT/apps/android/local.properties" ] && \
    SDK="$(sed -n 's/^sdk\.dir=//p' "$ROOT/apps/android/local.properties" | head -1)"
[ -z "$SDK" ] && SDK="$HOME/Library/Android/sdk"
[ -d "$SDK/emulator" ] || { echo "找不到 Android SDK：$SDK" >&2; exit 1; }

# ---- 坑 1：模拟器把 jwk 写到 $HOME/Library/Caches/TemporaryItems ----
# 写不进去会 **直接 Abort trap: 6**（不是降级），所以必须把 HOME 指到仓库内。
export HOME="$ROOT/.tooling/home"
# ---- 坑 2：临时目录 ----
export TMPDIR="$ROOT/.tooling/tmp"
# ---- 坑 3：AVD 与 Android 用户目录 ----
export ANDROID_AVD_HOME="$ROOT/.tooling/android-avd"
export ANDROID_USER_HOME="$ROOT/.tooling/android-home"
mkdir -p "$HOME" "$TMPDIR" "$ANDROID_AVD_HOME" "$ANDROID_USER_HOME"

ADB="$SDK/platform-tools/adb"
KEY="$HOME/.android/adbkey"

# ---- 坑 4：adb 密钥必须与 AVD 里授权的那把一致 ----
# AVD 的 userdata 里存着"已授权的 adb 公钥"，那是**创建 AVD 时**那把（在 ~/.android）。
# HOME 换到仓库内之后，adb 会另生成一把 → 设备状态变成 `unauthorized`（装不了包）。
if [ ! -f "$KEY" ]; then
    echo "⚠️  仓库内还没有 adb 密钥。先执行一次（从旧 HOME 复制，因为 AVD 里授权的就是它）：" >&2
    echo "    cp -a ~/.android/adbkey ~/.android/adbkey.pub $HOME/.android/" >&2
    echo "    否则 adb 会新生成一把 → 设备一直显示 unauthorized（装不了包）。" >&2
fi

# ---- 坑 5：被杀掉的模拟器会留下锁文件 → 下次报 "Running multiple emulators with the same AVD"
find "$ANDROID_AVD_HOME" -name "*.lock" -delete 2>/dev/null || true

"$ADB" start-server >/dev/null 2>&1 || true

ARGS=(-avd "$AVD" -memory 4096 -no-snapshot-save -no-boot-anim)
if [ "${1:-}" = "--background" ]; then
    nohup "$SDK/emulator/emulator" "${ARGS[@]}" > "$ROOT/.tooling/emulator.log" 2>&1 &
    echo "模拟器后台启动中（日志 .tooling/emulator.log），等待 adb 与系统启动完成 ..."
    for i in $(seq 1 80); do
        sleep 3
        # 两个条件都要满足：adb 认到设备，**并且** Android 系统起完。
        # 只等前者会得到一个"能 adb、但 PackageManager 还没起来"的假就绪——
        # 那时 install 会报 "Error: device is still booting"（实测踩到）。
        if "$ADB" devices | grep -q "device$" && \
           [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; then
        # ---- 端侧端点直通（可选但推荐）------------------------------------------------
# 背景（2026-09-21 实测）：宿主 Ollama 默认只绑 127.0.0.1，而**本机模拟器访问不到 10.0.2.2:11434**
# （自检第 1 项 edge_reachable=false）。与其把 Ollama 绑到 0.0.0.0（会暴露到局域网），
# 不如用 `adb reverse` 把模拟器的 11434 直通到宿主 11434——安全且不需要改 Ollama 配置。
# 用它的构建方式：./gradlew :app:assembleDebug -PsekbEdgeUrl=http://127.0.0.1:11434/v1
if [ -x "$ADB" ]; then
    if "$ADB" reverse tcp:11434 tcp:11434 >/dev/null 2>&1; then
        echo "   ↳ 已建立 adb reverse tcp:11434（端侧端点可用 127.0.0.1:11434 直通宿主）"
    else
        echo "   ⚠️  adb reverse 失败（不影响功能：仍可用 10.0.2.2，前提是 Ollama 绑到 0.0.0.0）"
    fi
fi

    echo "✅ 就绪：$("$ADB" devices | grep 'device$' | head -1)（boot_completed=1）"
            echo "   后续 adb 命令请带上同一个 HOME：HOME=$HOME"
            exit 0
        fi
    done
    echo "❌ 240s 内未就绪，看 .tooling/emulator.log（模拟器偶发启动即崩，重跑一次通常即可）" >&2
    exit 1
fi

exec "$SDK/emulator/emulator" "${ARGS[@]}"
