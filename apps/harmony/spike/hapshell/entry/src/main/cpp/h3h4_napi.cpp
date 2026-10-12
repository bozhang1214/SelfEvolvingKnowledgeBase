// ═══════════════════════════════════════════════════════════════════════════
// H3（网络端口）+ H4（凭证端口）的 NAPI 入口 —— **独立文件，工作流 E′**
//
// ## 为什么单独一个文件（协作约定，不是洁癖）
//
// `apps/harmony` 同时有两个工作流在改（E′ = H3/H4，F = H5 的 RDB/RAG）。
// 单独成文件之后，`napi_init.cpp` 里的改动只剩**两行**：
//   ① 文件顶部一句前置声明 `static void RegisterSekbH3H4(...)`；
//   ② `Init()` 里一句 `RegisterSekbH3H4(env, exports);`。
// 对方的 H5 代码块与本文件完全不相邻，rebase 时不会互相吃改动。
//
// ⚠️ 本文件里的 `registerH3H4` 是 `static`（**不导出符号**），在 `Init()` 里被调用。
//    第一版我曾把它写成 `__attribute__((constructor))` 里 `napi_module_register` 注册
//    **第二个 NAPI 模块**——那会让 ArkTS 侧要 `import` 两个模块，白白多一条链路，
//    也容易与主模块的注册顺序打架。一个模块、一个 exports 对象才是正解。
//
// ## H3 的形状：ArkTS 逐步驱动，Kotlin 只做"拿到数据之后"的事
//
// 共享层的 `HttpTransport` 是**同步**接口，而 ArkTS 的 HTTP 是异步的。若让 Kotlin 实现它、
// 内部再回调 ArkTS，Kotlin 是在被 ArkTS **同步**调进来的那一刻等 `await` 的结果——
// 而那条线程正卡在 NAPI 里，回调排不进它的任务队列 → **结构性死锁**
// （iOS 能用信号量，是因为 URLSession 的回调在另一条线程上；鸿蒙的 ArkTS 任务队列没有）。
//
// 所以本文件**没有**"把 ArkTS 函数注册给 Kotlin"的回调桥，只有：
//   · `h3Probe` / `h3Stream`：ArkTS 把**已观测事实**（期望码/实观测码/行数/字段是否解析出来）
//     传进来，Kotlin 做判定与 `PASS`/`FAIL` 渲染（判据只有一份）；
//   · `h3StreamLine` / `h3StreamBegin` / `h3PumpLines`：流式逐行的搬运与计数。
// 详见 `knspike/src/ohosMain/kotlin/NetPort.kt` 与 `H3NetSelfTest.kt` 的文件头。
// ═══════════════════════════════════════════════════════════════════════════
#include <napi/native_api.h>
#include <cstdlib>
#include <string>
#include <vector>

// ── Kotlin/Native 侧导出（`@CName`，见 knspike/src/ohosMain/kotlin/）──────────
// H3 判据：ArkTS 传已观测事实，Kotlin 判定 + 渲染。
extern "C" const char* sekb_spike_h3_probe(const char* name, const char* url, const char* want_code,
                                           const char* got_code, const char* declared_bytes,
                                           const char* body_chars, const char* found_fields);
extern "C" const char* sekb_spike_h3_stream(const char* url, const char* code,
                                            const char* ark_lines, const char* kotlin_lines);
extern "C" const char* sekb_spike_h3_stream_line(const char* line);
extern "C" const char* sekb_spike_h3_stream_begin();
extern "C" const char* sekb_spike_h3_pump_lines();
// H4 凭证（HUKS，cinterop 直调 libhuks_ndk.z.so；不经 ArkTS 桥）
extern "C" const char* sekb_spike_cred_selftest(const char* files_dir, const char* reset, const char* run_tag);
extern "C" const char* sekb_spike_huks_roundtrip(const char* payload);
// H5 取证（主线）：RDB 里 `source_id` 到底有没有落对（H8 自检出现"能检索到分、但 source 为空"的矛盾证据）
extern "C" const char* sekb_spike_rdb_id_probe(const char* database_dir, const char* run_tag);
// H5 分步检索评测（主线 2026-10-26 接出）：原来 `sekb_spike_rag_step` 只有 Kotlin 侧实现、
// **没有 NAPI 入口**，所以"分步跑评测"这条设计好的路一直没被用上（整段同步跑会撞 THREAD_BLOCK_6S）。
extern "C" const char* sekb_spike_rag_step(int step, const char* model_path, const char* vocab_text,
                                           const char* database_dir, const char* files_dir);

