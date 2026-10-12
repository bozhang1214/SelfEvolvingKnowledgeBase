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
#   bash scripts/harmony.sh probe [秒数]       # H3/HR9：平台库 dlopen 探针（libcurl 三探针的判定依据）
#   bash scripts/harmony.sh embed [秒数]       # H6：端侧嵌入验收（余弦 0.244 / L2 1.0000 / 确定性 / 降级）
#   bash scripts/harmony.sh net [秒数]         # H3（网络端口）+ H4（HUKS 凭证端口）自检，**两轮**取证：
#                                              #   第 1 轮 = 首跑（写凭证）；第 2 轮 = force-stop 后重启（验「重启后仍有效」）
#   bash scripts/harmony.sh sync-huks-headers  # 把 sysroot 里的 HUKS 头同步到仓库内（H4 cinterop 用）
#   bash scripts/harmony.sh rdbprobe [秒数]    # H5-F：RDB NDK C API 探针（建表/往返/关闭重开）
#   bash scripts/harmony.sh rag [秒数]         # H5-F：RAG 自检（含反向验证）+ 检索评测同表（5 档阈值）
#   bash scripts/harmony.sh h8selftest [秒数]  # H8（G）：**四页 UI 的自检表**（TAG=SEKB_HARMONY_SELFTEST）
#   bash scripts/harmony.sh h8selftest-red [秒] # H8：反向验证——注入错期望值，**必须恰好 1 个 FAIL**
#   bash scripts/harmony.sh h8shots            # H8：四页截图（用 SEKB_PAGE_* 动作确定性开页，不点屏幕）
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

# ── H4：把 HUKS 头文件从 OHOS sysroot 同步进仓库 ─────────────────────────────
# **为什么需要这一步**（实测，不是洁癖）：H4 的 cinterop 不能直接 include sysroot 里的头——
# 实测把 `-I<sysroot>/usr/include` 写进 `.def` 的 compilerOpts 时，本 fork 的 cinterop
# 会**静默产出空包**（exit 0、klib 的 `package huks {}` 是空的），Kotlin 侧报的是
# `Unresolved reference: huks`，离根因十万八千里。对照实验：同一份头从**仓库内普通目录**
# include 时正常生成 45 条声明。所以头文件逐字节复制进仓库
# `knspike/src/nativeInterop/include/`，本动作负责重新同步 + 打印哈希供复核。
sync_huks_headers() {
    local sr
    sr="$(find "$ROOT/.tooling/konan/dependencies" -maxdepth 1 -name 'sysroot-ohos-aarch64-*' | sort | tail -1)"
    [ -d "$sr" ] || die "找不到 OHOS sysroot（$ROOT/.tooling/konan/dependencies/sysroot-ohos-aarch64-*）"
    local inc="$ROOT/apps/harmony/spike/knspike/src/nativeInterop/include"
    mkdir -p "$inc/huks" "$inc/info"
    local f
    for f in huks/native_huks_api.h huks/native_huks_param.h huks/native_huks_type.h; do
        cp "$sr/usr/include/$f" "$inc/$f" || die "复制失败：$sr/usr/include/$f"
    done
    cp "$sr/usr/include/info/application_target_sdk_version.h" "$inc/info/" \
        || die "复制失败：application_target_sdk_version.h"
    echo "── HUKS 头已同步（来源 ${sr#$ROOT/}）──"
    for f in huks/native_huks_api.h huks/native_huks_param.h huks/native_huks_type.h \
             info/application_target_sdk_version.h; do
        printf '   %s  %s\n' "$(shasum -a 256 "$inc/$f" | cut -c1-16)" "$f"
    done
}

