#pragma once
#include "DeviceProfile.h"
#if __has_include("FirmwareBuildVersion.h")
#include "FirmwareBuildVersion.h"
#endif

#ifndef CYBERMIAO_FIRMWARE_VERSION
#define CYBERMIAO_FIRMWARE_VERSION "0.3.0-ota.1"
#endif
#ifndef CYBERMIAO_FIRMWARE_SEQUENCE
#define CYBERMIAO_FIRMWARE_SEQUENCE 1
#endif

// 小猫保持旧分区与 USB 升级方式，先仅对鹿开启 OTA。
#if CYBERMIAO_DEVICE_DEER
#define CYBERMIAO_OTA_ENABLED 1
#define CYBERMIAO_OTA_PROFILE "deer"
#else
#define CYBERMIAO_OTA_ENABLED 0
#define CYBERMIAO_OTA_PROFILE "cat"
#endif

#define CYBERMIAO_STRINGIFY_IMPL(value) #value
#define CYBERMIAO_STRINGIFY(value) CYBERMIAO_STRINGIFY_IMPL(value)
constexpr const char* CYBERMIAO_OTA_MARKER = "CYBERMIAO_OTA:" \
    CYBERMIAO_OTA_PROFILE ":" CYBERMIAO_FIRMWARE_VERSION ":" CYBERMIAO_STRINGIFY(CYBERMIAO_FIRMWARE_SEQUENCE);
