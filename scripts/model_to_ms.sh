#!/usr/bin/env bash
# ============================================================
# bge-small-zh：ONNX → MindSpore Lite `.ms`（鸿蒙端侧嵌入的模型依赖）
# ============================================================
# 用法：
#   bash scripts/model_to_ms.sh fetch      # 下载 MindSpore Lite 工具包（Linux aarch64，含 converter）
#   bash scripts/model_to_ms.sh rewrite    # 图重写（去掉 IsNaN）+ 证明数值等价
#   bash scripts/model_to_ms.sh convert    # 在 Docker 里跑 converter_lite → .ms
#   bash scripts/model_to_ms.sh check      # 用 benchmark 实际加载运行 .ms（这一步目前**失败**，见下）
#   bash scripts/model_to_ms.sh all        # fetch + rewrite + convert + check
#
# ⚠️ 写 shell 时注意：「`$` + 变量名 + 全角标点」这种"变量紧挨全角括号"的写法，bash 会把全角括号的
# 首字节当成变量名的一部分，报 `VAR\xef: unbound variable`（本脚本与 scripts/ios_app.sh
# 都实测踩过）。**一律写 `${VAR}`**。
#
# ## 为什么是 Linux-aarch64 的包，而不是 macOS 的
#
# MindSpore Lite **没有 macOS 版 converter**（官方只发 Linux-x86_64 / Linux-aarch64 / Windows-x86_64）。
# 本机是 Apple Silicon，所以用 **Linux-aarch64** 那份 —— 它与宿主同为 arm64，
# 在 arm64 容器里**原生执行、不需要 Rosetta/模拟**（实测：`--platform linux/amd64` 会去拉
# 不存在的镜像并 403，而 aarch64 包用本地已有的 arm64 镜像直接就能跑）。
#
# ## 为什么要做图重写
#
# converter_lite 不认 `IsNaN`（`not support onnx data type IsNaN`），而 HF 导出的 BERT
# 每层注意力都带一个 `Where(IsNaN(softmax), 0, softmax)` 的 NaN 保护。这段在本模型里是
# **死代码**（掩码是加性大负数、不是 -inf，softmax 不可能出 NaN），短路掉即可。
# 重写由 `scripts/model_to_ms.py` 完成，并用 onnxruntime **证明逐位等价**（不是"余弦接近 1"）。
#
# ## ⚠️ 当前状态（2026-09-25 修订）：`.ms` 能转换，但**两个产物都跑不起来**，且失败方式不同
#
# ⚠️ 本节曾把两个产物**混为一谈**，必须分开记——因为结论完全不同：
#   · `bge-small-zh-v1.5.ms`（**动态** shape）：`CONVERT RESULT SUCCESS:0` 产出 .ms，
#     `benchmark` **快速失败**：
#       FindBackendKernel return nullptr, name: /m/Flatten, type: Flatten
#       → Schedule subgraph failed → Build failed → Run Benchmark Failed : -1
#     典型的"上游动态形状没解析出来，下游算子形状未知，选不到 kernel"。
#   · `bge-static.ms`（**静态化**后：Flatten→Reshape + 常量内联；转换成功、CLS 余弦 1.0）：
#     `benchmark` **挂起**——**不是慢，是真的不动**：容器内该进程 CPU 时间恒为 `00:00:00`，
#     带与不带 `--inputShape` 都一样，且容器里还留着 **7 天前**同样挂着的同名进程（可复现）。
#     （此前把这一步记成"600s 无输出、原因不明"，实为 **0 CPU 的真挂起**；
#      而"固定 shape 在转换阶段失败"是**更早**的状态，后来转换已修好。）
#
# **本次同时补上一个测试覆盖缺口**：原 `check` 只跑**动态**那个产物，
# **从没人在 `bge-static.ms` 上跑过 benchmark**（只验过它能转换、余弦对）。
# 现 `check` 已参数化，并新增 `check-static` 专测静态产物：
#       bash scripts/model_to_ms.sh check-static
#
# ## 对 M7 路线判定的含义（需谨慎表述）
#
# **Linux-aarch64 runtime 跑不起来 ≠ 鸿蒙 runtime 跑不起来**：鸿蒙用系统自带的
# `libmindspore_lite_ndk`（另一份构建），其算子集**可能**含 Flatten kernel。
# 但 `bge-static.ms` 那个 **0 CPU 挂起是图/模型层面**的问题（与后端算子集关系不大），
# 大概率在鸿蒙上同样复现。因此性价比排序仍倾向：
#   ① 把 ONNX 里那段**动态掩码展开子图**（Shape/ConstantOfShape/Range/Gather 链）彻底改写成静态等价形式，
#      再转一次 —— 与 `scripts/model_to_ms.py` 已有的重写思路一致，但工作量更大；
#   ② 放弃 MindSpore Lite，回到社区版 OHOS ONNX Runtime（原方案，需找到预编译产物）；
#   ③ 按 RFC §4 D2 退路线 B（ArkTS 重写 + 契约夹具，仓库已有 37 条夹具）。
#
# ## 路线① 的工作量量化（2026-09-25，纯图结构分析，离线可做）
#
# 用 `onnx` 对 `ms-static-1x512.onnx`（静态输入 1×512）做分析，结论有两条关键的：
#
# 1. **`Flatten` 只属于动态那份产物**：静态 ONNX 里 `Flatten` 算子数 = **0**
#    （只有一个**名字**叫 `/m/Flatten`、但 `op_type` 是 `Reshape` 的节点——那是早先重写留下的名字）。
#    所以动态 `.ms` 的 `FindBackendKernel nullptr: /m/Flatten` 来自**源模型自带的 Flatten**，
#    而静态产物的 **0 CPU 挂起与 Flatten 无关，另有原因（尚未定位）**。
# 2. **静态 ONNX 仍带完整动态链**：Shape×9 / ConstantOfShape×2 / Range×3 / Gather×15 / Where×5 / Expand×1
#    ——输入虽已固定 1×512，这段"掩码展开"仍在图里。以 5 个 `Where` 的输出做**反向切片**：
#      · 掩码链总规模：**67 节点**
#      · 其中**不被 `input_ids` 主路径共享**（可安全删除的上界）：**18 节点**
#        （Unsqueeze×5、Gather×5、ConstantOfShape×2、Cast×2、Slice/Range/Reshape/Mul 各 1）
#      · 边界消费者：**9 个**（含 `/m/embeddings/Add` ×2）→ 需要重新接线
#
# ⚠️ **这是上界估计，不是已验证的修法**：它只说明"要动的图结构是有界的（18 个独占节点 + 9 个接线点）"，
# **没有证明**删掉这段就能解决 0 CPU 挂起——那目前仍是**假说**。
# 验证该假说最便宜的一步：造一份"掩码链已被宿主 additive mask 取代"的 ONNX 变体，
# 重跑 convert + benchmark，看挂起是否消失。
#
# ## ✅ 路线① 假说**已证实**：掩码链就是 0 CPU 挂起的成因（2026-09-25）
#
# `scripts/model_to_ms_maskfree.py` 造出"去掩码链"变体后：
#   · 转换：**CONVERT RESULT SUCCESS:0** → `bge-nomask.ms` 95,850,768 B、魔数 `MSL2`
#   · 运行：**Run Benchmark bge-nomask.ms Success.**（不再是 0 CPU 挂起）
#         PrepareTime = 148.073 ms；AvgRunTime = **66.810 ms**（512 长、2 线程、CPU）
#
# 即：**MindSpore Lite 是能用的**——之前跑不起来的原因是那段动态掩码展开链，
# 而不是 MindSpore Lite 本身缺算子。M7 的路线排序因此改变：**① 可行**，不必退回路线③。
#
# 关键的两个坑（都不是"猜"出来的，是报错直接指出来的）：
#   1. `SetMetaGraphInput] input Parameter_1 not found in graph`
#      → 掩码链是 `attention_mask` 的**唯一消费者**；删链后该输入成了"声明了但没人用"的死输入，
#        转换器按内部名去找它就失败。修法：**同步裁掉无消费者的图输入**（这才语义自洽——
#        无掩码模型本就只该吃 input_ids/token_type_ids），而不是加个假消费者去哄转换器。
#   2. 只删节点、不清元数据会留下陈旧的 `value_info` 与未引用的 `initializer`
#      （实测清掉 38 个 initializer）。
#
# ⚠️ **仍未完成的一步（生产化）**：本变体把 additive mask 固定成**全零常量**，
#    因此**只在无 padding 时严格等价**（已用 ORT 证明：关闭优化逐位相同）。要用于生产，
#    需把 additive mask 改为**模型输入**、由宿主按实际 padding 计算后喂入——
#    这条路现在已被证明走得通（图里不再有动态链），但还没做。
#
# ⚠️ 数字口径：66.8 ms 是 **Linux-aarch64 CPU** 上的数，**不是鸿蒙设备数**；
#    鸿蒙端的真实性能仍须在模拟器/设备上测（M6 第 4 条）。
#
# ## 复现性缺口：**已闭合**（2026-09-25）
#
# 上一轮记录"产出 `bge-static.ms` 的配方没固化在脚本里"。本轮查清并修好：
#   · 根因：`convert` 硬编码转**动态**那份 ONNX（`bge-small-zh-v1.5-ms.onnx`），
#     所以 `all`（fetch→rewrite→convert→check）**永远不会产出 `bge-static.ms`**——
#     脚本跑一遍复现不出它自己的关键产物。
#   · 证据（时间戳）：`ms-static-1x512.onnx` 与 `bge-static.ms` 同为 2026-09-22 **17:39**，
#     产物 94,808,432 B，与 RFC §10 E11 记录一致 → 静态产物确实由静态 ONNX 转换而来。
#   · 修法：`convert` 参数化 + 新增 `convert-static`：
#         bash scripts/model_to_ms.sh convert-static
#   · **复现验证**：重跑后产出 **94,808,432 B**、魔数 `MSL2`，与既有产物大小逐字节一致；
#     当前 sha256 = 1f071d3a2f75093b3fc543e03b2eaa632ad076cffe15bb4dead8e39739702890
#
# ⚠️ 同时更正 `apps/harmony/README.md` 里一行**过时**结论（原写"固定 shape 转换阶段就失败"）——
#    那是四步重写**完成之前**的状态，会让人误以为这条路根本走不通。
#
# 📌 **支持掩码链假说的旁证**：转换静态 ONNX 时反复报
#    `/m/ConstantOfShape infershape failed!`（**非致命**，转换仍成功）——
#    而 `ConstantOfShape` 正是掩码链里的算子。这不能证明挂起由它引起，但方向一致。
#
# ## 路线① 假说检验（2026-09-25）：变换已证明正确，但**转换没跑通**，且暴露出复现性缺口
#
# 新增 `scripts/model_to_ms_maskfree.py`：把掩码链摘掉（重接 4 处注意力 Add → 全零常量，
# 再做**迭代死代码消除**自动清链）。结果：
#   · 结构：331 → 285 节点（删除 46 个）；重接 4 处
#   · **等价性已证**：关闭 ORT 图优化时两图输出**逐位完全相同**（差 0.000e+00）；
#     默认优化下差 5.2e-06 —— 那是 ORT 融合/调度改变浮点归约顺序，**不是语义差异**。
#     （把这两者分开很重要：若不区分，就会把"融合噪声"误判成"变换有错"。）
#   · 等价边界：全零 additive mask 只在「无 padding」时严格等价；带 padding 时必然不同
#     （脚本 `--check-padded` 会把这个差异量化出来，而不是掩盖它）。故该变体**只能用于诊断**，
#     生产须改由宿主喂入 additive mask。
#
# ⚠️ **但转换失败**：`converter_lite --fmk=ONNX --modelFile=ms-static-nomask.onnx` 报
#   `SetMetaGraphInput failed` → `Convert to meta graph failed`（带/不带 `--inputShape` 都失败）。
#   因此"删掉掩码链能否消除 0 CPU 挂起"这个假说**仍未被检验**（连 `.ms` 都没产出）。
#
# ⚠️⚠️ **顺带查出一个复现性缺口（值得单独记住）**：产出 `bge-static.ms` 的那条转换调用
#   **没有固化在任何脚本里**（`model_to_ms.sh` 只有动态那份的 `convert`）。
#   也就是说**现有的 `bge-static.ms` 无法从仓库复现**——这对 M7 是实质风险：
#   一个"能转换成功"的配方如果只存在于某次手工操作里，后续任何人（包括未来的我）都无法重建它。
#   下一步应当先**把那条配方找回来并固化**，再谈要不要删掩码链。
#
# 待清理：容器内 6 个 `benchmark` 挂起进程（含 2 个 7 天前的）——0 CPU 无害，
# 但容器里没有 `pkill`、宿主 PID 又不匹配，需手工 `docker exec <c> kill <容器内PID>`。
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$ROOT/.tooling/mindspore-lite"
MS_VER="2.7.0"
MS_A64="mindspore-lite-${MS_VER}-linux-aarch64"
MS_SHA="3db8ce8a4e94c659f619f5588a73e347bce62d88ac0bea8a5689b8b889456a02"
MS_URL="https://ms-release.obs.cn-north-4.myhuaweicloud.com/${MS_VER}/MindSporeLite/lite/release/linux/aarch64/mindspore-lite-${MS_VER}-linux-aarch64.tar.gz"
PY="$ROOT/backend/.venv/bin/python"
SRC_ONNX="$ROOT/.tooling/models/bge-small-zh-v1.5/model.onnx"
LDP="/w/mindspore-lite/${MS_A64}/runtime/lib:/w/mindspore-lite/${MS_A64}/tools/converter/lib"
DOCKER_IMG="${SEKB_MS_IMAGE:-sekb-toolbox:latest}"

