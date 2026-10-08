#!/usr/bin/env bash
# ============================================================
# iOS App 构建/安装/启动（M5 第二步）
# ============================================================
# 为什么不用 .xcodeproj：手写 pbxproj 又长又脆，而这一步要证明的是
# 「SwiftUI + SharedCore.framework 能在模拟器上跑起来」——用 swiftc 直编 + 手工 .app 包
# 完全够用，而且全部可脚本化、可复现。等要接入真实签名/发布时再上 Xcode 工程。
#
# 用法：
#   bash scripts/ios_app.sh build     # 产出 .tooling/tmp/ios-app/Sekb.app
#   bash scripts/ios_app.sh run       # build + 装到已启动的模拟器 + 启动（打印自检输出）
#   bash scripts/ios_app.sh boot      # 启动一个 iPhone 模拟器（默认 iPhone 17 Pro）
#   bash scripts/ios_app.sh ui        # build + 装 + 逐页截图到 apps/ios/docs/screenshots/（视觉验证）
#   bash scripts/ios_app.sh screenshot <名字>   # 把模拟器当前画面存到 apps/ios/docs/screenshots/<名字>.png
#
# 关于 `ui`：无人值守下没法点界面，所以它靠两个**只读环境变量**把应用"摆"到要拍的状态：
#   `SEKB_INITIAL_SCREEN`（chat|history|documents|settings）→ 启动时停在那一页
#   `SEKB_DEMO_SEED=1`                                   → 写入演示数据（见 SekbDemoSeed.swift）
# 两者都默认关闭，正常使用不受影响。
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

