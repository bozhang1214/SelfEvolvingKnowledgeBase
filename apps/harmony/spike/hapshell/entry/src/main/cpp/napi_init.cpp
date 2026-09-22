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

EXTERN_C_START
static napi_value Init(napi_env env, napi_value exports) {
    napi_property_descriptor desc[] = {
        {"ping", nullptr, Ping, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"echoLen", nullptr, EchoLen, nullptr, nullptr, nullptr, napi_default, nullptr},
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
