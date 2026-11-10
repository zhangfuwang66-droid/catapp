#include "../esp32_servo_control/SetupSession.h"
#include <cstdlib>
#include <iostream>

#define CHECK(value) do { if (!(value)) { std::cerr << __func__ << ":" << __LINE__ << " " #value "\n"; std::exit(1); } } while (false)

int main() {
  SetupSession idle;
  CHECK(!idle.holdsNetwork(0, false));  // 无手机时仍可重连保存的网络。
  CHECK(idle.holdsNetwork(20000, true));
  idle.touch(20000);
  CHECK(idle.holdsNetwork(29999, false));  // 手机短暂断开也不立刻切信道。
  CHECK(!idle.holdsNetwork(30000, false));
  idle.touch(0xfffffff0);
  CHECK(idle.holdsNetwork(100, false));  // millis() 回绕。
  CHECK(!idle.holdsNetwork(10000, false));

  SetupSession scan;
  scan.startScan(0xfffffff0);
  CHECK(scan.scanning());
  CHECK(!scan.scanExpired(10000));
  CHECK(scan.scanExpired(12000));
  CHECK(scan.holdsNetwork(20000, false));
  scan.expireScan();
  CHECK(scan.scanTimedOut() && !scan.scanning());
  CHECK(!scan.holdsNetwork(20000, false));  // 超时不能永久阻止重连。
  scan.startScan(30000);
  CHECK(!scan.scanTimedOut());
  scan.finishScan();
  CHECK(!scan.scanning() && !scan.scanExpired(100000));

  SetupSession save;
  CHECK(!save.shouldReboot(100000));
  save.saved(0xfffffff0);
  CHECK(save.rebootPending() && save.holdsNetwork(10000, false));
  CHECK(!save.shouldReboot(1000));
  save.touch(2000);
  CHECK(save.shouldReboot(8000));  // 状态轮询不延迟重启。
  std::cout << "Setup session passed: client grace, bounded scanning/retry, save receipt delay, clock wrap.\n";
}
