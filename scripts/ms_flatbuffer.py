#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MSL2（`.ms`）图的**纯 Python 解析**——不依赖 flatc、不依赖 MindSpore Lite runtime。

## 为什么需要它

`.ms` 是 MindSpore Lite 的 flatbuffer 自定义格式（头部 4 字节长度 + 魔数 `MSL2`，
其后是 `mindspore::schema::MetaGraph`）。当"设备上算出来的向量不对"时，
第一件必须分清的事是**转换产物里图结构到底长什么样**：

  · 掩码输入还连着算子吗？还是被常量折叠了？
  · 每个输入的 `dims`/`dtype` 是什么？内核真正用的宽度是多少？

这些问题的答案全在 flatbuffer 里，而 flatbuffer 的 vtable 偏移是**公开的**
（schema 就随包发：`runtime/include/schema/model_generated.h`）。
所以这里手写一个最小读取器：够用、无依赖、可在 CI 里跑（不需要 docker、不需要设备）。

## 偏移来源（从 `model_generated.h` 抄，别再猜）

    MetaGraph: name=4 version=6 fmkType=8 inputIndex=10 outputIndex=12 memPoolSize=14
               nodes=16 allTensors=18 subGraph=20 obfuscate=22 obfMeta=24
    SubGraph : name=4 inputIndices=6 outputIndices=8 nodeIndices=10 tensorIndices=12
    CNode    : name=4 primitive=8 inputIndex=10 outputIndex=12 quantType=14 deviceType=16
    Tensor   : nodeType=4 dataType=6 dims=8 format=10 refCount=12 offset=14
               data=16 quantParams=18 quantClusters=20 name=22
    Primitive: value_type=4 value=6

## ⚠️ 一个实测的口径坑（写下来免得再错）

flatbuffer 里 `Tensor.dataType`（`TensorType`，`ops_types_generated.h`）与
ONNX/OHOS 的数据类型枚举**不是同一套编号**：

| flatbuffer `dtype` | 含义 | OHOS `OH_AI_DATATYPE_*` 里同名的值 |
|---|---|---|
| 34 | **INT32** | 34 也叫 INT32（**巧合一致**） |
| 35 | INT64 | OHOS 的 35 才是 INT64 |
| 43 | FLOAT32 | 43（一致） |

`bge-maskinput.ms` 的两个 token 输入 `dtype=34`，**在 OHOS 口径下就是 INT32**；
但运行期 `OH_AI_TensorGetDataType` 实测把三个输入都报成 34（掩码本该是 43），
所以**dtype getter 不可信**，唯一可信的判据是 `OH_AI_TensorGetDataSize`（缓冲字节数）。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

# flatbuffer TensorType → 元素字节数（只列这个模型/常见情况）
_TENSOR_TYPE_BYTES = {
    0: 0,     # None
    30: 1,    # BOOL
    32: 1,    # INT8
    33: 2,    # INT16
    34: 4,    # INT32
    35: 8,    # INT64
    37: 1,    # UINT8
    38: 2,    # UINT16
    39: 4,    # UINT32
    40: 8,    # UINT64
    42: 2,    # FLOAT16
    43: 4,    # FLOAT32
    44: 8,    # FLOAT64
}

_TENSOR_TYPE_NAMES = {
    30: "BOOL", 32: "INT8", 33: "INT16", 34: "INT32", 35: "INT64",
    37: "UINT8", 38: "UINT16", 39: "UINT32", 40: "UINT64",
    42: "FLOAT16", 43: "FLOAT32", 44: "FLOAT64",
}


def _u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _i32(b, o):
    return struct.unpack_from("<i", b, o)[0]


