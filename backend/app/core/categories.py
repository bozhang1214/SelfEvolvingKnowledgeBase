"""
知识库多级分类目录定义。

定义三级分类体系（大类 → 子类 → 细类），用于：
- 文档上传时由 LLM 自动匹配分类
- 前端分类目录树展示与筛选
- 手动重分类时的可选目录

分类目录可通过 ``DEFAULT_CATEGORY_TREE`` 获取，结构为：
    { 一级大类: { 二级子类: [三级细类, ...] } }

使用方式：
    from app.core.categories import DEFAULT_CATEGORY_TREE, flatten_categories

    tree = DEFAULT_CATEGORY_TREE
    flat = flatten_categories()  # [("技术开发","编程语言","Python"), ...]
"""
from __future__ import annotations

from typing import Any

# ============================================================
# 默认三级分类目录
# ============================================================

DEFAULT_CATEGORY_TREE: dict[str, dict[str, list[str]]] = {
    "技术开发": {
        "编程语言": [
            "Python",
            "JavaScript/TypeScript",
            "Go",
            "Java",
            "Rust",
        ],
        "AI / Agent 开发": [
            "大模型 / LLM",
            "Agent 框架",
            "RAG 检索增强",
            "Prompt 工程",
        ],
        "Web 开发": ["前端", "后端", "全栈"],
        "数据存储": ["关系型数据库", "非关系型数据库", "向量数据库"],
        "DevOps / 运维": ["容器编排 (Docker/K8s)", "CI/CD", "监控告警"],
        "系统设计": ["架构模式", "分布式系统"],
    },
    "产品设计": {
        "产品规划": ["需求分析", "产品路线图"],
        "用户体验 (UX)": ["交互设计", "用户研究"],
        "原型设计": ["原型工具", "设计规范"],
    },
    "商业管理": {
        "市场营销": ["内容营销", "增长策略"],
        "财务管理": ["财务核算", "投资融资"],
        "团队管理": ["组织架构", "绩效管理"],
    },
    "学习成长": {
        "阅读笔记": ["读书笔记", "文章摘要"],
        "课程学习": ["在线课程", "培训资料"],
        "技能提升": ["学习方法", "职业发展"],
    },
    "生活百科": {
        "健康养生": ["运动健身", "饮食营养"],
        "旅行出行": ["旅行攻略", "交通出行"],
        "美食生活": ["菜谱", "餐厅推荐"],
    },
    "其他": {
        "待分类": ["未分类"],
    },
}


# 默认归入分类（分类失败时使用）
DEFAULT_FALLBACK_CATEGORY: tuple[str, str, str] = ("其他", "待分类", "未分类")


def flatten_categories() -> list[tuple[str, str, str]]:
    """
    将三级目录展平为 (l1, l2, l3) 元组列表。

    用于 LLM 分类提示词构建与校验。
    """
    flat: list[tuple[str, str, str]] = []
    for l1, l2_dict in DEFAULT_CATEGORY_TREE.items():
        for l2, l3_list in l2_dict.items():
            for l3 in l3_list:
                flat.append((l1, l2, l3))
    return flat


def validate_category(l1: str, l2: str, l3: str) -> bool:
    """校验三级分类是否存在于默认目录中。"""
    l2_dict = DEFAULT_CATEGORY_TREE.get(l1)
    if not l2_dict:
        return False
    l3_list = l2_dict.get(l2)
    if l3_list is None:
        return False
    return l3 in l3_list


def build_category_prompt() -> str:
    """
    构建供 LLM 使用的分类目录提示文本。

    返回缩进展示的目录树，便于 LLM 理解层级关系。
    """
    lines: list[str] = []
    for l1, l2_dict in DEFAULT_CATEGORY_TREE.items():
        lines.append(f"- {l1}")
        for l2, l3_list in l2_dict.items():
            lines.append(f"  - {l2}")
            for l3 in l3_list:
                lines.append(f"    - {l3}")
    return "\n".join(lines)


def to_tree_response() -> list[dict[str, Any]]:
    """
    转换为前端目录树响应格式（Antd Tree 兼容）。

    返回结构：
        [{"key": l1, "title": l1, "children": [
            {"key": "l1/l2", "title": l2, "children": [
                {"key": "l1/l2/l3", "title": l3}
            ]}
        ]}]
    """
    tree: list[dict[str, Any]] = []
    for l1, l2_dict in DEFAULT_CATEGORY_TREE.items():
        l2_nodes: list[dict[str, Any]] = []
        for l2, l3_list in l2_dict.items():
            l3_nodes = [
                {"key": f"{l1}/{l2}/{l3}", "title": l3}
                for l3 in l3_list
            ]
            l2_nodes.append({
                "key": f"{l1}/{l2}",
                "title": l2,
                "children": l3_nodes,
            })
        tree.append({
            "key": l1,
            "title": l1,
            "children": l2_nodes,
        })
    return tree
