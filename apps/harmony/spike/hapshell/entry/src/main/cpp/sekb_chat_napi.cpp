// ═══════════════════════════════════════════════════════════════════════════
// H8（工作流 G）· 聊天流式 + 文档/检索端口的 NAPI 入口 —— **独立文件**
//
// ## 为什么独立成文件（协作约定，不是洁癖）
//
// `napi_init.cpp` 由**主线独占**（2026-10-12 静默覆盖事故后的硬约定，见
// `docs/COORDINATION.md`）。所以本文件只做两件事：
//   ① 自己建一张 `napi_property_descriptor` 表并 `napi_define_properties` 到**同一个** exports；
//   ② 由一个**已注册过的**兄弟文件调用一次 `RegisterSekbChat(env, exports)`。
//
// 挂载点选在 `h3h4_napi.cpp`（H3/H4 的注册文件）而不是 `napi_init.cpp`：那里只需加
// **一行前置声明 + 一行调用**，且 E′ 的 `sekb_spike_h3_*` 行为一字不动——比动主模块安全。
//
// ## 形状：与 H3 完全一致（ArkTS 搬字节，Kotlin 定语义）
//
//   ArkTS（HTTP + 按 `\n` 切行） ──同步 NAPI──▶ Kotlin（`ChatStreamPort.kt`）
//
// Kotlin 侧把每一行喂给**共享层 `SseParser`**、把路由交给**共享层 `PlaneRouter`**，
// 返回一行小 JSON 给 ArkTS 渲染。这里是纯搬运，**不含任何协议判断**。
//
// ## 每个入口的参数/返回（ArkTS 侧的名字同名）
//
//   chatRoute(question, history, deviceData, edgeBaseUrl, sekbBaseUrl) -> JSON 决策
//   chatStreamBegin()                       -> {"kind":"begin"}
//   chatStreamLine(line)                    -> {"kind":"token|thinking|done|error|none","piece":..}
//   chatStreamFinish()                      -> 同上（处理最后一段未终止的数据）
//   chatStreamSummary()                     -> 本轮计数/首字延迟（自检与审计）
//   ragOpen(modelPath, vocabText, dbDir, dbName) -> {"ok":bool,"reason":..}
//   ragStatus() / ragDocuments() / ragIngest(id,name,text,bytes) / ragRemove(id) / ragRetrieve(q,k)
//   ragEmbedStatus() / ragSummary()
// ═══════════════════════════════════════════════════════════════════════════
#include <napi/native_api.h>
#include <string>
#include <vector>

// ── Kotlin/Native 侧导出（`@CName`，见 knspike/src/ohosMain/kotlin/ChatStreamPort.kt 与 SekbAppPort.kt）──
extern "C" const char* sekb_chat_route(const char* question, const char* history,
                                       const char* device_data, const char* edge_base_url,
                                       const char* sekb_base_url);
extern "C" const char* sekb_chat_stream_begin();
extern "C" const char* sekb_chat_stream_line(const char* line);
extern "C" const char* sekb_chat_stream_finish();
extern "C" const char* sekb_chat_stream_summary();
extern "C" const char* sekb_rag_open(const char* model_path, const char* vocab_text, const char* db_dir,
                                     const char* db_name);
extern "C" const char* sekb_rag_status();
extern "C" const char* sekb_rag_documents();
extern "C" const char* sekb_rag_ingest(const char* source_id, const char* name, const char* text,
                                       const char* size_bytes);
extern "C" const char* sekb_rag_remove(const char* id);
extern "C" const char* sekb_rag_retrieve(const char* query, const char* top_k);
extern "C" const char* sekb_rag_embed_status();
extern "C" const char* sekb_rag_summary();
extern "C" const char* sekb_rag_set_threshold(const char* value);

namespace sekb_chat_napi {

/** 取第 i 个字符串参数（两段式：先问长度、再取内容），与 `h3h4_napi.cpp` 同一写法。 */
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

}  // namespace sekb_chat_napi

// ── chat*（5 个字符串参数：问题 / 历史 / deviceData / edgeBase / sekbBase）──
static napi_value ChatRoute(napi_env env, napi_callback_info info) {
    const std::string q = sekb_chat_napi::argAt(env, info, 5, 0);
    const std::string h = sekb_chat_napi::argAt(env, info, 5, 1);
    const std::string d = sekb_chat_napi::argAt(env, info, 5, 2);
    const std::string e = sekb_chat_napi::argAt(env, info, 5, 3);
    const std::string s = sekb_chat_napi::argAt(env, info, 5, 4);
    return sekb_chat_napi::returnString(env,
        sekb_chat_route(q.c_str(), h.c_str(), d.c_str(), e.c_str(), s.c_str()));
}

static napi_value ChatStreamBegin(napi_env env, napi_callback_info /*info*/) {
    return sekb_chat_napi::returnString(env, sekb_chat_stream_begin());
}

static napi_value ChatStreamLine(napi_env env, napi_callback_info info) {
    const std::string line = sekb_chat_napi::argAt(env, info, 1, 0);
    return sekb_chat_napi::returnString(env, sekb_chat_stream_line(line.c_str()));
}

