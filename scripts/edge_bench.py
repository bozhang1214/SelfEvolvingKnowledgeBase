#!/usr/bin/env python3
"""端侧 vs 云端：同题对照基准（M0 交付物之一）。

为什么需要它
------------
「端侧优先」不能靠感觉，要靠**同一批问题在两个平面上跑出来的数字**。本脚本按
**prefill / decode 分开计时**——这是端侧延迟的两个完全不同来源（见 `docs/RFC-端云协同与端侧Agent.md` §2.1）：

* **prefill**（读 prompt）= 算力受限 → 长输入的代价
* **decode**（逐 token 生成）= 内存带宽受限 → 长输出的代价

两种端点的取数方式不同，所以分别处理：

* **Ollama**（本地）：走原生 `/api/chat`，它直接回报 `prompt_eval_count / prompt_eval_duration /
  eval_count / eval_duration` → prefill 与 decode 的 tok/s 都是**精确值**，不靠估算。
* **OpenAI 兼容云端**（DeepSeek 等）：只能流式取"首 token 时间（TTFT）"，
  decode 速率 = `completion_tokens / (总时长 - TTFT)`（近似，但同口径可比）。

用法
----
```bash
# 本地三档（Ollama 必须已 pull 好模型）
python3 scripts/edge_bench.py --local qwen3.5:2b-mlx qwen3.5:4b-mlx qwen3.5:9b-mlx

# 加云端对照（key 从环境变量读，不落盘、不打印）
DEEPSEEK_API_KEY=sk-xxx python3 scripts/edge_bench.py --cloud deepseek-flash --local qwen3.5:4b-mlx

# 只看某一类任务
python3 scripts/edge_bench.py --local qwen3.5:4b-mlx --only intent
```

输出 Markdown 表格，可直接粘进方案文档。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - 运行环境缺依赖时给出可操作提示
    print("需要 httpx：backend/.venv/bin/python scripts/edge_bench.py ...")
    sys.exit(2)

OLLAMA = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
CLOUD_BASE = os.environ.get("EDGE_BENCH_CLOUD_BASE", "https://api.deepseek.com/v1")

#: 每类任务的 prompt 刻意覆盖 prefill / decode 的两种极端
#:   - intent：短进短出（端侧的主场）
#:   - short_qa：中进中出
#:   - structured：长输出（decode 受限，端侧吃亏）
#:   - long_context：长输入（prefill 受限）
FILLER = (
    "端侧推理的约束来自内存带宽：每生成一个 token 都要把全部权重读一遍，因此单用户场景下 "
    "解码速度近似等于内存带宽除以每 token 需读取的字节数。上下文增长会同时抬高 KV cache 的"
    "内存占用与每 token 的读取量，于是长上下文既吃内存又拖慢解码。"
)

TASKS: list[dict[str, Any]] = [
    {
        "name": "intent",
        "system": "你是意图分类器，只输出 JSON，不要解释。",
        "user": '把下面这句话分类为 [job_analysis, news, knowledge, chitchat] 之一，'
                '只输出 {"intent":"..."}：\n最近 AI 芯片出货量增长很快',
        "max_tokens": 64,
    },
    {
        "name": "short_qa",
        "system": "你是技术助理，回答简洁准确。",
        "user": "用 2-3 句话解释什么是 KV cache，以及它为什么影响推理速度。",
        "max_tokens": 256,
    },
    {
        "name": "structured",
        "system": "你是结构化输出助手，只输出 JSON。",
        "user": "围绕「端侧 LLM 部署」输出 JSON：{summary, points:[3 条], risk, next_step}",
        "max_tokens": 512,
    },
    {
        "name": "long_context",
        "system": "你是资料摘要助手。",
        "user": "把下面材料压成 3 条要点：\n" + (FILLER * 12),
        "max_tokens": 256,
    },
]


def _ms(ns: Any) -> float:
    try:
        return float(ns) / 1e6
    except (TypeError, ValueError):
        return 0.0


def _with_nonce(task: dict[str, Any], nonce: str) -> dict[str, Any]:
    """给 system 提示加一次性标记，破坏 Ollama 的**前缀缓存**。

    为什么必须这样：Ollama 会缓存已算过的 prompt 前缀，同一 prompt 复跑时
    `prompt_eval_duration` 只统计**未命中缓存**的部分——实测出现过 1025 token
    算出 42844 tok/s 的荒谬值（因为上一轮算过）。加 nonce 后每次都是冷前缀，
    prefill 数字才可比、可复现。
    """
    if not nonce:
        return task
    return {**task, "system": f"[run:{nonce}] {task['system']}"}


def bench_ollama(model: str, task: dict[str, Any], think: bool = False) -> dict[str, Any]:
    """本地：原生 /api/chat，拿精确的 prefill / decode 计时。

    `think=False` 是**默认**，因为实测差距极大：qwen3.5-2b 对同一个意图分类任务，
    开思考 = 190 token / 1926ms，关思考 = **6 token / 96ms**（答案完全相同，20 倍延迟差）。
    端侧的短任务（分类/路由/改写）必须关思考——否则白烧 10–30 倍算力。需要长推理时用 --think。
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": task["system"]},
            {"role": "user", "content": task["user"]},
        ],
        "stream": False,
        "options": {"temperature": 0, "num_predict": task["max_tokens"]},
        "think": bool(think),
    }
    t0 = time.perf_counter()
    r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=600)
    r.raise_for_status()
    d = r.json()
    wall = (time.perf_counter() - t0) * 1000

    pe_n, pe_d = d.get("prompt_eval_count", 0), _ms(d.get("prompt_eval_duration"))
    ev_n, ev_d = d.get("eval_count", 0), _ms(d.get("eval_duration"))
    return {
        "ok": True,
        "prompt_tokens": pe_n,
        "completion_tokens": ev_n,
        "load_ms": _ms(d.get("load_duration")),     # 冷启动加载：只在首次调用出现
        "ttft_ms": pe_d,                            # 首 token = prefill 完成（不含加载）
        "prefill_tps": (pe_n / (pe_d / 1000)) if pe_d else 0.0,
        "decode_tps": (ev_n / (ev_d / 1000)) if ev_d else 0.0,
        "wall_ms": wall,
        "text": (d.get("message") or {}).get("content", ""),
    }


