"""用户画像加载单测：防止 prompt/job/README.md 与内置兜底画像再次跑偏。"""
from __future__ import annotations

import yaml

from app.agents.job.profile import (
    _FALLBACK_PROFILE,
    _load_base_profile,
    _resolve_profile_path,
)

# 画像一旦退回「只懂客户端 + AI 工具推广」的旧版本，职位分析会系统性低估候选人。
_REQUIRED_README_KEYS = {
    "name",
    "age",
    "years_of_experience",
    "current_role",
    "target_role",
    "city",
    "min_salary",
    "company_priority",
    "background",
    "skill_stack",
    "gap_summary",
}
# 这些能力已由 SEKB 项目落地，不能再被列为 weak。
_MUST_NOT_BE_WEAK = ("LangGraph", "RAG", "MCP", "Python")


def test_profile_readme_is_found_and_parsable():
    path = _resolve_profile_path()
    assert path is not None, "未找到 prompt/job/README.md"
    text = _load_base_profile()
    assert text != _FALLBACK_PROFILE, "README 解析失败，已回退到内置画像"

    data = yaml.safe_load(text)
    profile = data["user_profile"]
    assert _REQUIRED_README_KEYS <= set(profile), "画像缺少必需字段"

    skill_stack = profile["skill_stack"]
    for level in ("strong", "medium", "weak"):
        assert isinstance(skill_stack[level], list) and skill_stack[level], f"skill_stack.{level} 为空"

    weak_text = " ".join(skill_stack["weak"])
    for skill in _MUST_NOT_BE_WEAK:
        assert skill not in weak_text, f"{skill} 已落地，不应仍列为 weak：{skill_stack['weak']}"


def test_profile_readme_covers_delivery_experience():
    """ToB 客户交付是 FDE/售前方向的核心匹配点，不能被漏掉。"""
    profile = yaml.safe_load(_load_base_profile())["user_profile"]
    blob = yaml.dump(profile, allow_unicode=True)

    assert "交付" in blob, "画像缺少客户交付经历"
    assert "运营商" in blob, "画像缺少运营商 ToB 背景"
    assert "SEKB" in blob, "画像缺少 SEKB 项目"
    assert "FDE" in profile["target_role"], "目标岗位未包含 FDE"


def test_target_role_is_a_single_direction():
    """简历按方向定制，画像只写**一个**主方向。

    曾把 target_role 写成「FDE · 端侧 Agent · 平台架构 · 售前」四个方向并列，
    会让每一次职位分析的目标都被稀释（分析无法判断你到底要哪个岗）。
    多方向应由「搜索关键词 + 按方向定制的简历版本」承载。
    """
    profile = yaml.safe_load(_load_base_profile())["user_profile"]
    target = profile["target_role"]
    assert "·" not in target, f"target_role 不应并列多个方向：{target!r}"
    assert len(target) <= 30, f"target_role 过长，疑似并列了多个方向：{target!r}"


def test_profile_readme_does_not_leak_salary_numbers():
    """画像文件在公开仓库中，不得出现具体薪资数字（否则等于公开谈薪锚）。

    具体数字应写在**不入库**的按用户画像（服务端 data/profile/{user_id}.json 的
    min_salary_k），而不是这个 git 跟踪且推送公开仓库的文件里。
    """
    import re

    profile = yaml.safe_load(_load_base_profile())["user_profile"]
    salary = str(profile["min_salary"])
    assert not re.search(r"\d", salary), f"min_salary 不应包含具体数字：{salary!r}"


def test_fallback_profile_stays_valid():
    """兜底画像同样要能解析且字段齐全（README 缺失时不能崩）。"""
    fallback = yaml.safe_load(_FALLBACK_PROFILE)
    assert _REQUIRED_README_KEYS <= set(fallback)
    assert "交付" in fallback["background"][1]
    assert "FDE" in fallback["target_role"]
