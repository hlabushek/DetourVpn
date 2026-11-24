package org.example.vpnpoc;

import android.annotation.TargetApi;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.MessageDigest;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.util.Base64;
import java.util.regex.Pattern;

/**
 * ADR-107 decision 5: Ed25519 check of the update manifest envelope. Pure java.security, so the
 * JVM unit tests run it unchanged. Android provides Ed25519 from API 33; callers must not call this
 * on older devices (they get no in-app update, fail closed).
 */
@TargetApi(33)
final class UpdateSignature {
    /**
     * Owner's update-signing public key: raw 32-byte Ed25519 key, standard base64. Must equal
     * {@code UPDATE_SIGNING_PUBLIC_KEY} in services/api/src/vpn_api/app_update.py (a test pins it).
     * Empty = no key generated yet (scripts/app_update_keygen.py), so every manifest is refused.
     */
    static final String PUBLIC_KEY_B64 = "N8vv9JWkQT+WW2+t055fIEq1kTjK9uYb3197MOIHEu8=";

    static final int MIN_API = 33;
    static final int MAX_PAYLOAD_BYTES = 16 * 1024;
    // SubjectPublicKeyInfo DER prefix for an Ed25519 key (RFC 8410).
    private static final byte[] SPKI_PREFIX = {
        0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00
    };
    private static final Pattern BASE64 = Pattern.compile("[A-Za-z0-9+/]+={0,2}");
    private static final Pattern KEY_ID = Pattern.compile("[0-9a-f]{16}");

    private UpdateSignature() {}

    /** The verified payload text, or {@code null} for any problem. Never throws. */
    static String verify(String publicKeyB64, String payloadB64, String signatureB64, String keyId) {
        try {
            byte[] key = decode(publicKeyB64);
            byte[] payload = decode(payloadB64);
            byte[] signature = decode(signatureB64);
            if (key == null || key.length != 32 || payload == null || payload.length == 0
                    || payload.length > MAX_PAYLOAD_BYTES || signature == null || signature.length != 64
                    || keyId == null || !KEY_ID.matcher(keyId).matches() || !keyId.equals(keyId(key)))
                return null;
            byte[] spki = new byte[SPKI_PREFIX.length + key.length];
            System.arraycopy(SPKI_PREFIX, 0, spki, 0, SPKI_PREFIX.length);
            System.arraycopy(key, 0, spki, SPKI_PREFIX.length, key.length);
            PublicKey publicKey = KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(spki));
            Signature verifier = Signature.getInstance("Ed25519");
            verifier.initVerify(publicKey);
            verifier.update(payload);
            if (!verifier.verify(signature)) return null;
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(java.nio.ByteBuffer.wrap(payload))
                    .toString();
        } catch (GeneralSecurityException | CharacterCodingException | IllegalArgumentException failure) {
            return null;
        }
    }

    /** Same short label as the API: first 16 hex characters of SHA-256(raw public key). */
    static String keyId(byte[] publicKey) throws GeneralSecurityException {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(publicKey);
        StringBuilder hex = new StringBuilder();
        for (int i = 0; i < 8; i++) hex.append(String.format(java.util.Locale.ROOT, "%02x", digest[i] & 0xff));
        return hex.toString();
    }

    private static byte[] decode(String value) {
        if (value == null || value.isEmpty() || value.length() % 4 != 0 || !BASE64.matcher(value).matches())
            return null;
        return Base64.getDecoder().decode(value);
    }
}
