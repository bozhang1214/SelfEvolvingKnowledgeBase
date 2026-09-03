"""系列文章识别单元测试。"""
from __future__ import annotations

from app.services.series import detect_series


class TestDetectSeries:
    def test_cn_part(self):
        r = detect_series("Flutter 教程 第2篇.md")
        assert r["series"] == "Flutter 教程"
        assert r["part"] == 2
        assert r["is_series"] is True

    def test_en_part(self):
        r = detect_series("React Native 实战 Part 1.md")
        assert r["series"] == "React Native 实战"
        assert r["part"] == 1

    def test_cn_stage(self):
        r = detect_series("大模型入门（上）.md")
        assert r["series"] == "大模型入门"
        assert r["part"] == 1

    def test_num_suffix(self):
        r = detect_series("论文阅读笔记 03.md")
        assert r["series"] == "论文阅读笔记"
        assert r["part"] == 3

    def test_not_series(self):
        r = detect_series("普通文档.md")
        assert r["is_series"] is False
        assert r["series"] == ""
