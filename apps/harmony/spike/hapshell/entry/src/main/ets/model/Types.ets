/**
 * 端侧会话的**领域类型与语义**（鸿蒙端）。
 *
 * 为什么单独成文件、而不是散在页面里：这些语义（"这条在哪算的、为什么"）在
 * **网页端、Android 端、鸿蒙端三处必须一致**——它是产品承诺（设备专属数据不出端）的表达。
 * 放在视图里会导致三端各写一份、慢慢漂移（Android 端就经历过：徽章文案与原因解释一度自相矛盾）。
 *
 * 口径对齐：Android 端的 `ChatViewModel.badgeOf` 与 `PlaneExplain`。
 */

/** 执行位置。`''` 表示还没结果（流式中）。 */
export type Plane = 'edge' | 'cloud' | '';

export interface ChatMessage {
  id: string;
  fromUser: boolean;
  text: string;
  plane: Plane;
  /** 服务端回传的升级次数；>0 表示"已上云（端侧不达标）" */
  escalated: number;
  /** 路由阶段原因码（如 `edge_preferred`） */
  reason: string;
  /** 升级阶段原因码（如 `degenerate`）；未升级为空 */
  escalateReason: string;
}

/**
 * 执行位置徽章。
 *
 * ⚠️ 与 Android 端同源语义：**客户端改道**（`clientEscalated`）服务端并不知道，
 * 只看服务端的 `escalated` 会把"客户端刚因为端侧不可用改道"显示成平平无奇的"云端完成"。
 */
export function badgeOf(m: ChatMessage, clientEscalated: boolean = false): string {
  if (m.fromUser) return '';
  if (m.plane === 'edge') return '本机完成';
  if (m.plane === 'cloud' || clientEscalated) {
    return (clientEscalated || m.escalated > 0) ? '已上云（端侧不达标）' : '云端完成';
  }
  return '';
}

/** 语义色：**只在徽章上用**——若界面别处也用绿，"绿色"就不再等于"在本机算的"。 */
export function badgeColor(m: ChatMessage): string {
  return m.plane === 'edge' ? '#389E0D' : '#D48806';
}

/** 原因码 → 用户能懂的一句话。**认不出的码原样透出**（`原因：<code>`），绝不返回"未知原因"。 */
export function explainReason(code: string): string {
  if (code.length === 0) return '';
  // 与端侧策略/升级信号保持同名（见 shared/route/PlaneRouter.kt 的 REASON_*/SIGNAL_*）
  if (code.indexOf('edge_preferred') === 0) return '这次请求在设备能力范围内，按设计优先由本机处理。';
  if (code.indexOf('input_over_edge_budget') === 0) return '输入超出端侧能处理的规模，按设计直接交给云端。';
  if (code.indexOf('output_over_edge_budget') === 0) return '预期回答长度超出端侧预算，按设计直接交给云端。';
  if (code.indexOf('escalation_blocked:device_only_requires_local_runtime') === 0) {
    return '这是设备专属内容，但当前端侧地址不是本机；为不让数据出端，这次请求没有发出。';
  }
  if (code.indexOf('escalation_blocked:device_only') === 0) return '这是设备专属内容，不允许改道云端。';
  if (code.indexOf('edge_unavailable') === 0) return '端侧运行时不可用（连不上或调用失败）。';
  if (code.indexOf('degenerate') === 0) return '端侧回答中途开始重复、退化。';
  if (code.indexOf('timeout') === 0) return '端侧生成超时。';
  if (code.indexOf('low_confidence') === 0) return '端侧对这次回答没有把握。';
  if (code.indexOf('json_invalid') === 0) return '端侧要调用的工具参数不是合法格式。';
  if (code.indexOf('empty') === 0) return '端侧没有产出有效内容。';
  if (code.indexOf('tool_hallucination') === 0) return '端侧试图调用不存在的工具。';
  if (code.indexOf('context_overflow') === 0) return '上下文超出端侧能容纳的长度。';
  return '原因：' + code;   // 认不出就原样透出，不让新码静默消失
}
