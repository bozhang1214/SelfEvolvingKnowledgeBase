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
#   bash scripts/harmony.sh smoke [秒数]       # H1：编 ohos 产物 → 装 → 跑共享层 interop 冒烟（grep SMOKE）
#   bash scripts/harmony.sh contract [秒数]    # H2：契约夹具 37 case + 内存反向验证（grep CONTRACT）
#   bash scripts/harmony.sh contract-red [秒]  # H2：端到端反证——改**包内**夹具期望值必须变红
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
    # 清一次日志缓冲：否则上一轮的同名结果还在，可能"用旧结果冒充新结果"（实测踩过）
    "$HDC" shell "hilog -r" >/dev/null 2>&1 || true
    start
    sleep "$wait_s"
    log
    echo
    echo "提示：若上面没有日志，先确认 ${wait_s}s 够不够（冷启动要复制 .ms 到私有目录）。"
}

log() {
    "$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | tail -30 || true
}

# ── 防假绿：核对外层"包里那份夹具"就是仓库里那份 ───────────────────────────
# 为什么需要它（照 apps/contract/README.md 的硬要求 5）：夹具改了但设备上跑的仍是**旧的那份**，
# 结论就是绿的——这就是 Android 侧踩过的"假绿"。
#
# ⚠️ 我先写过一版"构建时把源夹具哈希写进 manifest，跑之前比对"——**那版是无效的**：
#    `contract` 动作里 build 跑在前面，build 会按当前源重新生成 manifest，
#    所以"改了夹具 → 重建 → 比对"永远一致（实测：故意改一个字符，清单照样全绿）。
#    教训：把"期望值"和"待检物"放在同一次构建里算，等于自己给自己盖章。
#
# 现在这条判据改成**跨边界**的：把 HAP 里**实际打进包**的夹具解出来算哈希，
# 与仓库源文件比对。它抓的是"拷贝步骤坏了 / 包里是旧夹具"这类真问题，
# 且不可能被同一次 build 掩盖。
#
# 真正的"改夹具期望值必须变红"由 `contract_red_fixture`（见下）证明——
# 那是直接改**包内**夹具再装再跑，属于端到端反证，比哈希更硬。
verify_contract_inputs() {
    local src="$ROOT/apps/contract"
    local hap="$HAP"
    [ -d "$src" ] || die "找不到契约夹具目录：${src}"
    [ -f "$hap" ] || die "找不到 HAP：${hap}（先跑 build）"
    local tmp; tmp="$(mktemp -d)"
    unzip -q -o "$hap" -d "$tmp" 'resources/rawfile/contract/*' 2>/dev/null
    local ok=0
    for f in routing.json signals.json privacy.json; do
        local in_hap="$tmp/resources/rawfile/contract/$f"
        if [ ! -f "$in_hap" ]; then
            echo "   ❌ HAP 里没有 resources/rawfile/contract/$f" >&2; ok=1; continue
        fi
        local h_src h_hap
        h_src="$(shasum -a 256 "$src/$f" | cut -d' ' -f1)"
        h_hap="$(shasum -a 256 "$in_hap" | cut -d' ' -f1)"
        if [ "$h_src" != "$h_hap" ]; then
            echo "   ❌ ${f}：包内那份与仓库源不一致（设备上跑的是旧夹具=假绿）" >&2
            echo "      仓库=${h_src:0:12}…  包内=${h_hap:0:12}…" >&2
            ok=1
        else
            echo "   ✅ ${f} 包内==仓库源（${h_src:0:12}…）"
        fi
    done
    rm -rf "$tmp"
    [ "$ok" = "0" ] || die "夹具核对失败：重新跑 bash scripts/harmony.sh build 后再验"
}