# ── H3/HR9：平台库探针（dlopen，运行期权威判据）─────────────────────────────
# 为什么必须先做这个、而不是直接写 `HttpTransport`：
#   ① C2 定了"网络统一 libcurl"，但 **OHOS sysroot 里既没有 curl 头也没有 curl 库**
#      （实测 `usr/include/curl` 不存在、`usr/lib/aarch64-linux-ohos/` 里也没有）；
#   ② 设备侧 `hdc shell test -e` 判"有没有某个系统库"**不可信**：它对已确认在跑的
#      `libmindspore_lite_ndk.so` 也报 MISSING（SELinux 限制）；
#   ③ host 侧 grep 系统镜像更不可信：`libcurl.so` 字符串在镜像里有 64 处命中，
#      但那是包元数据里的串，**并不代表 /system/lib64/libcurl.so 存在**（实测 dlopen 失败）。
#   → 唯一可信的判据是**在设备上 dlopen**。本动作就是它。
probe() {
    local wait_s="${1:-25}"
    echo "── 1/3 构建（只重打 HAP；NAPI 侧探针不依赖 KMP 产物）──"
    bash "$ROOT/scripts/harmony_spike.sh" haponly || die "HAP 构建失败"

    echo
    echo "── 2/3 安装并启动（等待 ${wait_s}s）──"
    "$HDC" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
    "$HDC" shell "hilog -r" >/dev/null 2>&1 || true
    install
    start
    sleep "${wait_s}"

    echo
    echo "── 3/3 dlopen 结果（原始 hilog）──"
    local out
    out="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a -E 'LOAD (OK|FAIL)|PROBE' | tail -30 || true)"
    echo "${out}"
    [ -n "${out}" ] || die "没抓到探针行——检查 ${wait_s}s 够不够"

    # 两条断言分开写（一条"不该有"、一条"必须有"），避免把两类事实混成一句"看起来还行"：
    #   a) libcurl：C2 依赖它 → 必须**明确记录**它到底可用不可用（FAIL 也算结论，但要显式出现）
    #   b) H4/H5 要用的平台 C 能力：HUKS / RDB NDK / crypto 必须 OK，否则 H4/H5 要换方案
    echo
    if echo "${out}" | grep -aq 'PROBE1 FAIL'; then
        echo "⚠️ libcurl 在设备上**不可用**（已用 5 个名字/路径变体逐一 dlopen 验证）"
        echo "   → C2「鸿蒙与 Android 共用一份 libcurl」在本机模拟器上不成立，需要 owner 决策（见 HR9）"
    fi
    local miss=0
    for lib in libhuks_ndk.z.so libnative_rdb_ndk.z.so libohcrypto.so libmindspore_lite_ndk.so; do
        if echo "${out}" | grep -aq "LOAD OK   ${lib}"; then
            echo "   ✅ ${lib}"
        else
            echo "   ❌ ${lib} 不可用（H4/H5 需要它）" >&2; miss=1
        fi
    done
    [ "$miss" = "0" ] || die "H4/H5 依赖的平台库缺失（见上）"
    echo
    echo "✅ 平台能力探针完成（结论已如实打印；libcurl 不可用是**事实**，不是脚本失败）"
}