in_docker() {   # 在 arm64 容器里跑（原生，无模拟）
    docker run --rm -v "$WORK/..:/w" -w /w/mindspore-lite/work "$DOCKER_IMG" bash -c "$1"
}

fetch() {
    mkdir -p "$WORK"
    ( cd "$WORK"
      if [ ! -f ms-arm64.tar.gz ]; then
          echo "── 下载 MindSpore Lite ${MS_VER}（Linux-aarch64，含 converter）──"
          curl -sL -m 900 -o ms-arm64.tar.gz -w 'code=%{http_code} bytes=%{size_download}\n' "$MS_URL" || return 1
      fi
      got="$(shasum -a 256 ms-arm64.tar.gz | cut -d' ' -f1)"
      if [ "$got" != "$MS_SHA" ]; then
          echo "❌ sha256 不符：${got} ≠ ${MS_SHA}" >&2; return 1
      fi
      echo "✅ sha256 与官方一致（${MS_SHA}）"
      [ -x "$MS_A64/tools/converter/converter/converter_lite" ] || tar xzf ms-arm64.tar.gz
      [ -x "$MS_A64/tools/converter/converter/converter_lite" ] || { echo "❌ 解压后找不到 converter_lite" >&2; return 1; }
      mkdir -p work
    )
}

