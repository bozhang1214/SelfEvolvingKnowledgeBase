#!/usr/bin/env bash
# ============================================================
# 鸿蒙 spike 的**上机流水线**（对标 scripts/android.sh / scripts/ios_app.sh）
# ============================================================
# 为什么要有它：本文件出现之前，鸿蒙的"构建→装→起→看日志→抓崩溃"是一串**手敲**命令
# （散落在 apps/harmony/README.md 里），既不可复现也容易漏步骤。这里把它们收成一条命令。
#
# 用法：
#   bash scripts/harmony.sh build              # 编 KMP 产物 + HAP（委托 harmony_spike.sh all）
#   bash scripts/harmony.sh install            # 把未签名 HAP 装到模拟器
#   bash scripts/harmony.sh start              # 启动 EntryAbility
#   bash scripts/harmony.sh selftest [秒数]    # start + 抓 hilog（默认等 12s）
#   bash scripts/harmony.sh log                # 只抓最近的 hilog（按 TAG 过滤）
#   bash scripts/harmony.sh crash [--save]     # 列 faultlogger；--save 把最新一条拉回 docs/tmp/
#   bash scripts/harmony.sh all                # build + install + selftest
#
# 现场事实（都实测过，别再重新踩）：
#   1. **hdc 不在 PATH**：在 DevEco 的 SDK 里（本脚本已内置路径）。
#   2. 鸿蒙模拟器由 **DevEco IDE** 创建并启动，`scripts/emulator.sh` 只管 Android——
#      所以这里只**校验** target 在线，不负责起模拟器。
#   3. 模拟器实例是 **arm64**（nova 16 Pro / HarmonyOS 6.0.2），不是 x86_64。
#   4. **未签名 HAP 可以直接装**（模拟器不需签名材料）。
#   5. 崩溃栈在设备 `/data/log/faultlog/faultlogger/`；双击"应用消失"时先来这里取。
#   6. **写本脚本时自己踩到的坑**：`set -u` 下 `$VAR` **紧贴中文标点**（如 `$TAG）`）会被 bash
#      当成变量名的一部分（高字节按字母处理）→ 报 `TAG<乱码>: unbound variable`。
#      **变量一律写 `${VAR}`**；本脚本已全部改正。
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVECO="${DEVECO_HOME:-/Applications/DevEco-Studio.app/Contents}"
HDC="$DEVECO/sdk/default/openharmony/toolchains/hdc"
BUNDLE="${HARMONY_BUNDLE:-com.sekb.ohos.spike}"
ABILITY="${HARMONY_ABILITY:-EntryAbility}"
HAP="$ROOT/apps/harmony/spike/hapshell/entry/build/default/outputs/default/entry-default-unsigned.hap"
#: hilog 过滤标签：Index.ets 里 const TAG；接线后 self-test 会改名 SEKB_HARMONY_SELFTEST
TAG="${HARMONY_LOG_TAG:-SEKB_KN_SPIKE}"
CRASH_DIR="/data/log/faultlog/faultlogger"

die() { echo "❌ $*" >&2; exit 1; }

require_hdc() {
    [ -x "$HDC" ] || die "找不到 hdc：${HDC}（可用 DEVECO_HOME 覆盖 DevEco 路径）"
}

require_target() {
    local targets
    targets="$("$HDC" list targets 2>/dev/null | grep -v '^\[Empty\]' | head -3)"
    [ -n "$targets" ] || die "没有在线设备/模拟器。鸿蒙模拟器请先用 DevEco IDE 启动（本脚本不负责起模拟器）。"
    echo "   设备: $(echo "$targets" | tr '\n' ' ')"
}

build() {
    echo "── 构建（委托 scripts/harmony_spike.sh all）──"
    bash "$ROOT/scripts/harmony_spike.sh" all || die "构建失败（先看输出里的第一处 error，别用窄 grep 过滤）"
}

install() {
    [ -f "$HAP" ] || die "找不到 HAP：${HAP}（先跑 build）"
    echo "── 安装（$(du -h "$HAP" | cut -f1)）──"
    "$HDC" install "$HAP" || die "安装失败"
}

start() {
    echo "── 启动 $BUNDLE/$ABILITY ──"
    "$HDC" shell "aa start -a $ABILITY -b $BUNDLE" || die "启动失败"
}

# selftest：start 之后抓 TAG 日志。注意"已在运行"时 aboutToAppear 不会重跑，
# 所以这里先 force-stop（与 Android 侧 landing 自检同一个坑）。
selftest() {
    local wait_s="${1:-12}"
    echo "── 自检（等待 ${wait_s}s 抓 hilog TAG=${TAG}）──"
    "$HDC" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
    start
    sleep "$wait_s"
    log
    echo
    echo "提示：若上面没有日志，先确认 ${wait_s}s 够不够（冷启动要复制 .ms 到私有目录）。"
}

log() {
    "$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | tail -30 || true
}

crash() {
    echo "── faultlogger（${CRASH_DIR}）──"
    local listing newest
    listing="$("$HDC" shell "ls -t $CRASH_DIR/ 2>/dev/null | head -5" 2>/dev/null)"
    if [ -z "$listing" ]; then
        echo "   （无崩溃记录）"
        return 0
    fi
    echo "$listing" | sed 's/^/   /'
    if [ "${1:-}" = "--save" ]; then
        newest="$(echo "$listing" | head -1)"
        mkdir -p "$ROOT/docs/tmp"
        "$HDC" file recv "$CRASH_DIR/$newest" "$ROOT/docs/tmp/$newest" \
            && echo "   已保存: docs/tmp/$newest"
    else
        echo "   （加 --save 可把最新一条拉到 docs/tmp/）"
    fi
}

require_hdc
case "${1:-}" in
    build)    build ;;
    install)  require_target; install ;;
    start)    require_target; start ;;
    selftest) require_target; selftest "${2:-}" ;;
    log)      require_target; log ;;
    crash)    require_target; crash "${2:-}" ;;
    all)      require_target; build && install && selftest "${2:-}" ;;
    # 校验 target 的动作都要联网检查，所以放在 case 内做
    *) echo "用法：bash scripts/harmony.sh [build|install|start|selftest|log|crash|all]" >&2; exit 2 ;;
esac

# 说明：这里**不做** `require_target` 的统一前置检查，因为 `build` 是纯本机构建、
# 不需要设备（CI/无模拟器时也能跑）。需要设备的动作各自检查。