# ── H6：端侧嵌入验收（同空间三项 + 降级契约 + **掩码真的生效**）────────────────
# 与 Android `SelfTest.kt` 的 `rag_embed_sanity` 同一口径（同一批文本、同一期望）：
#   ① 两段无关文本余弦（Android 基线 **0.244**）；② L2 范数 **1.0000**；③ 两次嵌入逐位相同；
#   ④ 模型不可用时 **embed 返回同数量零向量**（HR8：返回空数组会让 ingest 越界崩溃）。
#   ⑤ **掩码生效**：正确掩码 vs 全零掩码必须明显不同（≈0 说明 mask 输入被运行时忽略）。
#
# ⚠️ 为什么 ⑤ 必须在设备上（而不只是 host）：本缺陷活到现在的直接原因是
#    "host 上对、设备上错"这件事**没有任何检查**。`embed_sanity_cosine` 是硬 FAIL 判据
#    （`|cos-0.244|<=0.02`），再加上掩码敏感性，两条一起才能在设备上把这类缺陷钉死。
#
# 设备跑之前先跑**转换产物侧**的两条（不需要设备、几秒钟）：
#   · `check-maskinput-wiring`：flatbuffer 里掩码输入还在计算路径上（没被折叠）；
#   · `check-maskinput-ms`：在 Linux runtime 上跑 `.ms`，与 ONNX 参考逐元素比对 + 掩码生效。
# 这样"产物坏了"与"设备喂数坏了"能在同一条命令里被分开定位。
embed() {
    local wait_s="${1:-40}"
    echo "── 0/3 转换产物侧断言（不需要设备）──"
    if ! bash "$ROOT/scripts/model_to_ms.sh" check-maskinput-wiring; then
        die "❌ 转换产物的图结构断言失败（掩码输入不在计算路径上）——先修转换，再谈设备"
    fi
    if [ -f "$ROOT/.tooling/mindspore-lite/work/maskref.json" ]; then
        if ! bash "$ROOT/scripts/model_to_ms.sh" check-maskinput-ms; then
            die "❌ 转换产物的数值保真断言失败（.ms 与 ONNX 不一致 / 掩码不生效）"
        fi
    else
        echo "   ⚠️ 缺 maskref.json，跳过数值保真断言（补跑：bash scripts/model_to_ms.sh check-maskinput-ref）"
    fi

    echo
    echo "── 1/3 构建（KMP 产物 + HAP，含 vocab.txt）──"
    bash "$ROOT/scripts/harmony_spike.sh" all || die "构建失败（先看输出里的第一处 error）"

    echo
    echo "── 2/3 安装并启动（等待 ${wait_s}s；首启要把 94MB 模型复制到私有目录）──"
    "$HDC" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
    "$HDC" shell "hilog -r" >/dev/null 2>&1 || true
    install
    start
    sleep "${wait_s}"

    echo
    echo "── 3/3 嵌入结果（原始 hilog；分两次查，避免明细把首行挤出窗口）──"
    local heads detail
    heads="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a 'EMBED' | tail -5 || true)"
    detail="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a -E 'embed_' | tail -16 || true)"
    echo "${heads}"
    echo "── 明细 ──"
    echo "${detail}"
    [ -n "${heads}" ] || die "没抓到 EMBED 行——检查 ${wait_s}s 够不够（冷启动要复制 94MB 模型）"

    if echo "${heads}" | grep -aq 'EMBED PASS'; then
        echo
        echo "✅ 端侧嵌入验收通过：$(echo "${heads}" | sed 's/.*SEKB_KN_SPIKE: //' | tail -1)"
    else
        die "❌ 嵌入验收未通过（明细见上）"
    fi
    local nfail
    nfail="$(echo "${detail}" | grep -ac 'FAIL ' || true)"
    [ "${nfail}" = "0" ] || die "❌ 明细里有 ${nfail} 个 FAIL 项"
    echo "   明细 FAIL=0"
}

# ── H5（工作流 F）①：RDB NDK C API 探针（HR2 要求"最小验证先于完整实现"）─────
# 一次回答四件事：**能打开库 / 能建表 / 能插入（含 BLOB）并查回来 / 关闭重开数据还在**。
# 为什么必须先做这个：HR2 的原话是"relationalStore 在模拟器上的可得性未知 →
# 先写一个最小验证，不通过就换方案"。H3 的 dlopen 探针已经证明 libnative_rdb_ndk.z.so
# **可加载**，但"能加载"与"能当索引使"是两件事（后者的硬判据是**重开后仍可读**）。
rdb_probe() {
    local wait_s="${1:-20}"
    echo "── 1/3 构建（KMP 产物 + HAP）──"
    bash "$ROOT/scripts/harmony_spike.sh" all || die "构建失败（先看输出里的第一处 error，别用窄 grep 过滤）"

    echo
    echo "── 2/3 安装并启动（等待 ${wait_s}s 抓 hilog TAG=${TAG}）──"
    "$HDC" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
    "$HDC" shell "hilog -r" >/dev/null 2>&1 || true
    install
    start
    sleep "${wait_s}"

    echo
    echo "── 3/3 RDB 探针结果（原始 hilog）──"
    local heads detail
    heads="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a 'RDB-PROBE' | tail -5 || true)"
    detail="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a -E 'rdb' | tail -30 || true)"
    echo "${heads}"
    echo "── 明细 ──"
    echo "${detail}"
    [ -n "${heads}" ] || die "没抓到 RDB-PROBE 行——检查 ${wait_s}s 够不够，或先跑 bash scripts/harmony.sh log"
    if echo "${heads}" | grep -aq 'RDB-PROBE PASS'; then
        echo
        echo "✅ RDB NDK C API 可用：$(echo "${heads}" | sed 's/.*SEKB_KN_SPIKE: //' | tail -1)"
    else
        die "❌ RDB 探针未通过（明细见上）——H5 需要改方案（例如退 ArkTS relationalStore 薄桥）"
    fi
}

