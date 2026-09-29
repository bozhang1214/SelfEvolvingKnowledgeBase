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
# ## ✅ 生产化（掩码改为模型输入）：**转换 + 等价 + 运行 三项全通**（2026-09-25）
#
# 上一轮这里写着"运行期尚未通过"，并**明确标注未判定**是①工具解析 4D `--inputShape` 的问题
# 还是②模型不接受 4D 输入。本轮把它分清了：
#
#   **是①——benchmark 工具解析不了 4D 的 `--inputShape`；模型本身没问题。**
#   决定性实验：**完全不传 `--inputShape`**（静态图自描述，`check` 支持 `shape=NONE`）：
#       start unified benchmark run
#       PrepareTime = 138.863 ms
#       Model = bge-maskinput.ms, NumThreads = 2, AvgRunTime = **65.838 ms**
#       Run Benchmark bge-maskinput.ms Success.
#
#   ⚠️ 区分"不传"与"传空"：上一轮我用 `--inputShape=''`（空串）来代表"不传"，那是错的——
#      工具会把它当参数解析并报 `ParseGraphInputShapeMap] token_type_ids`，结论无效。
#      **要"不传"就必须真的不传**（这正是上一轮我拒绝下结论的原因，也让本轮结论可信）。
#
# 于是生产形态的三项验证齐了：
#   · 转换：`CONVERT RESULT SUCCESS:0` → `bge-maskinput.ms` 94,802,472 B、魔数 `MSL2`
#   · 等价：关闭 ORT 优化时与原图**逐位相同**（无 padding / 有 padding / 全 padding 三种都过）
#   · 运行：`Run Benchmark Success`，AvgRunTime **65.838 ms**（512 长、2 线程、CPU）
#
# **重要推论：因为等价是"逐位相同"，嵌入空间与阈值都不必重标。**
# RFC 里"换运行时只影响向量空间、需按既有做法重标阈值"的提醒，在这里**不适用**——
# 图变换没有改变数值输出，所以 M5/M8 那套已标定的阈值继续有效。
#
# 宿主侧契约（Kotlin 实现时要照做）：additive mask 取值 —— 可见位置 `0`、被掩位置 `-inf`，
# 形状 `[1,1,512,512]`；实测该张量**四层完全相同**，故单个输入即可。
#

#
# `scripts/model_to_ms_maskfree.py --mask-input` 把 additive mask 由常量改为**模型输入**
# `sekb_additive_mask_zero: float32[1,1,512,512]`（由宿主按实际 padding 计算后喂入）。
#
# 实测事实与结论：
#   · 掩码取值：无 padding 时**恒为 0**；有 padding 时为 **{-inf, 0}**；
#     **四层完全相同** → 单个输入即够（不需要四个）。
#   · 转换：**CONVERT RESULT SUCCESS:0** → `bge-maskinput.ms` 94,802,472 B、魔数 `MSL2`。
#   · **等价性（三种输入都过）**：关闭 ORT 优化时与原图**逐位相同**——
#       无 padding ✅、有 padding（后 212 为 0）✅、全 padding ✅。
#       注：全 padding 那格 `array_equal` 报 False 是 **nan≠nan** 的判定假象；
#       实测**nan 位置完全一致、非 nan 部分逐位相同**（全 padding 下 softmax 全 -inf，两者都 nan）。
#   · ❌ **运行期未通过**：`benchmark` 报
#       Input tensor resize failed / InferShape failed, type: AddFusion,
#       name: /m/encoder/layer.0/attention/self/Add
#     即 4D 掩码输入被当作常量时能跑（`bge-nomask.ms` 66.8 ms 成功），改成**输入**后
#     Add 的 shape 推断在 benchmark 里失败。
#
# **未判定**：这究竟是 ①benchmark 工具对 4D `--inputShape` 的解析问题，还是 ②模型本身
# 在 MindSpore Lite 里不能接受 4D 中间输入。我试过"不带 --inputShape"，但那条调用**写错了**
# （给工具传了空的 --inputShape，工具随即报 `ParseGraphInputShapeMap] token_type_ids`），
# 所以**没有形成有效结论**——这一点必须如实标明，不能当成"工具问题"糊过去。
#
# 下一步（二选一，都不需要设备）：
#   a) 把掩码在**宿主侧展开成 1×1×512×512 但以 3D/2D 形状传入**？不行——语义需要 4D 广播。
#   b) 更稳的做法：**不改 Add 的输入**，而是让宿主把掩码**预先加到注意力分数上**？也不行（分数在图内）。
#   c) 现实可行的替代：把掩码**烘焙成常量**（= `bge-nomask.ms`，已验证能跑 66.8 ms），
#      代价是**不支持 padding**；对"定长 512 全量输入"的嵌入场景，可用**截断/补零后重算**规避，
#      或按 RFC 的做法**重标阈值**并把 padding 处理移到宿主（先按实际长度分桶、再统一补到 512 且不掩码）。
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
    # shape=NONE 表示**完全不传 `--inputShape`**（用于区分"工具解析 4D 形状失败"与"模型不接受 4D 输入"）。
    # 注意：曾经我用"传空串"来做这件事，那是错的——工具会把 `--inputShape=''` 当参数解析并报
    # `ParseGraphInputShapeMap] token_type_ids`，得出的结论无效。要"不传"就必须真的不传。
    local shape_arg=""
    if [ "$shape" != "NONE" ]; then
        shape_arg="--inputShape=${shape}"
    fi
    echo "── benchmark：${model}（inputShape=${shape}${shape_arg:+}）──"
    in_docker "export LD_LIBRARY_PATH=$LDP; /w/mindspore-lite/${MS_A64}/tools/benchmark/benchmark --modelFile=${model} ${shape_arg} --device=CPU --warmUpLoopCount=1 --loopCount=3" 2>&1 | tr -d '\000' | tail -14
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
    convert-maskinput) convert ms-static-maskinput.onnx bge-maskinput ;;
    check-maskinput)  check bge-maskinput.ms 'input_ids:1,512;token_type_ids:1,512;sekb_additive_mask_zero:1,1,512,512' ;;
    # 不带 --inputShape：模型本身是静态的，用于排除"工具解析 4D 形状"这一层
    # 真正**不传** --inputShape（用于区分工具解析问题 vs 模型不接受 4D 输入）
    check-maskinput-noshape) check bge-maskinput.ms NONE ;;
    check)   check ;;
    # 静态化产物（512 定长）：M7 路线判定的决定性实验——此前只验过它能转换，没验过它能运行
    check-static) check bge-static.ms 'input_ids:1,512;attention_mask:1,512;token_type_ids:1,512' ;;
    all)     fetch && rewrite && convert && check ;;
    *) echo "用法：bash scripts/model_to_ms.sh [fetch|rewrite|convert|check|all]" >&2; exit 2 ;;
esac
