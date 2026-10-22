Windows PowerShell
版权所有 (C) Microsoft Corporation。保留所有权利。

尝试新的跨平台 PowerShell https://aka.ms/pscore6

PS C:\Windows\system32> cd C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\server
PS C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\server> python app.py
C:\Users\DNUI\Desktop\AI_DesktopCat_Qwen3.5Omni-main\esp32_servo_control\server\app.py:11: DeprecationWarning: 'audioop' is deprecated and slated for removal in Python 3.13
  import audioop
[32mINFO[0m:     Started server process [[36m17308[0m]
[32mINFO[0m:     Waiting for application startup.
[32mINFO[0m:     Application startup complete.
[32mINFO[0m:     Uvicorn running on [1mhttp://0.0.0.0:8081[0m (Press CTRL+C to quit)
[32mINFO[0m:     10.221.119.237:65064 - "WebSocket /ws_audio" [accepted]
[32mINFO[0m:     connection open
[WS_AUDIO] session stopped
[WS_AUDIO] START command received
[REALTIME] connecting url=wss://dashscope.aliyuncs.com/api-ws/v1/realtime model=qwen3.5-omni-plus-realtime
[32mINFO[0m:     10.221.119.237:65065 - "WebSocket /ws/camera" [accepted]
[SCREEN] notify_screen_mode: cam_ws=set connected=True mode=0
[32mINFO[0m:     connection open
[HAND] tracking loop started
[HAND] disabled (need mediapipe + opencv + numpy)
[SCREEN] SCRMODE:0 sent to ESP32
[REALTIME] session.update turn_detection=server_vad threshold=0.5 silence_ms=900
[REALTIME] event session.created
[REALTIME] event session.updated
[REALTIME] session ready
[WS_AUDIO] realtime session connected
[WS_AUDIO] first audio chunk bytes=640
[WS_AUDIO] audio chunks=50 total_bytes=32000 elapsed_s=0.9
[WS_AUDIO] audio chunks=100 total_bytes=64000 elapsed_s=1.9
[REALTIME] event speech_started
[REALTIME] speech started
[REALTIME] image append sent after turn_audio_count=124 post_speech_audio_count=6
[WS_AUDIO] audio chunks=150 total_bytes=96000 elapsed_s=2.9
[REALTIME] event speech_stopped
[WS_AUDIO] audio chunks=200 total_bytes=128000 elapsed_s=3.9
[REALTIME] input transcript: 你好，小猫。
[REALTIME] final transcript=你好，小猫。
[REALTIME] event response.created
[REALTIME] response started
[AUDIO-PRIO] ON reason=realtime_response_started
[REALTIME] first text delta received
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=7680
[REALTIME] event response.done
[REALTIME] response done text=哎呀，你叫错啦，我是中山鹿！(歪头) 是不是把我当成哪家的小猫啦？
[AUDIO-PRIO] OFF reason=realtime_response_done
[WS_AUDIO] audio chunks=250 total_bytes=160000 elapsed_s=11.3
[WS_AUDIO] audio chunks=300 total_bytes=192000 elapsed_s=12.3
[WS_AUDIO] audio chunks=350 total_bytes=224000 elapsed_s=13.3
[REALTIME] event speech_started
[REALTIME] speech started
[REALTIME] image append sent after turn_audio_count=182 post_speech_audio_count=5
[WS_AUDIO] audio chunks=400 total_bytes=256000 elapsed_s=14.3
[WS_AUDIO] audio chunks=450 total_bytes=288000 elapsed_s=15.3
[WS_AUDIO] audio chunks=500 total_bytes=320000 elapsed_s=16.3
[WS_AUDIO] audio chunks=550 total_bytes=352000 elapsed_s=17.3
[REALTIME] event speech_stopped
[REALTIME] input transcript: 没有，没有，是我叫错了。我以后注意。
[REALTIME] final transcript=没有，没有，是我叫错了。我以后注意。
[REALTIME] event response.created
[REALTIME] response started
[AUDIO-PRIO] ON reason=realtime_response_started
[REALTIME] first text delta received
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=11520
[REALTIME] event response.done
[REALTIME] response done text=嘿嘿，没关系啦，你记住我就好！（开心地晃了晃脑袋）
[AUDIO-PRIO] OFF reason=realtime_response_done
[WS_AUDIO] audio chunks=600 total_bytes=384000 elapsed_s=22.7
[WS_AUDIO] audio chunks=650 total_bytes=416000 elapsed_s=23.7
[WS_AUDIO] audio chunks=700 total_bytes=448000 elapsed_s=24.7
[32mINFO[0m:     127.0.0.1:51134 - "WebSocket /ws/viewer" [accepted]
[32mINFO[0m:     connection open
[WS_AUDIO] audio chunks=750 total_bytes=480000 elapsed_s=25.7
[WS_AUDIO] audio chunks=800 total_bytes=512000 elapsed_s=26.7
[WS_AUDIO] audio chunks=850 total_bytes=544000 elapsed_s=27.7
[WS_AUDIO] audio chunks=900 total_bytes=576000 elapsed_s=28.7
[WS_AUDIO] audio chunks=950 total_bytes=608000 elapsed_s=29.7
[WS_AUDIO] audio chunks=1000 total_bytes=640000 elapsed_s=30.7
[WS_AUDIO] audio chunks=1050 total_bytes=672000 elapsed_s=31.8
[WS_AUDIO] audio chunks=1100 total_bytes=704000 elapsed_s=32.7
[WS_AUDIO] audio chunks=1150 total_bytes=736000 elapsed_s=33.8
[WS_AUDIO] audio chunks=1200 total_bytes=768000 elapsed_s=34.7
[WS_AUDIO] audio chunks=1250 total_bytes=800000 elapsed_s=35.8
[WS_AUDIO] audio chunks=1300 total_bytes=832000 elapsed_s=36.7
[WS_AUDIO] audio chunks=1350 total_bytes=864000 elapsed_s=37.8
[WS_AUDIO] audio chunks=1400 total_bytes=896000 elapsed_s=38.7
[WS_AUDIO] audio chunks=1450 total_bytes=928000 elapsed_s=39.7
[WS_AUDIO] audio chunks=1500 total_bytes=960000 elapsed_s=40.7
[WS_AUDIO] audio chunks=1550 total_bytes=992000 elapsed_s=41.7
[REALTIME] event speech_started
[REALTIME] speech started
[WS_AUDIO] audio chunks=1600 total_bytes=1024000 elapsed_s=42.7
[REALTIME] image append sent after turn_audio_count=1023 post_speech_audio_count=6
[WS_AUDIO] audio chunks=1650 total_bytes=1056000 elapsed_s=43.7
[REALTIME] event speech_stopped
[REALTIME] input transcript: 你看看。
[REALTIME] final transcript=你看看。
[VISION] activate keyword=你看看 text=你看看。
[REALTIME] session.update pushed (instructions refreshed)
[SCREEN] mode -> vision_dialog reason=vision:你看看
[SCREEN] notify_screen_mode: cam_ws=set connected=True mode=4
[SCREEN] SCRMODE:4 sent to ESP32
[REALTIME] event response.created
[REALTIME] response started
[AUDIO-PRIO] ON reason=realtime_response_started
[REALTIME] first text delta received
[REALTIME] event session.updated
[WS_AUDIO] audio chunks=1700 total_bytes=1088000 elapsed_s=45.0
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=15360
[REALTIME] audio delta bytes=7680
[REALTIME] event response.done
[REALTIME] response done text=我凑近看看哦……哇，是只小猫呀，好可爱！
[AUDIO-PRIO] OFF reason=realtime_response_done
[WS_AUDIO] audio chunks=1750 total_bytes=1120000 elapsed_s=47.5
[WS_AUDIO] audio chunks=1800 total_bytes=1152000 elapsed_s=49.8
[32mINFO[0m:     connection closed
[32mINFO[0m:     127.0.0.1:51178 - "WebSocket /ws_ui" [accepted]
[32mINFO[0m:     connection open
[VISION] pinned -> ON reason=ui
[REALTIME] session.update pushed (instructions refreshed)
[REALTIME] event session.updated
[VISION] pinned -> OFF reason=ui
[VISION] deactivate reason=ui
[REALTIME] session.update pushed (instructions refreshed)
[SCREEN] mode -> expression reason=ui
[SCREEN] notify_screen_mode: cam_ws=set connected=True mode=0
[SCREEN] SCRMODE:0 sent to ESP32
[REALTIME] event session.updated
[SCREEN] mode -> expression reason=ui
[SCREEN] notify_screen_mode: cam_ws=set connected=True mode=0
[SCREEN] SCRMODE:0 sent to ESP32