rdb_id_probe() {
    local wait_s="${1:-20}"
    echo "── 1/3 构建（KMP 产物 + HAP）──"
    bash "$ROOT/scripts/harmony_spike.sh" all || die "构建失败（先看输出里的第一处 error，别用窄 grep 过滤）"

    echo
    echo "── 2/3 安装并启动（等待 ${wait_s}s 抓 hilog TAG=${TAG}）──"
    "$HDC" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
    "$HDC" shell "hilog -r" >/dev/null 2>&1 || true
    install
    start
    sleep "${wait_s}"

    echo
    echo "── 3/3 RDB 探针结果（原始 hilog）──"
    local heads detail
    heads="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a 'RDBID' | tail -5 || true)"
    detail="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a -E 'RDBID|source_id|NOTE' | tail -30 || true)"
    echo "${heads}"
    echo "── 明细 ──"
    echo "${detail}"
    [ -n "${heads}" ] || die "没抓到 RDBID 行——检查 ${wait_s}s 够不够，或先跑 bash scripts/harmony.sh log"
    if echo "${heads}" | grep -aq 'RDBID PASS'; then
        echo
        echo "✅ RDB NDK C API 可用：$(echo "${heads}" | sed 's/.*SEKB_KN_SPIKE: //' | tail -1)"
    else
        die "❌ RDB 探针未通过（明细见上）——H5 需要改方案（例如退 ArkTS relationalStore 薄桥）"
    fi
}

