// M6 spike 第 1 条：证明 **KMP 产物能被 HAP 通过 NAPI 调用**。
//
// 链路：ArkTS `import knspike from 'libknspike.so'` → NAPI 模块 `knspike`
//       → 本文件 → `libkn.so`（Kotlin/Native 产物）里的 C 符号。
//
// 关键点：`sekb_spike_ping` / `sekb_spike_echo_len` 这两个符号是 Kotlin 侧用 `@CName`
// 钉死的（见 `knspike/src/ohosMain/kotlin/Spike.kt`）。C++ 侧**不需要** Kotlin 头文件，
// 只要声明一致即可——这也是 `@CName` 存在的意义（否则符号名会带 Kotlin 修饰，无法稳定引用）。
#include <napi/native_api.h>
#include <string>
#include <cstdio>
// H3：libcurl 探针用 dlopen/dlsym（**故意不在链接期依赖 libcurl**）
// 理由：OHOS sysroot 里**没有** libcurl 的头文件与 .so（实测：`usr/include/curl` 不存在、
// `usr/lib/aarch64-linux-ohos/` 里也没有），所以想"编得过"就得先交叉编译 curl+TLS 栈。
// 而设备系统镜像里 libcurl.so **是存在的**（grep 镜像 ext2：64 处命中）。
// → 先用 dlopen 做真机探针：能加载 + 能发 HTTPS GET + 能按行回调，三件事都有事实之后再决定
// 共享层的 HttpTransport 走"链接期依赖"还是"运行期 dlopen"。见 docs/多端跨端-鸿蒙端方案.md HR9。
#include <dlfcn.h>

// NAPI 头里没有这两个宏（实测 `grep EXTERN_C_START napi/*.h` 无匹配）→ 自己定义。
// 官方样例里有它们，是因为样例自己带了一个 `napi_init.h`；这里不引入额外头。
#ifndef EXTERN_C_START
#define EXTERN_C_START
#endif
#ifndef EXTERN_C_END
#define EXTERN_C_END
#endif

// Kotlin/Native 的 C 导出声明（与 `build/bin/ohosX64/debugShared/libkn_api.h` 一致）。
// ⚠️ `String` 参数在 C 侧就是 `const char*`（Kotlin/Native 会做转换），
//    不是某些资料说的"必须有 KString 包装"——以生成的 `libkn_api.h` 为准。
extern "C" int32_t sekb_spike_ping();
extern "C" int32_t sekb_spike_echo_len(const char* text);
// MindSpore Lite 自检（见 knspike/.../MindSporeProbe.kt）：
//   > 0 = 成功，值是输出张量元素数（期望 262144 = 1*512*512）
//   < 0 = 失败，负值是 MsStep 里的步骤码
extern "C" int32_t sekb_spike_mindspore_selftest(const char* model_path);
// 输出向量首元素的粗量化值（|v[0]|*1e6）——证明"数据真的流过来了"，而不是全零也过
extern "C" int32_t sekb_spike_mindspore_first_float(const char* model_path);
// 共享层 interop 冒烟（H1，见 knspike/.../Smoke.kt）：返回**多行**报告
//   SMOKE PASS n/total
//   PASS digests_hmac_vector — f7bc83f4…
//   ...
// 它跑的是 `apps/shared/src/commonMain`（经 `apps/shared-core` 编进本 .so），
// 所以它同时是"共享层真的编进来、且在设备上跑出了正确结果"的证据——比"能链接"强一档。
// ⚠️ Kotlin/Native 的 `String` 返回值在 C 侧就是 `const char*`（以生成的 libkn_api.h 为准），
//    不要自己去构造 KString。
extern "C" const char* sekb_spike_smoke();
// H2：契约夹具 runner。三份夹具文本 + red-check 开关（"0"=正常，"1"=把 routing[0] 期望平面反转）。
// 返回多行报告，首行 `CONTRACT PASS n/N` 或 `CONTRACT FAIL n/N`。
extern "C" const char* sekb_spike_contract(const char* routing, const char* signals,
                                           const char* privacy, const char* check);
// H2 反向验证：同一次调用里跑"原样夹具"与"反转期望值"两遍（返回两段报告，`-----` 分隔）。
extern "C" const char* sekb_spike_contract_pair(const char* routing, const char* signals,
                                                const char* privacy);