class _FB:
    """一个 flatbuffer 缓冲上的最小读取器。"""

    def __init__(self, body: bytes):
        self.b = body

    def field_off(self, table: int, vtoff: int) -> int:
        """字段相对表头的偏移；不存在返回 0。

        ⚠️ 这里有两个必须显式拦住的情况（都真实踩过，症状是"读到天文数字偏移"）：
          · `vt_off == 0` —— flatbuffer 用"vtable 偏移 0"表示**该字段不存在**；
            若不拦，`table - 0 = table` 会把表自身的第一个字段误当 vtable，读出垃圾；
          · vtable 越界 —— 说明调用方给的 `table` 地址本身是错的（例如把"偏移量"
            当成"地址"用）。**静默继续读会读到别处的字节、给出看似合理的假结论**，
            所以这里直接抛，不做"兜底猜测"。
        """
        if table <= 0 or table + 4 > len(self.b):
            raise ValueError(f"table 地址越界：{table}（缓冲 {len(self.b)} B）")
        vt_off = _i32(self.b, table)
        if vt_off == 0:
            return 0
        vt = table - vt_off
        if vt < 0 or vt + 2 > len(self.b):
            raise ValueError(f"vtable 地址越界：table={table} vt_off={vt_off} → vt={vt}")
        if vtoff >= _u16(self.b, vt):
            return 0
        return _u16(self.b, vt + vtoff)

    def scalar(self, table: int, vtoff: int, fmt: str, default):
        o = self.field_off(table, vtoff)
        return default if o == 0 else struct.unpack_from(fmt, self.b, table + o)[0]

    def offset(self, table: int, vtoff: int) -> int:
        o = self.field_off(table, vtoff)
        return 0 if o == 0 else table + o + _u32(self.b, table + o)

    def string(self, table: int, vtoff: int):
        p = self.offset(table, vtoff)
        if p == 0:
            return None
        n = _u32(self.b, p)
        return self.b[p + 4:p + 4 + n].decode("utf-8", "replace")

    def vec_scalars(self, table: int, vtoff: int, fmt: str):
        p = self.offset(table, vtoff)
        if p == 0:
            return []
        n = _u32(self.b, p)
        size = struct.calcsize(fmt)
        return [struct.unpack_from(fmt, self.b, p + 4 + size * i)[0] for i in range(n)]

    def vec_tables(self, table: int, vtoff: int):
        """`vector<offset<Table>>` → 各表地址。

        ⚠️ 偏移是**相对它自己那个 4 字节槽**的，而槽的位置在长度字段之后：
        元素 i 的槽地址 = p + 4 + 4*i（p 是长度字段地址）。
        实测写错这一处（漏掉 p 后面的 +4）时，每个表地址会落在元素**中间**，
        症状是"vtable 偏移读出天文数字"，而向量长度看起来是对的——极具迷惑性。
        """
        p = self.offset(table, vtoff)
        if p == 0:
            return []
        n = _u32(self.b, p)
        return [p + 4 + 4 * i + _u32(self.b, p + 4 + 4 * i) for i in range(n)]


@dataclass
class MsTensor:
    index: int
    name: Optional[str]
    dtype: int
    dims: list[int]
    node_type: int
    has_data: bool

    @property
    def dtype_name(self) -> str:
        return _TENSOR_TYPE_NAMES.get(self.dtype, f"#{self.dtype}")

    @property
    def element_num(self) -> int:
        n = 1
        for d in self.dims:
            n *= max(d, 1)
        return n

    @property
    def elem_bytes(self) -> int:
        return _TENSOR_TYPE_BYTES.get(self.dtype, 0)

    @property
    def declared_bytes(self) -> int:
        return self.element_num * self.elem_bytes


@dataclass
class MsNode:
    index: int
    name: Optional[str]
    op: str
    inputs: list[int] = field(default_factory=list)
    outputs: list[int] = field(default_factory=list)


@dataclass
class MsGraph:
    path: str
    file_bytes: int
    magic: str
    fmk_type: int
    input_indices: list[int]
    output_indices: list[int]
    nodes: list[MsNode]
    tensors: list[MsTensor]
    subgraph_inputs: list[int]

    # ── 便利访问 ──────────────────────────────────────────────────────────
    def tensor(self, i: int) -> MsTensor:
        return self.tensors[i]

    def by_name(self, name: str) -> list[MsTensor]:
        return [t for t in self.tensors if t.name == name]

    def consumers(self, tensor_index: int) -> list[MsNode]:
        return [n for n in self.nodes if tensor_index in n.inputs]

    def inputs(self) -> list[MsTensor]:
        return [self.tensors[i] for i in self.input_indices]

    def mask_input(self) -> MsTensor | None:
        """按名字找 additive mask 输入（本项目约定名 `sekb_additive_mask_zero`）。"""
        for t in self.inputs():
            if t.name and "additive_mask" in t.name:
                return t
        return None

    def attention_add_users(self, tensor_index: int) -> list[MsNode]:
        """该张量的消费者里，属于 `.../attention/self/Add` 的那些。"""
        return [n for n in self.consumers(tensor_index)
                if "attention/self/Add" in (n.name or "")]