# ── H5（工作流 F）②：RAG 自检 + 检索评测（H5 + H6 的另一半）────────────────
# 与 Android `SelfTest.kt` 的 rag_* 项**同名同口径**，并且评测**复用共享层**的
# `RetrievalEvalSet`/`RetrievalEvalRunner`（不另写一套），只是把索引换成 RDB 持久化实现。
#
# ⚠️ 口径（照 HR8，别退化成"看起来绿"）：
#   ① 首行必须是 `RAG PASS`；② 明细里 `FAIL ` 行数必须 == 0；
#   ③ 检索评测表（5 档阈值）必须打出来，且 0.40 档要与 Android 逐项相同
#      （26/30、30/30、0.928、0/3）——这一条由 Kotlin 侧的
#      `rag_eval_threshold_0_40_matches_android` 断言，脚本只负责把表打全。
rag() {
    local wait_s="${1:-90}"
    echo "── 1/3 构建（KMP 产物 + HAP；含 vocab.txt 与 94MB 模型）──"
    bash "$ROOT/scripts/harmony_spike.sh" all || die "构建失败（先看输出里的第一处 error，别用窄 grep 过滤）"

    echo
    echo "── 2/3 安装并启动（等待 ${wait_s}s；首启要复制 94MB 模型 + 跑 12 篇/33 问评测）──"
    "$HDC" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
    "$HDC" shell "hilog -r" >/dev/null 2>&1 || true
    install
    start

    echo
    echo "── 3/3 RAG 结果（原始 hilog）──"
    # ⚠️ **轮询**而不是一次 `sleep` 到位：RAG 要串在所有探针之后（模型会话是串行的），
    #    而 H3 的网络探针可能慢/挂——一次睡死会在"其实早就出结果"或"早就卡住"两种情况下
    #    都浪费掉整个窗口（实测踩到：睡满 400s 后 `hdc` 拉日志的那次调用被后续操作打断，
    #    结果什么都没拿到）。这里每 10s 问一次，拿到就立刻进入抓取阶段。
    local heads="" waited=0
    while [ "${waited}" -lt "${wait_s}" ]; do
        sleep 10
        waited=$((waited + 10))
        heads="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a 'RAG PASS\|RAG FAIL' | tail -3 || true)"
        [ -n "${heads}" ] && break
    done
    echo "   （等待 ${waited}s 后$([ -n "${heads}" ] && echo '拿到结果' || echo '仍未拿到结果')）"
    local detail evalrows
    detail="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a -E 'rag_|EVAL|NOTE' | tail -60 || true)"
    evalrows="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a 'EVAL-TABLE' | tail -10 || true)"
    echo "── 头部 ──"
    echo "${heads}"
    echo "── 明细（截尾 60 行）──"
    echo "${detail}"
    [ -n "${heads}" ] || die "没抓到 RAG 行——检查 ${wait_s}s 够不够（冷启动要复制 94MB 模型），或先跑 bash scripts/harmony.sh log"
    echo
    echo "── 检索评测表（阈值 → Hit@1 / Hit@3 / MRR / 误召回）──"
    echo "${evalrows}"

    if ! echo "${heads}" | grep -aq 'RAG PASS'; then
        die "❌ RAG 自检未通过（明细见上）"
    fi
    local nfail
    nfail="$(echo "${detail}" | grep -ac 'FAIL ' || true)"
    [ "${nfail}" = "0" ] || die "❌ 明细里有 ${nfail} 个 FAIL 项"
    echo
    echo "✅ RAG 自检通过：$(echo "${heads}" | sed 's/.*SEKB_KN_SPIKE: //' | tail -1)  明细 FAIL=0"
    [ -n "${evalrows}" ] || echo "⚠️ 没抓到 EVAL-TABLE 行（评测可能未跑完，检查 wait_s）"
}

