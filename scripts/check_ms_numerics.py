#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 **Linux 版 MindSpore Lite runtime** 上跑 `.ms`，回答"转换产物保真吗"。

## 为什么必须跑 runtime，而不是只看 flatbuffer

flatbuffer 只能证明"图上连着"，不能证明"运行期真的用了"。二者在本项目里**真的分叉过**：
图里 `sekb_additive_mask_zero → Transpose → 4×Add → Softmax` 一条不缺，
但设备上算出来的向量与 Android 差三倍。所以判据必须是**运行期的数值**：

① `sekb_additive_mask_zero` 在不在图输入里？（不在 → 被折叠）
② 每个输入的 `OH_AI_TensorGetDataSize` 与"元素数 × flatbuffer 声明的宽度"一致吗？
   （不一致 = 缓冲比声明小，宿主按声明宽度写就**越界**——本缺陷就是这条）
③ 正确掩码算出的 CLS 与 ONNX(ORT) 参考**余弦 ≈ 1**吗？
④ 掩码全零时结果**明显不同**吗？（相同 = 运行时忽略掩码）

## ⚠️ Linux runtime 与设备 runtime 的两点差异（不夸大结论）

* Linux 包导出的是 **MS API**（`MSModelCreate/...`），鸿蒙用的是 **OH_AI API**；
  两者是同一套 C 接口的两个名字（入参/语义一致）。
* Linux runtime 下**越界写会崩**（glibc 报 `malloc(): invalid size`），
  鸿蒙的 musl 不报——所以"设备上没崩"**不能**当作"没越界"的证据。

用法（由 `scripts/model_to_ms.sh --check-ms` 调用）：

    python3 scripts/check_ms_numerics.py --ms <bge-maskinput.ms> --ref <maskref.json>
    python3 scripts/check_ms_numerics.py --ms <bge-maskinput.ms> --ref <maskref.json> --red
