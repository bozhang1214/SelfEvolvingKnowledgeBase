#!/usr/bin/env bash
# ============================================================
# M0：把「Mac 端侧小模型」准备好并接进 Ollama（可重复执行）
# ============================================================
# 为什么不用 `ollama pull`（重要，别再踩一遍）
# ------------------------------------------------------------
# 本机直连 `registry.ollama.ai` 实测**不可用**：
#   * `-mlx` 档在 Ollama 上是 **625 个小 blob**（对不稳的国际链路最坏）
#   * blob 请求返回 **307 重定向后 0 字节**（CDN 不可达），`curl -4/-6` 都是 0 B/s
#   * 同时段国内链路 6.3 MB/s、ModelScope **16.8 MB/s** → 不是"慢"，是这条国际路径不通
# 因此改为：**ModelScope 下 GGUF（单文件、可断点续传）→ `ollama create` 导入**。
# 附带好处：GGUF 正是后面 Android(llama.cpp) 要用的格式。
#
# 用法：
#   bash scripts/edge_m0_setup.sh              # 下载 + 导入（已存在则跳过）
#   bash scripts/edge_m0_setup.sh --bench      # 再跑一次端云对照基准
# ============================================================
set -uo pipefail

MODEL_DIR="${MODEL_DIR:-$HOME/models/gguf}"
BASE="https://modelscope.cn/models/unsloth"
# 仓库|文件名|本地 Ollama 模型名
SPECS=(
  "Qwen3.5-2B-GGUF|Qwen3.5-2B-Q4_K_M.gguf|qwen3.5-2b"
  "Qwen3.5-4B-GGUF|Qwen3.5-4B-Q4_K_M.gguf|qwen3.5-4b"
  "Qwen3.5-9B-GGUF|Qwen3.5-9B-Q4_K_M.gguf|qwen3.5-9b"
)

mkdir -p "$MODEL_DIR"
cd "$MODEL_DIR" || exit 1

fail=0
for spec in "${SPECS[@]}"; do
    IFS='|' read -r repo file name <<<"$spec"
    echo "──────── $file → $name"
    if [ ! -f "$file" ]; then
        echo "  下载中（断点续传）..."
        if ! curl -L -C - --retry 5 --retry-delay 3 --retry-all-errors -m 3600 \
                -o "$file" "$BASE/$repo/resolve/master/$file" \
                -w "  code=%{http_code} 大小=%{size_download}B 平均=%{speed_download}B/s\n"; then
            echo "  ✗ 下载失败：$file"; fail=1; continue
        fi
    else
        echo "  ✓ 已存在，跳过下载"
    fi
    # 导入 Ollama：PARAMETER num_ctx 与方案 §2.4 的「2–4K 上下文预算」一致（留一倍余量）
    tmpf="$(mktemp)"
    printf 'FROM %s/%s\nPARAMETER num_ctx 8192\nPARAMETER temperature 0.2\n' "$MODEL_DIR" "$file" > "$tmpf"
    if ollama create "$name" -f "$tmpf" >/dev/null 2>&1; then
        echo "  ✓ 已导入为 $name"
    else
        echo "  ✗ ollama create 失败：$name"; fail=1
    fi
    rm -f "$tmpf"
done

echo "──────── 当前模型"
ollama list | head -12

if [ "${1:-}" = "--bench" ]; then
    echo "──────── 端云对照基准"
    key=""
    if [ -f ./.bench_key ]; then key="$(cat ./.bench_key)"; fi
    if [ -n "$key" ]; then
        DEEPSEEK_API_KEY="$key" python3 scripts/edge_bench.py \
            --local qwen3.5-2b qwen3.5-4b qwen3.5-9b --cloud deepseek-flash
    else
        echo "  （未提供 ./.bench_key，只跑本地）"
        python3 scripts/edge_bench.py --local qwen3.5-2b qwen3.5-4b qwen3.5-9b
    fi
fi

exit "$fail"
