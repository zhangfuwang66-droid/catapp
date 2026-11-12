#include "../esp32_servo_control/ConnectionScreenState.h"

#include <assert.h>
#include <stdint.h>
#include <string.h>

using ConnectionScreen::Input;
using ConnectionScreen::Stage;

static Input onlineAccount() {
  Input input;
  input.online = true;
  input.accounts = true;
  input.tls = true;
  input.clockReady = true;
  return input;
}

static void testStagePriorityAndPairing() {
  Input input = onlineAccount();
  input.link.completeVerification(false, 0);
  input.hasPairingCode = true;
  input.expiresIn = 300;
  assert(ConnectionScreen::selectStage(input) == Stage::PairingReady);
  input.expiresIn = 0;
  assert(ConnectionScreen::selectStage(input) == Stage::PairingRequest);
  input.expiresIn = 300;
  input.hasPairingCode = false;
  assert(ConnectionScreen::selectStage(input) == Stage::PairingRequest);
  input.hasPairingCode = true;
  input.link.bound = true;
  assert(ConnectionScreen::selectStage(input) == Stage::BindingCheck);

  // 状态优先于遗留代码：断服、身份无效和校时都不能显示上一次配对码。
  input.link.status = DeviceLinkStatus::ServerOffline;
  assert(ConnectionScreen::selectStage(input) == Stage::ServerOffline);
  input.link.status = DeviceLinkStatus::NeedCredential;
  assert(ConnectionScreen::selectStage(input) == Stage::NeedCredential);
  input.link.status = DeviceLinkStatus::CredentialInvalid;
  assert(ConnectionScreen::selectStage(input) == Stage::InvalidCredential);
  input.clockReady = false;
  assert(ConnectionScreen::selectStage(input) == Stage::TimeSync);
  input.online = false;
  assert(ConnectionScreen::selectStage(input) == Stage::WifiConnecting);
  input.portal = true;
  assert(ConnectionScreen::selectStage(input) == Stage::WifiSetup);
  input.online = true;
  assert(ConnectionScreen::selectStage(input) == Stage::WifiSetup);
}

static void testVerificationAndLocalMode() {
  Input input = onlineAccount();
  const DeviceLinkStatus pending[] = {DeviceLinkStatus::Local, DeviceLinkStatus::WifiOffline,
                                     DeviceLinkStatus::WaitingClock};
  for (DeviceLinkStatus status : pending) {
    input.link.status = status;
    input.hasPairingCode = true;
    input.expiresIn = 300;
    assert(ConnectionScreen::selectStage(input) == Stage::BindingCheck);
  }
  input.link.completeVerification(true, input.link.verificationRevision);
  assert(ConnectionScreen::selectStage(input) == Stage::Ready);
  input.link.requestVerification();
  assert(ConnectionScreen::selectStage(input) == Stage::BindingCheck);
  input.link.completeVerification(true, input.link.verificationRevision - 1);
  assert(ConnectionScreen::selectStage(input) == Stage::BindingCheck);
  input.link.completeVerification(true, input.link.verificationRevision);
  assert(ConnectionScreen::selectStage(input) == Stage::Ready);
  input.link.updateStatus(DeviceLinkStatus::ServerOffline);
  assert(input.link.businessAllowed());
  assert(ConnectionScreen::selectStage(input) == Stage::ServerOffline);

  input.accounts = false;
  assert(ConnectionScreen::selectStage(input) == Stage::Ready);
  input.clockReady = false;
  assert(ConnectionScreen::selectStage(input) == Stage::TimeSync);
  input.tls = false;
  assert(ConnectionScreen::selectStage(input) == Stage::Ready);
  input.online = false;
  assert(ConnectionScreen::selectStage(input) == Stage::WifiConnecting);
}

static void expectUrl(const char* source, const char* expected) {
  char output[256];
  assert(ConnectionScreen::formatDisplayUrl(source, output, sizeof(output)));
  assert(strcmp(output, expected) == 0);
  char inPlace[256];
  strcpy(inPlace, source);
  assert(ConnectionScreen::formatDisplayUrl(inPlace, inPlace, sizeof(inPlace)));
  assert(strcmp(inPlace, expected) == 0);
}

