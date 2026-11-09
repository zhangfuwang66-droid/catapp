#pragma once

#include <atomic>
#include <cstdint>

enum class SocketServicePhase { Waiting, Gate, CameraConnect, AudioConnect, CameraPoll, AudioPoll, Sleeping };

// 业务任务只更新计数，USB 按需读取；不在音视频任务中打印或分配诊断 JSON。
struct DeviceRuntimeDiagnostics {
  std::atomic<int32_t> cameraInitError{-1};
  std::atomic<bool> microphoneReady{false}, speakerReady{false};
  std::atomic<uint32_t> capturedFrames{0}, captureFailures{0}, sentFrames{0}, cameraSendFailures{0};
  std::atomic<uint32_t> capturedAudioChunks{0}, sentAudioChunks{0}, audioSendFailures{0};
  std::atomic<uint32_t> taskStartFailures{0}, socketHeartbeatMs{0};
  std::atomic<SocketServicePhase> socketPhase{SocketServicePhase::Waiting};

  static const char* phaseName(SocketServicePhase phase) {
    switch (phase) {
      case SocketServicePhase::Waiting: return "waiting";
      case SocketServicePhase::Gate: return "gate";
      case SocketServicePhase::CameraConnect: return "camera_connect";
      case SocketServicePhase::AudioConnect: return "audio_connect";
      case SocketServicePhase::CameraPoll: return "camera_poll";
      case SocketServicePhase::AudioPoll: return "audio_poll";
      case SocketServicePhase::Sleeping: return "sleeping";
    }
    return "unknown";
  }
};
