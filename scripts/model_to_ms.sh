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
# ⚠️ 写 shell 时注意：`$VAR（` 这种"变量紧挨全角括号"的写法，bash 会把全角括号的
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
# ## ⚠️ 当前状态：`.ms` 能转换，但**运行时 build 失败**（未解决）
#
# 两条路都试过，都失败，且失败点不同 —— 这两条错误信息是排查的起点：
#   · 动态 shape（不带 --inputShape）：`CONVERT RESULT SUCCESS:0` 产出 .ms，
#     但 `benchmark` 加载时 `FindBackendKernel return nullptr, name: /m/Flatten, type: Flatten`
#     → `/m/Flatten` 拿不到 kernel，典型的"上游动态形状没解析出来，下游算子形状未知"。
#   · 固定 shape（--inputShape="input_ids:1,512;..."）：转换阶段就
#     `SaveGraph] Convert to meta graph failed` / `HandleGraphCommon] Save graph failed`。
#
# 因此**"鸿蒙端侧嵌入"的模型依赖目前未打通**。下一步可选（按性价比）：
#   ① 把 ONNX 里那段**动态掩码展开子图**（Shape/ConstantOfShape/Range/Gather 链）改写成静态等价形式，
#      再走固定 shape 转换 —— 与本脚本已有的重写思路一致，但工作量更大；
#   ② 放弃 MindSpore Lite，回到社区版 OHOS ONNX Runtime（原方案，需找到预编译产物）；
#   ③ 按 RFC §4 D2 退路线 B（ArkTS 重写 + 契约夹具）。
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
    [ -f "$SRC_ONNX" ] || { echo "❌ 缺 $SRC_ONNX（先跑 bash scripts/fetch_embedding_model.sh）" >&2; return 1; }
    "$PY" "$ROOT/scripts/model_to_ms.py" "$SRC_ONNX" "$WORK/work/bge-small-zh-v1.5-ms.onnx" || return 1
}

convert() {
    echo "── converter_lite（动态 shape）──"
    in_docker "export LD_LIBRARY_PATH=$LDP; /w/mindspore-lite/${MS_A64}/tools/converter/converter/converter_lite --fmk=ONNX --modelFile=/w/mindspore-lite/work/bge-small-zh-v1.5-ms.onnx --outputFile=/w/mindspore-lite/work/bge-small-zh-v1.5" 2>&1 | tr -d '\000' | grep -E 'CONVERT RESULT|UNSUPPORTED OP|OP TYPE' || true
    ls -la "$WORK/work/bge-small-zh-v1.5.ms" 2>/dev/null && python3 -c "
d=open('$WORK/work/bge-small-zh-v1.5.ms','rb').read(16)
print('魔数:', d[4:8].decode('ascii', 'replace'), '（应为 MSL2）')"
}

check() {
    echo "── benchmark：实际加载并运行 .ms（Linux-aarch64 runtime）──"
    in_docker "export LD_LIBRARY_PATH=$LDP; /w/mindspore-lite/${MS_A64}/tools/benchmark/benchmark --modelFile=bge-small-zh-v1.5.ms --inputShape='input_ids:1,64;attention_mask:1,64;token_type_ids:1,64' --device=CPU --warmUpLoopCount=1 --loopCount=3" 2>&1 | tr -d '\000' | tail -12
}

case "${1:-all}" in
    fetch)   fetch ;;
    rewrite) rewrite ;;
    convert) convert ;;
    check)   check ;;
    all)     fetch && rewrite && convert && check ;;
    *) echo "用法：bash scripts/model_to_ms.sh [fetch|rewrite|convert|check|all]" >&2; exit 2 ;;
esac