# ── H3（网络端口）+ H4（HUKS 凭证端口）自检 ─────────────────────────────────
# 一条命令跑**两轮**，因为 H4 的"重启后仍有效"只能靠两轮对比证明：
#   第 1 轮：新装后首跑 → HUKS 生成/复用密钥、凭证写盘；`credential_restart` 记首轮状态
#   第 2 轮：`aa force-stop` 后**不重装**再起 → `load()` 必须读回上一轮写的 device_id
# 每一轮的判据都要求：首行 `H3 PASS n/n` / `CRED PASS n/n`，且明细里 **FAIL 行数 = 0**。
#
# ⚠️ 网络的两条**反向验证**写在应用内（不是脚本侧）：
#   `transport_refuses_bad_endpoint`（没人监听的端口）与 `transport_refuses_404`
#   （能连上但路径不存在）—— 期望都是**非 2xx**。它们若变绿说明判据是摆设。
#   为什么不做"脚本把 URL 改坏再跑一遍"：应用沙箱是 `/data/app/el2/100/base/<bundle>/...`，
#   `hdc shell` 是 shell 用户（uid=2000），**写不进去**（实测 `Permission denied`），
#   而 `param set` 也被拒（errNum 1001）；所以反向验证留在应用内，用真实坏端点取证。
net() {
    local wait_s="${1:-20}"

    echo "── 0/4 HUKS 头同步（H4 cinterop 的编译输入，逐字节来自 sysroot）──"
    sync_huks_headers >/dev/null || die "HUKS 头同步失败"
    echo "   ✅ 4 个头文件已就位（详见 bash scripts/harmony.sh sync-huks-headers）"

    echo
    echo "── 1/4 构建（KMP 产物 + HAP）──"
    bash "$ROOT/scripts/harmony_spike.sh" all || die "构建失败（先看输出里的第一处 error，别用窄 grep 过滤）"

    local port_pid=""
    ollama_forward() {
        local host="${SEKB_OLLAMA_HOST:-127.0.0.1}"
        local port="${SEKB_OLLAMA_PORT:-11434}"
        if curl -s -m 3 -o /dev/null "http://${host}:${port}/api/tags"; then
            "$HDC" rport "tcp:${port}" "tcp:${port}" >/dev/null 2>&1 \
                && echo "   ✅ 宿主 Ollama（${host}:${port}）已 rport 到设备同端口" \
                || echo "   ⚠️ hdc rport 失败（设备侧将依赖 10.0.2.2 网关别名）"
        else
            echo "   ⚠️ 宿主 ${host}:${port} 上没有 Ollama —— H3 的 transport_* 会 FAIL（不是 SKIP：这是环境缺失，脚本会如实报红）"
        fi
    }

    round() {
        local round_no="$1" label="$2"
        echo
        echo "── ${round_no}/4 ${label} ──"
        "$HDC" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
        "$HDC" shell "hilog -r" >/dev/null 2>&1 || true
        if [ "${round_no}" = "2" ]; then
            # 不重装：验"进程重启后凭证仍有效"
            start
        else
            install
            start
        fi
        sleep "${wait_s}"
        # ⚠️ 轮询等两行头部（实测踩过：H3 先出、CRED 是一百多毫秒之后才打完，
        #    单次抓取会抓到空的 CRED 行 —— 那是**抓取竞态**，不是自检失败）。
        # 取**全部**头部行（不是 tail -1）：H3 有三段报告（GET / 坏端点 / 404 / 流式），
        # 只看最后一行会把前面几段的状态藏起来——UI/凭证那轮就踩过"首行 PASS、后面有 FAIL"。
        local h3 cred tries
        h3=""; cred=""
        for tries in 1 2 3 4 5 6 7 8; do
            h3="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -aoE 'H3 (PASS|FAIL) [0-9]+/[0-9]+' | sort -u | tr '\n' ';' || true)"
            cred="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -aoE 'CRED (PASS|FAIL) [0-9]+/[0-9]+' | sort -u | tr '\n' ';' || true)"
            if [ -n "${h3}" ] && [ -n "${cred}" ]; then break; fi
            sleep 2
        done
        # 明确的红：任何一段 H3/CRED 报 FAIL 都不放过（不是只看最后一行）
        if echo "${h3}" | grep -aq 'H3 FAIL'; then
            echo "   ❌ 有 H3 段报 FAIL：${h3}"; bad_h3=1
        fi
        if echo "${cred}" | grep -aq 'CRED FAIL'; then
            echo "   ❌ 有 CRED 段报 FAIL：${cred}"; bad_h3=1
        fi
        echo "   H3:   ${h3}"
        echo "   CRED: ${cred}"
        local d3 dc nfail
        d3="$("$HDC" shell "hilog -x" 2>/dev/null | grep -a "$TAG" | grep -a -E 'PASS (transport_|huks_|credential_|layout_)' | tail -30 || true)"
        echo "   ── 明细 ──"
        echo "${d3}" | sed 's/^/   /'
        nfail="$(echo "${d3}" | grep -ac 'FAIL ' || true)"
        LAST_H3="${h3}"; LAST_CRED="${cred}"; LAST_NFAIL="${nfail}"
    }

    ollama_forward
    LAST_NFAIL=0
    bad_h3=0
    LAST_H3=""; LAST_CRED=""
    round 1 "第 1 轮：首跑（HUKS 生成/复用密钥 + 凭证写盘）"
    local r1_h3="${LAST_H3}" r1_cred="${LAST_CRED}"
    round 2 '第 2 轮：force-stop 后重启、**不重装**（验「重启后仍有效」）'
    local r2_h3="${LAST_H3}" r2_cred="${LAST_CRED}" nfail="${LAST_NFAIL}"

    echo
    echo "── 判定 ──"
    local bad=0
    if [ -n "${r1_h3}" ] && ! echo "${r1_h3}" | grep -aq 'H3 FAIL'; then
        echo "   ✅ H3 首轮：${r1_h3}"
    else
        echo "   ❌ H3 首轮没有 PASS 行（行='${r1_h3}'）"; bad=1
    fi
    if [ -n "${r2_h3}" ] && ! echo "${r2_h3}" | grep -aq 'H3 FAIL'; then
        echo "   ✅ H3 次轮：${r2_h3}"
    else
        echo "   ❌ H3 次轮没有 PASS 行（行='${r2_h3}'）"; bad=1
    fi
    if [ -n "${r2_cred}" ] && ! echo "${r2_cred}" | grep -aq 'CRED FAIL'; then
        echo "   ✅ H4：${r2_cred}"
    else
        echo "   ❌ H4 没有 PASS 行（行='${r2_cred}'）"; bad=1
    fi
    [ "${nfail}" = "0" ] || { echo "   ❌ 明细里有 ${nfail} 个 FAIL 项（见上）"; bad=1; }
    [ "${bad_h3}" = "0" ] || { echo "   ❌ 有 H3/CRED 段报 FAIL（见上）"; bad=1; }
    [ "${bad}" = "0" ] || die "❌ H3/H4 自检未通过（原始输出见上；**不要**把 FAIL 当 SKIP 放过）"
    echo
    echo "✅ H3（网络端口）+ H4（HUKS 凭证端口）自检通过（明细 FAIL=0）"
}

