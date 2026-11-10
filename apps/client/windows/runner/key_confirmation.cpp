#include "key_confirmation.h"

#include <windows.h>
#include <bcrypt.h>

#include <algorithm>
#include <cstring>

namespace detour {
namespace {

[[noreturn]] void Invalid() { throw std::invalid_argument("key_invalid"); }

bool Ok(NTSTATUS status) { return status >= 0; }

void Clamp(Key32* key) {
  (*key)[0] &= 248;
  (*key)[31] &= 127;
  (*key)[31] |= 64;
}

// RAII for CNG handles.
struct AlgHandle {
  BCRYPT_ALG_HANDLE h = nullptr;
  ~AlgHandle() {
    if (h) BCryptCloseAlgorithmProvider(h, 0);
  }
};
struct KeyHandle {
  BCRYPT_KEY_HANDLE h = nullptr;
  ~KeyHandle() {
    if (h) BCryptDestroyKey(h);
  }
};
struct SecretHandle {
  BCRYPT_SECRET_HANDLE h = nullptr;
  ~SecretHandle() {
    if (h) BCryptDestroySecret(h);
  }
};

void OpenX25519(AlgHandle* alg) {
  if (!Ok(BCryptOpenAlgorithmProvider(&alg->h, BCRYPT_ECDH_ALGORITHM, nullptr, 0))) Invalid();
  if (!Ok(BCryptSetProperty(alg->h, BCRYPT_ECC_CURVE_NAME,
                            reinterpret_cast<PUCHAR>(const_cast<wchar_t*>(BCRYPT_ECC_CURVE_25519)),
                            static_cast<ULONG>((wcslen(BCRYPT_ECC_CURVE_25519) + 1) * sizeof(wchar_t)),
                            0)))
    Invalid();
}

// Private blob: header + X(32) + Y(32) + d(32). X and Y may be zero on import; CNG derives X.
void ImportPrivate(const AlgHandle& alg, Key32 d, KeyHandle* key) {
  Clamp(&d);
  std::vector<uint8_t> blob(sizeof(BCRYPT_ECCKEY_BLOB) + 96, 0);
  auto* header = reinterpret_cast<BCRYPT_ECCKEY_BLOB*>(blob.data());
  header->dwMagic = BCRYPT_ECDH_PRIVATE_GENERIC_MAGIC;
  header->cbKey = 32;
  std::memcpy(blob.data() + sizeof(*header) + 64, d.data(), 32);
  NTSTATUS status = BCryptImportKeyPair(alg.h, nullptr, BCRYPT_ECCPRIVATE_BLOB, &key->h, blob.data(),
                                        static_cast<ULONG>(blob.size()), 0);
  SecureZeroMemory(blob.data(), blob.size());
  SecureZeroMemory(d.data(), d.size());
  if (!Ok(status)) Invalid();
}

}  // namespace

std::string Base64Encode(const uint8_t* data, size_t size) {
  static const char* kAlphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string out;
  for (size_t i = 0; i < size; i += 3) {
    uint32_t chunk = static_cast<uint32_t>(data[i]) << 16;
    if (i + 1 < size) chunk |= static_cast<uint32_t>(data[i + 1]) << 8;
    if (i + 2 < size) chunk |= data[i + 2];
    out.push_back(kAlphabet[(chunk >> 18) & 63]);
    out.push_back(kAlphabet[(chunk >> 12) & 63]);
    out.push_back(i + 1 < size ? kAlphabet[(chunk >> 6) & 63] : '=');
    out.push_back(i + 2 < size ? kAlphabet[chunk & 63] : '=');
  }
  return out;
}

bool Base64Decode32(const std::string& text, Key32* out) {
  if (text.size() != 44 || text[43] != '=') return false;
  auto value = [](char c) -> int {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
  };
  size_t at = 0;
  for (size_t i = 0; i < 44; i += 4) {
    int a = value(text[i]), b = value(text[i + 1]), c = value(text[i + 2]);
    int d = i + 3 < 43 ? value(text[i + 3]) : 0;  // the final '=' pad
    if (a < 0 || b < 0 || c < 0 || d < 0) return false;
    uint32_t chunk = (a << 18) | (b << 12) | (c << 6) | d;
    if (i + 4 == 44) {
      if ((c & 3) != 0) return false;  // non-canonical trailing bits
      (*out)[at++] = static_cast<uint8_t>(chunk >> 16);
      (*out)[at++] = static_cast<uint8_t>(chunk >> 8);
    } else {
      (*out)[at++] = static_cast<uint8_t>(chunk >> 16);
      (*out)[at++] = static_cast<uint8_t>(chunk >> 8);
      (*out)[at++] = static_cast<uint8_t>(chunk);
    }
  }
  return at == 32;
}

std::string GenerateDevicePrivateKey() {
  Key32 key{};
  if (!Ok(BCryptGenRandom(nullptr, key.data(), 32, BCRYPT_USE_SYSTEM_PREFERRED_RNG)))
    throw std::runtime_error("random_unavailable");
  Clamp(&key);
  std::string encoded = Base64Encode(key.data(), key.size());
  SecureZeroMemory(key.data(), key.size());
  return encoded;
}

std::string PublicKeyOf(const std::string& private_key_b64) {
  Key32 d{};
  if (!Base64Decode32(private_key_b64, &d)) Invalid();
  AlgHandle alg;
  OpenX25519(&alg);
  KeyHandle key;
  ImportPrivate(alg, d, &key);
  SecureZeroMemory(d.data(), d.size());
  std::vector<uint8_t> blob(sizeof(BCRYPT_ECCKEY_BLOB) + 96);
  ULONG written = 0;
  if (!Ok(BCryptExportKey(key.h, nullptr, BCRYPT_ECCPRIVATE_BLOB, blob.data(),
                          static_cast<ULONG>(blob.size()), &written, 0)) ||
      written != blob.size()) {
    SecureZeroMemory(blob.data(), blob.size());
    Invalid();
  }
  std::string encoded = Base64Encode(blob.data() + sizeof(BCRYPT_ECCKEY_BLOB), 32);  // X
  SecureZeroMemory(blob.data(), blob.size());
  return encoded;
}

Key32 X25519(const Key32& private_key, const Key32& peer_public) {
  AlgHandle alg;
  OpenX25519(&alg);
  KeyHandle mine;
  ImportPrivate(alg, private_key, &mine);
  std::vector<uint8_t> blob(sizeof(BCRYPT_ECCKEY_BLOB) + 64, 0);
  auto* header = reinterpret_cast<BCRYPT_ECCKEY_BLOB*>(blob.data());
  header->dwMagic = BCRYPT_ECDH_PUBLIC_GENERIC_MAGIC;
  header->cbKey = 32;
  std::memcpy(blob.data() + sizeof(*header), peer_public.data(), 32);
  KeyHandle theirs;
  if (!Ok(BCryptImportKeyPair(alg.h, nullptr, BCRYPT_ECCPUBLIC_BLOB, &theirs.h, blob.data(),
                              static_cast<ULONG>(blob.size()), 0)))
    Invalid();
  SecretHandle secret;
  if (!Ok(BCryptSecretAgreement(mine.h, theirs.h, &secret.h, 0))) Invalid();
  Key32 shared{};
  ULONG written = 0;
  if (!Ok(BCryptDeriveKey(secret.h, BCRYPT_KDF_RAW_SECRET, nullptr, shared.data(), 32, &written, 0)) ||
      written != 32) {
    SecureZeroMemory(shared.data(), shared.size());
    Invalid();
  }
  std::reverse(shared.begin(), shared.end());  // CNG raw secrets are little-endian reversed vs RFC 7748
  return shared;
}

std::vector<uint8_t> HmacSha256(const std::vector<uint8_t>& key, const std::string& message) {
  AlgHandle alg;
  if (!Ok(BCryptOpenAlgorithmProvider(&alg.h, BCRYPT_SHA256_ALGORITHM, nullptr,
                                      BCRYPT_ALG_HANDLE_HMAC_FLAG)))
    throw std::runtime_error("hmac_unavailable");
  std::vector<uint8_t> out(32);
  if (!Ok(BCryptHash(alg.h, const_cast<PUCHAR>(key.data()), static_cast<ULONG>(key.size()),
                     reinterpret_cast<PUCHAR>(const_cast<char*>(message.data())),
                     static_cast<ULONG>(message.size()), out.data(), 32)))
    throw std::runtime_error("hmac_failed");
  return out;
}

std::string ConfirmationAnswer(const std::string& private_key_b64,
                               const std::string& peer_public_key_b64, const std::string& transcript) {
  // "project-vpn:key-confirmation:v1:" + 32 lower-case hex + ":" + 64 lower-case hex
  static const std::string kPrefix = "project-vpn:key-confirmation:v1:";
  auto hex = [](const std::string& s, size_t from, size_t count) {
    for (size_t i = from; i < from + count; ++i)
      if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) return false;
    return true;
  };
  if (transcript.size() != kPrefix.size() + 32 + 1 + 64 || transcript.compare(0, kPrefix.size(), kPrefix) != 0 ||
      !hex(transcript, kPrefix.size(), 32) || transcript[kPrefix.size() + 32] != ':' ||
      !hex(transcript, kPrefix.size() + 33, 64))
    Invalid();
  Key32 priv{}, peer{};
  if (!Base64Decode32(private_key_b64, &priv) || !Base64Decode32(peer_public_key_b64, &peer)) Invalid();
  Key32 shared = X25519(priv, peer);
  SecureZeroMemory(priv.data(), priv.size());
  uint8_t any = 0;
  for (uint8_t b : shared) any |= b;
  if (any == 0) {  // low-order peer key
    SecureZeroMemory(shared.data(), shared.size());
    Invalid();
  }
  std::vector<uint8_t> mac = HmacSha256(std::vector<uint8_t>(shared.begin(), shared.end()), transcript);
  SecureZeroMemory(shared.data(), shared.size());
  static const char* kHex = "0123456789abcdef";
  std::string out;
  for (uint8_t b : mac) {
    out.push_back(kHex[b >> 4]);
    out.push_back(kHex[b & 15]);
  }
  return out;
}

}  // namespace detour
