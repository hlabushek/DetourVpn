// MethodChannel "project.vpn/tunnel.v1" for Windows (I-032 stage 3): a thin, allowlisted layer over
// TunnelController. Same method names and argument shapes as ClientActivity.java; unknown methods
// return notImplemented. No shell, no key export, no file import, no logging of values.
#pragma once

#include <flutter/binary_messenger.h>
#include <flutter/encodable_value.h>
#include <flutter/method_channel.h>

#include <memory>

#include "tunnel_controller.h"

namespace detour {

class TunnelChannel {
 public:
  explicit TunnelChannel(flutter::BinaryMessenger* messenger);
  // Stops the engine (graceful, then forced) before the window goes away.
  ~TunnelChannel();
  void Shutdown();
  // ADR-194: the PC resumed from sleep (WM_POWERBROADCAST). Never throws.
  void OnSystemResume();

 private:
  void Handle(const flutter::MethodCall<flutter::EncodableValue>& call,
              std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result);

  std::unique_ptr<TunnelController> controller_;
  std::unique_ptr<flutter::MethodChannel<flutter::EncodableValue>> channel_;
};

}  // namespace detour
