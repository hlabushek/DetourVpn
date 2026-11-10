// Test double for detour-engine.exe: reads stdin to EOF, prints PEER_STATS lines until Ctrl-C.
// Environment: FAKE_ENGINE_MARKER = file written on graceful exit, FAKE_ENGINE_IGNORE_CTRLC=1 =
// keep running after Ctrl-C (exercises the forced-termination path), FAKE_ENGINE_NO_HANDSHAKE=1.
// Not shipped. It is NOT a tunnel and proves nothing about handshake or egress.
#include <windows.h>

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <string>

namespace {
std::atomic<bool> g_stop{false};
bool g_ignore = false;
BOOL WINAPI OnCtrl(DWORD type) {
  if (type == CTRL_C_EVENT || type == CTRL_BREAK_EVENT) {
    if (!g_ignore) g_stop = true;
    return TRUE;
  }
  return FALSE;
}
}  // namespace

int main() {
  char* ignore = nullptr;
  size_t n = 0;
  if (_dupenv_s(&ignore, &n, "FAKE_ENGINE_IGNORE_CTRLC") == 0 && ignore) {
    g_ignore = std::string(ignore) == "1";
    free(ignore);
  }
  SetConsoleCtrlHandler(OnCtrl, TRUE);
  char buffer[4096];
  while (fread(buffer, 1, sizeof(buffer), stdin) > 0) {
  }
  char* no_handshake = nullptr;
  bool silent_handshake = _dupenv_s(&no_handshake, &n, "FAKE_ENGINE_NO_HANDSHAKE") == 0 && no_handshake &&
                          std::string(no_handshake) == "1";
  free(no_handshake);
  long exit_after_ms = 0;  // FAKE_ENGINE_EXIT_MS: die by itself (simulates an engine crash)
  char* exit_text = nullptr;
  if (_dupenv_s(&exit_text, &n, "FAKE_ENGINE_EXIT_MS") == 0 && exit_text) exit_after_ms = std::atol(exit_text);
  free(exit_text);
  const ULONGLONG started = GetTickCount64();
  while (!g_stop) {
    if (exit_after_ms > 0 && GetTickCount64() - started >= static_cast<ULONGLONG>(exit_after_ms)) return 3;
    long long handshake = silent_handshake ? 0 : static_cast<long long>(std::time(nullptr)) - 3;
    std::printf("PEER_STATS {\"peer_count\":1,\"last_handshake_time_sec\":%lld,\"rx_bytes\":1000,\"tx_bytes\":500}\n",
                handshake);
    std::fflush(stdout);
    Sleep(200);
  }
  char* marker = nullptr;
  if (_dupenv_s(&marker, &n, "FAKE_ENGINE_MARKER") == 0 && marker) {
    FILE* file = nullptr;
    if (fopen_s(&file, marker, "wb") == 0 && file) {
      std::fputs("graceful", file);
      std::fclose(file);
    }
    free(marker);
  }
  return 0;
}
