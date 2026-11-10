#include "secure_vault.h"

#include <windows.h>
#include <dpapi.h>
#include <shlobj.h>

#include <fstream>
#include <iterator>
#include <vector>

#include "key_confirmation.h"

namespace detour {
namespace {

constexpr size_t kMaxFileBytes = 32768;
constexpr size_t kMaxPlainBytes = 16384;

bool ValidName(const std::string& name) {
  return name == "device" || name == "session" || name == "login";
}

DATA_BLOB Entropy(const std::string& name, std::string* storage) {
  *storage = "project-vpn:" + name + ":v1";
  DATA_BLOB blob;
  blob.pbData = reinterpret_cast<BYTE*>(storage->data());
  blob.cbData = static_cast<DWORD>(storage->size());
  return blob;
}

}  // namespace

std::filesystem::path SecureVault::DefaultDirectory() {
  PWSTR raw = nullptr;
  if (FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData, 0, nullptr, &raw)))
    throw std::runtime_error("vault_path_unavailable");
  std::filesystem::path base(raw);
  CoTaskMemFree(raw);
  return base / L"DetourVpn" / L"vault";
}

std::filesystem::path SecureVault::FileFor(const std::string& name) const {
  if (!ValidName(name)) throw std::invalid_argument("vault_name_invalid");
  return dir_ / (name + ".vault");
}

std::optional<std::string> SecureVault::Read(const std::string& name) const {
  std::filesystem::path file = FileFor(name);
  std::error_code ec;
  if (!std::filesystem::exists(file, ec)) return std::nullopt;
  if (std::filesystem::file_size(file, ec) > kMaxFileBytes || ec) throw std::runtime_error("vault_corrupt");
  std::ifstream in(file, std::ios::binary);
  std::vector<char> data((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
  if (data.size() < 2 || data[0] != 1) throw std::runtime_error("vault_corrupt");
  std::string entropy_storage;
  DATA_BLOB entropy = Entropy(name, &entropy_storage);
  DATA_BLOB input{static_cast<DWORD>(data.size() - 1), reinterpret_cast<BYTE*>(data.data() + 1)};
  DATA_BLOB output{};
  if (!CryptUnprotectData(&input, nullptr, &entropy, nullptr, nullptr, CRYPTPROTECT_UI_FORBIDDEN, &output))
    throw std::runtime_error("vault_unprotect_failed");
  std::string plain(reinterpret_cast<char*>(output.pbData), output.cbData);
  SecureZeroMemory(output.pbData, output.cbData);
  LocalFree(output.pbData);
  return plain;
}

void SecureVault::Write(const std::string& name, const std::optional<std::string>& value) const {
  std::filesystem::path file = FileFor(name);
  std::error_code ec;
  if (!value) {
    std::filesystem::remove(file, ec);
    if (ec || std::filesystem::exists(file)) throw std::runtime_error("vault_delete_failed");
    return;
  }
  if (value->size() > kMaxPlainBytes) throw std::invalid_argument("vault_value_too_large");
  std::filesystem::create_directories(dir_, ec);
  if (ec) throw std::runtime_error("vault_directory_failed");

  std::string entropy_storage;
  DATA_BLOB entropy = Entropy(name, &entropy_storage);
  std::string plain = *value;
  DATA_BLOB input{static_cast<DWORD>(plain.size()), reinterpret_cast<BYTE*>(plain.data())};
  DATA_BLOB output{};
  BOOL ok = CryptProtectData(&input, L"DetourVpn", &entropy, nullptr, nullptr, CRYPTPROTECT_UI_FORBIDDEN,
                             &output);
  SecureZeroMemory(plain.data(), plain.size());
  if (!ok) throw std::runtime_error("vault_protect_failed");

  std::filesystem::path temp = file;
  temp += L".tmp";
  bool written = false;
  {
    std::ofstream out(temp, std::ios::binary | std::ios::trunc);
    const char version = 1;
    out.write(&version, 1);
    out.write(reinterpret_cast<const char*>(output.pbData), output.cbData);
    out.flush();
    written = out.good();
  }
  LocalFree(output.pbData);
  if (!written ||
      !MoveFileExW(temp.c_str(), file.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
    std::filesystem::remove(temp, ec);
    throw std::runtime_error("vault_write_failed");
  }
}

std::string SecureVault::DevicePrivateKey() const {
  if (auto saved = Read("device")) {
    Key32 check{};
    if (!Base64Decode32(*saved, &check)) throw std::runtime_error("vault_corrupt");
    SecureZeroMemory(check.data(), check.size());
    return *saved;
  }
  std::string fresh = GenerateDevicePrivateKey();
  Write("device", fresh);
  return fresh;
}

}  // namespace detour
