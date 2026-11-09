// 真实 HTTP 解析器 + 真实 PCM 缓冲，模拟时钟、网络到达和每 20ms 的扬声器消费。
#include "../esp32_servo_control/HttpAudioBody.h"
#include "../esp32_servo_control/PcmPlaybackBuffer.h"
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

#define CHECK(value) do { if (!(value)) { std::cerr << __func__ << ":" << __LINE__ << " " #value "\n"; std::exit(1); } } while (false)

uint32_t nowMs = 0;
uint32_t clockMs() { return nowMs; }
void idle() { ++nowMs; }
struct Packet { uint32_t at; std::string bytes; };
struct Client {
  std::vector<Packet> packets;
  size_t packet = 0, offset = 0;
  int available() {
    while (packet < packets.size() && offset == packets[packet].bytes.size()) { ++packet; offset = 0; }
    return packet == packets.size() || packets[packet].at > nowMs ? 0 : static_cast<int>(packets[packet].bytes.size() - offset);
  }
  bool connected() { return available() > 0 || packet < packets.size(); }
  int read(uint8_t* out, size_t wanted) {
    const auto count = std::min(wanted, static_cast<size_t>(available()));
    if (!count) return 0;
    std::copy_n(packets[packet].bytes.data() + offset, count, out);
    offset += count;
    return static_cast<int>(count);
  }
  int read() { uint8_t byte; return read(&byte, 1) == 1 ? byte : -1; }
};

std::string payload(size_t count) {
  std::string result(count, '\0');
  for (size_t i = 0; i < count; ++i) result[i] = static_cast<char>((i * 37 + 13) % 256);
  return result;
}
std::string chunk(const std::string& pcm) {
  char length[24];
  std::snprintf(length, sizeof(length), "%zx", pcm.size());
  return std::string(length) + "\r\n" + pcm + "\r\n";
}
struct Result { std::string pcm; uint32_t first = 0, maxGap = 0, rebuffers = 0; bool failed = false; };
Result play(Client& client, bool chunked, uint32_t sampleRate = 24000, size_t capacity = 65536) {
  nowMs = 0;
  HttpAudioBody<Client> body(client, chunked, clockMs, idle);
  std::vector<uint8_t> storage(capacity);
  PcmPlaybackBuffer pcm(storage.data(), storage.size(), sampleRate);
  const size_t bytes20 = sampleRate * 2 * 20 / 1000;
  uint8_t block[960];
  Result result;
  uint32_t previous = 0;
  bool finished = false;
  for (int attempt = 0; attempt < 20000; ++attempt) {
    const auto started = nowMs;
    pcm.receive(body);
    CHECK(nowMs == started); // 预取本身不能为了凑满一块而等待网络。
    finished = body.done();
    const size_t count = pcm.take(block, bytes20, finished);
    CHECK(count % 2 == 0);
    CHECK(pcm.size() <= pcm.capacity());
    if (count) {
      if (result.pcm.empty()) result.first = nowMs;
      else result.maxGap = std::max(result.maxGap, nowMs - previous);
      previous = nowMs;
      result.pcm.append(reinterpret_cast<char*>(block), count);
      nowMs += 20; // I2S DMA 的稳定供数节奏。
    } else if (finished) {
      result.rebuffers = pcm.rebuffers();
      result.failed = body.failed();
      return result;
    } else {
      ++nowMs;
    }
  }
  CHECK(false);
  return result;
}

void absorbs_short_network_stalls_without_changing_pcm() {
  const auto pcm = payload(48000 * 4 + 138);
  Client client;
  // 每 200ms 的音频在网络上以不均匀间隔到达，最长间隔 260ms。
  const uint32_t arrivals[] = {0, 240, 430, 690, 870, 1000, 1250, 1410, 1670, 1830,
                              2080, 2240, 2410, 2670, 2810, 3060, 3240, 3480, 3620, 3860, 4000};
  size_t index = 0;
  for (size_t offset = 0; offset < pcm.size(); offset += 9600)
    client.packets.push_back({arrivals[index++], chunk(pcm.substr(offset, 9600))});
  client.packets.back().bytes += "0\r\n\r\n";
  const auto result = play(client, true);
  CHECK(result.pcm == pcm);
  CHECK(result.first >= 240 && result.first <= 245);
  CHECK(result.maxGap == 20);
  CHECK(result.rebuffers == 0);
  CHECK(!result.failed);
}

