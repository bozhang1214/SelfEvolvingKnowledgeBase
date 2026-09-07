"""
系列文章识别模块。

从文件名识别「系列名 + 序号」，用于把同一系列的多篇文档归组展示。
纯启发式（无需 LLM），支持常见命名模式：

- "Flutter 教程 第1篇 / 第2篇 / 第3章"
- "React Native 实战 Part 1 / Part 2"
- "大模型入门（上）/（中）/（下）"
- "论文阅读笔记 01 / 02 / 03"

使用方式：
    from app.services.series import detect_series

    info = detect_series("Flutter 教程 第2篇.md")
    # {"series": "Flutter 教程", "part": 2, "is_series": True}
"""
from __future__ import annotations

import re

# 中文序号 → 数字
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 「第N篇/章/节/部/回/天/课/讲/期」模式
_RE_CN_PART = re.compile(r"第\s*([0-9一二三四五六七八九十]+)\s*[篇章节部回天课讲期]")
# 「Part N / PART N」
_RE_EN_PART = re.compile(r"[Pp]art\s*([0-9]+)")
# 「（上）/（中）/（下）」
_RE_CN_STAGE = re.compile(r"[（(]([上中下])[)）]")
# 「01 / 02 / 03」数字后缀（紧贴结尾）
_RE_NUM_SUFFIX = re.compile(r"[_\-\s]+(\d{1,3})$")


def _to_int(raw: str) -> int:
    """把阿拉伯数字或中文数字转为 int，无法识别返回 0。"""
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    if raw in _CN_NUM:
        return _CN_NUM[raw]
    if raw == "十":
        return 10
    # 处理「十一」「二十」等（仅覆盖到 20）
    if raw.endswith("十") and len(raw) == 2 and raw[0] in _CN_NUM:
        return _CN_NUM[raw[0]] * 10
    return 0


def _clean_base(name: str) -> str:
    """去掉文件名里的分隔符与序号残渣，得到系列名。"""
    base = name.strip()
    # 去掉尾部常见分隔/序号残留
    base = re.sub(r"[_\-—·\s]+$", "", base)
    base = base.strip(" （）()【】[]")
    return base


def detect_series(file_name: str) -> dict[str, object]:
    """
    从文件名识别系列名与序号。

    Args:
        file_name: 原始文件名（可含扩展名；文件夹上传时可能含相对路径，
                   如「3-Agent开发框架学习/第15天：...md」）

    Returns:
        {"series": str, "part": int, "is_series": bool}；
        无法识别系列时 series 为空串、is_series=False。
    """
    if not file_name:
        return {"series": "", "part": 0, "is_series": False}

    # 分离文件夹路径与文件名（利用「同一文件夹下多为同一系列」的规则）
    folder = ""
    name = file_name
    if "/" in file_name:
        folder, name = file_name.rsplit("/", 1)

    stem = name.rsplit(".", 1)[0] if "." in name else name

    # 文件夹最后一段作为「文件夹名」，文件名本身无系列名时用它兜底
    folder_base = folder.rstrip("/").rsplit("/", 1)[-1].strip() if folder else ""

    def _base_or_folder(base: str) -> str:
        return base if base else folder_base

    # 1. （上）/（中）/（下）
    m = _RE_CN_STAGE.search(name)
    if m:
        base = _base_or_folder(_clean_base(_RE_CN_STAGE.sub("", stem)))
        part = {"上": 1, "中": 2, "下": 3}[m.group(1)]
        if base:
            return {"series": base, "part": part, "is_series": True}

    # 2. 第N篇/章/节/部/回/天/课/讲/期
    m = _RE_CN_PART.search(stem)
    if m:
        part = _to_int(m.group(1))
        base = _base_or_folder(_clean_base(stem[: m.start()]))
        if base and part > 0:
            return {"series": base, "part": part, "is_series": True}

    # 3. Part N
    m = _RE_EN_PART.search(stem)
    if m:
        part = _to_int(m.group(1))
        base = _base_or_folder(_clean_base(stem[: m.start()]))
        if base and part > 0:
            return {"series": base, "part": part, "is_series": True}

    # 4. 结尾数字后缀（01 / 02 / 03）
    m = _RE_NUM_SUFFIX.search(stem)
    if m:
        part = _to_int(m.group(1))
        base = _base_or_folder(_clean_base(stem[: m.start()]))
        if base and part > 0:
            return {"series": base, "part": part, "is_series": True}

    return {"series": "", "part": 0, "is_series": False}