// H6：端侧嵌入验收（余弦 / L2 范数 / 确定性 / 失败即同数量零向量）。
// 参数：模型路径 + vocab.txt 内容（读文件是 ArkTS 侧的事）。
extern "C" const char* sekb_spike_embed_sanity(const char* model_path, const char* vocab_text);
// ── H5（工作流 F）：RAG 端口 ────────────────────────────────────────────────
// ① RDB 探针（HR2 要求的最小验证）：建表 + 插入 + 查询往返 + **关闭重开仍可读**。
//    参数只有 `databaseDir`（ArkTS 侧从 `UIAbilityContext.databaseDir` 取；
//    C/Kotlin 侧**不做路径推断**——App 沙箱路径与 bundle/模块名相关，自己拼必然错）。
//    返回多行报告，首行 `RDB-PROBE PASS|FAIL n/N`。
extern "C" const char* sekb_spike_rdb_probe(const char* database_dir);
// ② RAG 自检 + 检索评测（**走共享层** KnowledgeIndex/Retriever/RetrievalEvalRunner，
//    不在鸿蒙侧重写检索逻辑）。参数：模型路径 + vocab.txt 内容 + databaseDir。
//    返回多行报告，首行 `RAG PASS|FAIL n/N`。
extern "C" const char* sekb_spike_rag_selftest(const char* model_path, const char* vocab_text,
                                               const char* database_dir);
static napi_value Ping(napi_env env, napi_callback_info /*info*/) {
    napi_value out = nullptr;
    napi_create_int32(env, sekb_spike_ping(), &out);
    return out;
}

static napi_value EchoLen(napi_env env, napi_callback_info info) {
    size_t argc = 1;
    napi_value args[1] = {nullptr};
    napi_get_cb_info(env, info, &argc, args, nullptr, nullptr);
    if (argc < 1) {
        napi_value out = nullptr;
        napi_create_int32(env, -1, &out);
        return out;
    }
    // 两段式取字符串：先问长度、再取内容（NAPI 的固定套路）
    size_t len = 0;
    napi_get_value_string_utf8(env, args[0], nullptr, 0, &len);
    std::string buf(len + 1, '\0');
    napi_get_value_string_utf8(env, args[0], buf.data(), buf.size(), &len);
    buf.resize(len);

    napi_value out = nullptr;
    // 走 KMP 侧算长度：**不是** C++ 自己算——否则证明不了"调用到了 KMP 代码"
    napi_create_int32(env, sekb_spike_echo_len(buf.c_str()), &out);
    return out;
}

// 取一个字符串参数（NAPI 的固定套路：先问长度、再取内容）
static std::string ArgString(napi_env env, napi_callback_info info, bool* ok) {
    size_t argc = 1;
    napi_value args[1] = {nullptr};
    napi_get_cb_info(env, info, &argc, args, nullptr, nullptr);
    if (argc < 1) { *ok = false; return {}; }
    size_t len = 0;
    napi_get_value_string_utf8(env, args[0], nullptr, 0, &len);
    std::string buf(len + 1, '\0');
    napi_get_value_string_utf8(env, args[0], buf.data(), buf.size(), &len);
    buf.resize(len);
    *ok = true;
    return buf;
}

#define SEKB_STR_FN(cname, fn)                                                        \
    static napi_value cname(napi_env env, napi_callback_info info) {                   \
        bool ok = false;                                                              \
        std::string p = ArgString(env, info, &ok);                                     \
        napi_value out = nullptr;                                                     \
        napi_create_int32(env, ok ? fn(p.c_str()) : -1, &out);                        \
        return out;                                                                   \
    }

SEKB_STR_FN(MsSelfTest, sekb_spike_mindspore_selftest)
SEKB_STR_FN(MsFirstFloat, sekb_spike_mindspore_first_float)

// 共享层冒烟：无参 → 返回多行字符串。调用点见 pages/Index.ets（逐行打 hilog）。
static napi_value Smoke(napi_env env, napi_callback_info /*info*/) {
    const char* report = sekb_spike_smoke();
    napi_value out = nullptr;
    napi_create_string_utf8(env, report == nullptr ? "" : report, NAPI_AUTO_LENGTH, &out);
    return out;
}

// H6：嵌入验收（模型路径 + vocab 文本 → 多行报告）。
static napi_value EmbedSanity(napi_env env, napi_callback_info info) {
    size_t argc = 2;
    napi_value args[2] = {nullptr, nullptr};
    napi_get_cb_info(env, info, &argc, args, nullptr, nullptr);
    auto strAt = [&](size_t i) -> std::string {
        if (i >= argc || args[i] == nullptr) return {};
        size_t len = 0;
        napi_get_value_string_utf8(env, args[i], nullptr, 0, &len);
        std::string buf(len + 1, '\0');
        napi_get_value_string_utf8(env, args[i], buf.data(), buf.size(), &len);
        buf.resize(len);
        return buf;
    };
    const std::string model = strAt(0);
    const std::string vocab = strAt(1);
    const char* report = sekb_spike_embed_sanity(model.c_str(), vocab.c_str());
    napi_value out = nullptr;
    napi_create_string_utf8(env, report == nullptr ? "" : report, NAPI_AUTO_LENGTH, &out);
    return out;
}

