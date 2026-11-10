#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

// 默认沿用小猫。鹿版本须在整个草图编译时传入 -DCYBERMIAO_DEVICE_DEER=1。
#ifndef CYBERMIAO_DEVICE_DEER
#define CYBERMIAO_DEVICE_DEER 0
#endif
#if CYBERMIAO_DEVICE_DEER != 0 && CYBERMIAO_DEVICE_DEER != 1
#error "CYBERMIAO_DEVICE_DEER must be 0 or 1"
#endif

// 设置页使用相邻字符串字面量，保持 HTML 留在 Flash，避免整页复制到内部堆。
#if CYBERMIAO_DEVICE_DEER
#define CYBERMIAO_SHORT_NAME "小鹿"
#define CYBERMIAO_BRAND "CYBER DEER"
#define CYBERMIAO_SETUP_AVATAR "cat deer"
#else
#define CYBERMIAO_SHORT_NAME "小喵"
#define CYBERMIAO_BRAND "CYBER MIAO"
#define CYBERMIAO_SETUP_AVATAR "cat"
#endif

namespace DeviceProfile {
constexpr const char* type = CYBERMIAO_DEVICE_DEER ? "deer" : "cat";
constexpr const char* name = CYBERMIAO_DEVICE_DEER ? "赛博小鹿" : "赛博小喵";
constexpr const char* brand = CYBERMIAO_BRAND;
constexpr const char* setupPrefix = CYBERMIAO_DEVICE_DEER ? "CyberDeer-" : "CyberMiao-";

// 保持已有猫编号的字节序和大小写，设备类型前缀隔离两种产品的身份。
inline bool formatDeviceId(char* output, size_t capacity, uint64_t mac) {
  int length = snprintf(output, capacity, "%s-%04X%08X", type,
                        static_cast<unsigned int>(mac >> 32), static_cast<unsigned int>(mac));
  return length > 0 && static_cast<size_t>(length) < capacity;
}
}  // namespace DeviceProfile
