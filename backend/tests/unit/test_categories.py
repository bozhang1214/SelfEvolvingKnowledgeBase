"""
知识库多级分类目录的单元测试

测试内容：
- DEFAULT_CATEGORY_TREE 结构合法（三级、非空）
- flatten_categories 展平为 (l1, l2, l3) 元组列表
- validate_category 对合法/非法目录的判定
- build_category_prompt 生成 LLM 提示文本
- to_tree_response 生成前端目录树响应格式
- DEFAULT_FALLBACK_CATEGORY 默认降级分类
"""

from __future__ import annotations

from app.core.categories import (
    DEFAULT_CATEGORY_TREE,
    DEFAULT_FALLBACK_CATEGORY,
    build_category_prompt,
    flatten_categories,
    to_tree_response,
    validate_category,
)


class TestCategoryTree:
    """测试目录结构定义"""

    def test_tree_non_empty(self):
        assert len(DEFAULT_CATEGORY_TREE) > 0
        for l1, l2_dict in DEFAULT_CATEGORY_TREE.items():
            assert l1
            assert l2_dict, f"{l1} 下应有二级分类"
            for l2, l3_list in l2_dict.items():
                assert l2
                assert l3_list, f"{l1}/{l2} 下应有三级分类"

    def test_fallback_category_exists_in_tree(self):
        l1, l2, l3 = DEFAULT_FALLBACK_CATEGORY
        assert validate_category(l1, l2, l3) is True


class TestFlattenCategories:
    """测试目录展平"""

    def test_returns_tuples(self):
        flat = flatten_categories()
        assert len(flat) > 0
        assert all(isinstance(item, tuple) and len(item) == 3 for item in flat)

    def test_every_flattened_tuple_is_valid(self):
        """展平出的每个 (l1, l2, l3) 都能通过 validate_category"""
        for l1, l2, l3 in flatten_categories():
            assert validate_category(l1, l2, l3) is True, f"非法目录: {l1}/{l2}/{l3}"

    def test_contains_known_category(self):
        flat = flatten_categories()
        assert ("技术开发", "编程语言", "Python") in flat


class TestValidateCategory:
    """测试分类合法性校验"""

    def test_valid_category(self):
        assert validate_category("技术开发", "编程语言", "Python") is True

    def test_invalid_l1(self):
        assert validate_category("不存在的类", "编程语言", "Python") is False

    def test_invalid_l2(self):
        assert validate_category("技术开发", "不存在的子类", "Python") is False

    def test_invalid_l3(self):
        assert validate_category("技术开发", "编程语言", "不存在的细类") is False

    def test_empty_strings(self):
        assert validate_category("", "", "") is False


class TestBuildCategoryPrompt:
    """测试分类提示文本构建"""

    def test_contains_all_levels(self):
        prompt = build_category_prompt()
        assert "技术开发" in prompt
        assert "编程语言" in prompt
        assert "Python" in prompt

    def test_non_empty_and_multiline(self):
        prompt = build_category_prompt()
        assert prompt.strip()
        assert "\n" in prompt


class TestToTreeResponse:
    """测试前端目录树响应"""

    def test_returns_list_with_keys(self):
        tree = to_tree_response()
        assert isinstance(tree, list)
        assert len(tree) > 0
        assert all("key" in n and "title" in n for n in tree)

    def test_three_level_nesting(self):
        tree = to_tree_response()
        # 找到 "技术开发" 节点
        tech = next(n for n in tree if n["key"] == "技术开发")
        assert "children" in tech
        lang = next(n for n in tech["children"] if n["key"] == "技术开发/编程语言")
        assert "children" in lang
        python_node = next(n for n in lang["children"] if n["title"] == "Python")
        assert python_node["key"] == "技术开发/编程语言/Python"
