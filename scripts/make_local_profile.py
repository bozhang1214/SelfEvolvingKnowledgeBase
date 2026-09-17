#!/usr/bin/env python3
"""生成「全本地」配置档 `backend/config.local.yaml`（M0 交付物之一）。

为什么是「生成」而不是手抄
--------------------------
SEKB 只支持单文件配置（没有 overlay），所以本地档必须是 `config.yaml` 的完整副本。
手抄副本一定会漂移（改了主配置忘了改本地档），所以这里按**单一事实源**原则处理：
主配置是唯一权威，本地档是**派生产物**；`--check` 可在 CI/提交前校验两者是否同步。

派生的改动只有 llm 段三处 + 每个角色的 model：

| 项 | 主配置 | 本地档 |
|---|---|---|
| `llm.provider` | deepseek | ollama |
| `llm.api_key` | `${DEEPSEEK_API_KEY}` | `ollama`（非空即可，Ollama 不校验） |
| `llm.base_url` | `https://api.deepseek.com/v1` | `http://127.0.0.1:11434/v1` |
| 各角色 `model` | `deepseek-flash` | 按**档位**映射（见 TIERS） |

档位映射刻意做成三档，这样"端侧也能按角色分档"这件事在**同一端点内**就成立
（`LLMConfig.base_url` 是全局单点，但 `LLMRoleConfig.model` 是逐角色的）。

用法
----
```bash
python3 scripts/make_local_profile.py            # 生成/刷新 backend/config.local.yaml
python3 scripts/make_local_profile.py --check     # 只校验是否与主配置同步（CI 用）
```

启动本地档（无需改代码，入口已支持该环境变量）：
```bash
SEKB_CONFIG_PATH=config.local.yaml <启动命令>
```
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "config.yaml"
DST = ROOT / "backend" / "config.local.yaml"
#: 双平面（端云协同）档：主端点=云端，llm.planes.edge=本机 Ollama，路由开启
DST_DUAL = ROOT / "backend" / "config.edge-cloud.yaml"

LOCAL_BASE_URL = "http://127.0.0.1:11434/v1"
LOCAL_API_KEY = "ollama"

#: 角色 → 档位（short=短任务用最小档；default=主力；quality=要质量的长输出）
TIERS: dict[str, str] = {
    # 短进短出：分类/打分/路由 → 最小档，延迟优先
    "supervisor": "short",
    "critic": "short",
    "chat_simple": "short",
    "rerank": "short",
    "ragas": "short",
    # 主力：规划/执行/记录/职位分析
    "planner": "default",
    "executor": "default",
    "critic_complex": "default",
    "scribe": "default",
    "job_analysis": "default",
    # 长输出、要质量
    "news_report": "quality",
}
MODELS = {
    "short": "qwen3.5-2b",
    "default": "qwen3.5-4b",
    "quality": "qwen3.5-9b",
}

DUAL_BLOCK = """
  # ---- 端云双平面（M1）：主端点=云端，edge=本机 Ollama；由 plane_router 逐请求决策 ----
  # 阈值默认值取自 2026-09-17 M5 Pro 实测（scripts/edge_bench.py）：
  #   qwen3.5-2b decode 86–116 tok/s、923 tok 输入 TTFT 427ms；
  #   deepseek-flash decode 158–218 tok/s、TTFT 845–1306ms  → 端侧吃"短进短出"。
  planes:
    edge:
      provider: ollama
      api_key: ollama
      base_url: http://127.0.0.1:11434/v1
      models:
        short: qwen3.5-2b
        default: qwen3.5-4b
        quality: qwen3.5-9b
      max_output_tokens: 300      # 超过就不该端侧硬扛（2B 生成 500 token ≈ 4.3s）
      max_input_tokens: 2048      # 长输入虽不崩，但 TTFT 会抬高
      max_ttft_ms: 800
      disable_thinking: true      # 实测关思考快 25 倍（2283ms→89ms），端侧默认关
    routing:
      enabled: true
      prefer: edge
      # 6 类升级信号（RFC §4.2）：context_overflow 由输入预算直接改判，故不在此列
      escalate_on: [json_invalid, empty, degenerate, timeout, low_confidence, tool_hallucination]
      device_only_roles: []       # 数据分级 DEVICE_ONLY 的角色（永不出端），按需填
      # 流式前缀守卫：端侧先攒 60 字符再判断是否改道云端（用户未见到 token 才可改道），
      # 0 = 关闭。60 ≈ 2B 上 0.3–0.6s 首字延迟，是退化检测下限（40 字符）之上的最小代价。
      # json_invalid 不参与前缀判断：半截 JSON 必然不合法
      stream_guard_chars: 60
