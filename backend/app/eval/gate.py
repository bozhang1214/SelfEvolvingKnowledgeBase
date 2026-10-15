"""评测门禁：把「评测结果」从"跑完看一眼"变成"必须拦住退化"。

**为什么需要它**（2026-10-12 盘点结论）：
CI 里的评测步骤原本是

    python -m app.cli.main eval --dataset ... || echo "eval non-blocking"

—— **无论通过率多少都 exit 0**，而 ``app/cli/eval.py`` 全文件 0 处 ``sys.exit``。
也就是说评测早就接进 CI 了，但它是**假门禁**：退化了没人知道。
FDE 岗位 JD 明确要求「构建高性能的评估管道…确保准确性、安全性和延迟要求」，
所以这条得是真的。

**三条设计原则**：

1. **跑不起来 = 失败**。阈值需要的指标在报告里缺失、或基线文件读不出来，
   一律判失败 —— 一个"无法执行"的门禁如果算通过，就还是假门禁。
2. **不只看绝对阈值，还看相对基线**。绝对阈值（如 0.90）会掩盖**渐进退化**
   （每次掉一点、始终没破线）。所以同时比对 ``eval-baseline.json``，
   允许下降幅度由 ``max_regression`` 控制。
3. **判据要可追溯**。报告带 commit + prompt/画像指纹：否则无法判断"退化是代码引起的
   还是提示词/画像引起的"——这正是 2026-09-23 画像滞后事故的同类教训。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 数据结构
# ============================================================


@dataclass
class GateThresholds:
    """门禁判据。

    Attributes:
        pass_rate_min: 通过率下限（``<= 0`` 表示不检查该项）
        metric_mins: 各指标的**均值**下限，如 ``{"answer_groundedness": 0.8}``
        baseline: 基线内容（``load_baseline`` 的返回值；None 表示不比对基线）
        max_regression: 相对基线允许的最大下降幅度（0 = 不允许下降）
        baseline_path: 基线来源路径（仅用于报告展示）
    """

    pass_rate_min: float = 0.0
    metric_mins: dict[str, float] = field(default_factory=dict)
    baseline: dict[str, Any] | None = None
    max_regression: float = 0.0
    baseline_path: str = ""


@dataclass
class GateCheck:
    """单项检查结果。"""

    name: str
    ok: bool
    actual: float | None
    expected: str
    detail: str = ""


@dataclass
class GateResult:
    """门禁总结果。"""

    ok: bool
    checks: list[GateCheck] = field(default_factory=list)

    @property
    def failures(self) -> list[GateCheck]:
        return [c for c in self.checks if not c.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [
                {"name": c.name, "ok": c.ok, "actual": c.actual,
                 "expected": c.expected, "detail": c.detail}
                for c in self.checks
            ],
            "failures": [c.name for c in self.failures],
        }


# ============================================================
# 基线
# ============================================================


def load_baseline(path: str | Path) -> dict[str, Any] | None:
    """读基线文件；文件不存在或损坏返回 None（调用方决定这算不算失败）。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("基线文件读取失败", path=str(p), error=str(e)[:120])
        return None
    return data if isinstance(data, dict) else None


def make_baseline(report: Any, fingerprint: dict[str, Any] | None = None) -> dict[str, Any]:
    """从一次评测结果生成基线内容（供 `--update-baseline` 写盘）。"""
    return {
        "pass_rate": round(float(getattr(report, "pass_rate", 0.0)), 4),
        "total": int(getattr(report, "total", 0)),
        "passed": int(getattr(report, "passed", 0)),
        "metrics": {
            name: round(float(vals.get("mean", 0.0)), 4)
            for name, vals in (getattr(report, "aggregated", None) or {}).items()
            if isinstance(vals, dict)
        },
        "fingerprint": fingerprint or {},
    }


# ============================================================
# 指纹（判据可追溯）
# ============================================================


def _find_repo_root() -> Path | None:
    """从本文件向上找同时含 ``prompt/`` 的目录（容器里是 /app，本机是仓库根）。"""
    for parent in Path(__file__).resolve().parents:
        if (parent / "prompt").is_dir():
            return parent
    return None


def _md5_files(paths: list[Path]) -> str:
    h = hashlib.md5()
    for p in sorted(paths):
        try:
            h.update(p.read_bytes())
        except OSError:
            continue
    return h.hexdigest()[:12]


