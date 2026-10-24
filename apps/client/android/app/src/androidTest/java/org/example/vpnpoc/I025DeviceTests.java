package org.example.vpnpoc;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.net.VpnService;
import android.os.Bundle;
import android.os.Process;
import android.view.KeyEvent;
import io.flutter.plugin.common.MethodCall;
import io.flutter.plugin.common.MethodChannel;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

/** TEST APK ONLY. Fixed phases on a fresh, disposable installation; no WAN/native tunnel. */
public final class I025DeviceTests extends Instrumentation {
    private String phase;
    private String stage = "startup";
    private final Bundle facts = new Bundle();
    private ClientApplication app;
    @Override public void onCreate(Bundle args) {
        super.onCreate(args); phase = args.getString("phase", "invalid"); start();
    }
    @Override public void onStart() {
        try {
            app = (ClientApplication) getTargetContext().getApplicationContext();
            app.worker.submit(() -> {
                try { runPhase(); } catch (Exception failure) { throw new IllegalStateException("i025_native_check_failed"); }
            }).get(40, TimeUnit.SECONDS);
            if (phase.equals("permission")) permission();
            facts.putInt("process_id", Process.myPid());
            facts.putBoolean("passed", true);
            facts.putBoolean("tunnel_or_network_acceptance", false);
            finish(Activity.RESULT_OK, facts);
        } catch (Throwable ignored) {
            facts.clear(); facts.putBoolean("passed", false); facts.putString("failed_at", stage);
            finish(Activity.RESULT_CANCELED, facts);
        }
    }
    private void check(boolean value) { if (!value) throw new IllegalStateException(); }
    private File cipher(String name) { return new File(app.getNoBackupFilesDir(), name + ".vault"); }
    private File marker() { return new File(app.getCacheDir(), "i025-test-reference.json"); }
    private String fingerprint() throws Exception {
        return android.util.Base64.encodeToString(MessageDigest.getInstance("SHA-256")
            .digest(app.key.getPublicKey().toBase64().getBytes(StandardCharsets.US_ASCII)), android.util.Base64.NO_WRAP);
    }
    private void fact(String name) { facts.putBoolean(name, true); }
    private void runPhase() throws Exception {
        stage = phase;
        SecureVault vault = new SecureVault(app);
        if (phase.equals("verify_device_corruption")) {
            check(app.provider == null && app.key == null);
            byte[] corrupted = Files.readAllBytes(cipher("device").toPath());
            boolean rejected = false;
            try { vault.device(); } catch (Exception expected) { rejected = true; }
            check(rejected && Arrays.equals(corrupted, Files.readAllBytes(cipher("device").toPath())));
            Files.write(cipher("device").toPath(), Files.readAllBytes(new File(app.getCacheDir(), "i025-device-backup.ciphertext").toPath()));
            fact("corrupt_device_fails_closed_without_identity_replacement"); return;
        }
        check(app.provider != null && app.key != null && !app.provider.isUp());
        check(app.deadline == 0 && ((Boolean) app.snapshot().get("up")).equals(false));
        switch (phase) {
            case "seed":
                check(!marker().exists() && vault.read("session") == null);
                JSONObject reference = new JSONObject().put("fingerprint", fingerprint());
                Files.write(marker().toPath(), reference.toString().getBytes(StandardCharsets.UTF_8));
                String record = new JSONObject().put("origin", "https://i025.invalid")
                    .put("refresh_token", "TEST-ONLY-I025-NOT-A-REAL-CREDENTIAL").toString();
                vault.write("session", record);
                byte[] first = Files.readAllBytes(cipher("session").toPath());
                vault.write("session", record);
                byte[] second = Files.readAllBytes(cipher("session").toPath());
                check(!Arrays.equals(first, second) && record.equals(vault.read("session")));
                check(!new String(second, StandardCharsets.ISO_8859_1).contains("TEST-ONLY-I025"));
                check(!new String(Files.readAllBytes(cipher("device").toPath()), StandardCharsets.ISO_8859_1)
                    .contains(app.key.getPrivateKey().toBase64()));
                check((app.getApplicationInfo().flags & ApplicationInfo.FLAG_ALLOW_BACKUP) == 0);
                check(app.getSharedPreferences("routing", 0).edit()
                    .putStringSet("excluded", java.util.Set.of("org.example.vpnpoc")).commit());
                fact("real_keystore_roundtrip_and_randomized_authenticated_encryption");
                fact("no_plaintext_marker_or_device_private_key_in_ciphertext");
                fact("backup_disabled_and_no_backup_storage"); break;
            case "restart":
            case "restored_identity":
                check(fingerprint().equals(new JSONObject(new String(Files.readAllBytes(marker().toPath()), StandardCharsets.UTF_8)).getString("fingerprint")));
                check(app.deadline == 0 && !app.provider.isUp());
                if (phase.equals("restart")) {
                    check(new JSONObject(vault.read("session")).getString("refresh_token").equals("TEST-ONLY-I025-NOT-A-REAL-CREDENTIAL"));
                    check(app.getSharedPreferences("routing", 0).getStringSet("excluded", java.util.Set.of())
                        .equals(java.util.Set.of("org.example.vpnpoc")));
                    fact("encrypted_session_and_split_settings_survive_process_restart");
                }
                fact("same_device_identity_after_process_restart_no_auto_vpn"); break;
            case "logout":
                vault.write("session", null);
                check(vault.read("session") == null && !cipher("session").exists());
                fact("durable_session_erasure"); break;
            case "verify_logout":
                check(vault.read("session") == null && !cipher("session").exists());
                fact("erasure_survives_process_restart"); break;
            case "corrupt_session":
                vault.write("session", "TEST-ONLY-I025-CORRUPTION-PROBE");
                byte[] data = Files.readAllBytes(cipher("session").toPath());
                data[data.length - 1] ^= 1; Files.write(cipher("session").toPath(), data); break;
            case "verify_session_corruption":
                boolean rejected = false;
                try { vault.read("session"); } catch (Exception expected) { rejected = true; }
                check(rejected); vault.write("session", null);
                fact("tampered_gcm_session_rejected"); break;
            case "corrupt_device":
                byte[] device = Files.readAllBytes(cipher("device").toPath());
                Files.write(new File(app.getCacheDir(), "i025-device-backup.ciphertext").toPath(), device);
                device[device.length - 1] ^= 1; Files.write(cipher("device").toPath(), device); break;
            case "permission":
                check(VpnService.prepare(app) != null); break;
            default: throw new IllegalArgumentException();
        }
    }
    private Object channel(ClientActivity activity, String method, Object args) throws Exception {
        var pending = new CompletableFuture<Object>();
        var handle = ClientActivity.class.getDeclaredMethod("handle", MethodCall.class, MethodChannel.Result.class);
        handle.setAccessible(true);
        runOnMainSync(() -> {
            try {
                handle.invoke(activity, new MethodCall(method, args), new MethodChannel.Result() {
                    @Override public void success(Object value) { pending.complete(value); }
                    @Override public void error(String code, String message, Object details) { pending.completeExceptionally(new IllegalStateException()); }
                    @Override public void notImplemented() { pending.completeExceptionally(new IllegalStateException()); }
                });
            } catch (Exception failure) { pending.completeExceptionally(new IllegalStateException()); }
        });
        return pending;
    }
    @SuppressWarnings("unchecked")
    private void permission() throws Exception {
        stage = "permission_dialog";
        ClientActivity activity = (ClientActivity) startActivitySync(new Intent(app, ClientActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        waitForIdleSync();
        var permission = (CompletableFuture<Object>) channel(activity, "permission", null);
        boolean dialog = false;
        for (int i = 0; i < 40; i++) {
            var root = getUiAutomation().getRootInActiveWindow();
            if (root != null && "com.android.vpndialogs".contentEquals(root.getPackageName())) { dialog = true; break; }
            android.os.SystemClock.sleep(100);
        }
        check(dialog); sendKeyDownUpSync(KeyEvent.KEYCODE_BACK); waitForIdleSync();
        check(Boolean.FALSE.equals(permission.get(10, TimeUnit.SECONDS)));
        check(VpnService.prepare(app) != null);
        ((CompletableFuture<Object>) channel(activity, "disconnect", null)).get(10, TimeUnit.SECONDS);
        check(!app.provider.isUp() && app.deadline == 0);
        fact("real_system_vpn_permission_denial_no_tunnel");
        runOnMainSync(activity::finish);
    }
}