// 契约夹具 runner：4 个字符串参数（routing/signals/privacy/check）→ 多行报告。
static napi_value ContractRun(napi_env env, napi_callback_info info) {
    size_t argc = 4;
    napi_value args[4] = {nullptr, nullptr, nullptr, nullptr};
    napi_get_cb_info(env, info, &argc, args, nullptr, nullptr);
    auto strAt = [&](size_t i) -> std::string {
        if (i >= argc || args[i] == nullptr) return {};
        size_t len = 0;
        napi_get_value_string_utf8(env, args[i], nullptr, 0, &len);
        std::string buf(len + 1, '\0');
        napi_get_value_string_utf8(env, args[i], buf.data(), buf.size(), &len);
        buf.resize(len);
        return buf;
    };
    const std::string routing = strAt(0);
    const std::string signals = strAt(1);
    const std::string privacy = strAt(2);
    const std::string check = strAt(3);
    const char* report = sekb_spike_contract(routing.c_str(), signals.c_str(),
                                             privacy.c_str(), check.c_str());
    napi_value out = nullptr;
    napi_create_string_utf8(env, report == nullptr ? "" : report, NAPI_AUTO_LENGTH, &out);
    return out;
}

// H2 反向验证：3 个字符串参数 → 两段报告（原样 / 反转期望值）。
static napi_value ContractPair(napi_env env, napi_callback_info info) {
    size_t argc = 3;
    napi_value args[3] = {nullptr, nullptr, nullptr};
    napi_get_cb_info(env, info, &argc, args, nullptr, nullptr);
    auto strAt = [&](size_t i) -> std::string {
        if (i >= argc || args[i] == nullptr) return {};
        size_t len = 0;
        napi_get_value_string_utf8(env, args[i], nullptr, 0, &len);
        std::string buf(len + 1, '\0');
        napi_get_value_string_utf8(env, args[i], buf.data(), buf.size(), &len);
        buf.resize(len);
        return buf;
    };
    const std::string routing = strAt(0);
    const std::string signals = strAt(1);
    const std::string privacy = strAt(2);
    const char* report = sekb_spike_contract_pair(routing.c_str(), signals.c_str(), privacy.c_str());
    napi_value out = nullptr;
    napi_create_string_utf8(env, report == nullptr ? "" : report, NAPI_AUTO_LENGTH, &out);
    return out;
}


// ═══════════════════════════════════════════════════════════════════════════
// H3 · libcurl 三探针（HR9）
//
// HR9 要求"先写三个最小探针，任一不通就回头找 owner"。三个探针是：
//   ① 能编：本文件能编译并链接（**不需要** curl 头/库——走 dlopen+dlsym）
//   ② HTTPS GET 通：`curl_easy_perform` 拿到 HTTP 200
//   ③ SSE 逐行回调：写回调被**多次**触发（说明是流式分块到达，不是一次性缓冲），
//      且能在回调里按 `\n` 切行计数
//
// ⚠️ curl 的常量值（CURLOPT_* / CURLINFO_*）是 **ABI 稳定**的（curl 官方承诺），
//    所以这里可以硬编码而不需要头文件。函数原型也按官方签名写死。
//    `curl_easy_setopt` 是**变参**函数 → 用 `int(*)(void*, int, ...)` 取地址调用。
// ═══════════════════════════════════════════════════════════════════════════

