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

# 真机安装要用 adb（脚本其余部分只走 gradle，不需要它）
# ⚠️ 这里**不能**用 `find ... -name adb`：`.tooling/android-sdk/platform-tools` 是软链接，
# 而 find 默认不跟随符号链接 → 结果是空字符串，接着报
# `line 166: : command not found`（实测踩到，且错误信息完全指不到真正原因）。
# 直接给路径、再兜底到本机 Android SDK。
if [ -z "${ADB_BIN:-}" ]; then
    for _cand in \
        "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.tooling/android-sdk/platform-tools/adb" \
        "$HOME/Library/Android/sdk/platform-tools/adb"; do
        if [ -x "$_cand" ]; then ADB_BIN="$_cand"; break; fi
    done
fi
if [ -z "${ADB_BIN:-}" ]; then
    echo "❌ 找不到 adb：请设 ADB_BIN，或跑一次 scripts/android.sh test 生成 .tooling/android-sdk" >&2
fi

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
# KOTLIN/NATIVE（KMP 的 iOS/Mac target）默认把工具链下载到 ~/.konan —— 又是"工作区外写"，
# 在受限沙箱里直接失败（实测：FileNotFoundException .../kotlin-native-prebuilt-.../.lock）。
# 与 GRADLE_USER_HOME/ANDROID_USER_HOME 同一处理：收进仓库内 .tooling/。
export KONAN_DATA_DIR="${KONAN_DATA_DIR:-$ROOT/.tooling/konan}"
# 端侧（宿主 Ollama）地址：模拟器上用 adb reverse 时传 http://127.0.0.1:11434/v1
# （见 docs/... 与 emulator.sh 里的说明；不传则用 10.0.2.2）
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

# 需要设备的子命令必须先确认设备在线——否则 adb 的失败会被后面的 echo 掩盖成"成功"
require_device() {
    if ! "$1" get-state >/dev/null 2>&1; then
        echo "❌ 没有已连接的设备/模拟器；先跑 bash scripts/emulator.sh --background" >&2
        exit 1
    fi
}

# ============================================================
# 真机安装：自动越过华为的安装确认框
# ============================================================
# 背景（实测，2026-09-24 华为 Mate 40 Pro / HarmonyOS 4.2）：
# `adb install` 会在设备侧弹出两层确认——先是系统的「风险提示 → 继续安装」，
# 点掉后交给「华为应用市场」的风险检测再确认一次。**不点就一直挂着**
# （表现为 adb install 无限等待，而 macOS 没有 timeout 兜底）。
#
# 为什么不直接 `input tap x y`：按钮位置随机型/分辨率/系统版本变，硬编码坐标等于
# "只在我这台能用"。这里用 `uiautomator dump` **按按钮文字反查坐标**。
CONFIRM_TEXTS=("继续安装" "仍要安装" "允许" "确定" "安装")

# 华为的"应用市场风险检测"页里，`继续安装` **前面还有一个确认框**
# （"已了解此应用未经检测…"），不勾选则点 `继续安装` 无效——实测表现为
# 反复点击但对话框一直在。所以这里一并处理：先勾确认框，再点继续。
# 输出两行 "ack_x ack_y" 与 "ok_x ok_y"（缺失行留空）。
_find_install_targets() {
    "$ADB_BIN" shell uiautomator dump /sdcard/sekb_ui.xml >/dev/null 2>&1 || return 1
    "$ADB_BIN" pull /sdcard/sekb_ui.xml /tmp/sekb_ui.xml >/dev/null 2>&1 || return 1
    python3 - /tmp/sekb_ui.xml "${CONFIRM_TEXTS[@]}" <<'PYEOF'
import re, sys, xml.etree.ElementTree as ET
path, *texts = sys.argv[1:]
try:
    root = ET.parse(path).getroot()
except Exception:
    print("")
    print("")
    sys.exit(0)

def center(node):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds") or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return f"{(x1 + x2) // 2} {(y1 + y2) // 2}"

ack = ok = ""
for node in root.iter("node"):
    if node.get("clickable") != "true":
        continue
    label = (node.get("text") or "").strip()
    # 确认勾选框（措辞会变，所以用前缀匹配）
    if not ack and (label.startswith("已了解") or label.startswith("我已阅读")):
        ack = center(node) or ""
    # 确认按钮：只认 clickable 的，避免点到标题/商品名里的"安装"（实测页面里有多个）
    if not ok and label in texts:
        ok = center(node) or ""
print(ack)
print(ok)
PYEOF
}

# 后台守着安装确认框，直到超时（安装本身由前台 adb install 负责）
_auto_confirm_loop() {
    local deadline=$(( SECONDS + ${1:-300} ))
    while [ "$SECONDS" -lt "$deadline" ]; do
        local act xy
        act="$("$ADB_BIN" shell dumpsys activity activities 2>/dev/null | grep -m1 mResumedActivity || true)"
        case "$act" in
            *packageinstaller*|*appgallery*|*InstallDist*|*riskcheck*)
                targets="$(_find_install_targets || true)"
                # 用 bash 内建 read 取两行，**不要**用 `| sed -n 1p`：
                # 后者会在 sed 提前退出时让 python 写已关闭的管道，打出
                # `BrokenPipeError` 一堆噪音（实测），把真正的安装结果淹掉
                ack=""; ok=""
                { IFS= read -r ack || true; IFS= read -r ok || true; } <<< "$targets"
                if [ -n "$ack" ]; then
                    # shellcheck disable=SC2086
                    "$ADB_BIN" shell input tap $ack >/dev/null 2>&1
                    sleep 1
                fi
                if [ -n "$ok" ]; then
                    echo "   ↳ 越过安装确认框（勾选 ${ack:-无} / 继续 ${ok}）"
                    # shellcheck disable=SC2086
                    "$ADB_BIN" shell input tap $ok >/dev/null 2>&1
                fi
                ;;
        esac
        sleep 2
    done
}

