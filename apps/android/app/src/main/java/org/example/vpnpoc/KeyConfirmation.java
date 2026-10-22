package org.example.vpnpoc;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.amnezia.awg.crypto.Curve25519;
import org.amnezia.awg.crypto.Key;
import org.amnezia.awg.crypto.KeyPair;

/** Versioned key confirmation using upstream X25519 and platform HMAC-SHA256. */
final class KeyConfirmation {
    static String answer(KeyPair device, String ephemeralPublicKey, String transcript) throws Exception {
        if (!transcript.matches("project-vpn:key-confirmation:v1:[0-9a-f]{32}:[0-9a-f]{64}"))
            throw new IllegalArgumentException();
        byte[] shared = new byte[32];
        byte[] privateBytes = device.getPrivateKey().getBytes();
        try {
            Curve25519.eval(shared, 0, privateBytes, Key.fromBase64(ephemeralPublicKey).getBytes());
            int nonzero = 0;
            for (byte value : shared) nonzero |= value;
            if (nonzero == 0) throw new IllegalArgumentException();
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(shared, "HmacSHA256"));
            byte[] response = mac.doFinal(transcript.getBytes(StandardCharsets.US_ASCII));
            StringBuilder hex = new StringBuilder(64);
            for (byte value : response) hex.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
            Arrays.fill(response, (byte) 0);
            return hex.toString();
        } finally {
            Arrays.fill(privateBytes, (byte) 0);
            Arrays.fill(shared, (byte) 0);
        }
    }
}
