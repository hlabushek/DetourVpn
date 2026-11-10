// DPAPI-backed vault (current user) for the device key, the session and the pending login.
// Port of SecureVault.java: fixed record names, versioned format, bounded size, atomic replace,
// no plaintext files. The DPAPI entropy binds each blob to its record name, like the AAD on Android.
#pragma once

#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>

namespace detour {

class SecureVault {
 public:
  explicit SecureVault(std::filesystem::path directory) : dir_(std::move(directory)) {}

  // %LOCALAPPDATA%\DetourVpn\vault
  static std::filesystem::path DefaultDirectory();

  // name is one of "device", "session", "login". Throws std::runtime_error("vault_*") on failure
  // (corrupt or foreign blob, wrong user, oversize) and std::invalid_argument for a bad name/value.
  std::optional<std::string> Read(const std::string& name) const;
  // nullopt deletes the record and verifies it is gone.
  void Write(const std::string& name, const std::optional<std::string>& value) const;

  // Device private key (base64), generated on first use and kept only in the vault.
  std::string DevicePrivateKey() const;

 private:
  std::filesystem::path FileFor(const std::string& name) const;
  std::filesystem::path dir_;
};

}  // namespace detour
