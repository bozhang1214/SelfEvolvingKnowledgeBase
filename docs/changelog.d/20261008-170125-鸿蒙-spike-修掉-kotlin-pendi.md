## 2026-10-08（鸿蒙 spike：修掉 kotlin-pending 的同源 double free + 加构建期守卫）

- **背景**：上一轮修掉 `MsLite.kt` 的 double free（`memScoped` 缓冲交给 `OH_AI_TensorSetData`
  → 张量析构时二次释放）后，`kotlin-pending/MindSporeBgeEmbedding.kt` 里**还有同一处写法**。
  它未编入构建，所以上一轮的验证覆盖不到——启用那天会重演同一个崩溃。

- **改动**：
  - `MindSporeBgeEmbedding.kt`：同法修掉（新增 `fillI64`，写 `OH_AI_TensorGetMutableData`
    的模型自有缓冲；删掉三块 `allocArray` + `OH_AI_TensorSetData` 及其导入）。
    文件头/方法注释标注：**本次改法未经编译验证**（原因见下）。
  - `scripts/harmony_spike.sh`：新增构建期守卫 `verify_no_tensor_setdata`，
    `kn|hap|all|mindspore` 四个动作在**构建前**先扫 `knspike/src` 下所有 `.kt`
    （**含 `kotlin-pending/`**）：出现 `OH_AI_TensorSetData(` 调用即失败并打印文件:行号与正确写法。
  - `apps/harmony/README.md`：把"遗留"改成准确的现状——那处**已修但无法验证**，因为
    `apps/shared` 没有 ohos 目标、`spike` 也只 `include(":knspike")`，
    **启用它是功能级任务（加 ohos 目标 + 接线 + 真机跑通）**，不是改一行能验的。

- **验证**：
  - **守卫 A/B**：放置一个真实违规调用 → 守卫拦下、**退出码 1、且不进构建**；
    移除后 → 放行并 `kn` 构建成功（exit 0，`libkn.so` arm64-v8a 2.5M / x86_64 2.0M）。
  - 未做的验证（**明确标注**）：`kotlin-pending` 那处的编译与真机行为——
    当前工程无法编译它（结构原因如上），启用时必须与接线一起验证。