namespace h3h4_napi {

/** 取第 i 个字符串参数（两段式：先问长度、再取内容）。 */
std::string argAt(napi_env env, napi_callback_info info, size_t want, size_t i) {
    std::vector<napi_value> args(want, nullptr);
    size_t argc = want;
    napi_get_cb_info(env, info, &argc, args.data(), nullptr, nullptr);
    if (i >= argc || args[i] == nullptr) return {};
    size_t len = 0;
    napi_get_value_string_utf8(env, args[i], nullptr, 0, &len);
    std::string buf(len + 1, '\0');
    napi_get_value_string_utf8(env, args[i], buf.data(), buf.size(), &len);
    buf.resize(len);
    return buf;
}

napi_value returnString(napi_env env, const char* s) {
    napi_value out = nullptr;
    napi_create_string_utf8(env, s == nullptr ? "" : s, NAPI_AUTO_LENGTH, &out);
    return out;
}

}  // namespace h3h4_napi

// H3：通用判据入口（7 个字符串参数）
static napi_value H3Probe(napi_env env, napi_callback_info info) {
    const std::string name = h3h4_napi::argAt(env, info, 7, 0);
    const std::string url = h3h4_napi::argAt(env, info, 7, 1);
    const std::string want = h3h4_napi::argAt(env, info, 7, 2);
    const std::string got = h3h4_napi::argAt(env, info, 7, 3);
    const std::string bytes = h3h4_napi::argAt(env, info, 7, 4);
    const std::string chars = h3h4_napi::argAt(env, info, 7, 5);
    const std::string fields = h3h4_napi::argAt(env, info, 7, 6);
    return h3h4_napi::returnString(env, sekb_spike_h3_probe(
        name.c_str(), url.c_str(), want.c_str(), got.c_str(),
        bytes.c_str(), chars.c_str(), fields.c_str()));
}

// H3：流式判据（4 个字符串参数：url / code / ArkTS 行数 / Kotlin 行数）
static napi_value H3Stream(napi_env env, napi_callback_info info) {
    const std::string url = h3h4_napi::argAt(env, info, 4, 0);
    const std::string code = h3h4_napi::argAt(env, info, 4, 1);
    const std::string arkLines = h3h4_napi::argAt(env, info, 4, 2);
    const std::string knLines = h3h4_napi::argAt(env, info, 4, 3);
    return h3h4_napi::returnString(env, sekb_spike_h3_stream(
        url.c_str(), code.c_str(), arkLines.c_str(), knLines.c_str()));
}

// H3 流式：每个完整行调一次（ArkTS 切行 → 共享层 `SseParser` 解释）
static napi_value H3StreamLine(napi_env env, napi_callback_info info) {
    const std::string line = h3h4_napi::argAt(env, info, 1, 0);
    return h3h4_napi::returnString(env, sekb_spike_h3_stream_line(line.c_str()));
}

// H3 流式：开始新一轮行泵（重置进程内单例的计数，防止上一轮的行数混进来造成假绿）
static napi_value H3StreamBegin(napi_env env, napi_callback_info /*info*/) {
    return h3h4_napi::returnString(env, sekb_spike_h3_stream_begin());
}

// H3 流式：Kotlin 侧实际收到的行数（判据要证明"行真的跨 NAPI 到了 Kotlin"）
static napi_value H3PumpLines(napi_env env, napi_callback_info /*info*/) {
    return h3h4_napi::returnString(env, sekb_spike_h3_pump_lines());
}

// H4：凭证自检（3 个字符串参数：filesDir / reset / runTag）
static napi_value CredSelfTest(napi_env env, napi_callback_info info) {
    const std::string dir = h3h4_napi::argAt(env, info, 3, 0);
    const std::string reset = h3h4_napi::argAt(env, info, 3, 1);
    const std::string tag = h3h4_napi::argAt(env, info, 3, 2);
    return h3h4_napi::returnString(env,
        sekb_spike_cred_selftest(dir.c_str(), reset.c_str(), tag.c_str()));
}

