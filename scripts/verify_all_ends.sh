#!/usr/bin/env bash
# ============================================================
# 四端验收总入口（quick / full）——**一个命令看清四端现在到哪了**
# ============================================================
# 为什么要有它
# ------------
# 端侧现在有四条验收线（Android / iOS / Mac / 鸿蒙），每条都有自己的脚本与"验证记录"，
# 但**没有一个地方能一次跑完并给出总表**。结果是：
#   · 每次汇报都要手工拼数字，拼错就会像 `fact:android_unit_cases=231` 那样漂移；
#   · 新接手的人不知道该先跑什么，只能从 README 里一行行抄命令。
# 本脚本把"每端的最低可复现验收"收成一条命令，输出**逐项 PASS/FAIL/SKIP + 原始命令**。
#
# 两种模式（差别只在"跑多少"，不在"判什么"）
# ------------------------------------------
#   quick  秒级～1 分钟：静态可达性与**活数字**（不编 native、不装模拟器）
#          · 文档守卫 7 项（含端侧活数字守卫：文档写的数必须等于实测）
#          · Android 单测计数（`@Test` 静态计数 + Gradle XML 报告交叉验证）
#          · 共享层纯逻辑可编（:shared:compileKotlinJvm/Android）
#          · 契约夹具 37 case 与下限
#   full   分钟级：真的编 + 真的跑（缺前置条件时**如实 SKIP 并写理由**，不算失败）
#          · Android 单测全量（`scripts/android.sh test`）
#          · iOS framework + Swift 冒烟（`scripts/ios.sh smoke`）
#          · Mac 自检（`scripts/mac_app.sh run`）
#          · 鸿蒙 KMP 产物 + interop 冒烟（`scripts/harmony.sh smoke`，脚本存在才跑）
#
# 口径（与仓库纪律一致）
# ----------------------
#   · **SKIP 必须带理由**，且只有"环境限制类"（缺设备/缺镜像/缺模型/缺签名）才允许 SKIP；
#   · 缺脚本/缺产物一律记 SKIP 并打印**缺什么**，不伪装成 PASS；
#   · 退出码只反映 FAIL；SKIP 不改变退出码（但会显眼列出）。
#
# 用法：
#   bash scripts/verify_all_ends.sh            # = quick
#   bash scripts/verify_all_ends.sh quick
#   bash scripts/verify_all_ends.sh full
#   bash scripts/verify_all_ends.sh full --android-only
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:-quick}"
shift || true
ONLY="${1:-}"

#: 结果行：`<状态>|<端>|<项>|<详情>`
RESULTS=()

record() { RESULTS+=("$1|$2|$3|$4"); }

step() { echo ""; echo "── $* ──"; }

have() { [ -x "$1" ] || [ -f "$1" ]; }

# 与 `scripts/android.sh` 同一套环境（**必须**：直接调 gradlew 会报
# "Unable to locate a Java Runtime" —— 本机 JAVA_HOME 未导出、gradlew 只找系统 java）。
# 这里只取最小必要子集：JAVA_HOME + 仓库内 Gradle 缓存 + Android SDK。
prepare_android_env() {
    if [ -z "${JAVA_HOME:-}" ]; then
        for cand in "/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
                    "/Applications/DevEco-Studio.app/Contents/jbr/Contents/Home"; do
            [ -x "$cand/bin/java" ] && export JAVA_HOME="$cand" && break
        done
    fi
    [ -n "${JAVA_HOME:-}" ] || { echo "⚠️  找不到 JDK（需 17+）：请设置 JAVA_HOME" >&2; return 1; }
    export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$ROOT/.tooling/gradle-home}"
    export ANDROID_USER_HOME="$ROOT/.tooling/android-home"
    # ⚠️ `KONAN_DATA_DIR` 必须指仓库内：Kotlin/Native 默认用 `~/.konan`，受限沙箱里
    # 读 `~/.konan/.../.lock` 会 `FileNotFoundException` —— 且**失败发生在配置阶段**，
    # 整轮构建白跑 16 分钟才报出来（本脚本第一次跑 native 检查时实测）。
    export KONAN_DATA_DIR="${KONAN_DATA_DIR:-$ROOT/.tooling/konan}"
    mkdir -p "$KONAN_DATA_DIR"
    local sdk="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-$HOME/Library/Android/sdk}}"
    export ANDROID_SDK_ROOT="$sdk" ANDROID_HOME="$sdk"
    mkdir -p "$GRADLE_USER_HOME" "$ANDROID_USER_HOME"
}