namespace h3 {

constexpr int kCurlOptUrl = 10002;                 // CURLOPT_URL
constexpr int kCurlOptWriteData = 10001;           // CURLOPT_WRITEDATA
constexpr int kCurlOptWriteFunction = 20011;       // CURLOPT_WRITEFUNCTION
constexpr int kCurlOptFollowLocation = 52;         // CURLOPT_FOLLOWLOCATION
constexpr int kCurlOptTimeout = 13;                // CURLOPT_TIMEOUT（秒）
constexpr int kCurlOptNoproxy = 10023;             // CURLOPT_NOPROXY
constexpr int kCurlInfoResponseCode = 0x200002;    // CURLINFO_RESPONSE_CODE
constexpr long kCurlGlobalAll = 3;                 // CURL_GLOBAL_ALL

using CurlHandle = void;
using WriteCb = size_t (*)(char*, size_t, size_t, void*);

struct CurlApi {
    void* lib = nullptr;
    CurlHandle* (*easy_init)() = nullptr;
    int (*easy_setopt)() = nullptr;   // 变参；用 auto 存不了，改成下面的函数指针类型
    int (*easy_perform)(CurlHandle*) = nullptr;
    void (*easy_cleanup)(CurlHandle*) = nullptr;
    int (*easy_getinfo)(CurlHandle*, int, ...) = nullptr;
    int (*global_init)(long) = nullptr;
    void (*global_cleanup)() = nullptr;
    const char* (*easy_strerror)(int) = nullptr;
};

// 变参函数指针必须显式写类型：`int (*)(void*, int, ...)`
using SetoptFn = int (*)(void*, int, ...);
using GetinfoFn = int (*)(void*, int, ...);

struct Sink {
    int chunks = 0;
    size_t bytes = 0;
    int lines = 0;
};

size_t writeSink(char* data, size_t size, size_t nmemb, void* userdata) {
    Sink* s = static_cast<Sink*>(userdata);
    const size_t n = size * nmemb;
    s->chunks++;
    s->bytes += n;
    for (size_t i = 0; i < n; i++) {
        if (data[i] == '\n') s->lines++;
    }
    return n;   // 必须返回"吃掉"的字节数，否则 curl 会当成写失败
}

std::string probe(bool doNetwork) {
    std::string out;
    char buf[512];

    // ── 探针 0：dlopen 一组候选库，分清"库在不在" ─────────────────────────
    // 设备上 `hdc shell test -e /system/lib64/<name>` 会报 MISSING（SELinux），
    // 但对**已确认在跑**的 libmindspore_lite_ndk.so 同样报 MISSING —— 那个探测法不可信。
    // dlopen 是**运行期真实解析**，结论可信。
    // ⚠️ 名字/路径要穷举一遍再下结论：第一次只试了 `libcurl.so` 就判"没有"，
    // 而 OHOS 的系统库命名有 `.z.so` / `_shared` 等变体（DevEco 自己的预览器里那份就叫
    // `libcurl_shared.dylib`），漏试一个就会得出错误结论。
    const char* candidates[] = {
        "libcurl.so", "libcurl.z.so", "libcurl_shared.z.so",
        "libcurl_ndk.z.so", "/system/lib64/libcurl.so",
        // 下面这些不是 curl，而是"端侧端口要用到的平台 C 能力"——同一轮把它们问清楚，
        // 因为它们决定 H4/H5 能否**纯 Kotlin**（cinterop 直调 C）而不必经 ArkTS 桥：
        //   HUKS（凭证）/ sqlite·rdb（向量库）/ crypto（哈希）/ ssl（TLS）
        "libohcrypto.so", "libhuks_ndk.z.so", "libnet_ssl.so", "libmindspore_lite_ndk.so",
        "libsqlite.so", "libsqlite.z.so", "libnative_rdb_ndk.z.so", "libnative_rdb.z.so",
        "libcrypto.so", "libssl.so", "libz.so", "libhilog_ndk.z.so", "libace_napi.z.so",
    };
    for (const char* name : candidates) {
        void* h = dlopen(name, RTLD_LAZY);
        if (h != nullptr) {
            out += std::string("LOAD OK   ") + name + "\n";
            if (std::string(name) == "libcurl.so") {
                // 保留句柄给下面的 curl 调用（不再 dlclose：进程内复用）
                dlclose(h);
            }
        } else {
            const char* err = dlerror();
            out += std::string("LOAD FAIL ") + name + " — " + (err ? err : "?") + "\n";
        }
    }

    // ── 探针 ①：拿到 curl 函数指针（等价于"能编"的另一半：符号真的可解析）──
    CurlApi api;
    api.lib = dlopen("libcurl.so", RTLD_LAZY);
    if (api.lib == nullptr) {
        const char* err = dlerror();
        out += std::string("PROBE1 FAIL libcurl.so 加载不了：") + (err ? err : "?") + "\n";
        return out;
    }
    api.easy_init = reinterpret_cast<CurlHandle* (*)()>(dlsym(api.lib, "curl_easy_init"));
    api.easy_setopt = reinterpret_cast<int (*)()>(dlsym(api.lib, "curl_easy_setopt"));
    api.easy_perform = reinterpret_cast<int (*)(CurlHandle*)>(dlsym(api.lib, "curl_easy_perform"));
    api.easy_cleanup = reinterpret_cast<void (*)(CurlHandle*)>(dlsym(api.lib, "curl_easy_cleanup"));
    api.easy_getinfo = reinterpret_cast<int (*)(CurlHandle*, int, ...)>(dlsym(api.lib, "curl_easy_getinfo"));
    api.global_init = reinterpret_cast<int (*)(long)>(dlsym(api.lib, "curl_global_init"));
    api.global_cleanup = reinterpret_cast<void (*)()>(dlsym(api.lib, "curl_global_cleanup"));
    api.easy_strerror = reinterpret_cast<const char* (*)(int)>(dlsym(api.lib, "curl_easy_strerror"));

    const char* need[] = {"curl_easy_init", "curl_easy_setopt", "curl_easy_perform",
                          "curl_easy_cleanup", "curl_easy_getinfo"};
    int missing = 0;
    for (const char* n : need) {
        if (dlsym(api.lib, n) == nullptr) {
            out += std::string("PROBE1 FAIL 缺符号 ") + n + "\n";
            missing++;
        }
    }
    if (missing == 0) {
        out += "PROBE1 PASS libcurl.so 可加载且 5 个核心符号可解析\n";
    } else {
        return out;
    }

    if (!doNetwork) {
        out += "PROBE2 SKIP 未请求网络（参数为 0）\n";
        return out;
    }

    // ── 探针 ②③：HTTPS GET + 写回调逐行计数 ─────────────────────────────
    api.global_init(kCurlGlobalAll);
    CurlHandle* h = api.easy_init();
    if (h == nullptr) {
        out += "PROBE2 FAIL curl_easy_init 返回空\n";
        return out;
    }
    Sink sink;
    const char* url = "https://example.com/";
    reinterpret_cast<SetoptFn>(api.easy_setopt)(h, kCurlOptUrl, url);
    reinterpret_cast<SetoptFn>(api.easy_setopt)(h, kCurlOptFollowLocation, 1L);
    reinterpret_cast<SetoptFn>(api.easy_setopt)(h, kCurlOptTimeout, 20L);
    reinterpret_cast<SetoptFn>(api.easy_setopt)(h, kCurlOptWriteFunction, &writeSink);
    reinterpret_cast<SetoptFn>(api.easy_setopt)(h, kCurlOptWriteData, &sink);

    const int rc = api.easy_perform(h);
    long code = 0;
    api.easy_getinfo(h, kCurlInfoResponseCode, &code);
    snprintf(buf, sizeof(buf), "PROBE2 %s https GET rc=%d http=%ld bytes=%zu chunks=%d\n",
             (rc == 0 && code == 200) ? "PASS" : "FAIL", rc, code, sink.bytes, sink.chunks);
    out += buf;
    if (rc != 0 && api.easy_strerror != nullptr) {
        out += std::string("       curl 错误：") + api.easy_strerror(rc) + "\n";
    }
    snprintf(buf, sizeof(buf), "PROBE3 %s 写回调被调用 %d 次、按 '\\n' 数出 %d 行（流式分块到达）\n",
             (sink.chunks >= 2 && sink.lines > 0) ? "PASS" : "FAIL", sink.chunks, sink.lines);
    out += buf;

    api.easy_cleanup(h);
    if (api.global_cleanup != nullptr) api.global_cleanup();
    return out;
}

}  // namespace h3

