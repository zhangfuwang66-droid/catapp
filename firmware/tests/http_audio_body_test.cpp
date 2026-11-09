// 原生 C++ 测试：使用真实固件解析器，模拟 TCP/TLS 分片与网络停顿。
#include "../esp32_servo_control/HttpAudioBody.h"
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
  bool keepAlive = false;

  int available() {
    while (packet < packets.size() && offset == packets[packet].bytes.size()) { ++packet; offset = 0; }
    if (packet == packets.size() || packets[packet].at > nowMs) return 0;
    return static_cast<int>(packets[packet].bytes.size() - offset);
  }
  bool connected() { return available() > 0 || packet < packets.size() || keepAlive; }
  int read(uint8_t* out, size_t wanted) {
    const size_t count = std::min(wanted, static_cast<size_t>(available()));
    if (!count) return 0;
    std::copy_n(packets[packet].bytes.data() + offset, count, out);
    offset += count;
    return static_cast<int>(count);
  }
  int read() { uint8_t byte; return read(&byte, 1) == 1 ? byte : -1; }
};

std::string decode(Client& client, bool chunked, bool& failed, uint32_t timeout = 4) {
  HttpAudioBody<Client> body(client, chunked, clockMs, idle);
  std::string result;
  uint8_t block[960];
  for (int attempts = 0; !body.done() && attempts < 10000; ++attempts) {
    const size_t count = body.read(block, sizeof(block), timeout);
    result.append(reinterpret_cast<char*>(block), count);
  }
  CHECK(body.done());
  failed = body.failed();
  return result;
}

void fragmented_chunk_headers_and_pcm() {
  nowMs = 0;
  std::string pcm;
  for (int i = 0; i < 48000; ++i) pcm.push_back(static_cast<char>((i * 37 + 13) % 256));
  std::string encoded;
  for (size_t offset = 0; offset < pcm.size(); offset += 257) {
    const auto piece = pcm.substr(offset, 257); // 故意让 HTTP 分块切在 16 位采样中间。
    char hex[20];
    std::snprintf(hex, sizeof(hex), "%zx", piece.size());
    encoded += std::string(hex) + ";test=yes\r\n" + piece + "\r\n";
  }
  encoded += "0\r\n\r\n";
  Client client;
  for (size_t offset = 0; offset < encoded.size(); offset += 43)
    client.packets.push_back({static_cast<uint32_t>(offset / 43 * 9), encoded.substr(offset, 43)});
  bool failed;
  CHECK(decode(client, true, failed) == pcm);
  CHECK(!failed);
}

void timeout_preserves_chunk_length_line_and_payload() {
  nowMs = 0;
  Client client{{{0, "a"}, {15, "\r"}, {30, "\n123"}, {45, "4567890\r"}, {60, "\n0\r\n\r\n"}}};
  bool failed;
  CHECK(decode(client, true, failed) == "1234567890");
  CHECK(!failed);
}

void final_partial_pcm_is_delivered() {
  nowMs = 0;
  Client client{{{0, "4\r\nABCD\r\n2\r\nEF\r\n0\r\n\r\n"}}};
  bool failed;
  CHECK(decode(client, true, failed) == "ABCDEF");
  CHECK(!failed);
}

void non_chunked_eof_preserves_partial_read() {
  nowMs = 0;
  Client client{{{0, "ABC"}, {12, "DEFG"}}};
  bool failed;
  CHECK(decode(client, false, failed) == "ABCDEFG");
  CHECK(!failed);
}

void invalid_framing_never_becomes_audio() {
  for (const auto& encoded : {"g\r\nsecret", "1\nsecret", "100000000\r\nsecret", "1\r\nA!\nsecret",
                             "\r\nsecret", "1\r\nA\r!secret", "1\r!secret"}) {
    nowMs = 0;
    Client client{{{0, encoded}}};
    bool failed;
    const auto pcm = decode(client, true, failed);
    CHECK(failed);
    CHECK(pcm.empty() || pcm == "A");
  }
}

void truncated_response_retains_received_pcm_and_reports_error() {
  nowMs = 0;
  Client client{{{0, "8\r\nABCD"}}};
  bool failed;
  CHECK(decode(client, true, failed) == "ABCD");
  CHECK(failed);
}

void nonblocking_reads_and_clock_wrap_remain_bounded() {
  nowMs = UINT32_MAX - 2;
  Client client;
  client.keepAlive = true;
  HttpAudioBody<Client> body(client, true, clockMs, idle);
  uint8_t output[2];
  CHECK(body.read(output, 2, 0) == 0);
  CHECK(nowMs == UINT32_MAX - 2);
  CHECK(body.read(output, 2, 7) == 0);
  CHECK(nowMs == 4);
  CHECK(!body.done());
}

int main() {
  fragmented_chunk_headers_and_pcm();
  timeout_preserves_chunk_length_line_and_payload();
  final_partial_pcm_is_delivered();
  non_chunked_eof_preserves_partial_read();
  invalid_framing_never_becomes_audio();
  truncated_response_retains_received_pcm_and_reports_error();
  nonblocking_reads_and_clock_wrap_remain_bounded();
  std::cout << "7 HTTP audio body tests passed\n";
}
