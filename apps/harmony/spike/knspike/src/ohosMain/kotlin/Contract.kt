// SEKB 鸿蒙侧 · 契约夹具的 NAPI 入口（H2）
//
// 三份夹具由 ArkTS 侧从 **HAP rawfile** 读入后传进来（`apps/contract/*.json` 由
// `scripts/harmony_spike.sh build_hap` 拷进 `entry/src/main/resources/rawfile/contract/`）。
//
// 为什么走"文本入参"而不是让 native 侧自己读文件：
//   ① 鸿蒙的 sandbox 路径只有 ArkTS 侧拿得到（`getContext().filesDir`/`resourceManager`），
//      native 侧拿 rawfile 还得再引一套 resource manager 绑定，属于给一个测试 runner 引新依赖；
//   ② 入参是**文本**这一件事让"夹具内容"与"断言逻辑"彻底解耦——
//      反向验证变体可以在内存里改文本，不需要往仓库里塞第二份夹具（`apps/contract/**` 是共享区）。
@file:OptIn(kotlin.experimental.ExperimentalNativeApi::class)

package com.sekb.ohos.contract

/**
 * 跑契约夹具，返回多行报告：
 *
 * ```
 * CONTRACT PASS 41/41
 * PASS fixtures_parse — routing=true signals=true privacy=true
 * ...
 * ```
 *
 * `check` 非 "0" 时启用**反向验证**变体（routing 第一条 case 的期望平面反转），
 * 此时首行应为 `CONTRACT FAIL 1/N`——这正是"改夹具期望值必须变红"的那一步。
 */
@CName("sekb_spike_contract")
fun sekbSpikeContract(routingJson: String, signalsJson: String, privacyJson: String, check: String): String {
    val red = check != "0" && check.isNotEmpty()
    val outcome = ContractRunner.run(routingJson, signalsJson, privacyJson, redCheck = red)
    return render(outcome, red)
}

/**
 * **一次调用同时跑正常与反向验证两条**（`bash scripts/harmony.sh contract` 用这个入口）。
 *
 * 为什么合并成一次调用：反向验证的价值全在"两次结果必须一绿一红"这个**对照**上。
 * 分两次调用（两次启动 App）时，中间任何一次构建/安装出错都可能让"红"其实是别的原因；
 * 同一次调用里跑完，两条结论出自同一份夹具文本、同一个进程，对照才成立。
 *
 * 返回两段报告，中间用一行 `-----` 分隔：
 *   - 第 1 段：原样夹具 → `CONTRACT PASS n/N`
 *   - 第 2 段：反转 routing[0] 期望平面 → `CONTRACT FAIL 1/N`
 */
@CName("sekb_spike_contract_pair")
fun sekbSpikeContractPair(routingJson: String, signalsJson: String, privacyJson: String): String {
    val normal = ContractRunner.run(routingJson, signalsJson, privacyJson, redCheck = false)
    val red = ContractRunner.run(routingJson, signalsJson, privacyJson, redCheck = true)
    return render(normal, false) + "\n-----\n" + render(red, true)
}

private fun render(outcome: ContractOutcome, red: Boolean): String {
    val head = buildString {
        append("CONTRACT ")
        append(if (outcome.ok) "PASS" else "FAIL")
        append(' ')
        append(outcome.passed)
        append('/')
        append(outcome.total)
        if (red) {
            append(" [red-check ")
            append(if (outcome.redCheckApplied) "applied" else "NOT-applied")
            append(", 期望恰好 1 FAIL]")
        }
    }
    return (listOf(head) + outcome.lines).joinToString("\n")
}