// 返回多行探针报告（脚本 grep `PROBE`）。参数 "0" = 只做加载/符号，不发网络。
static napi_value LibProbe(napi_env env, napi_callback_info info) {
    size_t argc = 1;
    napi_value args[1] = {nullptr};
    napi_get_cb_info(env, info, &argc, args, nullptr, nullptr);
    bool network = true;
    if (argc >= 1 && args[0] != nullptr) {
        char b[8] = {0};
        size_t len = 0;
        napi_get_value_string_utf8(env, args[0], b, sizeof(b), &len);
        network = !(b[0] == '0');
    }
    const std::string report = h3::probe(network);
    napi_value out = nullptr;
    napi_create_string_utf8(env, report.c_str(), NAPI_AUTO_LENGTH, &out);
    return out;
}

namespace h5 {

using Store = void;
using Cursor = void;
using VBucket = void;

#pragma pack(1)
struct Config {
    int32_t selfSize;
    const char* dataBaseDir;
    const char* storeName;
    const char* bundleName;
    const char* moduleName;
    bool isEncrypt;
    int32_t securityLevel;
    int32_t area;
};
#pragma pack()

// OH_VBucket / OH_Cursor 的**函数指针表**（只取本探针用到的字段；顺序必须与头文件一致）
//
// ⚠️ 两个结构体的头部**不一样**（这一条必须照头文件抄，不能凭印象）：
//   · `OH_Cursor`   = `int64_t id;` 然后**直接**是 `getColumnCount`（**没有** capability）；
//   · `OH_VBucket`  = `int64_t id; uint16_t capability;` 之后才是 `putText`。
// 抄错一个字段，后面所有函数指针都会整体错位——症状是 `goToNextRow` 调用到别的函数上，
// 表现为莫名其妙的崩溃或死循环。所以下面两个 struct 与头文件逐字段对应。
struct VBucketVtbl {
    int64_t id;
    uint16_t capability;
    int (*putText)(VBucket*, const char*, const char*);
    int (*putInt64)(VBucket*, const char*, int64_t);
    int (*putReal)(VBucket*, const char*, double);
    int (*putBlob)(VBucket*, const char*, const uint8_t*, uint32_t);
    int (*putNull)(VBucket*, const char*);
    int (*clear)(VBucket*);
    int (*destroy)(VBucket*);
};

struct CursorVtbl2 {
    int64_t id;
    int (*getColumnCount)(Cursor*, int*);
    int (*getColumnType)(Cursor*, int32_t, uint32_t*);
    int (*getColumnIndex)(Cursor*, const char*, int*);
    int (*getColumnName)(Cursor*, int32_t, char*, int);
    int (*getRowCount)(Cursor*, int*);
    int (*goToNextRow)(Cursor*);
    int (*getSize)(Cursor*, int32_t, size_t*);
    int (*getText)(Cursor*, int32_t, char*, int);
    int (*getInt64)(Cursor*, int32_t, int64_t*);
    int (*getReal)(Cursor*, int32_t, double*);
    int (*getBlob)(Cursor*, int32_t, unsigned char*, int);
    int (*isNull)(Cursor*, int32_t, bool*);
    int (*destroy)(Cursor*);
};

struct Api {
    void* lib = nullptr;
    Store* (*getOrOpen)(const Config*, int*) = nullptr;
    int (*execute)(Store*, const char*) = nullptr;
    int (*closeStore)(Store*) = nullptr;
    int (*deleteStore)(const Config*) = nullptr;
    int (*insert)(Store*, const char*, VBucket*) = nullptr;
    Cursor* (*executeQuery)(Store*, const char*) = nullptr;
    VBucket* (*createValuesBucket)() = nullptr;
};

/** 把 3 个 float32 小端打包（与 Kotlin `encodeVector` 同口径）。 */
std::string packFloats3() {
    float v[3] = {1.5f, -2.25f, 1.0e-3f};
    std::string out(12, '\0');
    memcpy(out.data(), v, 12);
    return out;
}

std::string rdbLinkProbe(const std::string& databaseDir) {
    std::string out;
    Api api;

    // ── ① 运行期解析（这是唯一可信的"库在不在"判据）──────────────────────
    api.lib = dlopen("libnative_rdb_ndk.z.so", RTLD_LAZY);
    if (api.lib == nullptr) {
        const char* e = dlerror();
        return std::string("RDB-DLOPEN FAIL libnative_rdb_ndk.z.so 加载不了：") + (e ? e : "?") + "\n";
    }
    out += "LOAD OK   libnative_rdb_ndk.z.so\n";

    auto sym = [&](const char* n) -> void* { return dlsym(api.lib, n); };
    api.getOrOpen = reinterpret_cast<Store* (*)(const Config*, int*)>(sym("OH_Rdb_GetOrOpen"));
    api.execute = reinterpret_cast<int (*)(Store*, const char*)>(sym("OH_Rdb_Execute"));
    api.closeStore = reinterpret_cast<int (*)(Store*)>(sym("OH_Rdb_CloseStore"));
    api.deleteStore = reinterpret_cast<int (*)(const Config*)>(sym("OH_Rdb_DeleteStore"));
    api.insert = reinterpret_cast<int (*)(Store*, const char*, VBucket*)>(sym("OH_Rdb_Insert"));
    api.executeQuery = reinterpret_cast<Cursor* (*)(Store*, const char*)>(sym("OH_Rdb_ExecuteQuery"));
    api.createValuesBucket = reinterpret_cast<VBucket* (*)()>(sym("OH_Rdb_CreateValuesBucket"));

    struct { const char* name; void* p; } need[] = {
        {"OH_Rdb_GetOrOpen", reinterpret_cast<void*>(api.getOrOpen)},
        {"OH_Rdb_Execute", reinterpret_cast<void*>(api.execute)},
        {"OH_Rdb_CloseStore", reinterpret_cast<void*>(api.closeStore)},
        {"OH_Rdb_DeleteStore", reinterpret_cast<void*>(api.deleteStore)},
        {"OH_Rdb_Insert", reinterpret_cast<void*>(api.insert)},
        {"OH_Rdb_ExecuteQuery", reinterpret_cast<void*>(api.executeQuery)},
        {"OH_Rdb_CreateValuesBucket", reinterpret_cast<void*>(api.createValuesBucket)},
        // 下面 3 个是 OH_Cursor / OH_VBucket 的**字段**（不是顶层函数），存在即说明 vtable 可用
        {"OH_Rdb_CreatePredicates", sym("OH_Rdb_CreatePredicates")},
        {"OH_Rdb_Delete", sym("OH_Rdb_Delete")},
        {"OH_Rdb_InsertWithConflictResolution", sym("OH_Rdb_InsertWithConflictResolution")},
    };
    int missing = 0;
    for (auto& s : need) {
        if (s.p == nullptr) {
            out += std::string("SYMBOL MISSING ") + s.name + "\n";
            missing++;
        }
    }
    if (missing > 0) {
        out += "RDB-DLOPEN FAIL 缺 " + std::to_string(missing) + " 个符号\n";
        return out;
    }
    out += "SYMBOLS OK  10 个符号全部解析（含 GetOrOpen/Execute/Insert/ExecuteQuery/CloseStore）\n";

    // ── ② 结构体布局：45 字节（pack(1)）是**硬判据**────────────────────────
    char buf[256];
    snprintf(buf, sizeof(buf), "CONFIG SIZE sizeof(OH_Rdb_Config)=%zu（期望 45 = 4+8*4+1+4+4，pack(1)）\n",
             sizeof(Config));
    out += buf;

    // ── ③ 建表 + 插入（含 BLOB）+ 查询往返 ────────────────────────────────
    Config cfg{};
    cfg.selfSize = static_cast<int32_t>(sizeof(Config));
    cfg.dataBaseDir = databaseDir.c_str();
    cfg.storeName = "sekb_probe_cpp.db";
    cfg.bundleName = "com.sekb.ohos.spike";
    cfg.moduleName = "entry";
    cfg.isEncrypt = false;
    cfg.securityLevel = 1;   // S1
    cfg.area = 0;

    api.deleteStore(&cfg);   // 让探针可重复运行
    int err = 0;
    Store* store = api.getOrOpen(&cfg, &err);
    if (store == nullptr) {
        out += "OPEN FAIL errCode=" + std::to_string(err) + "\n";
        out += "RDB-DLOPEN FAIL 打开库失败\n";
        return out;
    }
    snprintf(buf, sizeof(buf), "OPEN OK   errCode=%d dir=%s\n", err, databaseDir.c_str());
    out += buf;

    const char* ddl =
        "CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, "
        "text TEXT NOT NULL, dim INTEGER NOT NULL, vector BLOB NOT NULL)";
    int rcExec = api.execute(store, ddl);
    api.execute(store, "DELETE FROM chunks WHERE source_id = 'probe'");
    snprintf(buf, sizeof(buf), "EXECUTE   rc=%d\n", rcExec);
    out += buf;

    std::string blob = packFloats3();
    VBucket* b = api.createValuesBucket();
    if (b == nullptr) {
        out += "RDB-DLOPEN FAIL CreateValuesBucket 返回空\n";
        api.closeStore(store);
        return out;
    }
    auto* bvt = reinterpret_cast<VBucketVtbl*>(b);
    int r1 = bvt->putText(b, "id", "probe#0");
    int r2 = bvt->putText(b, "source_id", "probe");
    int r3 = bvt->putText(b, "text", "端侧 RAG 探针文本");
    int r4 = bvt->putInt64(b, "dim", 3);
    int r5 = bvt->putBlob(b, "vector", reinterpret_cast<const uint8_t*>(blob.data()),
                          static_cast<uint32_t>(blob.size()));
    snprintf(buf, sizeof(buf), "PUT       text=%d/%d/%d int64=%d blob=%d\n", r1, r2, r3, r4, r5);
    out += buf;
    int rowId = api.insert(store, "chunks", b);
    bvt->destroy(b);
    snprintf(buf, sizeof(buf), "INSERT    rowId=%d\n", rowId);
    out += buf;

    Cursor* cur = api.executeQuery(store, "SELECT id, text, dim, vector FROM chunks WHERE source_id = 'probe'");
    if (cur == nullptr) {
        out += "RDB-DLOPEN FAIL ExecuteQuery 返回空\n";
        api.closeStore(store);
        return out;
    }
    auto* cvt = reinterpret_cast<CursorVtbl2*>(cur);
    int rows = 0;
    std::string gotId, gotText;
    int64_t gotDim = -1;
    bool blobOk = false;
    while (cvt->goToNextRow(cur) == 0) {
        rows++;
        int idx = -1;
        cvt->getColumnIndex(cur, "id", &idx);
        size_t n = 0;
        cvt->getSize(cur, idx, &n);
        std::string s(n + 1, '\0');
        cvt->getText(cur, idx, s.data(), static_cast<int>(n + 1));
        gotId = s.c_str();

        cvt->getColumnIndex(cur, "text", &idx);
        cvt->getSize(cur, idx, &n);
        std::string t(n + 1, '\0');
        cvt->getText(cur, idx, t.data(), static_cast<int>(n + 1));
        gotText = t.c_str();

        cvt->getColumnIndex(cur, "dim", &idx);
        cvt->getInt64(cur, idx, &gotDim);

        cvt->getColumnIndex(cur, "vector", &idx);
        cvt->getSize(cur, idx, &n);
        std::string vec(n, '\0');
        cvt->getBlob(cur, idx, reinterpret_cast<unsigned char*>(vec.data()), static_cast<int>(n));
        blobOk = (vec == blob);
    }
    cvt->destroy(cur);
    snprintf(buf, sizeof(buf), "QUERY     rows=%d id=%s dim=%lld text=%s\n", rows, gotId.c_str(),
             static_cast<long long>(gotDim), gotText.c_str());
    out += buf;
    snprintf(buf, sizeof(buf), "BLOB      %zuB 读出后逐字节相同=%s\n", blob.size(), blobOk ? "true" : "false");
    out += buf;

    int rcClose = api.closeStore(store);

    // ── ④ **关闭后重新打开**：持久化的硬判据 ──────────────────────────────
    int err2 = 0;
    Store* store2 = api.getOrOpen(&cfg, &err2);
    int rows2 = -1;
    if (store2 != nullptr) {
        Cursor* c2 = api.executeQuery(store2, "SELECT COUNT(*) AS n FROM chunks WHERE source_id = 'probe'");
        if (c2 != nullptr) {
            auto* v2 = reinterpret_cast<CursorVtbl2*>(c2);
            rows2 = 0;
            if (v2->goToNextRow(c2) == 0) {
                int idx = -1;
                v2->getColumnIndex(c2, "n", &idx);
                int64_t v = 0;
                v2->getInt64(c2, idx, &v);
                rows2 = static_cast<int>(v);
            }
            v2->destroy(c2);
        }
        api.closeStore(store2);
    }
    snprintf(buf, sizeof(buf), "REOPEN    close=%d reopen_err=%d 行数=%d\n", rcClose, err2, rows2);
    out += buf;

    bool ok = rcExec == 0 && rowId >= 1 && rows == 1 && gotId == "probe#0" && gotDim == 3 &&
              gotText == "端侧 RAG 探针文本" && blobOk && rows2 == 1;
    out += std::string("RDB-DLOPEN ") + (ok ? "PASS" : "FAIL") + "\n";
    return out;
}

}  // namespace h5

