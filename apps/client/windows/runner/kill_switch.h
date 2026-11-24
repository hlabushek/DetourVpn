// ADR-196 (W-01): Windows kill switch. While the user wants the VPN and the setting is on, all
// traffic is blocked except loopback, DHCP, the engine process, the tunnel adapter and this app to
// the API addresses. The firewall calls live behind KillSwitchBackend so the sequencing below is
// unit-tested with a fake; only kill_switch_wfp.cpp touches the Windows Filtering Platform.
#pragma once

#include <array>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace detour {

struct KillSwitchRules {
  std::filesystem::path engine_exe;      // permitted to send anywhere (it must reach the server)
  std::filesystem::path own_exe;         // permitted to the API addresses only
  std::vector<std::string> api_ipv4;     // IPv4 literals (primary and fallback API origins)
  std::optional<uint64_t> tunnel_luid;   // the engine's TUN adapter while it exists

  bool operator==(const KillSwitchRules& o) const {
    return engine_exe == o.engine_exe && own_exe == o.own_exe && api_ipv4 == o.api_ipv4 &&
           tunnel_luid == o.tunnel_luid;
  }
  bool operator!=(const KillSwitchRules& o) const { return !(*this == o); }
};

class KillSwitchBackend {
 public:
  virtual ~KillSwitchBackend() = default;
  // Deletes every filter, sublayer and provider registered under OUR provider key, left by an
  // earlier process. Never touches objects of another provider. True = nothing of ours remains.
  virtual bool RemoveLeftovers() = 0;
  // Installs the rule set, or atomically replaces the one installed earlier, in a dynamic WFP
  // session (Windows deletes it when the process exits). On false nothing new was applied: the
  // first Apply leaves nothing behind, a later one keeps the previous rules.
  virtual bool Apply(const KillSwitchRules& rules) = 0;
  // Deletes everything this backend added and closes the session. Idempotent.
  virtual void Remove() = 0;
};

// Leftover-sweep selection, kept pure so it is unit-tested: the ids of the filters registered under
// our provider key only. Filters of any other provider, or of none, are never selected.
using ProviderKey = std::array<uint8_t, 16>;
struct FilterEntry {
  uint64_t id = 0;
  std::optional<ProviderKey> provider;
};
std::vector<uint64_t> OwnedFilterIds(const std::vector<FilterEntry>& all, const ProviderKey& ours);

// Production backend (kill_switch_wfp.cpp). Creating it does not call WFP.
std::unique_ptr<KillSwitchBackend> CreateWfpKillSwitchBackend();

// Sequencing over a backend. Not thread-safe: the controller calls it under its own lock.
class KillSwitch {
 public:
  using Factory = std::function<std::unique_ptr<KillSwitchBackend>()>;
  explicit KillSwitch(Factory factory = nullptr) : factory_(std::move(factory)) {}
  ~KillSwitch() { Release(); }
  KillSwitch(const KillSwitch&) = delete;
  KillSwitch& operator=(const KillSwitch&) = delete;

  // Startup sweep of leftovers of our provider. Called only when the setting is on.
  void Sweep();
  // Installs or updates the block. The first engage of a process sweeps leftovers first.
  // Identical rules are not re-applied. False = failed (no backend, or the backend refused).
  bool Engage(const KillSwitchRules& rules);
  // Removes the block. Makes no backend call when nothing is engaged.
  void Release();
  bool engaged() const { return applied_.has_value(); }
  const std::optional<KillSwitchRules>& rules() const { return applied_; }

 private:
  bool EnsureBackend();
  Factory factory_;
  std::unique_ptr<KillSwitchBackend> backend_;
  std::optional<KillSwitchRules> applied_;
  bool swept_ = false;
};

}  // namespace detour
