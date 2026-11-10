// Device key handling and versioned key confirmation on Windows CNG (no new dependencies).
// Port of KeyConfirmation.java and the key part of SecureVault.java (ADR-095): X25519 through
// BCRYPT_ECDH_ALGORITHM + BCRYPT_ECC_CURVE_25519, HMAC-SHA256 through CNG. The private key stays in
// native memory and the DPAPI vault; it is never returned to Dart or sent to the backend.
#pragma once

#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace detour {

using Key32 = std::array<uint8_t, 32>;

std::string Base64Encode(const uint8_t* data, size_t size);
// Canonical 44-character base64 of exactly 32 bytes; false otherwise.
bool Base64Decode32(const std::string& text, Key32* out);

// Random 32-byte WireGuard-style (clamped) private key, base64.
std::string GenerateDevicePrivateKey();
// Public key (base64) for a base64 private key. Throws std::invalid_argument on a bad key.
std::string PublicKeyOf(const std::string& private_key_b64);

// key-confirmation answer: lower-case hex of HMAC-SHA256(X25519(private, peer), transcript).
// Throws std::invalid_argument for a bad transcript, bad peer key or a low-order peer key.
std::string ConfirmationAnswer(const std::string& private_key_b64,
                               const std::string& peer_public_key_b64,
                               const std::string& transcript);

// Exposed for tests.
std::vector<uint8_t> HmacSha256(const std::vector<uint8_t>& key, const std::string& message);
// Raw X25519 in RFC 7748 byte order. The private key is clamped before use (CNG requires it).
Key32 X25519(const Key32& private_key, const Key32& peer_public);

}  // namespace detour
