In file included from c:\Users\DNUI\Documents\Arduino\libraries\ESP32Servo\src/ESP32PWM.h:52,
                 from c:\Users\DNUI\Documents\Arduino\libraries\ESP32Servo\src/ESP32Servo.h:84,
                 from C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino:5:
C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\tools\esp32s3-libs\3.3.10-cn/include/driver/deprecated/driver/mcpwm.h:15:2: warning: #warning "legacy MCPWM driver is deprecated, please migrate to the new driver (include driver/mcpwm_prelude.h)" [-Wcpp]
   15 | #warning "legacy MCPWM driver is deprecated, please migrate to the new driver (include driver/mcpwm_prelude.h)"
      |  ^~~~~~~
In file included from c:\Users\DNUI\Documents\Arduino\libraries\ArduinoWebsockets\src/tiny_websockets/internals/ws_common.hpp:35,
                 from c:\Users\DNUI\Documents\Arduino\libraries\ArduinoWebsockets\src/tiny_websockets/message.hpp:3,
                 from c:\Users\DNUI\Documents\Arduino\libraries\ArduinoWebsockets\src/ArduinoWebsockets.h:4,
                 from C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino:3:
c:\Users\DNUI\Documents\Arduino\libraries\ArduinoWebsockets\src/tiny_websockets/network/esp32/esp32_tcp.hpp: In member function 'virtual websockets::network::TcpClient* websockets::network::Esp32TcpServer::accept()':
c:\Users\DNUI\Documents\Arduino\libraries\ArduinoWebsockets\src/tiny_websockets/network/esp32/esp32_tcp.hpp:50:39: warning: 'NetworkClient NetworkServer::available()' is deprecated: Renamed to accept(). [-Wdeprecated-declarations]
   50 |         auto client = server.available();
      |                       ~~~~~~~~~~~~~~~~^~
In file included from C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\hardware\esp32\3.3.10-cn\libraries\Network\src/Network.h:13,
                 from C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\hardware\esp32\3.3.10-cn\libraries\WiFi\src/WiFiGeneric.h:44,
                 from C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\hardware\esp32\3.3.10-cn\libraries\WiFi\src/WiFiSTA.h:30,
                 from C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\hardware\esp32\3.3.10-cn\libraries\WiFi\src/WiFi.h:34,
                 from c:\Users\DNUI\Documents\Arduino\libraries\ArduinoWebsockets\src/tiny_websockets/network/esp32/esp32_tcp.hpp:10:
C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\hardware\esp32\3.3.10-cn\libraries\Network\src/NetworkServer.h:50:17: note: declared here
   50 |   NetworkClient available() __attribute__((deprecated("Renamed to accept().")));
      |                 ^~~~~~~~~
C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino: In function 'bool initCamera()':
C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino:1932:10: warning: 'camera_config_t::<unnamed union>::pin_sscb_sda' is deprecated: please use pin_sccb_sda instead [-Wdeprecated-declarations]
 1932 |   config.pin_sscb_sda = CAM_PIN_SIOD;
      |          ^~~~~~~~~~~~
In file included from C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino:14:
C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\tools\esp32s3-libs\3.3.10-cn/include/espressif__esp32-camera/driver/include/esp_camera.h:123:13: note: declared here
  123 |         int pin_sscb_sda __attribute__((deprecated("please use pin_sccb_sda instead")));           /*!< GPIO pin for camera SDA line (legacy name) */
      |             ^~~~~~~~~~~~
C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino:1932:10: warning: 'camera_config_t::<unnamed union>::pin_sscb_sda' is deprecated: please use pin_sccb_sda instead [-Wdeprecated-declarations]
 1932 |   config.pin_sscb_sda = CAM_PIN_SIOD;
      |          ^~~~~~~~~~~~
C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\tools\esp32s3-libs\3.3.10-cn/include/espressif__esp32-camera/driver/include/esp_camera.h:123:13: note: declared here
  123 |         int pin_sscb_sda __attribute__((deprecated("please use pin_sccb_sda instead")));           /*!< GPIO pin for camera SDA line (legacy name) */
      |             ^~~~~~~~~~~~