rewrite() {
    [ -f "$SRC_ONNX" ] || { echo "❌ 缺 ${SRC_ONNX}（先跑 bash scripts/fetch_embedding_model.sh）" >&2; return 1; }
    "$PY" "$ROOT/scripts/model_to_ms.py" "$SRC_ONNX" "$WORK/work/bge-small-zh-v1.5-ms.onnx" || return 1
}

# ⚠️ **本条曾是个复现性缺口**：原来它硬编码转**动态**那份 ONNX，
# 于是 `all`（fetch→rewrite→convert→check）**永远不会产出 `bge-static.ms`**——
# 而 `bge-static.ms` 才是"静态化"那条路的关键产物。换句话说：脚本跑一遍复现不出它自己的产物。
# 现参数化 source/output，并加 `convert-static` 明确固化静态配方。
# 证据（时间戳）：`ms-static-1x512.onnx` 与 `bge-static.ms` 同为 2026-09-22 17:39，
# 产物 94,808,432 B —— 与 RFC §10 E11 记的"CONVERT RESULT SUCCESS:0 产出静态 .ms（94,808,432 B）"一致。
convert() {
    local src="${1:-bge-small-zh-v1.5-ms.onnx}"
    local out="${2:-bge-small-zh-v1.5}"
    echo "── converter_lite：${src} → ${out}.ms ──"
    in_docker "export LD_LIBRARY_PATH=$LDP; /w/mindspore-lite/${MS_A64}/tools/converter/converter/converter_lite --fmk=ONNX --modelFile=/w/mindspore-lite/work/${src} --outputFile=/w/mindspore-lite/work/${out}" 2>&1 | tr -d '\000' | grep -E 'CONVERT RESULT|UNSUPPORTED OP|OP TYPE|ERROR' | head -6 || true
    if [ -f "$WORK/work/${out}.ms" ]; then
        ls -la "$WORK/work/${out}.ms" | awk '{printf "  产物 %s：%d B\n", $9, $5}'
        python3 -c "
d=open('$WORK/work/${out}.ms','rb').read(16)
print('  魔数:', d[4:8].decode('ascii','replace'), '（应为 MSL2）')"
    else
        echo "  ❌ 未产出 ${out}.ms"
    fi
}

