package org.example.vpnpoc;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.AtomicFile;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.Arrays;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import org.amnezia.awg.crypto.Key;
import org.amnezia.awg.crypto.KeyPair;

/** Worker-only, app-private, backup-excluded authenticated encryption. No plaintext files. */
final class SecureVault {
    private final Context context;
    SecureVault(Context context) { this.context = context; }
    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        if (!store.containsAlias("detourvpn.v1")) {
            KeyGenerator generator = KeyGenerator.getInstance("AES", "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder("detourvpn.v1",
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setKeySize(256).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true).build());
            generator.generateKey();
        }
        return (SecretKey) store.getKey("detourvpn.v1", null);
    }
    private AtomicFile file(String name) {
        if (!java.util.Set.of("device", "session").contains(name)) throw new IllegalArgumentException();
        return new AtomicFile(new File(context.getNoBackupFilesDir(), name + ".vault"));
    }
    String read(String name) throws Exception {
        AtomicFile target = file(name);
        if (!target.getBaseFile().exists()) return null;
        if (target.getBaseFile().length() > 32768) throw new IllegalStateException();
        byte[] data = target.readFully();
        if (data.length < 29 || data[0] != 1) throw new IllegalStateException();
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Arrays.copyOfRange(data, 1, 13)));
        cipher.updateAAD((context.getPackageName() + ":" + name + ":v1").getBytes(StandardCharsets.UTF_8));
        byte[] plain = cipher.doFinal(data, 13, data.length - 13);
        try { return new String(plain, StandardCharsets.UTF_8); }
        finally { Arrays.fill(plain, (byte) 0); }
    }
    void write(String name, String value) throws Exception {
        AtomicFile target = file(name);
        if (value == null) {
            target.delete();
            if (target.getBaseFile().exists()) throw new IllegalStateException();
            return;
        }
        byte[] plain = value.getBytes(StandardCharsets.UTF_8);
        if (plain.length > 16384) throw new IllegalArgumentException();
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        cipher.updateAAD((context.getPackageName() + ":" + name + ":v1").getBytes(StandardCharsets.UTF_8));
        byte[] encrypted;
        try { encrypted = cipher.doFinal(plain); }
        finally { Arrays.fill(plain, (byte) 0); }
        FileOutputStream output = null;
        try {
            output = target.startWrite(); output.write(1); output.write(cipher.getIV());
            output.write(encrypted); target.finishWrite(output);
        } catch (Exception failure) { if (output != null) target.failWrite(output); throw failure; }
    }
    KeyPair device() throws Exception {
        String saved = read("device");
        if (saved != null) return new KeyPair(Key.fromBase64(saved));
        KeyPair pair = new KeyPair(); write("device", pair.getPrivateKey().toBase64()); return pair;
    }
}