C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino:1932:10: warning: 'camera_config_t::<unnamed union>::pin_sscb_sda' is deprecated: please use pin_sccb_sda instead [-Wdeprecated-declarations]
 1932 |   config.pin_sscb_sda = CAM_PIN_SIOD;
      |          ^~~~~~~~~~~~
C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\tools\esp32s3-libs\3.3.10-cn/include/espressif__esp32-camera/driver/include/esp_camera.h:123:13: note: declared here
  123 |         int pin_sscb_sda __attribute__((deprecated("please use pin_sccb_sda instead")));           /*!< GPIO pin for camera SDA line (legacy name) */
      |             ^~~~~~~~~~~~
C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino:1933:10: warning: 'camera_config_t::<unnamed union>::pin_sscb_scl' is deprecated: please use pin_sccb_scl instead [-Wdeprecated-declarations]
 1933 |   config.pin_sscb_scl = CAM_PIN_SIOC;
      |          ^~~~~~~~~~~~
C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\tools\esp32s3-libs\3.3.10-cn/include/espressif__esp32-camera/driver/include/esp_camera.h:127:13: note: declared here
  127 |         int pin_sscb_scl __attribute__((deprecated("please use pin_sccb_scl instead")));           /*!< GPIO pin for camera SCL line (legacy name) */
      |             ^~~~~~~~~~~~
C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino:1933:10: warning: 'camera_config_t::<unnamed union>::pin_sscb_scl' is deprecated: please use pin_sccb_scl instead [-Wdeprecated-declarations]
 1933 |   config.pin_sscb_scl = CAM_PIN_SIOC;
      |          ^~~~~~~~~~~~
C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\tools\esp32s3-libs\3.3.10-cn/include/espressif__esp32-camera/driver/include/esp_camera.h:127:13: note: declared here
  127 |         int pin_sscb_scl __attribute__((deprecated("please use pin_sccb_scl instead")));           /*!< GPIO pin for camera SCL line (legacy name) */
      |             ^~~~~~~~~~~~
C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino:1933:10: warning: 'camera_config_t::<unnamed union>::pin_sscb_scl' is deprecated: please use pin_sccb_scl instead [-Wdeprecated-declarations]
 1933 |   config.pin_sscb_scl = CAM_PIN_SIOC;
      |          ^~~~~~~~~~~~
C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\tools\esp32s3-libs\3.3.10-cn/include/espressif__esp32-camera/driver/include/esp_camera.h:127:13: note: declared here
  127 |         int pin_sscb_scl __attribute__((deprecated("please use pin_sccb_scl instead")));           /*!< GPIO pin for camera SCL line (legacy name) */
      |             ^~~~~~~~~~~~
C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino: In function 'void runConfigMode()':
C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino:3341:41: warning: 'NetworkClient NetworkServer::available()' is deprecated: Renamed to accept(). [-Wdeprecated-declarations]
 3341 |     WiFiClient client = server.available();
      |                         ~~~~~~~~~~~~~~~~^~
C:\Users\DNUI\AppData\Local\Arduino15\packages\esp32\hardware\esp32\3.3.10-cn\libraries\Network\src/NetworkServer.h:50:17: note: declared here
   50 |   NetworkClient available() __attribute__((deprecated("Renamed to accept().")));
      |                 ^~~~~~~~~
C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino:3387:9: error: 'isCaptivePortalProbe' was not declared in this scope
 3387 |     if (isCaptivePortalProbe(purePath)) {
      |         ^~~~~~~~~~~~~~~~~~~~
C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-mainone\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\esp32_servo_control\esp32_servo_control.ino:3388:7: error: 'sendRedirect' was not declared in this scope
 3388 |       sendRedirect(client, "http://" + ipToString(WiFi.softAPIP()) + "/");
      |       ^~~~~~~~~~~~
exit status 1

Compilation error: 'isCaptivePortalProbe' was not declared in this scope