# ⚠️ 构建期 `-P` 属性必须在 **assemble 与 install 两条路径上都传**。
# 曾经的 bug：只有 `assemble` 传了 `-PsekbEdgeUrl`，而 `install` 没传 —— 于是
#   `SEKB_EDGE_URL=http://127.0.0.1:11434/v1 bash scripts/android.sh install`
# 会被**静默忽略**（installDebug 复用上一次的 BuildConfig，仍是 `10.0.2.2`），
# 真机上表现为"端侧全部连不上"，而错误信息里根本看不到"你的环境变量没生效"。
# 这是实测踩到的（2026-09-24 真机第一次跑自检，5 个 edge_* 全 FAIL）。
PROP_FLAGS=()
[ -n "${SEKB_SEKB_URL:-}" ] && PROP_FLAGS+=("-PsekbBaseUrl=$SEKB_SEKB_URL")
[ -n "${SEKB_EDGE_URL:-}" ] && PROP_FLAGS+=("-PsekbEdgeUrl=$SEKB_EDGE_URL")
if [ ${#PROP_FLAGS[@]} -gt 0 ]; then
    echo "（构建期属性：${PROP_FLAGS[*]}）"
fi

STATUS=0
# ⚠️ 用 `while`+`shift` 而不是 `for task in "$@"`：有些任务要带**参数**
# （如 `push-policy <file>`）。`for` 会把参数也当成"任务"再跑一遍——
# 表现为 `./gradlew <文件路径>` → BUILD FAILED（实测踩到）。
while [ $# -gt 0 ]; do
    task="$1"; shift
    case "$task" in
        test)      ./gradlew :app:testDebugUnitTest --console=plain $MINOR_FLAG ;;
        assemble)  ./gradlew :app:assembleDebug --console=plain $MINOR_FLAG \
                       ${PROP_FLAGS[@]+"${PROP_FLAGS[@]}"} ;;
        # install 与 assemble 传**同一组**属性：只传一边是上面那个 bug 的根源
        install)   ./gradlew :app:installDebug --console=plain $MINOR_FLAG \
                       ${PROP_FLAGS[@]+"${PROP_FLAGS[@]}"} ;;
        install-real)
            # 面向"装一次要过华为确认框"的真机：gradle 只出 APK，安装交给 adb + 自动确认
            ./gradlew :app:assembleDebug --console=plain $MINOR_FLAG \
                ${PROP_FLAGS[@]+"${PROP_FLAGS[@]}"} || exit 1
            APK="$(find app/build/outputs/apk/debug -name '*-arm64-v8a-debug.apk' | head -1)"
            [ -n "$APK" ] || { echo "❌ 找不到 APK"; exit 1; }
            echo "安装 ${APK}（设备侧确认框会被自动点击）"
            _auto_confirm_loop 300 &
            watchdog=$!
            "$ADB_BIN" install -r "$APK"
            rc=$?
            kill "$watchdog" 2>/dev/null || true
            [ $rc -eq 0 ] || exit $rc ;;
        push-policy)
            # 自检用的**已签名**策略包（离线验证「应用→重建→阈值生效→回滚」）。
            # 生成方式见 README/VERIFICATION：用服务端 edge_policy 的 canonical+HMAC 签一份，
            # 空间戳必须与端侧 config 一致（否则会（正确地）被 policy_space_mismatch 拒掉）。
            PKG="com.sekb.ondevice"
            DEST="/data/data/${PKG}/files/sekb-e2e-policy.json"
            ADB="${ADB_BIN}"
            require_device "$ADB"
            POLICY_FILE="${1:-}"
            [ -n "$POLICY_FILE" ] || { echo "用法：bash scripts/android.sh push-policy <policy.json>" >&2; exit 2; }
            shift   # 消费掉文件参数，别让它被当成下一个任务
            # ⚠️ 脚本前面已经 `cd "$APP_DIR"`（要跑 ./gradlew），所以**相对路径必须按仓库根解析**，
            # 否则在仓库根执行 `push-policy apps/...` 会被解析成 apps/android/apps/...（实测踩到）。
            case "$POLICY_FILE" in
                /*) : ;;
                *) POLICY_FILE="$ROOT/$POLICY_FILE" ;;
            esac
            [ -f "$POLICY_FILE" ] || { echo "找不到策略文件：$POLICY_FILE" >&2; exit 2; }
            cat "$POLICY_FILE" | "$ADB" shell "run-as ${PKG} sh -c 'cat > ${DEST}'"
            echo "✅ 策略已写入 $DEST"
            "$ADB" shell "run-as ${PKG} ls -la files/sekb-e2e-policy.json" ;;
        push-sample)
            # 端侧 RAG 导入 E2E 用的样本文档：写进 App 内部 filesDir（App 自己能读，无需存储权限）
            PKG="com.sekb.ondevice"
            DEST="/data/data/${PKG}/files/sekb-sample.md"
            ADB="${ANDROID_SDK_ROOT}/platform-tools/adb"
            require_device "$ADB"
            SAMPLE="$(mktemp)"
            cat > "$SAMPLE" <<'SAMPLE_EOF'
# 端侧索引运维手册

本机索引存放在 SQLite 数据库中，每段切片以 float32 小端 BLOB 存储，512 维占 2KB。
检索默认使用暴力余弦，语料上万段之后再考虑换成近似最近邻索引。

换嵌入模型后必须重新计算已有切片的向量：混用两套向量空间会返回看似相关、实则错误的结果。
重新计算是原地进行的，切片文本不会丢失。

删除文档时只删除该文档的切片，其他文档不受影响。
误召回率高时应提高相似度阈值；召回率偏低时则相反。
SAMPLE_EOF
            "$ADB" shell "run-as ${PKG} sh -c 'cat > ${DEST}'" < "$SAMPLE"
            rm -f "$SAMPLE"
            # PDF 样本（用 JVM 单测的同一个夹具，内容是英文——便于用英文提问做检索断言）
            PDF_DEST="/data/data/${PKG}/files/sekb-sample.pdf"
            PDF_SRC="$ROOT/apps/android/app/src/test/resources/sample-text.pdf"
            [ -f "$PDF_SRC" ] && "$ADB" shell "run-as ${PKG} sh -c 'cat > ${PDF_DEST}'" < "$PDF_SRC"
            echo "✅ 样本已写入（md + pdf）"
            "$ADB" shell run-as "$PKG" ls -l "$DEST" "$PDF_DEST"
            ;;
        push-model)
            # 把 ONNX 模型推进 App 的**内部**私有目录。
            #
            # ⚠️ 不能用 `adb push` 直接推外部私有目录：那样文件属主是 shell、目录权限
            # `drwxrws--- shell:ext_data_rw`，App 不在该组里 → 读不到，表现为
            # "模型明明在、App 却说没找到"（踩过）。所以走 run-as + stdin 写成 App 自己的文件。
            # 需要 debuggable 构建（debug 版即可）与已连接的设备/模拟器。
            PKG="com.sekb.ondevice"
            # 必须用**绝对路径**：run-as 的工作目录不保证是 App 数据目录（实测不是），
            # 用相对路径会报 "can't create files/…: No such file or directory"。
            DATA="/data/data/${PKG}/files/models/bge-small-zh-v1.5"
            SRC="$ROOT/.tooling/models/bge-small-zh-v1.5"
            [ -f "$SRC/model.onnx" ] || { echo "缺模型：先跑 bash scripts/fetch_embedding_model.sh" >&2; exit 1; }
            ADB="${ANDROID_SDK_ROOT}/platform-tools/adb"
            require_device "$ADB"
            "$ADB" shell run-as "$PKG" mkdir -p "$DATA"
            for f in model.onnx vocab.txt; do
                # 整条远程命令必须是**一个**字符串：否则 adb shell 会把参数摊平，
                # `>` 由设备上的 shell 用户解释 → "Permission denied"（踩过）。
                "$ADB" shell "run-as ${PKG} sh -c 'cat > ${DATA}/${f}'" < "$SRC/$f"
            done
            echo "✅ fp32 模型已写入（${DATA}，94.9MB）"
            # int8 量化模型：存在就一起推（App 优先用它——体积 1/4、排序与 fp32 一致，
            # 但分数分布上移，阈值按 0.5 标定；空间戳与 fp32 不同，切换时自动原地重算索引）
            SRC8="$ROOT/.tooling/models/bge-small-zh-v1.5-int8"
            DATA8="/data/data/${PKG}/files/models/bge-small-zh-v1.5-int8"
            if [ -f "$SRC8/model.onnx" ]; then
                "$ADB" shell run-as "$PKG" mkdir -p "$DATA8"
                for f in model.onnx vocab.txt; do
                    "$ADB" shell "run-as ${PKG} sh -c 'cat > ${DATA8}/${f}'" < "$SRC8/$f"
                done
                echo "✅ int8 模型已写入（${DATA8}，23.9MB）"
            else
                echo "（未找到 int8 模型：先跑 bash scripts/fetch_embedding_model.sh --int8）"
            fi
            "$ADB" shell run-as "$PKG" ls -l "$DATA" "$DATA8" 2>/dev/null || "$ADB" shell run-as "$PKG" ls -l "$DATA"
            ;;
        *)         ./gradlew "$task" --console=plain $MINOR_FLAG ;;
    esac || STATUS=$?
done
exit $STATUS