def build_fingerprint(dataset_path: str | None = None) -> dict[str, Any]:
    """构造评测指纹：commit + 提示词 + 画像 + 数据集。

    没有它就无法区分「退化来自代码」还是「来自提示词/画像改动」。
    """
    fp: dict[str, Any] = {}
    # commit：容器内可能是 git 仓库也可能是构建产物，取不到就记 unknown（不阻断）
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(_find_repo_root() or Path.cwd()),
        )
        fp["commit"] = out.stdout.strip() if out.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        fp["commit"] = "unknown"

    root = _find_repo_root()
    if root is not None:
        prompt_dir = root / "prompt"
        if prompt_dir.is_dir():
            files = [p for p in prompt_dir.rglob("*") if p.is_file()]
            fp["prompt_md5"] = _md5_files(files)
            fp["prompt_files"] = len(files)
        profile = prompt_dir / "job" / "README.md"
        if profile.is_file():
            fp["job_profile_md5"] = _md5_files([profile])
    if dataset_path:
        ds = Path(dataset_path)
        if ds.is_file():
            fp["dataset_md5"] = _md5_files([ds])
            fp["dataset_name"] = ds.name
    return fp


# ============================================================
# 判定
# ============================================================


def evaluate_gate(report: Any, thresholds: GateThresholds) -> GateResult:
    """按判据判定一次评测结果，返回逐项检查明细。"""
    checks: list[GateCheck] = []
    pass_rate = float(getattr(report, "pass_rate", 0.0) or 0.0)

    # 1) 绝对阈值：通过率
    if thresholds.pass_rate_min > 0:
        checks.append(GateCheck(
            name="pass_rate_min",
            ok=pass_rate >= thresholds.pass_rate_min,
            actual=round(pass_rate, 4),
            expected=f">= {thresholds.pass_rate_min}",
        ))

    # 2) 关键指标下限（缺失即失败 —— 无法验证的门禁不能算通过）
    aggregated = getattr(report, "aggregated", None) or {}
    for name, minimum in (thresholds.metric_mins or {}).items():
        vals = aggregated.get(name)
        if not isinstance(vals, dict) or "mean" not in vals:
            checks.append(GateCheck(
                name=f"metric:{name}",
                ok=False,
                actual=None,
                expected=f">= {minimum}",
                detail=f"报告中缺少该指标的聚合值（现有指标：{', '.join(sorted(aggregated)) or '无'}）",
            ))
            continue
        mean = float(vals["mean"])
        checks.append(GateCheck(
            name=f"metric:{name}",
            ok=mean >= float(minimum),
            actual=round(mean, 4),
            expected=f">= {minimum}",
        ))

    # 3) 相对基线：防渐进退化
    baseline = thresholds.baseline
    if baseline:
        base_rate = baseline.get("pass_rate")
        if not isinstance(base_rate, (int, float)):
            checks.append(GateCheck(
                name="baseline", ok=False, actual=None,
                expected="基线含 pass_rate",
                detail=f"基线文件 {thresholds.baseline_path or '(内联)'} 缺少 pass_rate 字段",
            ))
        else:
            floor = float(base_rate) - float(thresholds.max_regression)
            checks.append(GateCheck(
                name="baseline_pass_rate",
                ok=pass_rate >= floor,
                actual=round(pass_rate, 4),
                expected=f">= {round(floor, 4)}（基线 {base_rate}，允许下降 {thresholds.max_regression}）",
                detail=f"基线 commit={baseline.get('fingerprint', {}).get('commit', '?')}",
            ))
        # 基线里记录的指标，同样不允许跌破允许幅度
        for name, base_val in (baseline.get("metrics") or {}).items():
            vals = aggregated.get(name)
            if not isinstance(vals, dict) or "mean" not in vals:
                continue  # 报告没这个指标就不额外判（避免因口径变化误报）
            floor = float(base_val) - float(thresholds.max_regression)
            mean = float(vals["mean"])
            checks.append(GateCheck(
                name=f"baseline_metric:{name}",
                ok=mean >= floor,
                actual=round(mean, 4),
                expected=f">= {round(floor, 4)}（基线 {base_val}）",
            ))

    return GateResult(ok=all(c.ok for c in checks) if checks else True, checks=checks)
