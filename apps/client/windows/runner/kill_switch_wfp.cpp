// ADR-196: the real kill-switch backend on the Windows Filtering Platform (fwpuclnt, part of the
// Windows SDK; no driver, no third-party code). Written from the public API documentation.
// Everything is added in a session opened with FWPM_SESSION_FLAG_DYNAMIC, so Windows deletes it
// when the session closes, including when the process crashes or is killed; nothing survives a
// reboot. NOT exercised by the unit tests (they use a fake backend) and not yet leak-tested.
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <windows.h>
#include <rpc.h>
#include <initguid.h>
#include <fwpmu.h>

#include <cstdio>
#include <cstring>
#include <vector>

#include "kill_switch.h"

namespace detour {
namespace {

// Our own keys. The leftover sweep deletes only objects registered under kProvider.
// {3b619d4d-ec79-430a-80ba-9a98d3d0cf6d}
constexpr GUID kProvider = {0x3b619d4d, 0xec79, 0x430a, {0x80, 0xba, 0x9a, 0x98, 0xd3, 0xd0, 0xcf, 0x6d}};
// {9d242034-a459-40e1-b9dc-c96dde6b0c6c}
constexpr GUID kSublayer = {0x9d242034, 0xa459, 0x40e1, {0xb9, 0xdc, 0xc9, 0x6d, 0xde, 0x6b, 0x0c, 0x6c}};

wchar_t kName[] = L"DetourVPN kill switch";

ProviderKey KeyOf(const GUID& guid) {
  ProviderKey key{};
  static_assert(sizeof(GUID) == sizeof(ProviderKey), "GUID is 16 bytes");
  std::memcpy(key.data(), &guid, key.size());
  return key;
}

bool ParseIpv4(const std::string& text, UINT32* out) {
  UINT32 value = 0;
  int parts = 0;
  size_t i = 0;
  while (parts < 4) {
    unsigned part = 0;
    size_t digits = 0;
    while (i < text.size() && text[i] >= '0' && text[i] <= '9' && digits < 3) {
      part = part * 10 + static_cast<unsigned>(text[i] - '0');
      ++i;
      ++digits;
    }
    if (digits == 0 || part > 255) return false;
    value = (value << 8) | part;  // WFP wants host byte order
    if (++parts < 4) {
      if (i >= text.size() || text[i] != '.') return false;
      ++i;
    }
  }
  if (i != text.size()) return false;
  *out = value;
  return true;
}

class AppId {
 public:
  explicit AppId(const std::filesystem::path& path) {
    if (FwpmGetAppIdFromFileName0(path.c_str(), &blob_) != ERROR_SUCCESS) blob_ = nullptr;
  }
  ~AppId() {
    if (blob_) FwpmFreeMemory0(reinterpret_cast<void**>(&blob_));
  }
  AppId(const AppId&) = delete;
  AppId& operator=(const AppId&) = delete;
  FWP_BYTE_BLOB* get() const { return blob_; }

 private:
  FWP_BYTE_BLOB* blob_ = nullptr;
};

FWPM_FILTER_CONDITION0 Condition(const GUID& field, FWP_MATCH_TYPE match) {
  FWPM_FILTER_CONDITION0 c{};
  c.fieldKey = field;
  c.matchType = match;
  return c;
}

class WfpBackend : public KillSwitchBackend {
 public:
  ~WfpBackend() override { Remove(); }

  bool RemoveLeftovers() override {
    HANDLE engine = nullptr;
    if (FwpmEngineOpen0(nullptr, RPC_C_AUTHN_WINNT, nullptr, nullptr, &engine) != ERROR_SUCCESS) return false;
    bool ok = true;
    std::vector<FilterEntry> all;
    HANDLE enumeration = nullptr;
    if (FwpmFilterCreateEnumHandle0(engine, nullptr, &enumeration) == ERROR_SUCCESS) {
      for (;;) {
        FWPM_FILTER0** entries = nullptr;
        UINT32 count = 0;
        if (FwpmFilterEnum0(engine, enumeration, 256, &entries, &count) != ERROR_SUCCESS) {
          ok = false;
          break;
        }
        for (UINT32 i = 0; i < count; ++i) {
          FilterEntry entry;
          entry.id = entries[i]->filterId;
          if (entries[i]->providerKey) entry.provider = KeyOf(*entries[i]->providerKey);
          all.push_back(entry);
        }
        FwpmFreeMemory0(reinterpret_cast<void**>(&entries));
        if (count < 256) break;
      }
      FwpmFilterDestroyEnumHandle0(engine, enumeration);
    } else {
      ok = false;
    }
    // Only filters of our provider are deleted; a failed enumeration deletes nothing of others.
    for (UINT64 id : OwnedFilterIds(all, KeyOf(kProvider)))
      if (FwpmFilterDeleteById0(engine, id) != ERROR_SUCCESS) ok = false;
    const DWORD sub = FwpmSubLayerDeleteByKey0(engine, &kSublayer);
    if (sub != ERROR_SUCCESS && sub != static_cast<DWORD>(FWP_E_SUBLAYER_NOT_FOUND)) ok = false;
    const DWORD prov = FwpmProviderDeleteByKey0(engine, &kProvider);
    if (prov != ERROR_SUCCESS && prov != static_cast<DWORD>(FWP_E_PROVIDER_NOT_FOUND)) ok = false;
    FwpmEngineClose0(engine);
    return ok;
  }