# ── 端侧嵌入（M5 端口③）：ONNX Runtime 静态 framework + bge-small-zh 模型 ──────────────
#
# ORT 的 iOS 产物**不在 GitHub**（GitHub 在本机不可达），而在 download.onnxruntime.ai：
#   curl -L -o ort-c-1.20.0.zip \
#     https://download.onnxruntime.ai/pod-archive-onnxruntime-c-1.20.0.zip
# 解压后是 `onnxruntime.xcframework`，三切片：ios-arm64 / ios-arm64_x86_64-simulator / macos-arm64_x86_64。
# 它是**静态** framework（`ar archive`），所以不需要 embed 进 .app、不需要 rpath，直接链接即可。
ORT_XCF="$ROOT/.tooling/ort-ios/extracted/onnxruntime.xcframework"
ORT_SIM="$ORT_XCF/ios-arm64_x86_64-simulator"
MODEL_ROOT="$ROOT/.tooling/models"

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
    #
    # `SEKB_SKIP_FRAMEWORK_BUILD=1` 复用已有的 framework 而不重编共享层。
    # 为什么需要这个开关：**共享工作区里别人可能正在改 `apps/shared/**`**，
    # 那一刻 gradle 编不过（实测踩到：别人编辑到一半，`:shared:compileKotlin*` 语法错误），
    # 而"我只想验自己这一端"这件事不该被别人的中间状态卡住。跳过时会**打印 framework 时间戳**，
    # 避免把陈旧产物当成新构建（默认关闭，正常路径仍然重编）。
    if [ "${SEKB_SKIP_FRAMEWORK_BUILD:-0}" = "1" ]; then
        echo "⚠️ 跳过共享层构建，复用已有 framework：$FW_IOS/SharedCore.framework（$(stat -f '%Sm' "$FW_IOS/SharedCore.framework" 2>/dev/null)）"
    else
        ( cd "$ROOT/apps/android" && ./gradlew --no-daemon -PsekbNativeTargets=true \
            :shared:linkDebugFrameworkIosSimulatorArm64 --console=plain -q ) || return 1
    fi
    [ -d "$FW_IOS/SharedCore.framework" ] || { echo "缺少 framework：$FW_IOS" >&2; return 1; }

    # 2) 编译 SwiftUI 源码（静态 framework：直接 -F 指目录 + -framework）
    rm -rf "$APP_BUNDLE"; mkdir -p "$APP_BUNDLE"
    local ort_flags=()
    if [ -d "$ORT_SIM/onnxruntime.framework" ]; then
        # `-lc++`：ORT 的静态库内部是 C++，只看 C 头文件会漏掉运行时依赖
        ort_flags=( -F "$ORT_SIM" -framework onnxruntime -lc++
                    -import-objc-header "$ROOT/apps/ios/App/SekbOrtBridge.h" )
        echo "✅ 链接 ONNX Runtime：$ORT_SIM"
    else
        # 缺 ORT 不阻塞其它端口：`OrtBgeEmbedding.swift` 里对 ORT 的引用会编译失败，
        # 所以这里**不是**"优雅降级"而是明确报错——避免产出一个"看起来能跑但没有嵌入"的包。
        echo "❌ 缺少 ONNX Runtime：$ORT_SIM" >&2
        echo "   获取方式见 scripts/ios_app.sh 顶部注释（download.onnxruntime.ai，不需要 GitHub）" >&2
        return 1
    fi
    # `-parse-as-library` 是必须的：单文件 swiftc 会按"脚本模式"编译，
    # 于是 `@main` 报 "'main' attribute cannot be used in a module that contains top-level code"（实测）
    xcrun swiftc -O -parse-as-library -sdk "$(xcrun --sdk iphonesimulator --show-sdk-path)" \
        -target "$TARGET" -module-cache-path "$TMPDIR/modulecache" \
        -F "$FW_IOS" -framework SharedCore \
        "${ort_flags[@]}" \
        -o "$APP_BUNDLE/$APP_NAME" \
        $ROOT/apps/ios/App/*.swift || return 1
    cp "$ROOT/apps/ios/App/Info.plist" "$APP_BUNDLE/Info.plist"
    # 端侧模型随包分发（bundle 只读但一定可读；Android 那边是 run-as 写内部目录）
    for v in bge-small-zh-v1.5 bge-small-zh-v1.5-int8; do
        if [ -f "$MODEL_ROOT/$v/model.onnx" ] && [ -f "$MODEL_ROOT/$v/vocab.txt" ]; then
            mkdir -p "$APP_BUNDLE/models/$v"
            cp "$MODEL_ROOT/$v/model.onnx" "$MODEL_ROOT/$v/vocab.txt" "$APP_BUNDLE/models/$v/"
            # ⚠️ 必须写 `${v}` 而不是 `$v`：紧跟中文全角括号时，bash 会把多字节字符的
            # 首字节当成变量名的一部分 → `v\xef: unbound variable`（实测踩到）
            echo "✅ 已放入模型 ${v}（$(du -h "$MODEL_ROOT/$v/model.onnx" | cut -f1)）"
        fi
    done
    # ⚠️ **故意不做 ad-hoc 签名**（2026-09-21 实测结论）：
    #   · 手搓未签名的 .app 调 Keychain → `SecItemAdd` 返回 **-34018 (errSecMissingEntitlement)**；
    #   · 但一旦 `codesign -s - --entitlements ...`（哪怕只带 `application-identifier`，
    #     或带 `keychain-access-groups`），模拟器 SpringBoard 就直接**拒绝启动**
    #     （`FBSOpenApplicationServiceErrorDomain code=1, denied by service delegate`）。
    # 所以两条路互斥：要么"能启动但 Keychain 不可用"，要么"Keychain 可用但起不来"。
    # 当前选择保住"能启动"（其余自检项都依赖它），Keychain 往返**如实记为未验**，
    # 待接入真实 Xcode 工程 + 签名身份（或真机）时补验。entitlements 文件留在
    # `apps/ios/App/Sekb.entitlements` 备用。
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
        # **先把日志流接上再启动 App**：`simctl launch` 之后才开始抓的话，会漏掉最开始那几行。
        # 为什么用 `log stream` 而不是 `log show --last`：后者要把时间窗内的历史日志整段读出来，
        # 在本机负载高、日志库大的时候实测要**几十分钟**（本工作流真踩到：一次 run 卡了 54 分钟，
        # 最后还因为窗口过期而报"没抓到总结行"）。`log stream` 是增量输出，秒级返回。
        # 不用 `--console-pty`：受限环境里分配 pty 会失败（实测 "Unable to open pty: Error 1"）。
        STREAM_LOG="$TMPDIR/ios-selftest-stream.log"
        : > "$STREAM_LOG"
        # ⚠️ **不要**套一层 `( ... ) &` 子 shell：那样 `$!` 是子 shell 的 PID，kill 掉它之后
        # 真正的 `xcrun ... log stream` 会变成孤儿继续跑（实测：日志文件一直在长，
        # 外层调用也会因为还挂着这个子进程而迟迟不返回）。直接后台起 `xcrun`，拿它自己的 PID。
        xcrun simctl spawn "$DEV" log stream --style compact \
            --predicate 'process == "Sekb"' >"$STREAM_LOG" 2>/dev/null &
        STREAM_PID=$!
        # 必须先 terminate：`simctl launch` 对**已在运行**的 App 只是切到前台，
        # `onAppear` 不会再触发 → 无人值守时抓不到自检输出（实测踩过）。
        xcrun simctl terminate "$DEV" "$BUNDLE_ID" >/dev/null 2>&1 || true
        xcrun simctl uninstall "$DEV" "$BUNDLE_ID" >/dev/null 2>&1 || true
        xcrun simctl install "$DEV" "$APP_BUNDLE" || { kill "$STREAM_PID" 2>/dev/null; exit 1; }
        # 数据目录必须落在仓库内：模拟器进程继承本机文件策略，`~/Library/Application Support`
        # 与 CoreSimulator 的容器目录实测都是 `Operation not permitted`（写不进去 →
        # 会话/索引静默不落盘，"重启后历史还在吗"就验不了）。
        # `SIMCTL_CHILD_*` 是 simctl 给被启动进程注入环境变量的方式。
        SIMCTL_CHILD_SEKB_DATA_DIR="${SEKB_IOS_DATA_DIR:-$TMPDIR/ios-app-data}" \
            xcrun simctl launch "$DEV" "$BUNDLE_ID" >/dev/null || { kill "$STREAM_PID" 2>/dev/null; exit 1; }
        # 自检里含真实网络调用（宿主 Ollama 冷加载可能十几秒）**加上端侧真模型的检索评测**
        # （12 篇语料 + 33 条问题 × 5 个阈值，模拟器上 ONNX 单条 30–60ms）→ 等它跑完
        sleep "${SEKB_IOS_WAIT:-150}"
        kill "$STREAM_PID" 2>/dev/null
        wait "$STREAM_PID" 2>/dev/null
        RAW="$(grep -E "SEKB_IOS_SELFTEST" "$STREAM_LOG" 2>/dev/null)"
        if ! printf '%s' "$RAW" | grep -qE "SEKB_IOS_SELFTEST PASS="; then
            # 兜底：流式抓取被环境限制时（例如 `log stream` 被拒）回到历史查询这条路
            echo "（log stream 没抓到总结行，回退 log show --last 300s）" >&2
            RAW="$(xcrun simctl spawn "$DEV" log show --last 300s --style compact \
                --predicate 'process == "Sekb"' 2>/dev/null | grep -E "SEKB_IOS_SELFTEST")"
        fi
        # 以**最后一个 `=== RUN START ===` 标记**为界：日志窗口里可能残留上一次运行的条目
        # （上一次是 FAIL、这一次是 PASS 时，混在一起看起来像"同一轮自相矛盾"——实测踩到）
        OUT="$(printf '%s\n' "$RAW" | awk '/=== RUN START ===/{buf=""} {buf=buf $0 "\n"} END{printf "%s", buf}')"
        [ -n "$OUT" ] || OUT="$RAW"
        printf '%s\n' "$OUT" || {
                echo "（未从日志抓到自检输出；把模拟器界面切到前台可看到 List 中的结果）"
                exit 1
            }
        # 只要没抓到总结行就算失败——避免"跑起来了但自检没跑完"被当成成功
        printf '%s\n' "$OUT" | grep -qE "SEKB_IOS_SELFTEST PASS=" || {
            echo "❌ 抓到了日志但没有自检总结行（自检可能中途崩溃）" >&2; exit 1; }
        ;;
    screenshot)
        NAME="${2:-screen}"
        [ -n "${SEKB_IOS_DEVICE:-}" ] || SEKB_IOS_DEVICE="$(boot_sim)" || exit 1
        OUT_DIR="$ROOT/apps/ios/docs/screenshots"; mkdir -p "$OUT_DIR"
        xcrun simctl io "$SEKB_IOS_DEVICE" screenshot --type=png "$OUT_DIR/$NAME.png" || exit 1
        echo "✅ 截图：$OUT_DIR/$NAME.png"
        ;;
    ui)
        # ── 视觉验证：逐页截图（空状态 + 有数据两种状态）─────────────────────────────
        build_app || exit 1
        DEV="$(boot_sim)" || exit 1
        echo "模拟器：$DEV"
        OUT_DIR="$ROOT/apps/ios/docs/screenshots"; mkdir -p "$OUT_DIR"
        EMPTY_DATA="$TMPDIR/ios-ui-empty"
        DEMO_DATA="$TMPDIR/ios-ui-demo"
        # 每次都从零开始：空状态截图必须**真的是空的**
        rm -rf "$EMPTY_DATA" "$DEMO_DATA"
        xcrun simctl terminate "$DEV" "$BUNDLE_ID" >/dev/null 2>&1 || true
        xcrun simctl uninstall "$DEV" "$BUNDLE_ID" >/dev/null 2>&1 || true
        xcrun simctl install "$DEV" "$APP_BUNDLE" || exit 1

        shoot() {  # $1=文件名 $2=页面 $3=数据目录 $4=种子(0/1) $5=等待秒数
            local name="$1" page="$2" data="$3" seed="$4" wait="${5:-8}"
            xcrun simctl terminate "$DEV" "$BUNDLE_ID" >/dev/null 2>&1 || true
            SIMCTL_CHILD_SEKB_DATA_DIR="$data" \
            SIMCTL_CHILD_SEKB_INITIAL_SCREEN="$page" \
            SIMCTL_CHILD_SEKB_DEMO_SEED="$seed" \
                xcrun simctl launch "$DEV" "$BUNDLE_ID" >/dev/null || return 1
            sleep "$wait"
            xcrun simctl io "$DEV" screenshot --type=png "$OUT_DIR/$name.png" >/dev/null || return 1
            echo "✅ $name.png（页面=$page 种子=$seed 数据目录=$(basename "$data")）"
        }
        # 1) 空状态：**关键回归点**——空状态不能把底部输入栏挤出屏幕
        #    （Android 真机上正是这个 bug：整屏只有空状态、用户没法输入）
        shoot "01-chat-empty"   chat      "$EMPTY_DATA" 0 6
        # 2) 有数据的四页：Markdown / 引用来源 / 执行位置徽章 / 历史 / 文档 / 设置
        shoot "02-chat"         chat      "$DEMO_DATA"  1 10
        shoot "03-history"      history   "$DEMO_DATA"  1 6
        shoot "04-documents"    documents "$DEMO_DATA"  1 6
        shoot "05-settings"     settings  "$DEMO_DATA"  1 "${SEKB_UI_SETTINGS_WAIT:-20}"
        echo "自检日志另取：bash scripts/ios_app.sh run"
        ;;
    *) echo "用法：bash scripts/ios_app.sh [boot|build|run|ui|screenshot <名字>]" >&2; exit 2 ;;
esac