static void testDisplayUrl() {
  expectUrl("https://123.57.23.227:443", "https://123.57.23.227");
  expectUrl("https://cat.example:443/path:443?q=:443#part:443", "https://cat.example/path:443?q=:443#part:443");
  expectUrl("https://cat.example:443?x=1", "https://cat.example?x=1");
  expectUrl("https://cat.example:443#section", "https://cat.example#section");
  expectUrl("HTTPS://cat.example:443/", "HTTPS://cat.example/");
  expectUrl("https://[2001:db8::443]:443/status?x=1#part", "https://[2001:db8::443]/status?x=1#part");
  expectUrl("https://[::1]:8443/", "https://[::1]:8443/");
  expectUrl("https://cat.example:8443/path", "https://cat.example:8443/path");
  expectUrl("http://cat.example:443/", "http://cat.example:443/");
  expectUrl("https://cat.example/path:443", "https://cat.example/path:443");
  expectUrl("https://cat.example:4430/", "https://cat.example:4430/");
  expectUrl("https://2001:db8::443", "https://2001:db8::443");
  expectUrl("https://[::1:443", "https://[::1:443");
  expectUrl("", "");

  char output[sizeof("https://x")];
  assert(ConnectionScreen::formatDisplayUrl("https://x:443", output, sizeof(output)));
  assert(strcmp(output, "https://x") == 0);
  output[0] = 'x';
  assert(!ConnectionScreen::formatDisplayUrl("https://xx:443", output, sizeof(output)));
  assert(output[0] == '\0');
  output[0] = 'x';
  assert(!ConnectionScreen::formatDisplayUrl("https://x", output, sizeof(output) - 1));
  assert(output[0] == '\0');
  assert(!ConnectionScreen::formatDisplayUrl(nullptr, output, sizeof(output)));
  assert(output[0] == '\0');
  assert(!ConnectionScreen::formatDisplayUrl("x", nullptr, 0));
  char untouched = 'x';
  assert(!ConnectionScreen::formatDisplayUrl("x", &untouched, 0));
  assert(untouched == 'x');
  char one = 'x';
  assert(!ConnectionScreen::formatDisplayUrl("x", &one, 1));
  assert(one == '\0');
}

static void testSuccessFeedback() {
  ConnectionScreen::SuccessFeedback feedback;
  assert(!feedback.visible(Stage::Ready, 0));
  assert(!feedback.visible(Stage::Ready, 2000));
  const Stage allowed[] = {Stage::PairingReady, Stage::PairingRequest, Stage::BindingCheck};
  for (Stage previous : allowed) {
    assert(!feedback.visible(previous, 3000));
    assert(feedback.visible(Stage::Ready, 3001));
    assert(feedback.visible(Stage::Ready, 4200));
    assert(!feedback.visible(Stage::Ready, 4201));
    assert(!feedback.visible(Stage::Ready, 6000));
  }
  const Stage other[] = {Stage::Boot, Stage::WifiConnecting, Stage::WifiSetup, Stage::TimeSync,
                        Stage::ServerOffline, Stage::NeedCredential, Stage::InvalidCredential};
  for (Stage previous : other) {
    assert(!feedback.visible(previous, 7000));
    assert(!feedback.visible(Stage::Ready, 7001));
  }
  assert(!feedback.visible(Stage::PairingReady, 8000));
  assert(feedback.visible(Stage::Ready, 8001));
  assert(!feedback.visible(Stage::ServerOffline, 8002));
  assert(!feedback.visible(Stage::Ready, 8003));

  ConnectionScreen::SuccessFeedback atZero;
  assert(!atZero.visible(Stage::BindingCheck, UINT32_MAX));
  assert(atZero.visible(Stage::Ready, 0));
  assert(atZero.visible(Stage::Ready, 1199));
  assert(!atZero.visible(Stage::Ready, 1200));

  ConnectionScreen::SuccessFeedback rollover;
  assert(!rollover.visible(Stage::BindingCheck, UINT32_MAX - 601));
  assert(rollover.visible(Stage::Ready, UINT32_MAX - 600));
  assert(rollover.visible(Stage::Ready, 598));
  assert(!rollover.visible(Stage::Ready, 599));
}

int main() {
  testStagePriorityAndPairing();
  testVerificationAndLocalMode();
  testDisplayUrl();
  testSuccessFeedback();
  return 0;
}
