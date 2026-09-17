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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写入")
    args = ap.parse_args()

    src = SRC.read_text(encoding="utf-8")
    want = derive(src)

    if args.check:
        if not DST.exists():
            print("❌ backend/config.local.yaml 不存在；运行 python3 scripts/make_local_profile.py")
            return 1
        if DST.read_text(encoding="utf-8") != want:
            print("❌ backend/config.local.yaml 与 config.yaml 不同步；"
                  "运行 python3 scripts/make_local_profile.py 重新生成")
            return 1
        print("✅ 本地档与主配置同步")
        return 0

    DST.write_text(want, encoding="utf-8")
    missing = sorted(set(TIERS) - set(re.findall(r"^    ([a-z_]+):", src, flags=re.MULTILINE)))
    print(f"✅ 已生成 {DST.relative_to(ROOT)}")
    print(f"   档位：short={MODELS['short']} / default={MODELS['default']} / quality={MODELS['quality']}")
    if missing:
        print(f"   ⚠️ TIERS 里登记但 config.yaml 中不存在的角色：{missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