  bool Apply(const KillSwitchRules& rules) override {
    const bool fresh = engine_ == nullptr;
    if (fresh) {
      FWPM_SESSION0 session{};
      session.flags = FWPM_SESSION_FLAG_DYNAMIC;
      session.displayData.name = kName;
      if (FwpmEngineOpen0(nullptr, RPC_C_AUTHN_WINNT, nullptr, &session, &engine_) != ERROR_SUCCESS) {
        engine_ = nullptr;
        return false;
      }
    }
    if (FwpmTransactionBegin0(engine_, 0) != ERROR_SUCCESS) {
      if (fresh) Close();
      return false;
    }
    bool ok = !fresh || AddProviderAndSublayer();
    for (UINT64 id : filters_)
      if (ok && FwpmFilterDeleteById0(engine_, id) != ERROR_SUCCESS) ok = false;
    std::vector<UINT64> added;
    ok = ok && AddFilters(rules, &added);
    ok = ok && FwpmTransactionCommit0(engine_) == ERROR_SUCCESS;
    if (!ok) {
      FwpmTransactionAbort0(engine_);
      if (fresh) Close();
      return false;
    }
    filters_ = std::move(added);
    return true;
  }

  void Remove() override {
    if (!engine_) return;
    // Explicit delete first; closing the dynamic session removes anything left regardless.
    if (FwpmTransactionBegin0(engine_, 0) == ERROR_SUCCESS) {
      bool ok = true;
      for (UINT64 id : filters_) ok = ok && FwpmFilterDeleteById0(engine_, id) == ERROR_SUCCESS;
      ok = ok && FwpmSubLayerDeleteByKey0(engine_, &kSublayer) == ERROR_SUCCESS;
      ok = ok && FwpmProviderDeleteByKey0(engine_, &kProvider) == ERROR_SUCCESS;
      if (ok) FwpmTransactionCommit0(engine_);
      else FwpmTransactionAbort0(engine_);
    }
    Close();
  }

 private:
  void Close() {
    if (engine_) FwpmEngineClose0(engine_);
    engine_ = nullptr;
    filters_.clear();
  }

  bool AddProviderAndSublayer() {
    FWPM_PROVIDER0 provider{};
    provider.providerKey = kProvider;
    provider.displayData.name = kName;
    if (FwpmProviderAdd0(engine_, &provider, nullptr) != ERROR_SUCCESS) return false;
    FWPM_SUBLAYER0 sublayer{};
    sublayer.subLayerKey = kSublayer;
    sublayer.displayData.name = kName;
    sublayer.providerKey = const_cast<GUID*>(&kProvider);
    sublayer.weight = 0xFFFF;
    return FwpmSubLayerAdd0(engine_, &sublayer, nullptr) == ERROR_SUCCESS;
  }

  bool Add(const GUID& layer, FWP_ACTION_TYPE action, UINT8 weight, std::vector<FWPM_FILTER_CONDITION0> conditions,
           std::vector<UINT64>* added) {
    FWPM_FILTER0 filter{};
    filter.providerKey = const_cast<GUID*>(&kProvider);
    filter.subLayerKey = kSublayer;
    filter.layerKey = layer;
    filter.displayData.name = kName;
    filter.action.type = action;
    filter.weight.type = FWP_UINT8;
    filter.weight.uint8 = weight;
    filter.numFilterConditions = static_cast<UINT32>(conditions.size());
    filter.filterCondition = conditions.empty() ? nullptr : conditions.data();
    UINT64 id = 0;
    if (FwpmFilterAdd0(engine_, &filter, nullptr, &id) != ERROR_SUCCESS) return false;
    added->push_back(id);
    return true;
  }

