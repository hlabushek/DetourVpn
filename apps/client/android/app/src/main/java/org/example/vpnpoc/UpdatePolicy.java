package org.example.vpnpoc;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.security.MessageDigest;
import java.util.Locale;

/**
 * Decision and integrity rules for the in-app updater (ADR-107). Pure logic: no UI, no network,
 * no install. The installer, dialog and FileProvider are NOT implemented yet.
 */
final class UpdatePolicy {
    enum Decision { NONE, OPTIONAL, BLOCKING }

    private UpdatePolicy() {}

    /**
     * BLOCKING when the installed build is below the minimum supported build; OPTIONAL when a
     * newer build exists; NONE otherwise. A manifest older than the installed build never
     * produces an update (downgrades are refused).
     */
    static Decision decide(int installedCode, int latestCode, int minSupportedCode) {
        if (installedCode < 1 || latestCode < 1 || minSupportedCode < 1 || minSupportedCode > latestCode)
            throw new IllegalArgumentException("manifest_invalid");
        if (latestCode <= installedCode) return Decision.NONE;
        return installedCode < minSupportedCode ? Decision.BLOCKING : Decision.OPTIONAL;
    }

    /** Only HTTPS download URLs are accepted; no redirects to other schemes are followed. */
    static boolean isAcceptableUrl(String url) {
        return url != null && url.startsWith("https://") && url.length() <= 512
                && url.matches("https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/[!-~]*");
    }

    /** Compares the file's SHA-256 with the manifest value (lowercase hex, 64 chars). */
    static boolean sha256Matches(File file, String expectedHex) throws Exception {
        if (expectedHex == null || !expectedHex.matches("[0-9a-f]{64}")) return false;
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream in = new FileInputStream(file)) {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = in.read(buffer)) != -1) digest.update(buffer, 0, read);
        }
        StringBuilder hex = new StringBuilder(64);
        for (byte b : digest.digest()) hex.append(String.format(Locale.ROOT, "%02x", b & 0xff));
        // Constant-time comparison is not needed: the value is public (it is in the manifest).
        return hex.toString().equals(expectedHex);
    }
}
