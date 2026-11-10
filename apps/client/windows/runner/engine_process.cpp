#include "engine_process.h"

#include <cstdlib>
#include <cstring>

namespace detour {
namespace {

// Console attach / Ctrl-C is process-global; serialise it.
std::mutex g_console_mutex;

bool ReadInt(const std::string& line, const char* key, int64_t* out) {
  std::string needle = std::string("\"") + key + "\":";
  size_t at = line.find(needle);
  if (at == std::string::npos) return false;
  at += needle.size();
  size_t end = at;
  while (end < line.size() && line[end] >= '0' && line[end] <= '9') ++end;
  if (end == at || end - at > 18) return false;
  *out = std::strtoll(line.c_str() + at, nullptr, 10);
  return true;
}

std::vector<wchar_t> EnvironmentBlock(bool peer_stats) {
  std::vector<wchar_t> block;
  LPWCH env = GetEnvironmentStringsW();
  if (env) {
    const wchar_t* p = env;
    while (*p) {
      size_t len = wcslen(p);
      // Never inherit a stats switch from the parent environment.
      if (_wcsnicmp(p, L"DETOUR_PEER_STATS=", 18) != 0) block.insert(block.end(), p, p + len + 1);
      p += len + 1;
    }
    FreeEnvironmentStringsW(env);
  }
  if (peer_stats) {
    const wchar_t extra[] = L"DETOUR_PEER_STATS=1";
    block.insert(block.end(), extra, extra + wcslen(extra) + 1);
  }
  block.push_back(L'\0');
  return block;
}

}  // namespace

std::optional<PeerStats> ParsePeerStatsLine(const std::string& line) {
  static const std::string kPrefix = "PEER_STATS ";
  if (line.compare(0, kPrefix.size(), kPrefix) != 0) return std::nullopt;
  int64_t peers = 0, handshake = 0, rx = 0, tx = 0;
  if (!ReadInt(line, "peer_count", &peers) || peers != 1 ||
      !ReadInt(line, "last_handshake_time_sec", &handshake) || !ReadInt(line, "rx_bytes", &rx) ||
      !ReadInt(line, "tx_bytes", &tx))
    return std::nullopt;
  PeerStats stats;
  stats.last_handshake_unix = handshake;
  stats.rx_bytes = rx;
  stats.tx_bytes = tx;
  stats.received_at = std::chrono::steady_clock::now();
  return stats;
}

EngineProcess::~EngineProcess() {
  Stop(std::chrono::seconds(3));
  CloseHandles();
}

void EngineProcess::CloseHandles() {
  if (reader_.joinable()) reader_.join();
  if (process_) CloseHandle(process_);
  if (job_) CloseHandle(job_);  // KILL_ON_JOB_CLOSE ends anything still running
  process_ = nullptr;
  job_ = nullptr;
  pid_ = 0;
}

void EngineProcess::Start(const std::wstring& exe, const std::wstring& args, const std::string& stdin_data,
                          bool peer_stats) {
  if (process_) throw std::runtime_error("engine_start_failed");
  SECURITY_ATTRIBUTES inherit{sizeof(inherit), nullptr, TRUE};
  HANDLE stdin_read = nullptr, stdin_write = nullptr, stdout_read = nullptr, stdout_write = nullptr;
  HANDLE null_handle = INVALID_HANDLE_VALUE;
  auto fail = [&]() {
    for (HANDLE h : {stdin_read, stdin_write, stdout_read, stdout_write})
      if (h) CloseHandle(h);
    if (null_handle != INVALID_HANDLE_VALUE) CloseHandle(null_handle);
    if (job_) {
      CloseHandle(job_);
      job_ = nullptr;
    }
    throw std::runtime_error("engine_start_failed");
  };

  if (!CreatePipe(&stdin_read, &stdin_write, &inherit, 0) ||
      !CreatePipe(&stdout_read, &stdout_write, &inherit, 0))
    fail();
  SetHandleInformation(stdin_write, HANDLE_FLAG_INHERIT, 0);
  SetHandleInformation(stdout_read, HANDLE_FLAG_INHERIT, 0);
  null_handle = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &inherit,
                            OPEN_EXISTING, 0, nullptr);
  if (null_handle == INVALID_HANDLE_VALUE) fail();

