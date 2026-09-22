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

add_library(knspike SHARED napi_init.cpp)
target_link_libraries(knspike PUBLIC libace_napi.z.so kn)