def load(path: str) -> MsGraph:
    raw = open(path, "rb").read()
    if len(raw) < 8:
        raise ValueError(f"{path} 太短，不是 MSL2 文件")
    magic = raw[4:8].decode("ascii", "replace")
    if magic != "MSL2":
        raise ValueError(f"{path} 魔数={magic!r}（应为 MSL2）")
    fb = _FB(raw[8:])
    # flatbuffer：首 4 字节是 root table 的偏移，**相对缓冲起点**（不是相对它自己的地址）。
    # 实测：本文件 root 偏移=32，而 table=32 处读出的 vtable 偏移=22、vtable 大小=22，
    # 全部自洽；写成 `+4` 会让 table 落在数据里、vtable 偏移读出天文数字。
    mg = _u32(raw[8:], 0)

    nodes = []
    for k, n in enumerate(fb.vec_tables(mg, 16)):
        prim = fb.offset(n, 8)
        vt = fb.scalar(prim, 4, "<B", 0) if prim else 0
        nodes.append(MsNode(
            index=k,
            name=fb.string(n, 4),
            op=_prim_name(vt),
            inputs=fb.vec_scalars(n, 10, "<I"),
            outputs=fb.vec_scalars(n, 12, "<I"),
        ))

    tensors = []
    for k, t in enumerate(fb.vec_tables(mg, 18)):
        tensors.append(MsTensor(
            index=k,
            name=fb.string(t, 22),
            dtype=fb.scalar(t, 6, "<i", -1),
            dims=fb.vec_scalars(t, 8, "<i"),
            node_type=fb.scalar(t, 4, "<i", 0),
            has_data=bool(fb.field_off(t, 16)),
        ))

    subs = fb.vec_tables(mg, 20)
    sub_in = fb.vec_scalars(subs[0], 6, "<I") if subs else []

    return MsGraph(
        path=path, file_bytes=len(raw), magic=magic,
        fmk_type=fb.scalar(mg, 8, "<i", -1),
        input_indices=fb.vec_scalars(mg, 10, "<I"),
        output_indices=fb.vec_scalars(mg, 12, "<I"),
        nodes=nodes, tensors=tensors, subgraph_inputs=sub_in,
    )


_PRIM_NAMES: Optional[dict[int, str]] = None


def _prim_name(v: int) -> str:
    """`PrimitiveType` 名字表（从随包的 ops_generated.h 抽，缺失时退回编号）。"""
    global _PRIM_NAMES
    if _PRIM_NAMES is None:
        _PRIM_NAMES = {}
        import os
        import re
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(here)
        cands = []
        for base, dirs, files in os.walk(os.path.join(root, ".tooling", "mindspore-lite")):
            if "ops_generated.h" in files and os.sep + "schema" + os.sep in base + os.sep:
                cands.append(os.path.join(base, "ops_generated.h"))
            if len(base.split(os.sep)) > 12:
                dirs[:] = []
        for c in cands[:1]:
            try:
                src = open(c, encoding="utf-8", errors="replace").read()
                i = src.index("enum PrimitiveType : uint8_t {")
                j = src.index("};", i)
                for mm in re.finditer(r"PrimitiveType_(\w+) = (\d+),", src[i:j]):
                    _PRIM_NAMES[int(mm.group(2))] = mm.group(1)
            except Exception:
                pass
    return _PRIM_NAMES.get(v, f"op#{v}")


if __name__ == "__main__":
    import json
    import sys

    g = load(sys.argv[1])
    print(json.dumps({
        "path": g.path,
        "file_bytes": g.file_bytes,
        "magic": g.magic,
        "fmk_type": g.fmk_type,
        "node_count": len(g.nodes),
        "tensor_count": len(g.tensors),
        "inputs": [
            {"index": t.index, "name": t.name, "dtype": t.dtype, "dtype_name": t.dtype_name,
             "dims": t.dims, "element_num": t.element_num, "declared_bytes": t.declared_bytes,
             "consumers": [n.name for n in g.consumers(t.index)]}
            for t in g.inputs()
        ],
        "outputs": g.output_indices,
        "subgraph_inputs": g.subgraph_inputs,
    }, ensure_ascii=False, indent=2))
