## 2026-09-23（apps/README 与 VERIFICATION 纠偏：四端状态、UI 分层矛盾、契约数 32→37）

- **背景**：真机调试顺延到次日，本轮做**与设备无关**的收尾。`apps/README.md` 是 `apps/` 树的入口，
  实测发现它有三类问题：**状态过期**（iOS/Mac 早已完成却写"规划中"）、
  **与 owner 已确认决策矛盾**（分层表写 UI 放 `shared/ui` = Compose Multiplatform"一套 UI 四端跑"，
  而 D1 已定**各端原生、不用 CMP**）、**数字过期**。

- **改动**：
  - `apps/README.md`（46 增 / 20 删）：
    - 目录清单标注完成度（iOS/Mac ✅、鸿蒙 🟡），并新增**四端状态表**（各自检数 + 三端同表说明）。
    - 方案版本 v1.0「待 owner 确认」→ **v1.1.0 已确认**；CMP 评估标注为**已归档、不采用**。
    - **修掉 UI 分层矛盾**：`shared/ui`（CMP）那行改成"各端原生"并显式划掉旧说法，
      同时补上**嵌入运行时是唯一没能"写一次"的**（Android Java API / iOS·Mac C API / 鸿蒙 MindSpore Lite，
      语义靠共享层 `EmbeddingProvider` + 同一分词器 + 同一空间戳对齐）。
    - 「按需编译」从"Android（唯一已可用的端）"扩到**四端各有命令**（含 `harmony_spike.sh`、`model_to_ms.sh`）。
    - 合并了重复的 `.tooling/` 说明段落，并补上鸿蒙侧 `HVIGOR_USER_HOME`/`HOME` 的 EPERM 原因。
  - `apps/android/docs/VERIFICATION.md`：契约 case 数 **32 → 37**（routing 10 + signals 14 + privacy 13，实点）。
    ⚠️ **有意不动**同文件里"M4 第 5 步（2026-09-21）"那段的 `207 tests`——那是**历史运行日志**，
    改了就成篡改历史；过期的是"现在时"的事实陈述，不是带日期的记录。

- **验证**：
  - `bash docs/tech/.validation/doc_guard.sh` → **6/6 全通过**（改的是链接密集文件，重点看第 1 项全仓链接校验）。
  - 自写脚本校验两个文件的 **Markdown 表格列数一致** → 无异常。
  - `grep` 复查无 `规划中`/`v1.0，待 owner 确认`/`唯一已可用` 等过期表述残留。
  - 写入的代码规模数字均为实测：`shared/commonMain` 35 文件/4,066 行、`android/app` 2,695 行/15 文件、
    `ios/App` 1,118 行/5 文件。