require_hdc

# ── H8（工作流 G）：四页 UI 的自检表 + 截图取证 ────────────────────────────────
# 为什么单独一个动作而不是改 `selftest`：`selftest` 是 C/H1–H6 的自检面板
# （TAG=SEKB_KN_SPIKE、入口 pages/Index），**别人在用**。H8 的自检表有自己的 TAG、
# 自己的入口（EntryAbility 收到 action 后在进程内直接跑 `runSelfTest`），互不干扰。
#
# ⚠️ `-A` 与 `-a/-b/-m` 的**顺序有讲究**：实测 `aa start -A SEKB_SELFTEST -b ... -a ...`
#    会把 action 丢掉（onCreate 收到空 action），必须写成 `-a … -b … -m … -A …`。
H8_TAG="SEKB_HARMONY_SELFTEST"

h8_selftest() {
    local wait_s="${1:-100}" red="${2:-}"
    local action="SEKB_SELFTEST"
    [ "$red" = "red" ] && action="SEKB_SELFTEST_RED"
    require_target
    echo "── H8 自检（action=${action}，等待 ${wait_s}s 抓 hilog TAG=${H8_TAG}）──"
    "$HDC" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
    "$HDC" shell "hilog -r" >/dev/null 2>&1 || true
    "$HDC" shell "aa start -a $ABILITY -b $BUNDLE -m entry -A ${action}" || die "启动失败"
    sleep "$wait_s"
    local out
    out="$("$HDC" shell "hilog -x -T ${H8_TAG}" 2>/dev/null | sed 's/.*SEKB_HARMONY_SELFTEST//')"
    echo "$out"
    echo
    echo "── 判定 ──"
    local summary
    # ⚠️ 上面的 `sed` 已经把 `SEKB_HARMONY_SELFTEST` 前缀去掉了（它同时也是 hilog 的 tag），
    #    所以这里匹配的是 **`PASS=n FAIL=n SKIP=n`** 这一段，不要再带上那个前缀。
    summary="$(echo "$out" | grep -aE 'PASS=[0-9]+ FAIL=[0-9]+ SKIP=[0-9]+' | tail -1 | tr -d '\r')"
    [ -n "$summary" ] || die "没抓到汇总行（自检没跑完？把等待秒数调大：bash scripts/harmony.sh h8selftest 180）"
    echo "   SEKB_HARMONY_SELFTEST ${summary}"
    local fails
    fails="$(echo "$summary" | sed -n 's/.*FAIL=\([0-9]*\).*/\1/p')"
    if [ "$red" = "red" ]; then
        # 反向验证：**必须**恰好 1 个 FAIL（见 SelfTestRunner.stepRedCheck）
        [ "$fails" = "1" ] || die "反向验证失败：期望恰好 1 个 FAIL，实测 FAIL=${fails}（表可能是摆设）"
        echo "   ✅ 反向验证成立：注入错期望值后恰好 1 个 FAIL=${fails}"
    else
        [ "$fails" = "0" ] || die "H8 自检有 ${fails} 个 FAIL（明细见上）"
        echo "   ✅ H8 自检 FAIL=0"
    fi
}

