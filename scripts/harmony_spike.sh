#!/usr/bin/env bash
# ============================================================
# M6 spike 第 1 条：构建 HAP 并验证「KMP 产物被 HAP 通过 NAPI 调用」
# ============================================================
# 链路：ArkTS `import knspike from 'libknspike.so'` → NAPI 模块 `knspike`
#       → （C++ 里声明为 undefined 的 C 符号）→ `libkn.so`（Kotlin/Native 产物）
#
# 用法：
#   bash scripts/harmony_spike.sh kn      # 只编 KMP 产物（libkn.so，ohosArm64 + ohosX64）
#   bash scripts/harmony_spike.sh hap     # 编 HAP（会先确保 libkn.so 就位）+ 检查链路
#   bash scripts/harmony_spike.sh all     # kn + hap
#
# ⚠️ 三处「不这么做就跑不起来」的坑（都实测踩过，改脚本前先读）：
#
# 1. **hvigor 默认往 `~/.hvigor` 写**，在受限沙箱（如 DSH 的 workspace-write）里直接 EPERM。
#    解法：`HVIGOR_USER_HOME` 指到仓库内 `.tooling/`。这跟 `scripts/android.sh` 把
#    GRADLE_USER_HOME/ANDROID_USER_HOME 收进仓库是同一个道理——**构建状态不落仓库外**。
# 2. **hvigor 会 `npm install -g pnpm`**，而 npm 默认写 `~/.npm` → 同样 EPERM。
#    解法：把 `HOME`、`npm_config_cache`、`npm_config_prefix`、`npm_config_userconfig`
#    一并指到仓库内。**只在这个脚本里覆盖 HOME**，不要全局改（签名那步要用真实 HOME 找 `~/.ohos`）。
# 3. **`PackageHap` 需要 JDK**，DevEco 自带的是 `Contents/jbr`；不设 `JAVA_HOME` 会报
#    "Unable to locate a Java Runtime"（而且失败发生在 native 编译**成功之后**，容易误判成编译问题）。
#
# 另外：`libs/<abi>/libkn.so` 必须与 `entry/src/main/cpp/CMakeLists.txt` 里的
# `IMPORTED_LOCATION`（`../../../libs/${OHOS_ARCH}/libkn.so`）对得上——改目录要同步改。
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVECO="${DEVECO_HOME:-/Applications/DevEco-Studio.app/Contents}"
SPIKE="$ROOT/apps/harmony/spike"
HAP_PROJ="$SPIKE/hapshell"

export NODE_HOME="$DEVECO/tools/node"
export PATH="$NODE_HOME/bin:$PATH"
export DEVECO_SDK_HOME="$DEVECO/sdk"
export JAVA_HOME="$DEVECO/jbr/Contents/Home"
export TMPDIR="$ROOT/.tooling/tmp"
export HVIGOR_USER_HOME="$ROOT/.tooling/hvigor-home"
export HOME="$HVIGOR_USER_HOME/user"
export npm_config_cache="$HVIGOR_USER_HOME/npm-cache"
export npm_config_prefix="$HVIGOR_USER_HOME/npm-global"
export npm_config_userconfig="$HVIGOR_USER_HOME/npmrc"
mkdir -p "$TMPDIR" "$HVIGOR_USER_HOME" "$HOME" "$npm_config_cache" "$npm_config_prefix"

# KMP 侧：Kotlin/Native 需要 JAVA_HOME（Gradle）+ 仓库内 KONAN_DATA_DIR
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$ROOT/.tooling/gradle-home}"
export KONAN_DATA_DIR="${KONAN_DATA_DIR:-$ROOT/.tooling/konan}"

ACTION="${1:-all}"

build_kn() {
    echo "── 1/2 编译 KMP 产物（libkn.so：ohosArm64 真机 + ohosX64 模拟器）──"
    grep -q 'android' "$SPIKE/settings.gradle.kts" 2>/dev/null || true
    ( cd "$SPIKE" && ./gradlew --no-daemon \
        :knspike:linkDebugSharedOhosArm64 :knspike:linkDebugSharedOhosX64 --console=plain -q ) || return 1
    for abi_dir in ohosArm64:arm64-v8a ohosX64:x86_64; do
        local from="${abi_dir%%:*}" to="${abi_dir##*:}"
        local so="$SPIKE/knspike/build/bin/$from/debugShared/libkn.so"
        [ -f "$so" ] || { echo "❌ 缺少 $so" >&2; return 1; }
        mkdir -p "$HAP_PROJ/entry/libs/$to"
        cp "$so" "$HAP_PROJ/entry/libs/$to/libkn.so"
        echo "   ✅ libs/$to/libkn.so（$(du -h "$so" | cut -f1)）"
    done
}

build_hap() {
    echo "── 2/2 构建 HAP（hvigor）──"
    ( cd "$HAP_PROJ" && "$DEVECO/tools/hvigor/bin/hvigorw" \
        assembleHap --mode module -p product=default --no-daemon ) || return 1
}