// H5 备用判据：C++ dlopen 版 RDB 往返探针（不依赖链接期，见 namespace h5 的说明）
static napi_value RdbLinkProbe(napi_env env, napi_callback_info info) {
    const std::string dir = napi_args::argAt(env, info, 1, 0);
    return napi_args::returnString(env, h5::rdbLinkProbe(dir).c_str());
}

// RDB 探针（1 个字符串参数：databaseDir）
static napi_value RdbProbe(napi_env env, napi_callback_info info) {
    const std::string dir = napi_args::argAt(env, info, 1, 0);
    return napi_args::returnString(env, sekb_spike_rdb_probe(dir.c_str()));
}

// RAG 自检 + 检索评测（3 个字符串参数：模型路径 / vocab 内容 / databaseDir）
static napi_value RagSelfTest(napi_env env, napi_callback_info info) {
    const std::string model = napi_args::argAt(env, info, 3, 0);
    const std::string vocab = napi_args::argAt(env, info, 3, 1);
    const std::string dir = napi_args::argAt(env, info, 3, 2);
    return napi_args::returnString(env, sekb_spike_rag_selftest(model.c_str(), vocab.c_str(), dir.c_str()));
}


// H3/H4 的入口在**独立文件** `h3h4_napi.cpp` 里注册（工作流 E′ 只加这一行声明 + 一句调用，
// 目的是把与工作流 F（H5/RDB）同时改这个文件的冲突面压到最小）。
void RegisterSekbH3H4(napi_env env, napi_value exports);