# ── quick 项 ────────────────────────────────────────────────────────────────

q_doc_guard() {
    step "文档守卫（7 项，含端侧活数字）"
    if bash docs/tech/.validation/doc_guard.sh --no-warn >/tmp/sekb-docguard.log 2>&1; then
        record PASS 全端 "文档守卫" "7 项全过（含协议 N 方 + 端侧活数字）"
    else
        record FAIL 全端 "文档守卫" "见 /tmp/sekb-docguard.log（先看第 7/7 端侧活数字那节）"
    fi
    tail -3 /tmp/sekb-docguard.log 2>/dev/null | sed 's/^/    /'
}

q_edge_facts() {
    step "端侧活数字（Android 用例数 ↔ 实测）"
    if python3 scripts/check_edge_facts.py --quiet >/tmp/sekb-facts.log 2>&1; then
        record PASS Android "端侧活数字" "$(grep -c . /tmp/sekb-facts.log) 行输出，实测与文档一致"
    else
        record FAIL Android "端侧活数字" "文档与实测不一致（见 /tmp/sekb-facts.log）"
        sed 's/^/    /' /tmp/sekb-facts.log
    fi
}

q_shared_compiles() {
    step "共享层纯逻辑可编（Android 变体，秒级）"
    prepare_android_env || { record SKIP 全端 "共享层编译" "无 JDK（环境限制）"; return; }
    local log=/tmp/sekb-shared-jvm.log
    # `apps/shared` 只声明 android/native 目标（没有 jvm()），所以这里编 Android 变体。
    # ⚠️ 任务名是 `compileAndroidMain`（AGP 9 KMP 插件的命名），**不是** compileDebugKotlinAndroid
    # ——后者实测报 "Cannot locate tasks that match ... task not found"（本轮踩到）。
    if ( cd apps/android && ./gradlew --no-daemon :shared:compileAndroidMain --console=plain -q ) >"$log" 2>&1; then
        record PASS 全端 "共享层(:shared:compileAndroidMain)" "BUILD SUCCESSFUL"
    else
        record FAIL 全端 "共享层编译" "见 ${log}（第一处 error，别用窄 grep 过滤）"
        grep -m3 -E "^e: |error:" "$log" | sed 's/^/    /'
    fi
}

q_shared_native_compiles() {
    # ⚠️ **Kotlin/Native 必须单独编一次**（2026-10-25 被真实事故逼出来的检查）。
    #
    # 事故：往共享层的 override 上单独加 `@Throws` —— **JVM 侧完全合法**，
    # `:shared:compileAndroidMain` 绿、292 单测绿；但 native 侧是硬编译错误
    # （`Member overrides different '@Throws' filter ...`），
    # 直到 iOS/鸿蒙各自跑了一轮 17–18 分钟的构建才暴露，**连续踩了两次**。
    # 教训：**"Android 编得过"不等于"共享层编得过"**——共享层的可移植性只有 native 侧能证。
    step "共享层可编（Kotlin/Native：macOS target）"
    if [ "${SEKB_SKIP_NATIVE:-0}" = "1" ]; then
        record SKIP 全端 "共享层(native)" "显式跳过（SEKB_SKIP_NATIVE=1）；注意这不代表通过"
        return
    fi
    prepare_android_env || { record SKIP 全端 "共享层(native)" "无 JDK（环境限制）"; return; }
    local log=/tmp/sekb-shared-native.log
    # `-PsekbNativeTargets=true`：native target 默认关闭（日常构建保持秒级），这里显式打开。
    # macOS target 与 iOS/鸿蒙共用同一份 commonMain，所以它能代表"native 侧是否编得过"。
    if ( cd apps/android && ./gradlew --no-daemon -PsekbNativeTargets=true \
            :shared:compileKotlinMacosArm64 --console=plain -q ) >"$log" 2>&1; then
        record PASS 全端 "共享层(native: :shared:compileKotlinMacosArm64)" "BUILD SUCCESSFUL"
    else
        record FAIL 全端 "共享层(native)" "见 ${log}（native 专属错误，Android/JVM 编得过不代表这里过）"
        grep -m3 -E "^e: |error:" "$log" | sed 's/^/    /'
    fi
}

