cmake_minimum_required(VERSION 3.5.0)
project(knspike)

set(CMAKE_CXX_STANDARD 17)

# OHOS 的 ohos.toolchain.cmake 会提供 OHOS_ARCH（arm64-v8a / x86_64），
# 用它去挑对应 ABI 的 KMP 产物——一份 CMakeLists 同时服务真机与模拟器。
if(NOT OHOS_ARCH)
    set(OHOS_ARCH arm64-v8a)
endif()
message(STATUS "knspike: OHOS_ARCH=${OHOS_ARCH}")

add_library(kn SHARED IMPORTED)
set_target_properties(kn PROPERTIES
    IMPORTED_LOCATION ${CMAKE_CURRENT_SOURCE_DIR}/../../../libs/${OHOS_ARCH}/libkn.so)

# ⚠️ 两个翻译单元（不是 glob）：`napi_init.cpp` 是主模块，
# `h3h4_napi.cpp` 是 H3/H4 的入口（工作流 E′ 独立成文件，减少与 H5 同时改本目录的冲突面）。
# 新增 .cpp 时**要显式加到这里**（glob 会让"漏加文件"变成静默少功能）。
add_library(knspike SHARED napi_init.cpp h3h4_napi.cpp)
target_link_libraries(knspike PUBLIC libace_napi.z.so kn)
