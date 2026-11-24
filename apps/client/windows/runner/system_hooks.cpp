// Winsock first: winsock2.h must precede windows.h or the legacy winsock.h conflicts.
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <iphlpapi.h>

#include "system_hooks.h"

#include <commdlg.h>
#include <netlistmgr.h>
#include <shellapi.h>
#include <shlobj.h>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <map>

#include "config_factory.h"
#include "kill_switch.h"

namespace detour {
namespace {

bool ProcessIsElevated() {
  HANDLE token = nullptr;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return false;
  TOKEN_ELEVATION elevation{};
  DWORD size = 0;
  bool elevated = GetTokenInformation(token, TokenElevation, &elevation, sizeof(elevation), &size) &&
                  elevation.TokenIsElevated != 0;
  CloseHandle(token);
  return elevated;
}

bool InternetReachable() {
  INetworkListManager* manager = nullptr;
  if (FAILED(CoCreateInstance(CLSID_NetworkListManager, nullptr, CLSCTX_ALL, IID_INetworkListManager,
                              reinterpret_cast<void**>(&manager))) ||
      !manager)
    return false;
  NLM_CONNECTIVITY connectivity = NLM_CONNECTIVITY_DISCONNECTED;
  bool ok = SUCCEEDED(manager->GetConnectivity(&connectivity)) &&
            (connectivity & NLM_CONNECTIVITY_IPV4_INTERNET) != 0;
  manager->Release();
  return ok;
}

std::vector<std::string> ResolveIpv4(const std::string& host) {
  WSADATA data;
  if (WSAStartup(MAKEWORD(2, 2), &data) != 0) return {};
  std::vector<std::string> out;
  addrinfo hints{};
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_STREAM;
  addrinfo* result = nullptr;
  if (getaddrinfo(host.c_str(), nullptr, &hints, &result) == 0) {
    for (addrinfo* p = result; p; p = p->ai_next) {
      char text[INET_ADDRSTRLEN] = {};
      auto* addr = reinterpret_cast<sockaddr_in*>(p->ai_addr);
      if (InetNtopA(AF_INET, &addr->sin_addr, text, sizeof(text))) {
        std::string ip = text;
        if (std::find(out.begin(), out.end(), ip) == out.end()) out.push_back(ip);
      }
    }
    freeaddrinfo(result);
  }
  WSACleanup();
  return out;
}

// ADR-194: identity of the usable physical IPv4 network: every adapter that is up, is not loopback,
// is not a virtual tunnel (the engine's Wintun adapter included) and has an IPv4 default gateway,
// as "interface index + gateway" pairs. "" = no usable network. The value is compared in memory by
// the watchdog and never logged, shown or sent.
std::string PhysicalNetworkId() {
  ULONG size = 16 * 1024;
  std::vector<unsigned char> buffer;
  ULONG result = ERROR_BUFFER_OVERFLOW;
  for (int attempt = 0; attempt < 3 && result == ERROR_BUFFER_OVERFLOW; ++attempt) {
    buffer.resize(size);
    result = GetAdaptersAddresses(AF_UNSPEC,
                                  GAA_FLAG_INCLUDE_GATEWAYS | GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST |
                                      GAA_FLAG_SKIP_DNS_SERVER | GAA_FLAG_SKIP_FRIENDLY_NAME,
                                  nullptr, reinterpret_cast<IP_ADAPTER_ADDRESSES*>(buffer.data()), &size);
  }
  if (result != NO_ERROR) return {};
  std::vector<std::string> entries;
  for (auto* a = reinterpret_cast<IP_ADAPTER_ADDRESSES*>(buffer.data()); a; a = a->Next) {
    if (a->OperStatus != IfOperStatusUp || a->IfType == IF_TYPE_SOFTWARE_LOOPBACK ||
        a->IfType == IF_TYPE_PROP_VIRTUAL || a->IfType == IF_TYPE_TUNNEL)
      continue;
    if (a->Description && std::wstring(a->Description).find(L"Wintun") != std::wstring::npos) continue;
    for (auto* g = a->FirstGatewayAddress; g; g = g->Next) {
      if (!g->Address.lpSockaddr || g->Address.lpSockaddr->sa_family != AF_INET) continue;
      const auto* in = reinterpret_cast<const sockaddr_in*>(g->Address.lpSockaddr);
      entries.push_back(std::to_string(a->IfIndex) + "/" + std::to_string(in->sin_addr.S_un.S_addr));
    }
  }
  std::sort(entries.begin(), entries.end());
  std::string id;
  for (const auto& e : entries) id += e + ";";
  return id;
}

// ADR-196: LUID of the adapter that carries the TUN's fixed IPv6 ULA (config_factory.h), i.e. the
// engine's tunnel adapter. Nothing when no such adapter exists. Never logged.
std::optional<uint64_t> TunnelLuid() {
  in6_addr wanted{};
  if (InetPtonA(AF_INET6, kTunIpv6Ula, &wanted) != 1) return std::nullopt;
  ULONG size = 16 * 1024;
  std::vector<unsigned char> buffer;
  ULONG result = ERROR_BUFFER_OVERFLOW;
  for (int attempt = 0; attempt < 3 && result == ERROR_BUFFER_OVERFLOW; ++attempt) {
    buffer.resize(size);
    result = GetAdaptersAddresses(AF_INET6,
                                  GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER |
                                      GAA_FLAG_SKIP_FRIENDLY_NAME,
                                  nullptr, reinterpret_cast<IP_ADAPTER_ADDRESSES*>(buffer.data()), &size);
  }
  if (result != NO_ERROR) return std::nullopt;
  for (auto* a = reinterpret_cast<IP_ADAPTER_ADDRESSES*>(buffer.data()); a; a = a->Next) {
    for (auto* u = a->FirstUnicastAddress; u; u = u->Next) {
      if (!u->Address.lpSockaddr || u->Address.lpSockaddr->sa_family != AF_INET6) continue;
      const auto* in = reinterpret_cast<const sockaddr_in6*>(u->Address.lpSockaddr);
      if (std::memcmp(&in->sin6_addr, &wanted, sizeof(wanted)) == 0) return a->Luid.Value;
    }
  }
  return std::nullopt;
}

std::filesystem::path OwnExe() {
  wchar_t buffer[MAX_PATH * 2];
  DWORD n = GetModuleFileNameW(nullptr, buffer, static_cast<DWORD>(std::size(buffer)));
  return std::filesystem::path(std::wstring(buffer, n));
}

void ScanShortcuts(const std::filesystem::path& root, std::map<std::string, AppEntry>* found,
                   const std::string& own_lower) {
  std::error_code ec;
  if (!std::filesystem::exists(root, ec)) return;
  IShellLinkW* link = nullptr;
  if (FAILED(CoCreateInstance(CLSID_ShellLink, nullptr, CLSCTX_INPROC_SERVER, IID_IShellLinkW,
                              reinterpret_cast<void**>(&link))))
    return;
  IPersistFile* file = nullptr;
  if (FAILED(link->QueryInterface(IID_IPersistFile, reinterpret_cast<void**>(&file)))) {
    link->Release();
    return;
  }
  for (std::filesystem::recursive_directory_iterator it(root, std::filesystem::directory_options::skip_permission_denied, ec), end;
       it != end && found->size() < 300; it.increment(ec)) {
    if (ec) break;
    if (!it->is_regular_file(ec) || it->path().extension() != L".lnk") continue;
    if (FAILED(file->Load(it->path().c_str(), STGM_READ))) continue;
    wchar_t target[MAX_PATH * 2] = {};
    if (FAILED(link->GetPath(target, static_cast<int>(std::size(target)), nullptr, SLGP_RAWPATH))) continue;
    std::string path = WideToUtf8(target);
    std::string lower = path;
    for (char& c : lower) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    if (!IsValidAppPath(path) || lower == own_lower || found->count(lower)) continue;
    found->emplace(lower, AppEntry{path, WideToUtf8(it->path().stem().wstring())});
  }
  file->Release();
  link->Release();
}

}  // namespace

std::string WideToUtf8(const std::wstring& wide) {
  if (wide.empty()) return {};
  int n = WideCharToMultiByte(CP_UTF8, 0, wide.data(), static_cast<int>(wide.size()), nullptr, 0, nullptr, nullptr);
  std::string out(static_cast<size_t>(n), '\0');
  WideCharToMultiByte(CP_UTF8, 0, wide.data(), static_cast<int>(wide.size()), out.data(), n, nullptr, nullptr);
  return out;
}

std::wstring Utf8ToWide(const std::string& utf8) {
  if (utf8.empty()) return {};
  int n = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), nullptr, 0);
  std::wstring out(static_cast<size_t>(n), L'\0');
  MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), out.data(), n);
  return out;
}

