// Production wiring of ControllerHooks plus the Windows-only helpers behind the channel methods
// `apps`, `pickApp` and `openLogin`. Kept separate from tunnel_controller so tests inject fakes.
#pragma once

#include <string>
#include <vector>

#include "tunnel_controller.h"

namespace detour {

ControllerHooks DefaultHooks();

struct AppEntry {
  std::string path;   // absolute .exe path (UTF-8)
  std::string label;  // display name (UTF-8)
};

// Executables behind Start-menu shortcuts (all users + current user), deduplicated, valid paths only,
// the app itself excluded. Needs COM initialised on the calling thread. Bounded to 300 entries.
std::vector<AppEntry> InstalledApps(const std::string& own_exe_utf8);

// Modal file picker for one .exe. Empty string when cancelled.
std::string PickExecutable();

// Opens https://t.me/... only. Throws NativeError("login_unavailable") otherwise.
void OpenTelegramLogin(const std::string& url);

std::string WideToUtf8(const std::wstring& wide);
std::wstring Utf8ToWide(const std::string& utf8);

}  // namespace detour