  job_ = CreateJobObjectW(nullptr, nullptr);
  if (!job_) fail();
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
  limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
  if (!SetInformationJobObject(job_, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) fail();

  // Inherit exactly the three handles we hand over.
  HANDLE handles[3] = {stdin_read, stdout_write, null_handle};
  SIZE_T attr_size = 0;
  InitializeProcThreadAttributeList(nullptr, 1, 0, &attr_size);
  std::vector<char> attr_storage(attr_size);
  auto* attrs = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attr_storage.data());
  if (!InitializeProcThreadAttributeList(attrs, 1, 0, &attr_size)) fail();
  if (!UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handles, sizeof(handles),
                                 nullptr, nullptr)) {
    DeleteProcThreadAttributeList(attrs);
    fail();
  }

  STARTUPINFOEXW startup{};
  startup.StartupInfo.cb = sizeof(startup);
  startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
  startup.StartupInfo.hStdInput = stdin_read;
  startup.StartupInfo.hStdOutput = stdout_write;
  startup.StartupInfo.hStdError = null_handle;
  startup.lpAttributeList = attrs;

  std::wstring command = L"\"" + exe + L"\" " + args;
  std::vector<wchar_t> env = EnvironmentBlock(peer_stats);
  PROCESS_INFORMATION info{};
  BOOL created = CreateProcessW(exe.c_str(), command.data(), nullptr, nullptr, TRUE,
                                EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW | CREATE_SUSPENDED |
                                    CREATE_UNICODE_ENVIRONMENT,
                                env.data(), nullptr, &startup.StartupInfo, &info);
  DeleteProcThreadAttributeList(attrs);
  CloseHandle(stdin_read);
  stdin_read = nullptr;
  CloseHandle(stdout_write);
  stdout_write = nullptr;
  CloseHandle(null_handle);
  null_handle = INVALID_HANDLE_VALUE;
  if (!created) fail();
  if (!AssignProcessToJobObject(job_, info.hProcess)) {
    TerminateProcess(info.hProcess, 1);
    CloseHandle(info.hProcess);
    CloseHandle(info.hThread);
    fail();
  }
  ResumeThread(info.hThread);
  CloseHandle(info.hThread);
  process_ = info.hProcess;
  pid_ = info.dwProcessId;

  // Deliver the config and close stdin so the engine sees EOF.
  DWORD done = 0;
  size_t offset = 0;
  bool delivered = true;
  while (offset < stdin_data.size()) {
    if (!WriteFile(stdin_write, stdin_data.data() + offset, static_cast<DWORD>(stdin_data.size() - offset),
                   &done, nullptr) ||
        done == 0) {
      delivered = false;
      break;
    }
    offset += done;
  }
  CloseHandle(stdin_write);
  stdin_write = nullptr;
  if (!delivered) {
    TerminateProcess(process_, 1);
    WaitForSingleObject(process_, 5000);
    if (stdout_read) CloseHandle(stdout_read);
    CloseHandle(process_);
    CloseHandle(job_);
    process_ = nullptr;
    job_ = nullptr;
    throw std::runtime_error("engine_start_failed");
  }
  reader_ = std::thread([this, stdout_read]() { ReaderLoop(stdout_read); });
}

void EngineProcess::ReaderLoop(HANDLE pipe) {
  std::string pending;
  char buffer[1024];
  DWORD got = 0;
  while (ReadFile(pipe, buffer, sizeof(buffer), &got, nullptr) && got > 0) {
    pending.append(buffer, got);
    if (pending.size() > 65536) pending.clear();  // bounded; the engine only prints short lines
    size_t eol;
    while ((eol = pending.find('\n')) != std::string::npos) {
      std::string line = pending.substr(0, eol);
      pending.erase(0, eol + 1);
      if (!line.empty() && line.back() == '\r') line.pop_back();
      if (auto stats = ParsePeerStatsLine(line)) {
        std::lock_guard<std::mutex> lock(mutex_);
        stats_ = stats;
      }
    }
  }
  CloseHandle(pipe);
}

bool EngineProcess::Running() {
  if (!process_) return false;
  return WaitForSingleObject(process_, 0) == WAIT_TIMEOUT;
}

std::optional<DWORD> EngineProcess::ExitCode() {
  if (!process_ || Running()) return std::nullopt;
  DWORD code = 0;
  if (!GetExitCodeProcess(process_, &code)) return std::nullopt;
  return code;
}

std::optional<PeerStats> EngineProcess::Stats() {
  std::lock_guard<std::mutex> lock(mutex_);
  return stats_;
}

bool EngineProcess::SendCtrlC() {
  std::lock_guard<std::mutex> lock(g_console_mutex);
  FreeConsole();
  bool sent = false;
  if (AttachConsole(pid_)) {
    SetConsoleCtrlHandler(nullptr, TRUE);  // this process ignores the event it is about to raise
    sent = GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0) != 0;
    Sleep(100);
    FreeConsole();
    SetConsoleCtrlHandler(nullptr, FALSE);
  }
  return sent;
}

void EngineProcess::Stop(std::chrono::milliseconds grace) {
  if (!process_) return;
  if (Running()) {
    SendCtrlC();
    if (WaitForSingleObject(process_, static_cast<DWORD>(grace.count())) == WAIT_TIMEOUT) {
      TerminateProcess(process_, 1);
      WaitForSingleObject(process_, 5000);
    }
  }
  CloseHandles();
  std::lock_guard<std::mutex> lock(mutex_);
  stats_.reset();
}

}  // namespace detour