void longer_outage_rebuffers_once_instead_of_dripping_short_blocks() {
  const auto pcm = payload(48000);
  Client client{{{0, chunk(pcm.substr(0, 19200))}, {1100, chunk(pcm.substr(19200)) + "0\r\n\r\n"}}};
  const auto result = play(client, true);
  CHECK(result.pcm == pcm);
  CHECK(result.maxGap > 600);
  CHECK(result.rebuffers == 1);
  CHECK(!result.failed);
}

void full_buffer_backpressure_and_closed_source_drain_the_entire_tail() {
  const auto pcm = payload(48000 * 3 + 126);
  Client client{{{0, pcm}}};
  const auto result = play(client, false);
  CHECK(result.pcm == pcm);
  CHECK(result.maxGap == 20);
  CHECK(result.rebuffers == 0);
  CHECK(!result.failed);
}

void odd_http_chunks_and_fragmented_length_lines_preserve_samples() {
  const auto pcm = payload(18002);
  std::string wire;
  for (size_t offset = 0; offset < pcm.size(); offset += 257) wire += chunk(pcm.substr(offset, 257));
  wire += "0\r\n\r\n";
  Client client;
  for (size_t offset = 0; offset < wire.size(); offset += 43)
    client.packets.push_back({static_cast<uint32_t>(offset / 43), wire.substr(offset, 43)});
  const auto result = play(client, true);
  CHECK(result.pcm == pcm);
  CHECK(!result.failed);
}

void short_and_empty_responses_do_not_wait_for_a_full_prebuffer() {
  for (const auto& pcm : {std::string(), payload(138), payload(4798)}) {
    Client client{{{0, pcm.empty() ? "0\r\n\r\n" : chunk(pcm) + "0\r\n\r\n"}}};
    const auto result = play(client, true);
    CHECK(result.pcm == pcm);
    CHECK(result.first <= 2); // 每次预取有预算限制，不必等待 320ms 水位。
    CHECK(result.rebuffers == 0);
    CHECK(!result.failed);
  }
}

void fallback_capacity_and_all_supported_rates_remain_bounded() {
  for (const uint32_t rate : {8000, 12000, 16000, 24000}) {
    const auto pcm = payload(rate * 2 * 2 + 38);
    Client client{{{0, chunk(pcm) + "0\r\n\r\n"}}};
    const auto result = play(client, true, rate, 8192);
    CHECK(result.pcm == pcm);
    CHECK(result.maxGap == 20);
    CHECK(result.rebuffers == 0);
  }
}

void truncated_stream_drains_received_samples_but_reports_framing_error() {
  const auto pcm = payload(9001);
  Client client{{{0, "4000\r\n" + pcm}}};
  const auto result = play(client, true);
  CHECK(result.pcm == pcm.substr(0, 9000)); // 半个 int16 不得伪造为一个新采样。
  CHECK(result.failed);
}

void tail_wait_is_not_counted_as_mid_response_rebuffering() {
  const auto pcm = payload(19200);
  Client client{{{0, chunk(pcm)}, {900, "0\r\n\r\n"}}};
  const auto result = play(client, true);
  CHECK(result.pcm == pcm);
  CHECK(result.rebuffers == 0);
  CHECK(!result.failed);
}

int main() {
  absorbs_short_network_stalls_without_changing_pcm();
  longer_outage_rebuffers_once_instead_of_dripping_short_blocks();
  full_buffer_backpressure_and_closed_source_drain_the_entire_tail();
  odd_http_chunks_and_fragmented_length_lines_preserve_samples();
  short_and_empty_responses_do_not_wait_for_a_full_prebuffer();
  fallback_capacity_and_all_supported_rates_remain_bounded();
  truncated_stream_drains_received_samples_but_reports_framing_error();
  tail_wait_is_not_counted_as_mid_response_rebuffering();
  std::cout << "8 PCM playback buffer tests passed\n";
}
