#include "tunnel_channel.h"

#include <flutter/standard_method_codec.h>

#include <algorithm>
#include <optional>
#include <set>

#include "config_factory.h"
#include "system_hooks.h"

namespace detour {
namespace {

using flutter::EncodableList;
using flutter::EncodableMap;
using flutter::EncodableValue;

const EncodableMap* Args(const flutter::MethodCall<EncodableValue>& call) {
  return std::get_if<EncodableMap>(call.arguments());
}

const EncodableValue* Field(const EncodableMap* map, const char* key) {
  if (!map) return nullptr;
  auto it = map->find(EncodableValue(key));
  return it == map->end() ? nullptr : &it->second;
}

std::optional<std::string> GetString(const EncodableMap* map, const char* key) {
  const EncodableValue* v = Field(map, key);
  if (v && std::holds_alternative<std::string>(*v)) return std::get<std::string>(*v);
  return std::nullopt;
}

std::optional<int64_t> GetInt(const EncodableMap* map, const char* key) {
  const EncodableValue* v = Field(map, key);
  if (!v) return std::nullopt;
  if (std::holds_alternative<int32_t>(*v)) return std::get<int32_t>(*v);
  if (std::holds_alternative<int64_t>(*v)) return std::get<int64_t>(*v);
  return std::nullopt;
}

// Missing list -> empty; wrong element type -> nullopt.
std::optional<std::vector<std::string>> GetStrings(const EncodableMap* map, const char* key) {
  std::vector<std::string> out;
  const EncodableValue* v = Field(map, key);
  if (!v || std::holds_alternative<std::monostate>(*v)) return out;
  const auto* list = std::get_if<EncodableList>(v);
  if (!list) return std::nullopt;
  for (const auto& item : *list) {
    const auto* s = std::get_if<std::string>(&item);
    if (!s) return std::nullopt;
    out.push_back(*s);
  }
  return out;
}

// Session records: values are strings; numbers are stringified like JSONObject.getString on Android.
std::optional<TunnelController::Record> GetRecord(const EncodableMap* args) {
  const EncodableValue* v = Field(args, "value");
  if (!v || std::holds_alternative<std::monostate>(*v)) return std::nullopt;
  const auto* map = std::get_if<EncodableMap>(v);
  if (!map) throw NativeError("native_operation_failed", "Операция VPN не выполнена");
  TunnelController::Record record;
  for (const auto& [key, value] : *map) {
    const auto* name = std::get_if<std::string>(&key);
    if (!name) throw NativeError("native_operation_failed", "Операция VPN не выполнена");
    if (const auto* s = std::get_if<std::string>(&value)) record[*name] = *s;
    else if (const auto* i = std::get_if<int32_t>(&value)) record[*name] = std::to_string(*i);
    else if (const auto* l = std::get_if<int64_t>(&value)) record[*name] = std::to_string(*l);
    else throw NativeError("native_operation_failed", "Операция VPN не выполнена");
  }
  return record;
}

EncodableValue RecordValue(const std::optional<TunnelController::Record>& record) {
  if (!record) return EncodableValue();
  EncodableMap map;
  for (const auto& [key, value] : *record) map[EncodableValue(key)] = EncodableValue(value);
  return EncodableValue(map);
}

EncodableValue StringList(const std::vector<std::string>& values) {
  EncodableList list;
  for (const auto& v : values) list.emplace_back(v);
  return EncodableValue(list);
}

WindowGrant GrantFrom(const EncodableMap* args) {
  auto connection = GetString(args, "connection");
  auto runtime = GetString(args, "runtime");
  auto generation = GetInt(args, "generation");
  if (!connection || !runtime || !generation) throw NativeError("native_operation_failed", "Операция VPN не выполнена");
  return WindowGrant{*connection, *runtime, *generation};
}

[[noreturn]] void Bad() { throw NativeError("native_operation_failed", "Операция VPN не выполнена"); }

}  // namespace

TunnelChannel::TunnelChannel(flutter::BinaryMessenger* messenger)
    : controller_(std::make_unique<TunnelController>(DefaultHooks())),
      channel_(std::make_unique<flutter::MethodChannel<EncodableValue>>(
          messenger, "project.vpn/tunnel.v1", &flutter::StandardMethodCodec::GetInstance())) {
  channel_->SetMethodCallHandler(
      [this](const flutter::MethodCall<EncodableValue>& call,
             std::unique_ptr<flutter::MethodResult<EncodableValue>> result) { Handle(call, std::move(result)); });
}

TunnelChannel::~TunnelChannel() { Shutdown(); }

void TunnelChannel::Shutdown() {
  if (channel_) channel_->SetMethodCallHandler(nullptr);
  controller_.reset();  // stops the engine
}

void TunnelChannel::Handle(const flutter::MethodCall<EncodableValue>& call,
                           std::unique_ptr<flutter::MethodResult<EncodableValue>> result) {
  static const std::set<std::string> kAllowed = {
      "permission", "publicKey", "prove", "connect", "connectWindow", "advanceWindow", "disconnect",
      "snapshot", "sessionRead", "sessionWrite", "pendingLoginRead", "pendingLoginWrite", "splitRead",
      "splitModeRead", "splitLastModeRead", "splitWrite", "splitDomainsRead", "apps", "nodeRead",
      "nodeWrite", "openLogin", "capabilities", "pickApp", "dnsRead", "dnsWrite", "connectVless"};
  const std::string& method = call.method_name();
  if (!controller_ || !kAllowed.count(method)) {
    result->NotImplemented();
    return;
  }
  const EncodableMap* args = Args(call);
  try {
    TunnelController& c = *controller_;
    if (method == "permission") {
      result->Success(EncodableValue(c.Permission()));
    } else if (method == "publicKey") {
      result->Success(EncodableValue(c.PublicKey()));
    } else if (method == "prove") {
      auto key = GetString(args, "ephemeralKey");
      auto transcript = GetString(args, "transcript");
      if (!key || !transcript) Bad();
      result->Success(EncodableValue(c.Prove(*key, *transcript)));
    } else if (method == "connect" || method == "connectWindow") {
      auto config = GetString(args, "config");
      auto remaining = GetInt(args, "remainingMs");
      if (!config || !remaining) Bad();
      if (method == "connectWindow") c.Connect(*config, *remaining, GrantFrom(args));
      else c.Connect(*config, *remaining, std::nullopt);
      result->Success();
    } else if (method == "connectVless") {
      // ADR-115: a panel server profile; every field is re-checked by CreateVlessEngineConfig.
      VlessRealityProfile profile;
      auto server = GetString(args, "server");
      auto port = GetInt(args, "port");
      auto uuid = GetString(args, "uuid");
      auto flow = GetString(args, "flow");
      auto sni = GetString(args, "sni");
      auto public_key = GetString(args, "publicKey");
      auto short_id = GetString(args, "shortId");
      auto fingerprint = GetString(args, "fingerprint");
      auto max_ms = GetInt(args, "maxMs");
      if (!server || !port || !uuid || !flow || !sni || !public_key || !short_id || !fingerprint ||
          !max_ms || *port < 1 || *port > 65535)
        Bad();
      profile.server = *server;
      profile.port = static_cast<int>(*port);
      profile.uuid = *uuid;
      profile.flow = *flow;
      profile.sni = *sni;
      profile.public_key = *public_key;
      profile.short_id = *short_id;
      profile.fingerprint = *fingerprint;
      c.ConnectVless(profile, *max_ms);
      std::fill(profile.uuid.begin(), profile.uuid.end(), '\0');
      result->Success();
    } else if (method == "advanceWindow") {
      auto remaining = GetInt(args, "remainingMs");
      if (!remaining) Bad();
      c.AdvanceWindow(GrantFrom(args), *remaining);
      result->Success();
    } else if (method == "disconnect") {
      c.Disconnect();
      result->Success();
    } else if (method == "snapshot") {
      TunnelSnapshot s = c.Snapshot();
      EncodableMap map;
      map[EncodableValue("up")] = EncodableValue(s.up);
      map[EncodableValue("network")] = EncodableValue(s.network);
      map[EncodableValue("handshakeAge")] = EncodableValue(s.handshake_age);
      map[EncodableValue("remainingMs")] = EncodableValue(s.remaining_ms);
      if (s.traffic_bytes) map[EncodableValue("trafficBytes")] = EncodableValue(*s.traffic_bytes);
      if (s.received_bytes) map[EncodableValue("receivedBytes")] = EncodableValue(*s.received_bytes);
      map[EncodableValue("reason")] = EncodableValue(s.reason);
      result->Success(EncodableValue(map));
    } else if (method == "sessionRead") {
      result->Success(RecordValue(c.ReadSession()));
    } else if (method == "sessionWrite") {
      c.WriteSession(GetRecord(args));
      result->Success();
    } else if (method == "pendingLoginRead") {
      result->Success(RecordValue(c.ReadPendingLogin()));
    } else if (method == "pendingLoginWrite") {
      c.WritePendingLogin(GetRecord(args));
      result->Success();
    } else if (method == "nodeRead") {
      auto node = c.ReadNode();
      result->Success(node ? EncodableValue(*node) : EncodableValue());
    } else if (method == "nodeWrite") {
      c.WriteNode(GetString(args, "id"));
      result->Success();
    } else if (method == "dnsRead") {
      result->Success(EncodableValue(c.Dns()));
    } else if (method == "dnsWrite") {
      auto server = GetString(args, "server");
      if (!server) Bad();
      result->Success(EncodableValue(c.WriteDns(*server)));
    } else if (method == "splitRead") {
      result->Success(StringList(c.SplitApps()));
    } else if (method == "splitDomainsRead") {
      result->Success(StringList(c.SplitDomains()));
    } else if (method == "splitModeRead") {
      result->Success(EncodableValue(c.SplitMode()));
    } else if (method == "splitLastModeRead") {
      result->Success(EncodableValue(c.SplitLastMode()));
    } else if (method == "splitWrite") {
      auto apps = GetStrings(args, "packages");
      auto domains = GetStrings(args, "domains");
      if (!apps || !domains || !Field(args, "packages")) Bad();
      result->Success(StringList(c.WriteSplit(GetString(args, "mode").value_or("exclude"), *apps, *domains)));
    } else if (method == "apps") {
      EncodableList list;
      for (const AppEntry& app : InstalledApps(WideToUtf8(DefaultHooks().own_exe.wstring()))) {
        EncodableMap item;
        // "packageName" carries the executable path so the existing Dart models work unchanged.
        item[EncodableValue("packageName")] = EncodableValue(app.path);
        item[EncodableValue("label")] = EncodableValue(app.label);
        item[EncodableValue("icon")] = EncodableValue();
        list.emplace_back(item);
      }
      result->Success(EncodableValue(list));
    } else if (method == "pickApp") {
      std::string path = PickExecutable();
      result->Success(path.empty() ? EncodableValue() : EncodableValue(path));
    } else if (method == "capabilities") {
      result->Success(StringList(TunnelController::Capabilities()));
    } else if (method == "openLogin") {
      auto url = GetString(args, "url");
      if (!url) Bad();
      OpenTelegramLogin(*url);
      result->Success();
    }
  } catch (const NativeError& error) {
    result->Error(error.code, error.message);
  } catch (const std::exception&) {
    result->Error("native_operation_failed", "Операция VPN не выполнена");
  }
}

}  // namespace detour