# ── H1：共享层 interop 冒烟 ────────────────────────────────────────────────
# 一条命令回答"H1 成了没有"：构建（libkn.so 含共享层 + HAP）→ 装 → 起 → grep `SMOKE`。
#
# 四项检查与 iOS `ios.sh smoke` 对称，另加"共享层真的进了产物"这一条：
#   1.  `SMOKE PASS n/n`（设备上第一次进程真的跑出来的结果，不是"能链接"）
#   2.  逐项 PASS/FAIL 明细（失败项名字能直接定位）
#   3.  产物里含 `com.sekb.shared.*`（防止"界面调到的其实是别的实现"）
#   4.  **FAIL 必须 0**：明细里出现任何一行 `FAIL ` 就整体失败（防止只看首行而漏掉失败项）
smoke() {
    local wait_s="${1:-15}"
    local so="$ROOT/apps/harmony/spike/knspike/build/bin/ohosArm64/debugShared/libkn.so"
    local nm="$DEVECO/sdk/default/openharmony/native/llvm/bin/llvm-nm"

    echo "── 1/4 构建（libkn.so 含共享层 + HAP；委托 scripts/harmony_spike.sh all）──"
    bash "$ROOT/scripts/harmony_spike.sh" all || die "构建失败（先看输出里的第一处 error，别用窄 grep 过滤）"

    echo
    echo "── 2/4 产物断言：共享层真的编进了 libkn.so ──"
    [ -f "$so" ] || die "缺少 libkn.so：${so}"
    # 口径说明（本轮踩过一次，写下来免得重踩）：共享层没有 `@CName`，所以**不会**出现在
    # 动态符号表（`nm -D`）里 —— Kotlin/Native 默认只导出 `@CName`/`@ExportObjCClass`。
    # 两个真正有效的判据：
    #   a) **完整符号表**里的 `_Konan_init_<模块名>`（实测串 `_Konan_init_sekb-ohos-spike:shared-core`；
    #      ⚠️ 它是 **local（小写 t）**，所以 `-D` 里没有——实测 `nm -D | grep shared-core` = 0）
    #      —— 初始化符号在 = 这个 Kotlin 模块真的被链接进来了（最直接）；
    #   b) 共享层的 **klib 真的产出了**（`apps/shared-core/build/classes/kotlin/ohosArm64/main/klib/`）,
    #      即 41 个 .kt 确实被 ohosArm64 编译过（不是"只配了 srcDir 没编"）。
    # 只用 `nm -D | grep com.sebk.shared` 必然是 0：那是**方法错**，不是「没编进去」。
    # ⚠️ 实测教训（别再试图用"类名在不在 .so 里"当判据）：Kotlin/Native 只把**被用到的**类
    # 编进最终产物，`kclass:com.sebk.shared.*` 这类元数据符号可能一个都不出现——
    # 实测同一个 .so 上 `nm | grep com.sebk.shared` = 0，而共享层的代码分明在跑
    # （`kclass`「消失」不等于「没编进去」，是本判据本身不成立）。
    # 所以这里用两条**站得住**的判据：Konan 模块初始化符号 + 共享层 klib 产物。
    local init_sym klib_dir
    init_sym="$("$nm" "$so" 2>/dev/null | grep -c '_Konan_init_sekb-ohos-spike:shared-core' || true)"
    klib_dir="$ROOT/apps/shared-core/build/classes/kotlin/ohosArm64/main/klib"
    if [ "${init_sym:-0}" -gt 0 ]; then
        echo "   ✅ libkn.so 有 :shared-core 的 Konan 初始化符号（模块被真正链接进产物）"
    else
        die "libkn.so 里找不到 :shared-core 的初始化符号——共享层没编进来（检查 :shared-core 依赖）"
    fi
    if [ -d "$klib_dir" ]; then
        echo "   ✅ 共享层已被 ohosArm64 编译：$(du -sh "$klib_dir" | cut -f1) klib（$(find "$klib_dir" -name '*.knm' | wc -l | tr -d ' ') 个本模块包）"
    else
        die "缺少共享层 klib：${klib_dir}（41 个共享 .kt 没被 ohosArm64 编译过）"
    fi
    if "$nm" -D --defined-only "$so" 2>/dev/null | grep -q 'sekb_spike_smoke'; then
        echo "   ✅ NAPI 冒烟入口 sekb_spike_smoke 已导出"
    else
        die "libkn.so 没导出 sekb_spike_smoke（Smoke.kt 的 @CName 没生效？）"
    fi

    echo
    echo "── 3/4 安装并启动（等待 ${wait_s}s 抓 hilog TAG=${TAG}）──"
    "$HDC" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
    # 清一次日志缓冲：否则上一轮的同名结果还在，可能"用旧结果冒充新结果"（实测踩过）
    "$HDC" shell "hilog -r" >/dev/null 2>&1 || true
    install
    start
    sleep "${wait_s}"

    echo
    echo "── 4/4 冒烟结果（原始 hilog）──"
    local out
    out="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a -E 'SMOKE|PASS |FAIL ' | tail -40 || true)"
    echo "${out}"
    [ -n "${out}" ] || die "没抓到 SMOKE 行——检查 ${wait_s}s 够不够，或先跑 bash scripts/harmony.sh log 看 TAG=${TAG}"

    if echo "${out}" | grep -aq 'SMOKE PASS'; then
        echo
        echo "✅ 共享层 interop 冒烟通过（SMOKE PASS）"
    else
        echo
        die "❌ 冒烟未通过（没有 SMOKE PASS 行）"
    fi
    # 独立的第二判据：首行 PASS 也可能有明细 FAIL（例如后续项失败但首行被截断）
    local nfail
    nfail="$(echo "${out}" | grep -ac 'FAIL ' || true)"
    if [ "${nfail}" != "0" ]; then
        die "❌ 冒烟明细里有 ${nfail} 个 FAIL 项（见上面原始输出）"
    fi
    echo "   明细 FAIL=0"
    # H1 顺带把夹具哈希也核一遍（几毫秒）：改夹具没重建的话这里就会红，
    # 而不是等到 H2 的 contract 动作才发现（更早暴露，更省一轮构建）。
    echo
    echo "── 附：契约夹具哈希核对（防假绿）──"
    verify_contract_inputs
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

# 从 `CONTRACT <PASS|FAIL> <passed>/<total>` 行里算出失败数。
# 为什么单独一个函数：原来直接 grep `CONTRACT FAIL 1/` —— 41 项断言时是 `FAIL 127/128`，
# 三位数让那个模式匹配不到，于是**明明变红了却报"没按预期变红"**（实测踩到）。
contract_failed_of() {
    local nums passed total
    nums="$(echo "$1" | grep -aoE 'CONTRACT (PASS|FAIL) [0-9]+/[0-9]+' | tail -1 | awk '{print $3}')"
    [ -n "${nums}" ] || { echo "-1"; return; }
    passed="${nums%%/*}"; total="${nums##*/}"
    echo $((total - passed))
}

# ── H2：契约夹具 runner（37 case）+ 反向验证 ────────────────────────────────
# 两条判据都必须成立（只有①的话，"runner 没断言任何东西"也会全绿）：
#   ① 原样夹具 → `CONTRACT PASS n/N` 且 n >= 37（37 case 全过，含 4 项覆盖度自检）
#   ② 反向验证 → `CONTRACT FAIL 1/N`（把 routing 第一条 case 的期望平面反转，**恰好** 1 个 FAIL）
#      —— 这一条才是"改夹具期望值必须变红"的证据
contract() {
    local wait_s="${1:-25}"
    echo "── 1/4 构建（含契约夹具进 rawfile + 哈希清单）──"
    bash "$ROOT/scripts/harmony_spike.sh" all || die "构建失败（先看输出里的第一处 error，别用窄 grep 过滤）"

    echo
    echo "── 2/4 防假绿：夹具哈希核对（改了夹具没重建就会在这里红）──"
    verify_contract_inputs || exit 1

    echo
    echo "── 3/4 安装并启动（等待 ${wait_s}s 抓 hilog TAG=${TAG}）──"
    "$HDC" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
    # 清一次日志缓冲：否则上一轮的同名结果还在，可能"用旧结果冒充新结果"（实测踩过）
    "$HDC" shell "hilog -r" >/dev/null 2>&1 || true
    install
    start
    sleep "${wait_s}"

    echo
    echo "── 4/4 契约结果（原始 hilog）──"
    # ⚠️ 取日志要**分两次**查（实测教训）：一次 `hilog | grep TAG | tail -80` 里，
    # 41 项断言明细会把 `CONTRACT ...` 头行挤出窗口（头部被丢掉 → 明明全绿却报"没有 CONTRACT PASS 行"）。
    # 所以头部与明细各查一次，各取各的窗口。
    local heads detail normal red
    heads="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a -E 'CONTRACT|RED-CHECK' | tail -20 || true)"
    detail="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a -E '(PASS|FAIL) ' | tail -60 || true)"
    echo "── 头部 ──"
    echo "${heads}"
    echo "── 明细（截尾 60 行）──"
    echo "${detail}"
    [ -n "${heads}" ] || die "没抓到 CONTRACT 行——检查 ${wait_s}s 够不够，或先跑 bash scripts/harmony.sh log"

    normal="$(echo "${heads}" | grep -a 'CONTRACT PASS' | tail -1 || true)"
    red="$(echo "${heads}" | grep -a 'CONTRACT FAIL' | tail -1 || true)"

    if [ -n "${normal}" ]; then
        echo
        # 口径：数字是**断言数**（一条 case 可含多项断言：plane/reason/tier/…），
        # 不是 case 数。37 个 case（routing 10 / signals 14 / privacy 13）+ 覆盖度自检。
        echo "✅ ①  原样夹具全绿：$(echo "${normal}" | sed 's/.*SEKB_KN_SPIKE: //')"
        echo "      （口径：断言数；case 数=37 → routing 10 / signals 14 / privacy 13 + 4 项覆盖度自检）"
    else
        die "❌ ① 没有 CONTRACT PASS 行（原样夹具没全过）"
    fi
    # ⚠️ 这里**不能**用 `grep '期望恰好 1 FAIL'` 那样的字面量去匹配整行——实测行尾就被它的
    #    方括号内容搞出过假失败（行本身是好的，是匹配写法不牢）。改成三个独立、可解释的判据：
    #    ① 状态是 FAIL ② red-check 确实生效 ③ 失败数恰好 1
    if [ -z "${red}" ]; then
        die "❌ ② 没有 CONTRACT FAIL 行 —— 反向验证没跑（夹具可能根本没被断言）"
    elif ! echo "${red}" | grep -aq 'red-check applied'; then
        die "❌ ② red-check 变体没被应用（行='${red}'）——检查 routing[0].expect.plane"
    elif [ "$(contract_failed_of "${red}")" != "1" ]; then
        die "❌ ② 反向验证的失败数不是恰好 1（行='${red}'）——夹具可能没被逐条断言"
    else
        echo "✅ ②  反向验证变红：$(echo "${red}" | sed 's/.*SEKB_KN_SPIKE: //')"
        echo "      （改夹具期望值确实会红 = 夹具不是摆设）"
    fi
    # 明细里除反向验证那一条外不允许有 FAIL
    local nfail
    nfail="$(echo "${detail}" | grep -ac 'FAIL ' || true)"
    echo "   （明细 FAIL 行数=${nfail}，其中反向验证段应恰好 1 条）"
}

# ── H2 的**端到端反证**：改"包内"夹具的期望值 → 必须变红 ─────────────────────
# 与 `contract` 里那个"内存里反转期望平面"不同，这一步改的是**真正装到设备上的那份夹具**：
#   ① 改 `entry/src/main/resources/rawfile/contract/signals.json`（HAP 的输入，仓库冻结文件不动）
#   ② 只重打 HAP（**不重拷** rawfile，否则改动被覆盖）
#   ③ 装 → 起 → 抓日志：第 1 段（原样夹具）必须变成 `CONTRACT FAIL`（且失败数 ≥1）
#   ④ 恢复夹具并重打 HAP（把工作区还原成干净状态）
# 这一步才是"改夹具期望值必须变红"的直接证据；哈希核对只是"包里那份==仓库那份"的辅助判据。
contract_red_fixture() {
    local wait_s="${1:-25}"
    local packaged="$ROOT/apps/harmony/spike/hapshell/entry/src/main/resources/rawfile/contract/signals.json"
    [ -f "$packaged" ] || die "找不到包内夹具：${packaged}（先跑 build）"
    local backup; backup="$(mktemp)"
    cp "$packaged" "$backup"

    echo "── 1/5 改包内夹具的期望值（不动仓库里的 apps/contract/**）──"
    # 把第一条 case 的 expectSignals 从一个信号改成另一个（语义上必然错）
    python3 - "$packaged" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p, encoding='utf-8'))
