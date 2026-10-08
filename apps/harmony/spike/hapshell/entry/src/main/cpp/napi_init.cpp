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
    };
    napi_define_properties(env, exports, sizeof(desc) / sizeof(desc[0]), desc);
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