ControllerHooks DefaultHooks() {
  ControllerHooks hooks;
  hooks.vault_dir = SecureVault::DefaultDirectory();
  hooks.prefs_path = hooks.vault_dir.parent_path() / L"prefs.txt";
  hooks.own_exe = OwnExe();
  hooks.engine_dir = hooks.own_exe.parent_path();
  hooks.is_admin = ProcessIsElevated;
  hooks.network_available = InternetReachable;
  // ADR-194: GetTickCount64 is monotonic and keeps counting through sleep and hibernation, so a
  // window deadline cannot outlive its real time across a sleep. (Whether steady_clock, which is
  // QueryPerformanceCounter, counts sleep is not guaranteed by the documentation.)
  hooks.now_ms = []() { return static_cast<int64_t>(GetTickCount64()); };
  hooks.unix_seconds = []() { return static_cast<int64_t>(std::time(nullptr)); };
  hooks.resolve_ipv4 = ResolveIpv4;
  hooks.physical_network = PhysicalNetworkId;
  // ADR-196: the real firewall backend. Created lazily, and only when the setting is on.
  hooks.kill_switch_backend = CreateWfpKillSwitchBackend;
  hooks.tunnel_luid = TunnelLuid;
  return hooks;
}

std::vector<AppEntry> InstalledApps(const std::string& own_exe_utf8) {
  std::string own_lower = own_exe_utf8;
  for (char& c : own_lower) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  std::map<std::string, AppEntry> found;
  PWSTR common = nullptr, user = nullptr;
  if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_CommonPrograms, 0, nullptr, &common)))
    ScanShortcuts(common, &found, own_lower);
  if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_Programs, 0, nullptr, &user)))
    ScanShortcuts(user, &found, own_lower);
  CoTaskMemFree(common);
  CoTaskMemFree(user);
  std::vector<AppEntry> apps;
  for (auto& entry : found) apps.push_back(std::move(entry.second));
  std::sort(apps.begin(), apps.end(), [](const AppEntry& a, const AppEntry& b) { return a.label < b.label; });
  return apps;
}

