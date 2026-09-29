/**
 * 端侧 Agent 的 **NAPI 边界契约**（鸿蒙端）。
 *
 * ## 为什么先定契约、后接实现
 *
 * 真实推理链路要等 **M6 第 4 条**在模拟器/设备上验证通过（HAP 能装能起、`.ms` 能跑）。
 * 在运行时未验证之前就把调用写进界面，等于**把未验证的假设固化到 UI 里**——
 * 一旦运行时行为与预期不符，界面要跟着返工。
 *
 * 所以这里只做两件事：① 定义界面依赖的接口；② 提供一个**明确报错**的桩实现。
 * 接线时只替换实现（`UnwiredEdgeAgent` → 基于 NAPI 的实现），界面一行不用改。
 *
 * ## 原生侧已有/待接的入口（对照 `MsLite.kt` / `MindSporeProbe.kt`）
 *
 * 已就绪（编译+link 已验证）：
 *   - `sekb_spike_mindspore_selftest(path)` / `sekb_spike_mindspore_first_float(path)`
 *   - `MsSession.predictTyped(...)`：可喂 **int64 + float32**（掩码就是 float32）
 * 待接：
 *   - 文本 → tokenize（vocab.txt 分词）→ 3D 输入 + **additive mask**（可见 0 / 被掩 -inf）
 *     → `predictTyped` → 取 CLS 向量 → L2 归一化 → 与向量库检索
 *   - 生成侧（对话）：端侧 LLM 端点 + `StreamGuard` 等价逻辑
 */

import { Plane } from './Types';

/** 一次嵌入的结果。`space` 是**向量空间标识**：跨端交换是否可复用的唯一判据。 */
export interface EmbeddingResult {
  vector: number[];
  space: string;
}

/** 一次回答的结果。字段与 Android 端 `ChatOutcome` 对齐，便于三端同语义。 */
export interface AgentReply {
  text: string;
  plane: Plane;
  escalated: number;
  reason: string;
  escalateReason: string;
}

export interface EdgeAgent {
  /** 文本 → 定长归一化向量（端侧嵌入）。 */
  embed(text: string): Promise<EmbeddingResult>;

  /** 提问 → 回答（端侧优先，必要时改道云端）。 */
  ask(question: string): Promise<AgentReply>;
}

/**
 * 未接线的桩：**必须明确失败，不能假装成功**。
 *
 * 回一个"看起来像回答"的假结果是最坏的选择——它会让人以为链路通了
 * （Android 端就吃过类似的亏：调试 UI 把内部指标摆出来，看起来"很完整"）。
 */
export class UnwiredEdgeAgent implements EdgeAgent {
  private static readonly MSG: string =
    '端侧推理尚未接线：需先在模拟器/设备上通过 M6 第 4 条（HAP 能装能起、.ms 能跑），再接 NAPI。';

  async embed(_text: string): Promise<EmbeddingResult> {
    throw new Error(UnwiredEdgeAgent.MSG);
  }

  async ask(_question: string): Promise<AgentReply> {
    throw new Error(UnwiredEdgeAgent.MSG);
  }
}

/** 当前使用的实现。接线时**只改这一行**。 */
export function currentAgent(): EdgeAgent {
  return new UnwiredEdgeAgent();
}
