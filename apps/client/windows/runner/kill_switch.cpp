#include "kill_switch.h"

namespace detour {

std::vector<uint64_t> OwnedFilterIds(const std::vector<FilterEntry>& all, const ProviderKey& ours) {
  std::vector<uint64_t> ids;
  for (const auto& entry : all)
    if (entry.provider && *entry.provider == ours) ids.push_back(entry.id);
  return ids;
}

bool KillSwitch::EnsureBackend() {
  if (!backend_ && factory_) backend_ = factory_();
  return backend_ != nullptr;
}

void KillSwitch::Sweep() {
  if (applied_ || swept_ || !EnsureBackend()) return;  // never sweep our own live session
  swept_ = backend_->RemoveLeftovers();
}

bool KillSwitch::Engage(const KillSwitchRules& rules) {
  if (applied_ && *applied_ == rules) return true;
  if (!EnsureBackend()) return false;
  const bool first = !applied_;
  if (first && !swept_) {
    // A leftover provider with our key would make the add fail; clear it first (ours only).
    swept_ = backend_->RemoveLeftovers();
    if (!swept_) return false;
  }
  if (!backend_->Apply(rules)) {
    if (first) backend_->Remove();  // nothing half-installed
    return false;
  }
  applied_ = rules;
  return true;
}

void KillSwitch::Release() {
  if (!applied_) return;
  applied_.reset();
  if (backend_) backend_->Remove();
}

}  // namespace detour
