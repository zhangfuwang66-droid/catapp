#pragma once

#include <cstdint>
#include <esp32-hal-psram.h>
#include <esp_heap_caps.h>
#include <mbedtls/platform.h>

namespace DeviceTlsMemory {

// Arduino ESP32 的预编译 mbedTLS 默认只从内部堆分配；多个 TLS 连接会耗尽
// 内部堆，即使 PSRAM 仍有余量。证书和 TLS 缓冲优先放 PSRAM，给 DMA、Wi-Fi
// 和任务栈保留内部内存。两种内存都由 heap_caps_free 释放。
inline void* calloc(size_t count, size_t size) {
  if (size && count > SIZE_MAX / size) return nullptr;
  void* memory = heap_caps_calloc(count, size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!memory) memory = heap_caps_calloc(count, size, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  return memory;
}

// 只在 setup 中、启动 Wi-Fi 和全部 TLS 业务任务前调用一次；运行中不切换分配器。
// 未配置 PSRAM 或核心不提供运行时钩子时，保留核心原分配器并在诊断中返回 false。
inline bool begin() {
#if defined(MBEDTLS_PLATFORM_MEMORY) && !defined(MBEDTLS_PLATFORM_CALLOC_MACRO) && !defined(MBEDTLS_PLATFORM_FREE_MACRO)
  return psramFound() && mbedtls_platform_set_calloc_free(calloc, heap_caps_free) == 0;
#else
  return false;
#endif
}

}  // namespace DeviceTlsMemory