// H4：最小加解密往返（1 个字符串参数：负载）
static napi_value HuksRoundtrip(napi_env env, napi_callback_info info) {
    const std::string payload = h3h4_napi::argAt(env, info, 1, 0);
    return h3h4_napi::returnString(env, sekb_spike_huks_roundtrip(payload.c_str()));
}

/**
 * H8（工作流 G）的入口注册在**它自己的文件** `sekb_chat_napi.cpp` 里。
 *
 * 为什么由本文件代挂一行：`napi_init.cpp` 是**主线独占**（协作硬约定，见文件头），
 * 而本文件已经是被 `Init()` 调用的注册点。这里只加**一行调用**，
 * E′ 自己的 `sekb_spike_h3_*` / `sekb_spike_cred_*` 行为一字未动。
 */
void RegisterSekbChat(napi_env env, napi_value exports);

/**
 * H5 分步检索评测入口：`ragStep(step, modelPath, vocabText, databaseDir, filesDir)`。
 *
 * `step` 是数字，NAPI 侧`argAt` 只取字符串，所以这里用一个取数字的小包装。
 * 步进语义（Kotlin 侧定义）：0=建库+导入 1=检索与闸门 2=落盘/删除复核 3=评测 4=收尾。
 * 每步之间由 ArkTS `await` 让出主线程，避免 `THREAD_BLOCK_6S`。
 */
napi_value RagStep(napi_env env, napi_callback_info info) {
    const std::string stepS = h3h4_napi::argAt(env, info, 5, 0);
    const std::string model = h3h4_napi::argAt(env, info, 5, 1);
    const std::string vocab = h3h4_napi::argAt(env, info, 5, 2);
    const std::string dir = h3h4_napi::argAt(env, info, 5, 3);
    const std::string files = h3h4_napi::argAt(env, info, 5, 4);
    const int step = stepS.empty() ? 0 : std::atoi(stepS.c_str());
    return h3h4_napi::returnString(env,
        sekb_spike_rag_step(step, model.c_str(), vocab.c_str(), dir.c_str(), files.c_str()));
}

/** 主线取证入口：RDB 的 source_id 往返（2 个字符串参数：databaseDir / runTag）。 */
napi_value RdbIdProbe(napi_env env, napi_callback_info info) {
    const std::string dir = h3h4_napi::argAt(env, info, 2, 0);
    const std::string tag = h3h4_napi::argAt(env, info, 2, 1);
    return h3h4_napi::returnString(env, sekb_spike_rdb_id_probe(dir.c_str(), tag.c_str()));
}

/**
 * 把 H3/H4 的入口挂到主模块的 exports 上（由 `napi_init.cpp` 的 `Init()` 调用）。
 *
 * `napi_define_properties` 是**追加**语义：与主模块已有的 17 个入口互不影响。
 */
void RegisterSekbH3H4(napi_env env, napi_value exports) {
    napi_property_descriptor desc[] = {
        // H3（网络端口）：ArkTS 把已观测事实交给 Kotlin 判定（无反向回调，见文件头）
        {"h3Probe", nullptr, H3Probe, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"h3Stream", nullptr, H3Stream, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"h3StreamLine", nullptr, H3StreamLine, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"h3StreamBegin", nullptr, H3StreamBegin, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"h3PumpLines", nullptr, H3PumpLines, nullptr, nullptr, nullptr, napi_default, nullptr},
        // H4（凭证端口）：HUKS 自检（纯 Kotlin/Native，cinterop 直链）
        {"credSelfTest", nullptr, CredSelfTest, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"huksRoundtrip", nullptr, HuksRoundtrip, nullptr, nullptr, nullptr, napi_default, nullptr},
        // H5 取证（主线独占 napi_init.cpp，故由本文件代挂；见 RdbIdProbe.kt 头部说明）
        {"rdbIdProbe", nullptr, RdbIdProbe, nullptr, nullptr, nullptr, napi_default, nullptr},
        // H5 分步检索评测（主线接出；见 RagStep 的说明——原来只有 Kotlin 实现没有 NAPI 入口）
        {"ragStep", nullptr, RagStep, nullptr, nullptr, nullptr, napi_default, nullptr},
    };
    napi_define_properties(env, exports, sizeof(desc) / sizeof(desc[0]), desc);
    // H8：一行代挂（见上方 RegisterSekbChat 的说明）
    RegisterSekbChat(env, exports);
}