verify_link() {
    echo
    echo "── 验证：HAP 内的 NAPI 模块是否真的引用 KMP 符号 ──"
    local hap
    hap="$(find "$HAP_PROJ/entry/build" -name '*.hap' 2>/dev/null | head -1)"
    [ -n "$hap" ] || { echo "❌ 没找到 HAP 产物" >&2; return 1; }
    echo "HAP: ${hap#$ROOT/}（$(du -h "$hap" | cut -f1)）"

    local nm="$DEVECO/sdk/default/openharmony/native/llvm/bin/llvm-nm"
    local tmp; tmp="$(mktemp -d)"
    unzip -q -o "$hap" -d "$tmp" 'libs/*' 2>/dev/null
    local ok=0
    for abi in arm64-v8a x86_64; do
        # 三个断言缺一不可：
        #   a) libknspike.so 存在（NAPI 模块编出来了）
        #   b) libkn.so 同包（KMP 产物被真的打进 HAP，而不是只拷到工程目录）
        #   c) libknspike.so 里 sekb_spike_* 是 **undefined**（U）——说明它不自己实现，
        #      而是在加载期由 libkn.so 解析。三者同时成立才叫"链路接上了"。
        local spike_so="$tmp/libs/$abi/libknspike.so" kn_so="$tmp/libs/$abi/libkn.so"
        [ -f "$spike_so" ] || { echo "❌ $abi: 缺 libknspike.so" >&2; ok=1; continue; }
        [ -f "$kn_so" ]    || { echo "❌ $abi: HAP 里没有 libkn.so（KMP 产物没进包）" >&2; ok=1; continue; }
        local undef
        undef="$("$nm" -D --undefined-only "$spike_so" 2>/dev/null | grep -cE 'sekb_spike_(ping|echo_len)')"
        if [ "$undef" = "2" ]; then
            echo "   ✅ $abi: libknspike.so 引用 sekb_spike_ping/echo_len（undefined，由同包 libkn.so 解析）"
        else
            echo "   ❌ $abi: 只找到 $undef/2 个 KMP 符号引用" >&2; ok=1
        fi
    done
    rm -rf "$tmp"
    [ "$ok" = "0" ] || return 1
    echo
    echo "✅ spike 第 1 条（构建 + 链接层面）通过。"
    echo "   ⚠️ 注意口径：「能装能起 + 真的调通」属于 spike 第 4 条，需要签名 HAP + 设备/模拟器，"
    echo "      本脚本只证明到「产物齐备且 NAPI↔KMP 引用成立」——不要把这两件事混为一谈。"
}

verify_mindspore() {
    echo
    echo "── 验证：MindSpore Lite 是否真能用（spike 第 3 条的可行性）──"
    # 背景：原方案要"自建 ONNX Runtime for OHOS"，实测方向错误——Kotlin/Native 的 OHOS 工具链
    # **已预置** HarmonyOS 的 MindSpore Lite 平台绑定（`platformDef/ohos_*/MindSpore.def`，
    # `package = platform.MindSporeLiteKit.MindSpore`、`linkerOpts = -lmindspore_lite_ndk`），
    # 所以不需要碰 GitHub、也不需要往 HAP 里塞几十 MB 的 .so。
    local nm="$DEVECO/sdk/default/openharmony/native/llvm/bin/llvm-nm"
    local re
    re="$(dirname "$nm")/llvm-readelf"
    local ok=0
    for target in ohosArm64 ohosX64; do
        local so="$SPIKE/knspike/build/bin/$target/debugShared/libkn.so"
        [ -f "$so" ] || { echo "❌ 缺少 $so（先跑 kn）" >&2; ok=1; continue; }
        echo "-- $target"
        # a) 探针函数导出成功 → 平台 klib 真的编译链接过了
        if [ "$("$nm" -D --defined-only "$so" 2>/dev/null | grep -cE 'sekb_spike_mindspore')" -ge 1 ]; then
            echo "   ✅ 导出 sekb_spike_mindspore_*"
        else
            echo "   ❌ 没找到 sekb_spike_mindspore_* 导出" >&2; ok=1
        fi
        # b) OH_AI_* 必须是 **weak undefined**（w）：说明由系统在加载期提供，而不是我们自己实现
        local n_oh
        n_oh="$("$nm" -D --undefined-only "$so" 2>/dev/null | grep -cE 'OH_AI_')"
        if [ "$n_oh" -ge 5 ]; then
            echo "   ✅ $n_oh 个 OH_AI_* 为 undefined（运行时由系统提供）"
        else
            echo "   ❌ OH_AI_* undefined 数量=$n_oh（预期 ≥5）" >&2; ok=1
        fi
        # c) DT_NEEDED 必须点名 libmindspore_lite_ndk.so：证明 .def 里的 linkerOpts 真的生效
        if "$re" -d "$so" 2>/dev/null | grep -q 'libmindspore_lite_ndk.so'; then
            echo "   ✅ DT_NEEDED 含 libmindspore_lite_ndk.so（linkerOpts 生效）"
        else
            echo "   ❌ DT_NEEDED 里没有 libmindspore_lite_ndk.so" >&2; ok=1
        fi
    done
    [ "$ok" = "0" ] || return 1
    echo
    echo "✅ spike 第 3 条的**可行性**成立（编译 + 链接层面）。"
    echo "   ⚠️ 仍未完成：① 真跑嵌入需要有设备/模拟器（只有运行期才验证得了）；"
    echo "      ② MindSpore Lite 只吃 .ms（头文件里 OH_AI_MODELTYPE 只有 MINDIR），"
    echo "         还要把 bge-small-zh 从 ONNX 用 converter_lite 转一次——本机与 DevEco 里都**没有**该工具。"
}

case "$ACTION" in
    kn)   build_kn ;;
    hap)  build_hap && verify_link ;;
    all)  build_kn && build_hap && verify_link ;;
    mindspore) build_kn && verify_mindspore ;;
    *) echo "用法：bash scripts/harmony_spike.sh [kn|hap|all|mindspore]" >&2; exit 2 ;;
esac