# 四页截图（每页一张）。⚠️ **不靠点屏幕**：模拟器上 `uitest uiInput click` 的坐标与页面
# 坐标实测对不上（点标签栏会落到滑杆上），所以用 `SEKB_PAGE_*` 动作**确定性地**开页。
h8_shots() {
    require_target
    local dir="$ROOT/apps/harmony/docs/screenshots"
    mkdir -p "$dir"
    local -a pages=(CHAT HISTORY DOCS SETTINGS)
    local -a names=(ui-chat-empty ui-history ui-documents ui-settings)
    echo "── H8 四页截图 → ${dir} ──"
    for i in "${!pages[@]}"; do
        local action="SEKB_PAGE_${pages[$i]}" name="${names[$i]}"
        "$HDC" shell "aa force-stop $BUNDLE" >/dev/null 2>&1 || true
        sleep 2
        "$HDC" shell "aa start -a $ABILITY -b $BUNDLE -m entry -A ${action}" >/dev/null || die "启动 ${action} 失败"
        sleep "${H8_SHOT_WAIT:-25}"
        "$HDC" shell "snapshot_display -f /data/local/tmp/${name}.jpeg" >/dev/null || die "截图失败 ${name}"
        "$HDC" file recv "/data/local/tmp/${name}.jpeg" "$dir/${name}.jpeg" >/dev/null || die "取回失败 ${name}"
        echo "   ✅ ${name}.jpeg（${action}）"
    done
}

case "${1:-}" in
    build)    build ;;
    install)  require_target; install ;;
    start)    require_target; start ;;
    selftest) require_target; selftest "${2:-}" ;;
    smoke)    require_target; smoke "${2:-}" ;;
    contract) require_target; contract "${2:-}" ;;
    contract-red) require_target; contract_red_fixture "${2:-}" ;;
    probe)    require_target; probe "${2:-}" ;;
    embed)    require_target; embed "${2:-}" ;;
    net)      require_target; net "${2:-}" ;;
    sync-huks-headers) sync_huks_headers ;;
    # H5（工作流 F）：RDB 探针 / RAG 自检 + 检索评测
    rdbprobe) require_target; rdb_probe "${2:-}" ;;
    rdbid)    require_target; rdb_id_probe "${2:-}" ;;
    # H8（工作流 G）：四页 UI 的自检表（TAG=SEKB_HARMONY_SELFTEST）+ 反证 + 截图
    h8selftest)     h8_selftest "${2:-}" ;;
    h8selftest-red) h8_selftest "${2:-}" red ;;
    h8shots)        h8_shots ;;
    rag)      require_target; rag "${2:-}" ;;
    log)      require_target; log ;;
    crash)    require_target; crash "${2:-}" ;;
    all)      require_target; build && install && selftest "${2:-}" ;;
    # 校验 target 的动作都要联网检查，所以放在 case 内做
    *) echo "用法：bash scripts/harmony.sh [build|install|start|selftest|smoke|contract|contract-red|probe|embed|rdbprobe|rag|h8selftest|h8selftest-red|h8shots|log|crash|all]" >&2; exit 2 ;;
esac

# 说明：这里**不做** `require_target` 的统一前置检查，因为 `build` 是纯本机构建、
# 不需要设备（CI/无模拟器时也能跑）。需要设备的动作各自检查。