def bench_openai_compat(model: str, task: dict[str, Any]) -> dict[str, Any]:
    """云端（OpenAI 兼容）：流式取 TTFT，用 usage 估 decode 速率。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return {"ok": False, "err": "缺少 DEEPSEEK_API_KEY"}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": task["system"]},
            {"role": "user", "content": task["user"]},
        ],
        "temperature": 0,
        "max_tokens": task["max_tokens"],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    t0 = time.perf_counter()
    ttft = 0.0
    t_first = t_last = 0.0
    chunks: list[str] = []
    usage: dict[str, Any] = {}
    with httpx.stream("POST", f"{CLOUD_BASE}/chat/completions",
                      headers={"Authorization": f"Bearer {key}"},
                      json=body, timeout=600) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if obj.get("usage"):
                usage = obj["usage"]
            for ch in obj.get("choices") or []:
                delta = ch.get("delta") or {}
                # thinking 类模型把文本放在 reasoning_content；漏掉它会导致
                # 「completion_tokens>0 但 TTFT=0」这种自相矛盾的行
                piece = (delta.get("content") or "") + (delta.get("reasoning_content") or "")
                if piece:
                    now = (time.perf_counter() - t0) * 1000
                    if not ttft:
                        ttft = now
                        t_first = now
                    t_last = now
                    chunks.append(piece)
    total = (time.perf_counter() - t0) * 1000
    comp = int(usage.get("completion_tokens") or 0)
    # 跨 token 速率用「首片→末片」的墙钟窗口；窗口 <150ms 时样本太短、算出来是噪声
    # （曾因此算出 60000 tok/s 这种荒谬值），这种情况下报 0 并由表格显示为「—」。
    window_ms = t_last - t_first
    measurable = comp > 1 and window_ms >= 150
    return {
        "ok": True,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": comp,
        "load_ms": 0.0,
        "ttft_ms": ttft,
        "prefill_tps": 0.0,          # 云端不回报 prefill 计时，留空而不是编造
        "decode_tps": ((comp - 1) / (window_ms / 1000)) if measurable else 0.0,
        "wall_ms": total,
        "text": "".join(chunks),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", nargs="*", default=[], help="本地 Ollama 模型（可多个）")
    ap.add_argument("--cloud", nargs="*", default=[], help="云端模型（OpenAI 兼容）")
    ap.add_argument("--only", default="", help="只跑某个任务（intent/short_qa/structured/long_context）")
    ap.add_argument("--think", action="store_true",
                    help="本地开启思考模式（默认关；短任务开思考会让延迟涨 10-20 倍）")
    ap.add_argument("--no-nonce", action="store_true",
                    help="不加一次性 nonce（会命中 Ollama 前缀缓存，prefill 数字偏高）")
    args = ap.parse_args()

    tasks = [t for t in TASKS if not args.only or t["name"] == args.only]
    nonce = "" if args.no_nonce else os.urandom(4).hex()
    rows: list[tuple[str, str, dict[str, Any]]] = []

    for model in args.local:
        for task in tasks:
            try:
                res = bench_ollama(model, _with_nonce(task, nonce), think=args.think)
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:80]}"}
            rows.append(("本地 Ollama", model, {"task": task["name"], **res}))

    for model in args.cloud:
        for task in tasks:
            try:
                res = bench_openai_compat(model, task)
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:80]}"}
            rows.append(("云端 API", model, {"task": task["name"], **res}))

    # 「等长输出预估」：端云实际生成的 token 数常不同（一方先自然收尾、一方顶到 max_tokens），
    # 直接比总时长会误导。按 decode 速率折算到 500 token，才是公平的横比。
    print("\n| 平面 | 模型 | 任务 | 输入tok | 输出tok | 加载(ms) | TTFT(ms) | prefill(tok/s) "
          "| decode(tok/s) | 总时长(ms) | 生成500tok预计(s) |")
    print("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for plane, model, r in rows:
        if not r.get("ok"):
            print(f"| {plane} | `{model}` | {r['task']} | — | — | — | — | — | — | ❌ {r.get('err')} |")
            continue
        pf = f"{r['prefill_tps']:.0f}" if r["prefill_tps"] else "—"
        load = f"{r.get('load_ms', 0):.0f}" if r.get("load_ms") else "—"
        dec = f"{r['decode_tps']:.1f}" if r["decode_tps"] else "—"
        est = f"{500 / r['decode_tps']:.1f}" if r["decode_tps"] else "—"
        print(f"| {plane} | `{model}` | {r['task']} | {r['prompt_tokens']} | {r['completion_tokens']} "
              f"| {load} | {r['ttft_ms']:.0f} | {pf} | {dec} | {r['wall_ms']:.0f} | {est} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