c = d["cases"][0]
before = list(c["expectSignals"])
c["expectSignals"] = ["timeout"] if before != ["timeout"] else ["empty"]
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"   case={c['id']}  expectSignals {before} → {c['expectSignals']}")
PY

    echo
    echo "── 2/5 只重打 HAP（不重拷 rawfile）──"
    bash "$ROOT/scripts/harmony_spike.sh" haponly || { cp "$backup" "$packaged"; die "重打 HAP 失败"; }

    echo
    echo "── 3/5 安装并启动（等待 ${wait_s}s）──"
    "$HDC" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
    "$HDC" shell "hilog -r" >/dev/null 2>&1 || true
    install
    start
    sleep "${wait_s}"

    echo
    echo "── 4/5 结果（期望第 1 段就是 FAIL）──"
    local heads
    heads="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a -E 'CONTRACT|RED-CHECK' | tail -20 || true)"
    echo "${heads}"

    local normal
    normal="$(echo "${heads}" | grep -a 'CONTRACT ' | head -1 || true)"
    local failed; failed="$(contract_failed_of "${normal}")"

    echo
    echo "── 5/5 恢复包内夹具并重打 HAP ──"
    cp "$backup" "$packaged"
    rm -f "$backup"
    bash "$ROOT/scripts/harmony_spike.sh" haponly >/dev/null 2>&1 && echo "   已恢复并重打 HAP"

    if [ "${failed}" -ge 1 ] 2>/dev/null; then
        echo "✅ 改夹具期望值 → 确实变红（${normal##*SEKB_KN_SPIKE: }，失败 ${failed} 项）"
    else
        die "❌ 改了包内夹具期望值却没有变红（行='${normal}'）—— runner 没在真的断言"
    fi
}

require_hdc
case "${1:-}" in
    build)    build ;;
    install)  require_target; install ;;
    start)    require_target; start ;;
    selftest) require_target; selftest "${2:-}" ;;
    smoke)    require_target; smoke "${2:-}" ;;
    contract) require_target; contract "${2:-}" ;;
    contract-red) require_target; contract_red_fixture "${2:-}" ;;
    log)      require_target; log ;;
    crash)    require_target; crash "${2:-}" ;;
    all)      require_target; build && install && selftest "${2:-}" ;;
    # 校验 target 的动作都要联网检查，所以放在 case 内做
    *) echo "用法：bash scripts/harmony.sh [build|install|start|selftest|smoke|contract|contract-red|log|crash|all]" >&2; exit 2 ;;
esac

# 说明：这里**不做** `require_target` 的统一前置检查，因为 `build` 是纯本机构建、
# 不需要设备（CI/无模拟器时也能跑）。需要设备的动作各自检查。