  bool AddFilters(const KillSwitchRules& rules, std::vector<UINT64>* added) {
    AppId engine_app(rules.engine_exe);
    AppId own_app(rules.own_exe);
    if (!engine_app.get() || !own_app.get()) return false;
    UINT64 luid = rules.tunnel_luid.value_or(0);

    const GUID v4[] = {FWPM_LAYER_ALE_AUTH_CONNECT_V4, FWPM_LAYER_ALE_AUTH_RECV_ACCEPT_V4};
    const GUID v6[] = {FWPM_LAYER_ALE_AUTH_CONNECT_V6, FWPM_LAYER_ALE_AUTH_RECV_ACCEPT_V6};
    for (int family = 0; family < 2; ++family) {
      for (const GUID& layer : family == 0 ? v4 : v6) {
        // Lowest weight: block everything else.
        if (!Add(layer, FWP_ACTION_BLOCK, 0, {}, added)) return false;
        // Loopback.
        auto loopback = Condition(FWPM_CONDITION_FLAGS, FWP_MATCH_FLAGS_ALL_SET);
        loopback.conditionValue.type = FWP_UINT32;
        loopback.conditionValue.uint32 = FWP_CONDITION_FLAG_IS_LOOPBACK;
        if (!Add(layer, FWP_ACTION_PERMIT, 12, {loopback}, added)) return false;
        // The engine process (it must reach the VPN server; split-tunnel direct traffic too).
        auto engine = Condition(FWPM_CONDITION_ALE_APP_ID, FWP_MATCH_EQUAL);
        engine.conditionValue.type = FWP_BYTE_BLOB_TYPE;
        engine.conditionValue.byteBlob = engine_app.get();
        if (!Add(layer, FWP_ACTION_PERMIT, 12, {engine}, added)) return false;
        // The tunnel adapter while it exists.
        if (rules.tunnel_luid) {
          auto tun = Condition(FWPM_CONDITION_IP_LOCAL_INTERFACE, FWP_MATCH_EQUAL);
          tun.conditionValue.type = FWP_UINT64;
          tun.conditionValue.uint64 = &luid;
          if (!Add(layer, FWP_ACTION_PERMIT, 12, {tun}, added)) return false;
        }
        // DHCP (v4: client 68 <-> server 67; v6: client 546 <-> server 547) keeps the link up.
        auto udp = Condition(FWPM_CONDITION_IP_PROTOCOL, FWP_MATCH_EQUAL);
        udp.conditionValue.type = FWP_UINT8;
        udp.conditionValue.uint8 = IPPROTO_UDP;
        auto local = Condition(FWPM_CONDITION_IP_LOCAL_PORT, FWP_MATCH_EQUAL);
        local.conditionValue.type = FWP_UINT16;
        local.conditionValue.uint16 = family == 0 ? 68 : 546;
        auto remote = Condition(FWPM_CONDITION_IP_REMOTE_PORT, FWP_MATCH_EQUAL);
        remote.conditionValue.type = FWP_UINT16;
        remote.conditionValue.uint16 = family == 0 ? 67 : 547;
        if (!Add(layer, FWP_ACTION_PERMIT, 12, {udp, local, remote}, added)) return false;
      }
    }
    // This app to the API addresses only (outbound TCP, IPv4). No DNS is permitted here.
    for (const auto& text : rules.api_ipv4) {
      UINT32 address = 0;
      if (!ParseIpv4(text, &address)) return false;
      auto app = Condition(FWPM_CONDITION_ALE_APP_ID, FWP_MATCH_EQUAL);
      app.conditionValue.type = FWP_BYTE_BLOB_TYPE;
      app.conditionValue.byteBlob = own_app.get();
      auto tcp = Condition(FWPM_CONDITION_IP_PROTOCOL, FWP_MATCH_EQUAL);
      tcp.conditionValue.type = FWP_UINT8;
      tcp.conditionValue.uint8 = IPPROTO_TCP;
      auto to = Condition(FWPM_CONDITION_IP_REMOTE_ADDRESS, FWP_MATCH_EQUAL);
      to.conditionValue.type = FWP_UINT32;
      to.conditionValue.uint32 = address;
      if (!Add(FWPM_LAYER_ALE_AUTH_CONNECT_V4, FWP_ACTION_PERMIT, 12, {app, tcp, to}, added)) return false;
    }
    return true;
  }

  HANDLE engine_ = nullptr;
  std::vector<UINT64> filters_;
};

}  // namespace

std::unique_ptr<KillSwitchBackend> CreateWfpKillSwitchBackend() { return std::make_unique<WfpBackend>(); }

}  // namespace detour