"""

HEADER_DUAL = """# ⚠️ 本文件由 scripts/make_local_profile.py 从 config.yaml 生成，请勿手工编辑。
# 「端云协同」档：主端点=云端 DeepSeek，端侧平面=本机 Ollama，逐请求路由。
# 需要：本机 ollama 已 pull/导入 qwen3.5-2b/4b/9b（见 scripts/edge_m0_setup.sh）
#      且 .env 里有 DEEPSEEK_API_KEY（本档在线使用；纯离线请用 config.local.yaml）。
# 启动：SEKB_CONFIG_PATH=config.edge-cloud.yaml <启动命令>
#
"""

HEADER = """# ⚠️ 本文件由 scripts/make_local_profile.py 从 config.yaml 生成，请勿手工编辑。
# 「全本地」档：LLM 全部走本机 Ollama（离线可用），需要先 pull 好对应模型。
# 启动：SEKB_CONFIG_PATH=config.local.yaml <启动命令>
# 校验同步：python3 scripts/make_local_profile.py --check
#
"""


def _llm_block_span(text: str) -> tuple[int, int]:
    """定位顶层 `llm:` 段的字符区间（到下一个顶层键为止）。"""
    m = re.search(r"^llm:\s*$", text, flags=re.MULTILINE)
    if not m:
        raise SystemExit("config.yaml 里找不到顶层 llm: 段")
    start = m.start()
    nxt = re.search(r"^[a-z_]+:\s*$", text[m.end():], flags=re.MULTILINE)
    end = m.end() + (nxt.start() if nxt else len(text) - m.end())
    return start, end


def derive(text: str) -> str:
    start, end = _llm_block_span(text)
    block = text[start:end]

    block = block.replace("  provider: deepseek", "  provider: ollama", 1)
    block = re.sub(r"^  api_key:.*$", f"  api_key: {LOCAL_API_KEY}", block, count=1, flags=re.MULTILINE)
    block = re.sub(r"^  base_url:.*$", f"  base_url: {LOCAL_BASE_URL}", block, count=1, flags=re.MULTILINE)

    # 逐角色替换 model：按行扫描，用"最近的上一层角色名"判断归属，
    # 避免用复杂正则去猜嵌套结构（也避免误伤 roles 之外的 model 字段）。
    roles_at = block.find("  roles:")
    if roles_at < 0:
        raise SystemExit("llm 段里找不到 roles:")
    head, roles = block[:roles_at], block[roles_at:]

    out: list[str] = []
    current: str | None = None
    for line in roles.splitlines(keepends=True):
        m_role = re.match(r"^    ([a-z_]+):", line)
        if m_role:
            current = m_role.group(1)
        stripped = line.lstrip()
        if current in TIERS and stripped.startswith("model:"):
            indent = line[: len(line) - len(stripped)]
            line = f"{indent}model: {MODELS[TIERS[current]]}\n"
        out.append(line)
    roles = "".join(out)

    return HEADER + text[:start] + head + roles + text[end:]


def derive_dual(text: str) -> str:
    """在**主配置**（云端）基础上插入 `llm.planes`，得到端云协同档。

    与 `derive()` 的区别：那个是"全本地"（把所有角色换成本地模型），
    这个是"双平面"（云端仍旧是云端，另加一个端侧平面 + 路由）。
    """
    start, end = _llm_block_span(text)
    block = text[start:end].rstrip("\n")
    return HEADER_DUAL + text[:start] + block + "\n" + DUAL_BLOCK + text[end:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写入")
    args = ap.parse_args()

    src = SRC.read_text(encoding="utf-8")
    want = derive(src)

    if args.check:
        bad = 0
        for path, expected in ((DST, want), (DST_DUAL, derive_dual(src))):
            if not path.exists():
                print(f"❌ {path.name} 不存在；运行 python3 scripts/make_local_profile.py")
                bad = 1
            elif path.read_text(encoding="utf-8") != expected:
                print(f"❌ {path.name} 与 config.yaml 不同步；"
                      "运行 python3 scripts/make_local_profile.py 重新生成")
                bad = 1
        if bad:
            return 1
        print("✅ 两个派生档（全本地 / 端云双平面）与主配置同步")
        return 0

    DST.write_text(want, encoding="utf-8")
    DST_DUAL.write_text(derive_dual(src), encoding="utf-8")
    missing = sorted(set(TIERS) - set(re.findall(r"^    ([a-z_]+):", src, flags=re.MULTILINE)))
    print(f"✅ 已生成 {DST.relative_to(ROOT)}（全本地：所有角色走本机 Ollama）")
    print(f"✅ 已生成 {DST_DUAL.relative_to(ROOT)}（端云双平面：云端 + 端侧路由）")
    print(f"   档位：short={MODELS['short']} / default={MODELS['default']} / quality={MODELS['quality']}")
    if missing:
        print(f"   ⚠️ TIERS 里登记但 config.yaml 中不存在的角色：{missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