EXTERN_C_START
static napi_value Init(napi_env env, napi_value exports) {
    napi_property_descriptor desc[] = {
        {"ping", nullptr, Ping, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"echoLen", nullptr, EchoLen, nullptr, nullptr, nullptr, napi_default, nullptr},
        // 真机日入口：传 .ms 的**真实文件路径**，返回元素数（>0）或步骤码（<0）
        {"msSelfTest", nullptr, MsSelfTest, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"msFirstFloat", nullptr, MsFirstFloat, nullptr, nullptr, nullptr, napi_default, nullptr},
        // H1：共享层 interop 冒烟（多行字符串），脚本 grep `SMOKE PASS`
        {"smoke", nullptr, Smoke, nullptr, nullptr, nullptr, napi_default, nullptr},
        // H2：契约夹具 runner（37 case），脚本 grep `CONTRACT`
        {"contractRun", nullptr, ContractRun, nullptr, nullptr, nullptr, napi_default, nullptr},
        // H2 反向验证：原样 + 反转期望值两遍（一绿一红才叫"夹具不是摆设"）
        {"contractPair", nullptr, ContractPair, nullptr, nullptr, nullptr, napi_default, nullptr},
        // H3：libcurl 三探针（dlopen，运行期真实解析）
        {"libProbe", nullptr, LibProbe, nullptr, nullptr, nullptr, napi_default, nullptr},
        // H6：端侧嵌入验收
        {"embedSanity", nullptr, EmbedSanity, nullptr, nullptr, nullptr, napi_default, nullptr},
        // ── H5（工作流 F）：RDB 探针 + RAG 自检/检索评测 ──
        {"rdbProbe", nullptr, RdbProbe, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"rdbLinkProbe", nullptr, RdbLinkProbe, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"ragSelfTest", nullptr, RagSelfTest, nullptr, nullptr, nullptr, napi_default, nullptr},
        // ── H3/H4（工作流 E′）：**注册在独立文件 `h3h4_napi.cpp` 里** ──
        // 这样两边同时改 `apps/harmony` 时的冲突面只有下面这一行调用。
        // 见该文件头部：为什么 H3 的判据由 ArkTS 把已观测事实交给 Kotlin，而不是 Kotlin 发起请求。
    };
    napi_define_properties(env, exports, sizeof(desc) / sizeof(desc[0]), desc);
    // H3/H4 的入口注册在独立文件中（只加这一行，见 h3h4_napi.cpp）
    RegisterSekbH3H4(env, exports);
    return exports;
}
EXTERN_C_END

static napi_module knSpikeModule = {
    .nm_version = 1,
    .nm_flags = 0,
    .nm_filename = nullptr,
    .nm_register_func = Init,
    .nm_modname = "knspike",
    .nm_priv = nullptr,
    .reserved = {0},
};

extern "C" __attribute__((constructor)) void RegisterKnSpikeModule(void) {
    napi_module_register(&knSpikeModule);
}