"""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

import numpy as np

# runtime 库的位置：优先环境变量，其次"仓库内相对路径"（脚本可被任意挂载点调用）
def _lib_candidates() -> list:
    import os
    root = Path(__file__).resolve().parent.parent
    out = []
    env = os.environ.get("SEKB_MS_LIB")
    if env:
        out.append(env)
    # 1) 相对本仓库（无论仓库被挂到 /w 还是别的路径都成立）
    out += [
        str(root / ".tooling/mindspore-lite/mindspore-lite-2.7.0-linux-aarch64/runtime/lib/libmindspore-lite.so"),
        # 2) 兼容 model_to_ms.sh 的固定挂载点
        "/w/mindspore-lite/mindspore-lite-2.7.0-linux-aarch64/runtime/lib/libmindspore-lite.so",
    ]
    return out

# MSLite 内部 TensorType（注意与 OHOS 的 OH_AI_DATATYPE_* 不是一套编号：
# 这里 34=INT32 / 35=INT64 / 43=FLOAT32，见 scripts/ms_flatbuffer.py 头部说明）
TENSOR_TYPE_BYTES = {30: 1, 32: 1, 33: 2, 34: 4, 35: 8, 37: 1, 38: 2,
                     39: 4, 40: 8, 42: 2, 43: 4, 44: 8}
TENSOR_TYPE_NAMES = {30: "BOOL", 32: "INT8", 33: "INT16", 34: "INT32", 35: "INT64",
                     37: "UINT8", 38: "UINT16", 39: "UINT32", 40: "UINT64",
                     42: "FLOAT16", 43: "FLOAT32", 44: "FLOAT64"}

# MS API 里的 dtype 枚举（types 头：kMSDataTypeNumberTypeInt32=34 / Int64=35 / Float32=43）
MS_DT_INT32, MS_DT_INT64, MS_DT_FP32 = 34, 35, 43


class HandleArray(ctypes.Structure):
    _fields_ = [("handle_num", ctypes.c_size_t), ("handle_list", ctypes.POINTER(ctypes.c_void_p))]


class Runner:
    def __init__(self, model_path: str, lib_path: str):
        self.lib = ctypes.CDLL(lib_path)
        L = self.lib
        L.MSContextCreate.restype = ctypes.c_void_p
        L.MSDeviceInfoCreate.restype = ctypes.c_void_p
        L.MSDeviceInfoCreate.argtypes = [ctypes.c_int]
        L.MSContextSetThreadNum.argtypes = [ctypes.c_void_p, ctypes.c_int32]
        L.MSContextAddDeviceInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        L.MSModelCreate.restype = ctypes.c_void_p
        L.MSModelBuildFromFile.restype = ctypes.c_int
        L.MSModelBuildFromFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
        L.MSModelGetInputs.restype = HandleArray
        L.MSModelGetInputs.argtypes = [ctypes.c_void_p]
        L.MSModelGetOutputs.restype = HandleArray
        L.MSModelGetOutputs.argtypes = [ctypes.c_void_p]
        L.MSModelPredict.restype = ctypes.c_int
        L.MSModelPredict.argtypes = [ctypes.c_void_p, HandleArray, ctypes.POINTER(HandleArray),
                                     ctypes.c_void_p, ctypes.c_void_p]
        L.MSTensorGetName.restype = ctypes.c_char_p
        L.MSTensorGetName.argtypes = [ctypes.c_void_p]
        L.MSTensorGetElementNum.restype = ctypes.c_int64
        L.MSTensorGetElementNum.argtypes = [ctypes.c_void_p]
        L.MSTensorGetDataSize.restype = ctypes.c_size_t
        L.MSTensorGetDataSize.argtypes = [ctypes.c_void_p]
        L.MSTensorGetDataType.restype = ctypes.c_int
        L.MSTensorGetDataType.argtypes = [ctypes.c_void_p]
        L.MSTensorGetMutableData.restype = ctypes.c_void_p
        L.MSTensorGetMutableData.argtypes = [ctypes.c_void_p]

        ctx = L.MSContextCreate()
        L.MSContextSetThreadNum(ctx, 2)
        L.MSContextAddDeviceInfo(ctx, L.MSDeviceInfoCreate(0))
        self.model = L.MSModelCreate()
        st = L.MSModelBuildFromFile(self.model, model_path.encode(), 0, ctx)
        if st != 0:
            raise SystemExit(f"❌ MSModelBuildFromFile status={st}")
        self.arr = L.MSModelGetInputs(self.model)
        self.outs = L.MSModelGetOutputs(self.model)
        self.tensors: dict[str, ctypes.c_void_p] = {}
        self.meta: dict[str, dict] = {}
        for i in range(self.arr.handle_num):
            t = self.arr.handle_list[i]
            name = L.MSTensorGetName(t).decode()
            self.tensors[name] = t
            self.meta[name] = {
                "element_num": int(L.MSTensorGetElementNum(t)),
                "data_size": int(L.MSTensorGetDataSize(t)),
                "dtype": int(L.MSTensorGetDataType(t)),
            }
        self.out_num = int(L.MSTensorGetElementNum(self.outs.handle_list[0]))

    def put(self, name: str, values: np.ndarray, red_skip_mask: bool = False) -> int:
        """按**缓冲实际字节数**写；返回写入字节数。red_skip_mask=True 时故意不写（留初始值）。"""
        t = self.tensors[name]
        size = int(self.lib.MSTensorGetDataSize(t))
        p = self.lib.MSTensorGetMutableData(t)
        if red_skip_mask:
            return 0
        if values.nbytes != size:
            raise SystemExit(
                f"❌ 要写 {name} {values.nbytes} B，但张量缓冲只有 {size} B —— "
                f"按声明宽度写就必然越界（这正是被修掉的那个缺陷）")
        ctypes.memmove(p, values.ctypes.data, size)
        return size

    def predict(self) -> np.ndarray:
        o = HandleArray()
        st = self.lib.MSModelPredict(self.model, self.arr, ctypes.byref(o), None, None)
        if st != 0:
            raise SystemExit(f"❌ MSModelPredict status={st}")
        p = self.lib.MSTensorGetMutableData(o.handle_list[0])
        return np.ctypeslib.as_array(ctypes.cast(p, ctypes.POINTER(ctypes.c_float)),
                                     shape=(self.out_num,)).copy()


def cos(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-30))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ms", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--lib", default="")
    ap.add_argument("--red", action="store_true",
                    help="反证：故意**不写掩码**（留初始零）→ 必须变红")
    ap.add_argument("--ref-key", choices=["auto", "i64", "i32"], default="auto",
                    help="用哪份参考做逐元素比对；auto = 取与 .ms 声明的 token dtype 一致的那份")
    ap.add_argument("--max-abs-diff", type=float, default=1e-5,
                    help="与参考 CLS 向量的最大逐元素差上限（默认 1e-5）")
    ap.add_argument("--delta-min", type=float, default=0.05)
    a = ap.parse_args()

    ms = Path(a.ms)
    if not ms.is_file():
        print(f"❌ 缺 {ms}", file=sys.stderr)
        return 2

    lib = a.lib
    if not lib:
        for c in _lib_candidates():
            if Path(c).is_file():
                lib = c
                break
    if not lib or not Path(lib).is_file():
        print("❌ 找不到 libmindspore-lite.so（用 --lib 指定）", file=sys.stderr)
        return 2

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ms_flatbuffer

    g = ms_flatbuffer.load(str(ms))
    ref = json.loads(Path(a.ref).read_text(encoding="utf-8"))
    mask_name = ref["mask_input"]

    print(f"── 被测产物 {ms.name}：{g.file_bytes} B，节点 {len(g.nodes)}，张量 {len(g.tensors)} ──")
    print("── flatbuffer 声明的图输入 ──")
    for t in g.inputs():
        print(f"   [{t.index}] {t.name} dtype={t.dtype}({t.dtype_name}) dims={t.dims} "
              f"元素数={t.element_num} 声明字节={t.declared_bytes} "
              f"消费者={[n.name for n in g.consumers(t.index)]}")

    r = Runner(str(ms), lib)
    print("── runtime 报告的输入缓冲 ──")
    checks: list[tuple[str, bool, str]] = []

    if mask_name not in r.meta:
        checks.append(("mask_input_present", False,
                       f"运行期输入里没有 {mask_name}：{[*r.meta]}（输入被常量折叠了）"))
    else:
        checks.append(("mask_input_present", True, f"输入名={[*r.meta]}"))

    # ② 声明宽度 vs 缓冲宽度
    #    注意：runtime 的 dtype 值可能与 flatbuffer 不同（实测会不同），所以判据以
    #    "缓冲字节数 / 元素数" 落在 {1,2,4,8} 且 ≤ 声明宽度 为准，并**打印实测值**。
    for name, m in r.meta.items():
        n, sz = m["element_num"], m["data_size"]
        if n <= 0:
            checks.append((f"buffer_size[{name}]", False, f"元素数={n} 非法"))
            continue
        stride = sz / n
        ft = next((t for t in g.tensors if t.name == name), None)
        declared = ft.declared_bytes if ft else -1
        ok = stride in (1.0, 2.0, 4.0, 8.0) and (declared < 0 or sz <= declared)
        detail = (f"元素数={n} 缓冲={sz}B 实测每元素={stride}B；"
                  f"flatbuffer 声明={TENSOR_TYPE_NAMES.get(ft.dtype, ft.dtype) if ft else '?'}"
                  f"（{declared}B）")
        if not ok:
            detail += " ← 缓冲比声明还大/非整数宽度，异常"
        checks.append((f"buffer_size[{name}]", ok, detail))

    # 参考向量按 label 索引
    cases = ref["cases"]
    # 选参考：与 .ms 里 token 输入的**声明宽度**一致的那份（int32 的产物就比 int32 参考）
    ref_key = a.ref_key
    if ref_key == "auto":
        stride = None
        for name in ("input_ids",):
            t = r.tensors[name]
            stride = int(r.lib.MSTensorGetDataSize(t)) // int(r.lib.MSTensorGetElementNum(t))
        ref_key = "i32" if stride == 4 else "i64"
        print(f"── auto 参考选择：input_ids 缓冲每元素 {stride} B → 用 {ref_key} 参考 ──")
    ref_cls = ref["reference_cls"][ref_key]
    d_refs = float(max(np.max(np.abs(np.array(x) - np.array(y)))
                       for x, y in zip(ref["reference_cls"]["i64"], ref["reference_cls"]["i32"])))
    print(f"── 两份参考自身的最大逐元素差 = {d_refs:.3e}（应 ≈0，证明 dtype 声明不改语义）──")

    per_case = {}
    for k, case in enumerate(cases):
        ids = np.array(case["input_ids"], dtype=np.int64).reshape(1, ref["maxlen"])
        types = np.array(case["token_type_ids"], dtype=np.int64).reshape(1, ref["maxlen"])
        mask = np.zeros((1, 1, ref["maxlen"], ref["maxlen"]), dtype=np.float32)
        mask[0, 0, :, len(case["token_ids"]):] = -np.inf
        mask_zero = np.zeros_like(mask)

        # ① token 输入：**按缓冲实际宽度**打包（int32 就是 int32），不硬编码 int64
        for name, src in (("input_ids", ids), ("token_type_ids", types)):
            t = r.tensors[name]
            want = int(r.lib.MSTensorGetDataSize(t))
            stride = want // int(r.lib.MSTensorGetElementNum(t))
            packed = src.astype({4: np.int32, 8: np.int64}[stride]).reshape(-1)
            r.put(name, packed)
        r.put(mask_name, mask, red_skip_mask=a.red)
        out = r.predict()
        v = out[:512].astype(np.float64)
        v = v / (np.linalg.norm(v) + 1e-12)
        per_case[(case["label"], "proper")] = v

        r.put(mask_name, mask_zero)
        out0 = r.predict()
        v0 = out0[:512].astype(np.float64)
        per_case[(case["label"], "zero")] = v0 / (np.linalg.norm(v0) + 1e-12)

        d = float(np.max(np.abs(v - np.asarray(ref_cls[k], dtype=np.float64))))
        checks.append((f"cls_vs_onnx[{case['label']}]", d <= a.max_abs_diff,
                       f"与 ONNX({ref_key}) 参考的最大逐元素差={d:.3e}（阈值 ≤{a.max_abs_diff}）"))

    delta = cos(per_case[("A", "proper")], per_case[("A", "zero")])
    eff = abs(delta - 1.0)
    checks.append(("mask_effective", eff > a.delta_min,
                   f"正确掩码 vs 全零掩码的余弦={delta:.6f}，偏离 1 的量={eff:.4f}"
                   f"（阈值 >{a.delta_min}；≈0 说明 mask 输入被运行时忽略）"))

    c_ms = cos(per_case[("A", "proper")], per_case[("B", "proper")])
    checks.append(("ms_matches_host_cosine", abs(c_ms - ref["host_cosine"]["proper"]) <= 0.002,
                   f".ms cos(A,B)={c_ms:.4f} vs host ORT {ref['host_cosine']['proper']:.4f}"
                   f"（阈值 ±0.002；这就是'转换产物与 ONNX 同空间'的数值判据）"))

    print()
    nfail = 0
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name} — {detail}")
        nfail += 0 if ok else 1
    verdict = "PASS" if nfail == 0 else "FAIL"
    print(f"\nMS-CHECK {verdict} {len(checks) - nfail}/{len(checks)}")
    if a.red:
        # 反证模式：**期望** mask_effective / cls_vs_onnx 变红
        if nfail > 0:
            print("✅ 反证成立：不喂掩码 → 确实变红（判据不是摆设）")
            return 0
        print("❌ 反证失败：不喂掩码竟然还是全绿 → 判据无效", file=sys.stderr)
        return 1
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