# benchmark 是**判定 M7 路线**的关键一步：`.ms` 能被转换 ≠ 能被运行。
# 参数化模型名与输入形状，是为了能分别验**两个不同产物**——它们此前被混为一谈：
#   · bge-small-zh-v1.5.ms：**动态 shape**（旧路径），实测 Flatten 拿不到 kernel
#   · bge-static.ms：**静态化**后的产物（重写 Flatten→Reshape + 常量内联），
#     **此前的记录只验了它的转换成功与余弦，从未对它跑过 benchmark**
check() {
    local model="${1:-bge-small-zh-v1.5.ms}"
    local shape="${2:-input_ids:1,64;attention_mask:1,64;token_type_ids:1,64}"
    echo "── benchmark：实际加载并运行 ${model}（inputShape=${shape}）──"
    in_docker "export LD_LIBRARY_PATH=$LDP; /w/mindspore-lite/${MS_A64}/tools/benchmark/benchmark --modelFile=${model} --inputShape='${shape}' --device=CPU --warmUpLoopCount=1 --loopCount=3" 2>&1 | tr -d '\000' | tail -14
}

case "${1:-all}" in
    fetch)   fetch ;;
    rewrite) rewrite ;;
    convert) convert ;;
    # 静态化配方（四步重写后的 ONNX → 静态 .ms）；用 all 跑不出来，必须显式指定
    convert-static) convert ms-static-1x512.onnx bge-static ;;
    # 去掩码链变体（假说检验用；由 scripts/model_to_ms_maskfree.py 生成）
    convert-nomask) convert ms-static-nomask.onnx bge-nomask ;;
    # 去掩码链变体的运行期检验（输入里已无 attention_mask）
    check-nomask)  check bge-nomask.ms 'input_ids:1,512;token_type_ids:1,512' ;;
    check)   check ;;
    # 静态化产物（512 定长）：M7 路线判定的决定性实验——此前只验过它能转换，没验过它能运行
    check-static) check bge-static.ms 'input_ids:1,512;attention_mask:1,512;token_type_ids:1,512' ;;
    all)     fetch && rewrite && convert && check ;;
    *) echo "用法：bash scripts/model_to_ms.sh [fetch|rewrite|convert|check|all]" >&2; exit 2 ;;
esac
