#include "../esp32_servo_control/DeviceProfile.h"

#include <assert.h>
#include <string.h>

int main() {
  char id[24];
  // 同一芯片在两种产品中不能共用设备身份；默认猫编号必须兼容已绑定设备。
  assert(DeviceProfile::formatDeviceId(id, sizeof(id), UINT64_C(0x649020AD4F7C)));
#if CYBERMIAO_DEVICE_DEER
  assert(strcmp(id, "deer-649020AD4F7C") == 0);
  assert(strcmp(DeviceProfile::type, "deer") == 0);
  assert(strcmp(DeviceProfile::setupPrefix, "CyberDeer-") == 0);
  assert(strcmp(DeviceProfile::brand, "CYBER DEER") == 0);
  assert(DeviceProfile::formatDeviceId(id, sizeof(id), UINT64_C(0xC8381AAD4F7C)));
  assert(strcmp(id, "deer-C8381AAD4F7C") == 0);
#else
  assert(strcmp(id, "cat-649020AD4F7C") == 0);
  assert(strcmp(DeviceProfile::type, "cat") == 0);
  assert(strcmp(DeviceProfile::setupPrefix, "CyberMiao-") == 0);
  assert(strcmp(DeviceProfile::brand, "CYBER MIAO") == 0);
#endif
  char tooSmall[4];
  assert(!DeviceProfile::formatDeviceId(tooSmall, sizeof(tooSmall), UINT64_C(0xC8381AAD4F7C)));
  assert(DeviceProfile::formatDeviceId(id, sizeof(id), 1));
  assert(strstr(id, "-000000000001") != nullptr);
  return 0;
}