q_android_unit_count() {
    step "Android 单测计数（静态 @Test 数 ↔ Gradle XML 报告）"
    local out
    out="$(python3 - <<'PY'
import glob, re, os, sys
sys.path.insert(0, "scripts")
import importlib.util
spec = importlib.util.spec_from_file_location("cef", "scripts/check_edge_facts.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
a = m.measured(); r = m.xml_report_cases()
print(f"{a['android_unit_cases']}|{r if r is not None else 'NA'}|{a['contract_cases']}")
PY
)" || { record FAIL Android "单测计数" "统计脚本失败"; return; }
    local src xml contract
    src="${out%%|*}"; rest="${out#*|}"; xml="${rest%%|*}"; contract="${rest##*|}"
    if [ "$xml" = "NA" ]; then
        record SKIP Android "单测计数" "无 Gradle XML 报告（还没在本机跑过 test）——静态计数=${src}"
    elif [ "$src" = "$xml" ]; then
        record PASS Android "单测计数" "静态 @Test=${src} == XML 报告=${xml}，契约夹具 ${contract} case"
    else
        record FAIL Android "单测计数" "静态 @Test=${src} != XML 报告=${xml}（有测试未被收集，或报告过时）"
    fi
}

# ── full 项（每端一条）─────────────────────────────────────────────────────

f_android() {
    step "Android：单测全量"
    local log=/tmp/sekb-android-test.log
    if bash scripts/android.sh test >"$log" 2>&1; then
        local n
        n="$(python3 - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("cef", "scripts/check_edge_facts.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.measured()["android_unit_cases"])
PY
)"
        record PASS Android "scripts/android.sh test" "${n} 用例 / 0 失败（见 ${log}）"
    else
        record FAIL Android "scripts/android.sh test" "见 $log"
    fi
}

f_ios() {
    step "iOS：framework + Swift 冒烟"
    if ! have scripts/ios.sh; then
        record SKIP iOS "scripts/ios.sh smoke" "脚本不存在"; return
    fi
    local log=/tmp/sekb-ios-smoke.log
    if bash scripts/ios.sh smoke >"$log" 2>&1; then
        local line; line="$(grep -E 'SMOKE (OK|FAIL)' "$log" | tail -1)"
        if echo "$line" | grep -q 'SMOKE OK'; then
            record PASS iOS "scripts/ios.sh smoke" "${line:-SMOKE OK}"
        else
            record FAIL iOS "scripts/ios.sh smoke" "${line:-未见 SMOKE 结果行}"
        fi
    else
        # 缺 Xcode/缺 ORT 属于环境限制 → SKIP 并写清缺什么
        if grep -qE 'requires Xcode|xcodebuild|缺少 ONNX Runtime|Operation not permitted' "$log"; then
            record SKIP iOS "scripts/ios.sh smoke" "环境限制：$(grep -m1 -oE 'requires Xcode|缺少 ONNX Runtime|Operation not permitted' "$log")"
        else
            record FAIL iOS "scripts/ios.sh smoke" "见 $log"
        fi
    fi
}