static napi_value ChatStreamFinish(napi_env env, napi_callback_info /*info*/) {
    return sekb_chat_napi::returnString(env, sekb_chat_stream_finish());
}

static napi_value ChatStreamSummary(napi_env env, napi_callback_info /*info*/) {
    return sekb_chat_napi::returnString(env, sekb_chat_stream_summary());
}

// ── rag*（文档页 / 检索来源）──
static napi_value RagOpen(napi_env env, napi_callback_info info) {
    const std::string model = sekb_chat_napi::argAt(env, info, 4, 0);
    const std::string vocab = sekb_chat_napi::argAt(env, info, 4, 1);
    const std::string dir = sekb_chat_napi::argAt(env, info, 4, 2);
    // 库名：空串 → 共享层的默认库（界面用固定库累积，自检用隔离库，见 SekbAppPort.kt）
    const std::string name = sekb_chat_napi::argAt(env, info, 4, 3);
    return sekb_chat_napi::returnString(env,
        sekb_rag_open(model.c_str(), vocab.c_str(), dir.c_str(), name.c_str()));
}

static napi_value RagStatus(napi_env env, napi_callback_info /*info*/) {
    return sekb_chat_napi::returnString(env, sekb_rag_status());
}

static napi_value RagDocuments(napi_env env, napi_callback_info /*info*/) {
    return sekb_chat_napi::returnString(env, sekb_rag_documents());
}

static napi_value RagIngest(napi_env env, napi_callback_info info) {
    const std::string id = sekb_chat_napi::argAt(env, info, 4, 0);
    const std::string name = sekb_chat_napi::argAt(env, info, 4, 1);
    const std::string text = sekb_chat_napi::argAt(env, info, 4, 2);
    const std::string bytes = sekb_chat_napi::argAt(env, info, 4, 3);
    return sekb_chat_napi::returnString(env,
        sekb_rag_ingest(id.c_str(), name.c_str(), text.c_str(), bytes.c_str()));
}

static napi_value RagRemove(napi_env env, napi_callback_info info) {
    const std::string id = sekb_chat_napi::argAt(env, info, 1, 0);
    return sekb_chat_napi::returnString(env, sekb_rag_remove(id.c_str()));
}

static napi_value RagRetrieve(napi_env env, napi_callback_info info) {
    const std::string q = sekb_chat_napi::argAt(env, info, 2, 0);
    const std::string k = sekb_chat_napi::argAt(env, info, 2, 1);
    return sekb_chat_napi::returnString(env, sekb_rag_retrieve(q.c_str(), k.c_str()));
}

static napi_value RagEmbedStatus(napi_env env, napi_callback_info /*info*/) {
    return sekb_chat_napi::returnString(env, sekb_rag_embed_status());
}

static napi_value RagSummary(napi_env env, napi_callback_info /*info*/) {
    return sekb_chat_napi::returnString(env, sekb_rag_summary());
}

// 检索阈值（设置页）：0<v≤1，非法值回落 0.40（回落规则在 Kotlin 侧，只有一份）
static napi_value RagSetThreshold(napi_env env, napi_callback_info info) {
    const std::string v = sekb_chat_napi::argAt(env, info, 1, 0);
    return sekb_chat_napi::returnString(env, sekb_rag_set_threshold(v.c_str()));
}

/**
 * 注册 H8 的入口到**已存在的** exports 对象（由 `h3h4_napi.cpp` 的注册函数调用一次）。
 *
 * 为什么不自己 `napi_module_register` 第二个模块：那会让 ArkTS 侧要 import 两个模块，
 * 白多一条链路，也容易与主模块的注册顺序打架（`h3h4_napi.cpp` 文件头记过这个坑）。
 */
void RegisterSekbChat(napi_env env, napi_value exports) {
    napi_property_descriptor desc[] = {
        // ── H8 聊天流式：ArkTS 搬行，Kotlin（共享层 SseParser/PlaneRouter）定语义 ──
        {"chatRoute", nullptr, ChatRoute, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"chatStreamBegin", nullptr, ChatStreamBegin, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"chatStreamLine", nullptr, ChatStreamLine, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"chatStreamFinish", nullptr, ChatStreamFinish, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"chatStreamSummary", nullptr, ChatStreamSummary, nullptr, nullptr, nullptr, napi_default, nullptr},
        // ── H8 文档页 + 检索来源（走共享层 KnowledgeIndex/Retriever + F 的 RDB 实现）──
        {"ragOpen", nullptr, RagOpen, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"ragStatus", nullptr, RagStatus, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"ragDocuments", nullptr, RagDocuments, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"ragIngest", nullptr, RagIngest, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"ragRemove", nullptr, RagRemove, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"ragRetrieve", nullptr, RagRetrieve, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"ragEmbedStatus", nullptr, RagEmbedStatus, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"ragSummary", nullptr, RagSummary, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"ragSetThreshold", nullptr, RagSetThreshold, nullptr, nullptr, nullptr, napi_default, nullptr},
    };
    napi_define_properties(env, exports, sizeof(desc) / sizeof(desc[0]), desc);
}