std::string PickExecutable() {
  wchar_t buffer[MAX_PATH * 2] = {};
  OPENFILENAMEW dialog{};
  dialog.lStructSize = sizeof(dialog);
  dialog.lpstrFilter = L"Программы (*.exe)\0*.exe\0";
  dialog.lpstrFile = buffer;
  dialog.nMaxFile = static_cast<DWORD>(std::size(buffer));
  dialog.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR | OFN_HIDEREADONLY;
  if (!GetOpenFileNameW(&dialog)) return {};
  std::string path = WideToUtf8(buffer);
  return IsValidAppPath(path) ? path : std::string();
}

void OpenTelegramLogin(const std::string& url) {
  static const std::string kPrefix = "https://t.me/";
  bool ok = url.size() > kPrefix.size() && url.size() <= 2048 && url.compare(0, kPrefix.size(), kPrefix) == 0 &&
            std::none_of(url.begin(), url.end(), [](unsigned char c) { return c <= 0x20 || c == 0x7f || c == '@' || c == '\\' || c == '"'; });
  // Host must be exactly t.me: the prefix check already excludes "t.me.evil", ports and userinfo.
  // The app runs elevated; launching through explorer.exe opens the link in the user's own browser
  // session instead of an elevated browser.
  std::wstring parameter = L"\"" + Utf8ToWide(url) + L"\"";
  if (!ok || reinterpret_cast<INT_PTR>(ShellExecuteW(nullptr, L"open", L"explorer.exe", parameter.c_str(),
                                                     nullptr, SW_SHOWNORMAL)) <= 32)
    throw NativeError("login_unavailable", "Не удалось открыть Telegram");
}

}  // namespace detour