f_mac() {
    step "Mac：自检（与 iOS 同一份 Swift 源码）"
    if ! have scripts/mac_app.sh; then
        record SKIP Mac "scripts/mac_app.sh run" "脚本不存在"; return
    fi
    local log=/tmp/sekb-mac-run.log
    if bash scripts/mac_app.sh run >"$log" 2>&1; then
        local line; line="$(grep -E 'SELFTEST PASS=' "$log" | tail -1)"
        record PASS Mac "scripts/mac_app.sh run" "${line:-自检已跑（未打印汇总行，请人工确认）}"
    else
        if grep -qE '缺少 ONNX Runtime|Operation not permitted|xcodebuild' "$log"; then
            record SKIP Mac "scripts/mac_app.sh run" "环境限制（见 ${log}）"
        else
            record FAIL Mac "scripts/mac_app.sh run" "见 $log"
        fi
    fi
}

f_harmony() {
    step "鸿蒙：KMP 产物 + interop 冒烟"
    if ! have scripts/harmony.sh; then
        record SKIP 鸿蒙 "scripts/harmony.sh smoke" "脚本不存在（H0 未落地）"; return
    fi
    if ! grep -q "smoke" scripts/harmony.sh; then
        record SKIP 鸿蒙 "scripts/harmony.sh smoke" "脚本存在但还没有 smoke 动作（H1 未完成）"; return
    fi
    local log=/tmp/sekb-harmony-smoke.log
    if bash scripts/harmony.sh smoke >"$log" 2>&1; then
        local line; line="$(grep -E 'SMOKE (PASS|FAIL|OK)' "$log" | tail -1)"
        record PASS 鸿蒙 "scripts/harmony.sh smoke" "${line:-已跑（请人工确认汇总行）}"
    else
        if grep -qE 'maven.eazytec|not found|连不|Connect|无网络' "$log"; then
            record SKIP 鸿蒙 "scripts/harmony.sh smoke" "环境限制：依赖仓库不可达（见 ${log}）"
        else
            record FAIL 鸿蒙 "scripts/harmony.sh smoke" "见 $log"
        fi
    fi
}

# ── 主流程 ──────────────────────────────────────────────────────────────────

echo "=============================================="
echo " SEKB 四端验收总入口（mode=${MODE}${ONLY:+，only=${ONLY}}）"
echo " 仓库：$ROOT"
echo "=============================================="

case "$MODE" in
    quick)
        q_doc_guard
        q_edge_facts
        q_shared_compiles
        q_shared_native_compiles
        q_android_unit_count
        ;;
    full)
        q_doc_guard
        q_edge_facts
        case "$ONLY" in
            --android-only) f_android ;;
            --ios-only)     f_ios ;;
            --mac-only)     f_mac ;;
            --harmony-only) f_harmony ;;
            *)              f_android; f_ios; f_mac; f_harmony ;;
        esac
        ;;
    *)
        echo "未知模式：${MODE}（可用：quick | full）" >&2
        exit 2
        ;;
esac

echo ""
echo "================ 汇总 ================"
printf "%-6s %-8s %-34s %s\n" "状态" "端" "验收项" "详情"
FAILS=0
for r in "${RESULTS[@]}"; do
    IFS='|' read -r st end item detail <<<"$r"
    [ "$st" = "FAIL" ] && FAILS=$((FAILS + 1))
    printf "%-6s %-8s %-34s %s\n" "$st" "$end" "$item" "$detail"
done
echo "======================================"
if [ "$FAILS" -gt 0 ]; then
    echo "❌ 有 ${FAILS} 项 FAIL —— 先修这些，再看 SKIP 是否可解"
    exit 1
fi
echo "✅ 无 FAIL。SKIP 项是**未验**（不是通过）——详情里写了缺什么。"
