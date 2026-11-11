package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.Signature;
import java.util.Arrays;
import java.util.Base64;
import org.junit.Before;
import org.junit.Test;

/** TEST ONLY: ADR-107 manifest signature with throwaway keys made in memory (never stored). */
public class UpdateSignatureTest {
    private static final String MANIFEST =
            "{\"version_code\":30,\"version_name\":\"0.1.0\",\"apk_sha256\":\"" + "0".repeat(64)
                    + "\",\"release_notes_ru\":\"Исправления\"}";
    private String publicKey;
    private String keyId;
    private String payload;
    private String signature;
    private KeyPair other;

    private static String raw(KeyPair pair) {
        byte[] spki = pair.getPublic().getEncoded();
        return Base64.getEncoder().encodeToString(Arrays.copyOfRange(spki, spki.length - 32, spki.length));
    }

    private static String sign(KeyPair pair, byte[] data) throws Exception {
        Signature signer = Signature.getInstance("Ed25519");
        signer.initSign(pair.getPrivate());
        signer.update(data);
        return Base64.getEncoder().encodeToString(signer.sign());
    }

    @Before
    public void setUp() throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("Ed25519");
        KeyPair pair = generator.generateKeyPair();
        other = generator.generateKeyPair();
        publicKey = raw(pair);
        keyId = UpdateSignature.keyId(Base64.getDecoder().decode(publicKey));
        byte[] data = MANIFEST.getBytes(StandardCharsets.UTF_8);
        payload = Base64.getEncoder().encodeToString(data);
        signature = sign(pair, data);
    }

    @Test
    public void validSignatureReturnsTheExactPayload() {
        assertEquals(MANIFEST, UpdateSignature.verify(publicKey, payload, signature, keyId));
    }

    @Test
    public void tamperedManifestIsRefused() {
        String tampered = Base64.getEncoder().encodeToString(
                MANIFEST.replace("\"version_code\":30", "\"version_code\":31").getBytes(StandardCharsets.UTF_8));
        assertNull(UpdateSignature.verify(publicKey, tampered, signature, keyId));
        byte[] flipped = Base64.getDecoder().decode(signature);
        flipped[0] ^= 1;
        assertNull(UpdateSignature.verify(publicKey, payload, Base64.getEncoder().encodeToString(flipped), keyId));
    }

    @Test
    public void wrongKeyIsRefused() throws Exception {
        String otherKey = raw(other);
        String otherId = UpdateSignature.keyId(Base64.getDecoder().decode(otherKey));
        assertNull(UpdateSignature.verify(otherKey, payload, signature, otherId));
        // A signature by another key with the app's key id is refused too.
        String foreign = sign(other, MANIFEST.getBytes(StandardCharsets.UTF_8));
        assertNull(UpdateSignature.verify(publicKey, payload, foreign, keyId));
        assertNull(UpdateSignature.verify(publicKey, payload, signature, otherId));
    }

    @Test
    public void missingKeyAndMalformedInputAreRefused() {
        assertNull(UpdateSignature.verify(UpdateSignature.PUBLIC_KEY_B64, payload, signature, keyId));
        assertNull(UpdateSignature.verify(null, payload, signature, keyId));
        assertNull(UpdateSignature.verify(publicKey, null, signature, keyId));
        assertNull(UpdateSignature.verify(publicKey, payload, "not base64!", keyId));
        assertNull(UpdateSignature.verify(publicKey, payload, signature, null));
        assertNull(UpdateSignature.verify(publicKey, payload.substring(1), signature, keyId));
        assertNull(UpdateSignature.verify("AAAA", payload, signature, keyId));
    }

    @Test
    public void platformEd25519IsRequired() {
        // Android ships Ed25519 in java.security from API 33; older devices get no update.
        assertEquals(33, UpdateSignature.MIN_API);
    }
}
