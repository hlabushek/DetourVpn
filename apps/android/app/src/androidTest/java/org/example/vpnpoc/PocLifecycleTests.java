package org.example.vpnpoc;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.net.VpnService;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityNodeInfo;
import java.io.File;
import java.net.Socket;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

/** TEST APK ONLY. Real API/native tunnel; token arrives in private debug storage via ADB.
 * Loopback HTTP is carried by authorized ADB reverse, never a public plaintext endpoint.
 * No token, private key, profile, raw response or exception is emitted.
 */
@android.annotation.TargetApi(33)
final class PocLifecycleTests {
    private final Instrumentation host;
    private final boolean expiry;
    private final boolean restart;
    private PocApplication app;
    private String token;
    private String tunnelId;
    private File resultFile;
    private final JSONObject facts = new JSONObject();
    private final org.json.JSONArray controlEvents = new org.json.JSONArray();
    private String stage = "initialize";
    private boolean localStartupOnly;
    PocLifecycleTests(Instrumentation host, boolean expiry, boolean restart) {
        this.host = host; this.expiry = expiry; this.restart = restart;
    }

    private JSONObject request(String method, String path, JSONObject body) throws Exception {
        byte[] payload = body == null ? new byte[0] : body.toString().getBytes(StandardCharsets.UTF_8);
        try (Socket socket = new Socket()) {
            socket.connect(new java.net.InetSocketAddress("127.0.0.1", 18766), 3000);
            socket.setSoTimeout(5000);
            String headers = method + " " + path + " HTTP/1.1\r\nHost: localhost\r\n"
                + "Authorization: Bearer " + token + "\r\nContent-Type: application/json\r\n"
                + "Content-Length: " + payload.length + "\r\nConnection: close\r\n\r\n";
            socket.getOutputStream().write(headers.getBytes(StandardCharsets.US_ASCII));
            socket.getOutputStream().write(payload); socket.getOutputStream().flush();
            byte[] raw = socket.getInputStream().readNBytes(32769);
            if (raw.length > 32768) throw new IllegalStateException();
            String text = new String(raw, StandardCharsets.UTF_8);
            int split = text.indexOf("\r\n\r\n");
            if (split < 0) throw new IllegalStateException();
            String first = text.substring(0, text.indexOf("\r\n"));
            int status = Integer.parseInt(first.split(" ")[1]);
            JSONObject response = new JSONObject(text.substring(split + 4));
            facts.put("last_http_status", status);
            if (status >= 400) {
                String code = response.optJSONObject("error") == null ? "unexpected_error"
                    : response.getJSONObject("error").optString("code", "unexpected_error");
                // Fixed allowlist only: no raw responses or server-provided text in evidence.
                facts.put("api_error", java.util.Set.of("tunnel_node_unavailable", "tunnel_profile_unavailable",
                    "device_key_proof_required", "device_key_proof_invalid", "insufficient_quota",
                    "session_device_mismatch", "tunnel_node_capacity", "tunnel_config_not_ready",
                    "rate_limited", "device_revoked", "tunnel_exists").contains(code) ? code : "unexpected_error");
            } else facts.remove("api_error");
            if (status != 200 && status != 202 && status != 409) throw new IllegalStateException();
            response.put("http_status", status);
            return response;
        }
    }
    private void status(String value) throws Exception {
        stage = value; facts.put("stage", stage);
        facts.put("control_events", controlEvents);
        Files.write(resultFile.toPath(), facts.toString().getBytes(StandardCharsets.UTF_8));
    }
    private void controlEvent(String event, String phase) throws Exception {
        controlEvents.put(event);
        status(phase);
    }
    private String egress() {
        HttpsURLConnection connection = null;
        try {
            facts.remove("egress_http_status");
            facts.put("egress_result", "unavailable");
            connection = (HttpsURLConnection) new URL("https://api.ipify.org").openConnection();
            // A distinct platform TLS factory prevents reuse of sockets opened before
            // a VPN route change. Keep default trust managers and hostname verification.
            javax.net.ssl.SSLContext tls = javax.net.ssl.SSLContext.getInstance("TLS");
            tls.init(null, null, null);
            connection.setSSLSocketFactory(tls.getSocketFactory());
            connection.setConnectTimeout(3000); connection.setReadTimeout(3000);
            connection.setInstanceFollowRedirects(false); connection.setUseCaches(false);
            connection.setRequestProperty("Connection", "close");
            facts.put("egress_http_status", connection.getResponseCode());
            if (connection.getResponseCode() != 200) return "";
            try (var stream = connection.getInputStream()) {
                String value = new String(stream.readNBytes(80), StandardCharsets.US_ASCII).trim();
                if (!value.matches("[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+")) return "";
                facts.put("egress_result", value.equals("45.10.166.106") ? "staging" : "other");
                return value;
            }
        } catch (Exception ignored) { return ""; }
        finally { if (connection != null) connection.disconnect(); }
    }
    private void check(boolean value) { if (!value) throw new IllegalStateException(); }
    private boolean up() throws Exception {
        return app.worker.submit(() -> app.provider.isUp()).get(5, TimeUnit.SECONDS);
    }
    private boolean awaitNetworkRestore() throws Exception {
        // Native DOWN precedes asynchronous VpnService/netd route and DNS teardown.
        // Require a fresh, normally verified HTTPS response over the restored route.
        long deadline = SystemClock.elapsedRealtime() + 20000;
        do {
            boolean down = !up();
            facts.put("native_down_after_disconnect", down);
            if (down) {
                String restored = egress();
                if (!restored.isEmpty() && !restored.equals("45.10.166.106")) return true;
            }
            SystemClock.sleep(250);
        } while (SystemClock.elapsedRealtime() < deadline);
        return false;
    }
    private void connect(String profile) throws Exception {
        status("profile_parse");
        var parsed = Profile.parse(profile, app.key);
        facts.put("profile_parsed", true);
        status("native_activity");
        MainActivity activity = (MainActivity) host.startActivitySync(new Intent(host.getTargetContext(),
            MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        host.runOnMainSync(() -> activity.getWindow().addFlags(
            android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON));
        var pending = MainActivity.class.getDeclaredField("pending"); pending.setAccessible(true);
        facts.put("permission_required", VpnService.prepare(activity) != null);
        status("native_start");
        java.util.concurrent.atomic.AtomicBoolean clicked = new java.util.concurrent.atomic.AtomicBoolean();
        host.runOnMainSync(() -> {
            try { pending.set(activity, parsed); } catch (Exception failure) { throw new AssertionError(); }
            android.widget.LinearLayout layout = (android.widget.LinearLayout) ((android.view.ViewGroup)
                activity.findViewById(android.R.id.content)).getChildAt(0);
            for (int i=0; i<layout.getChildCount(); i++)
                if (layout.getChildAt(i) instanceof android.widget.Button button
                    && button.getText().toString().equals("Подключить")) {
                    clicked.set(true); button.performClick();
                }
        });
        facts.put("connect_button_clicked", clicked.get());
        for (int i=0; i<40; i++) {
            AccessibilityNodeInfo root = host.getUiAutomation().getRootInActiveWindow();
            if (root != null && "com.android.vpndialogs".contentEquals(root.getPackageName()))
                for (var button : root.findAccessibilityNodeInfosByViewId("android:id/button1"))
                    if (button.isEnabled()) button.performAction(AccessibilityNodeInfo.ACTION_CLICK);
            if (up()) { facts.put("native_interface_up", true); status("native_up"); return; }
            SystemClock.sleep(250);
        }
        facts.put("ui_reported_failure", app.status.equals("Подключение не удалось · проверьте разрешение и staging profile"));
        facts.put("permission_still_required", VpnService.prepare(activity) != null);
        facts.put("keyguard_locked", activity.getSystemService(android.app.KeyguardManager.class).isKeyguardLocked());
        if (localStartupOnly) {
            try {
                app.worker.submit(() -> { app.provider.connect(parsed); return null; }).get(5, TimeUnit.SECONDS);
                facts.put("direct_native_start_succeeded", up());
            } catch (java.util.concurrent.ExecutionException failure) {
                Throwable cause = failure.getCause();
                facts.put("native_exception_type", cause == null ? "none" : cause.getClass().getName());
                facts.put("native_failure", cause instanceof org.amnezia.awg.backend.BackendException backend
                    ? backend.getReason().name() : cause instanceof SecurityException ? "security_exception"
                    : cause instanceof android.app.ForegroundServiceStartNotAllowedException ? "foreground_start_not_allowed"
                    : "unknown_native_failure");
            }
        }
        throw new IllegalStateException();
    }

    void runLocalStartupCheck() {
        // TEST APK ONLY. Loopback peer has no server. UP is not a VPN/handshake claim.
        host.waitForIdleSync();
        boolean passed = false;
        localStartupOnly = true;
        try {
            app = (PocApplication) host.getTargetContext().getApplicationContext();
            resultFile = new File(host.getTargetContext().getFilesDir(), "i012-result.json");
            check(app.provider != null && !up());
            status("local_baseline");
            check(!egress().isEmpty());
            String profile = "[Interface]\nAddress = 10.203.71.2/30\nDNS = 192.0.2.53\nMTU = 1280\n"
                + "Jc = 4\nJmin = 64\nJmax = 96\nS1 = 16\nS2 = 24\nS3 = 32\nS4 = 40\n"
                + "H1 = 101234\nH2 = 202345\nH3 = 303456\nH4 = 404567\n"
                + "HeaderProtectionKey = " + new org.amnezia.awg.crypto.KeyPair().getPublicKey().toBase64()
                + "\n[Peer]\nPublicKey = " + new org.amnezia.awg.crypto.KeyPair().getPublicKey().toBase64()
                + "\nEndpoint = 127.0.0.1:51820\nAllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n";
            connect(profile);
            check(up());
            status("native_disconnect");
            app.worker.submit(() -> { app.provider.disconnect(); return null; }).get(10, TimeUnit.SECONDS);
            status("network_restore");
            check(awaitNetworkRestore());
            facts.put("disconnect_restores_network", true);
            passed = true; status("complete");
        } catch (Throwable ignored) {
            try { facts.put("error", "local_native_startup_failed"); status(stage); } catch (Exception suppressed) { }
        } finally {
            try { if (app != null && app.provider != null) app.worker.submit(() -> {
                app.provider.disconnect(); return null; }).get(10, TimeUnit.SECONDS); } catch (Exception ignored) { }
        }
        Bundle result = new Bundle(); result.putString("lifecycle_facts", facts.toString());
        host.finish(passed ? Activity.RESULT_OK : Activity.RESULT_CANCELED, result);
    }

    void runFreshEgressCheck() {
        // TEST APK ONLY. Independent, normally verified TLS connections; no VPN changes.
        host.waitForIdleSync();
        boolean passed = false;
        try {
            resultFile = new File(host.getTargetContext().getFilesDir(), "i012-result.json");
            status("fresh_egress_probe");
            for (int i = 0; i < 2; i++) check(!egress().isEmpty());
            facts.put("fresh_egress_probes", true);
            passed = true; status("complete");
        } catch (Throwable ignored) {
            try { facts.put("error", "fresh_egress_probe_failed"); status(stage); } catch (Exception suppressed) { }
        }
        Bundle result = new Bundle(); result.putString("lifecycle_facts", facts.toString());
        host.finish(passed ? Activity.RESULT_OK : Activity.RESULT_CANCELED, result);
    }

    void runControlEventCheck() {
        // TEST APK ONLY. Deliberately overwrite the visible phase immediately.
        // No node operations: host acknowledges event delivery through a private file.
        host.waitForIdleSync();
        boolean passed = false;
        try {
            resultFile = new File(host.getTargetContext().getFilesDir(), "i012-result.json");
            File ack = new File(host.getTargetContext().getFilesDir(), "i012-local-control-ack");
            ack.delete();
            controlEvent("restart_agent", "waiting_agent_restart");
            status("waiting_revoke_ack");
            long deadline = SystemClock.elapsedRealtime() + 10000;
            while (!ack.exists() && SystemClock.elapsedRealtime() < deadline) SystemClock.sleep(50);
            check(ack.exists()); ack.delete();
            facts.put("durable_control_event_acknowledged", true);
            passed = true; status("complete");
        } catch (Throwable ignored) {
            try { facts.put("error", "control_event_delivery_failed"); status(stage); } catch (Exception suppressed) { }
        }
        Bundle result = new Bundle(); result.putString("lifecycle_facts", facts.toString());
        host.finish(passed ? Activity.RESULT_OK : Activity.RESULT_CANCELED, result);
    }
    void run() {
        host.waitForIdleSync();
        boolean passed = false;
        try {
            app = (PocApplication) host.getTargetContext().getApplicationContext();
            check(app.provider != null && !up());
            resultFile = new File(host.getTargetContext().getFilesDir(), "i012-result.json");
            File input = new File(host.getTargetContext().getFilesDir(), "i012-access-token");
            token = new String(Files.readAllBytes(input.toPath()), StandardCharsets.US_ASCII).trim(); input.delete();
            check(token.matches("[A-Za-z0-9_-]{43}"));
            String baseline = egress();
            check(!baseline.isEmpty() && !baseline.equals("45.10.166.106"));
            facts.put("baseline_egress_differs_from_staging", true);
            status("device_registration");
            JSONObject device = request("POST", "/api/v1/devices", new JSONObject().put("platform", "android")
                .put("key_type", "x25519").put("public_key", app.key.getPublicKey().toBase64()));
            JSONObject challenge = request("POST", "/api/v1/tunnels/key-challenge/" + device.getString("id"), new JSONObject());
            String answer = KeyConfirmation.answer(app.key, challenge.getString("ephemeral_public_key"), challenge.getString("transcript"));
            status("tunnel_request");
            JSONObject tunnel = request("POST", "/api/v1/tunnels", new JSONObject().put("device_id", device.getString("id"))
                .put("idempotency_key", UUID.randomUUID().toString()).put("proof_id", challenge.getString("id"))
                .put("proof_response", answer).put("budget_bytes", 16777216));
            tunnelId = tunnel.getString("id");
            check(tunnel.getString("state").equals("provision_pending"));
            facts.put("device_key_proof_accepted", true); status("waiting_node_ack");
            JSONObject config = null;
            for (int i=0; i<30; i++) {
                JSONObject candidate = request("GET", "/api/v1/tunnels/" + tunnelId + "/config", null);
                if (candidate.getInt("http_status") == 200) { config = candidate; break; }
                SystemClock.sleep(200);
            }
            check(config != null); facts.put("config_after_node_ack", true); status("native_connect");
            connect(config.getString("config"));
            status("staging_egress");
            boolean egress = false;
            for (int i=0; i<4 && !egress; i++) { egress = egress().equals("45.10.166.106"); if (!egress) SystemClock.sleep(500); }
            check(egress); facts.put("native_up_and_staging_egress", true);
            status("fresh_handshake");
            long handshake = app.worker.submit(() -> app.provider.lastHandshakeSeconds()).get(5, TimeUnit.SECONDS);
            check(handshake > 0 && Math.abs(System.currentTimeMillis()/1000 - handshake) < 60);
            facts.put("fresh_handshake", true);
            if (expiry) {
                controlEvent("pause_control", "waiting_lease_expiry");
                long deadline = java.time.Instant.parse(config.getString("expires_at")).toEpochMilli();
                SystemClock.sleep(Math.max(0, deadline - System.currentTimeMillis()) + 2500);
                check(up() && egress().isEmpty());
                facts.put("lease_expiry_stops_traffic", true);
                controlEvent("restore_control", "lease_cutoff_without_control");
            } else if (restart) {
                long deadline = java.time.Instant.parse(config.getString("expires_at")).toEpochMilli();
                check(System.currentTimeMillis() + 15000 < deadline);
                controlEvent("restart_agent", "waiting_agent_restart");
            } else {
                long deadline = java.time.Instant.parse(config.getString("expires_at")).toEpochMilli();
                status("revoke_precondition");
                facts.put("lease_valid_before_revoke", System.currentTimeMillis() + 15000 < deadline);
                check(facts.getBoolean("lease_valid_before_revoke"));
                String before = request("GET", "/api/v1/tunnels/" + tunnelId, null).getString("state");
                check(before.equals("active"));
                status("api_revoke");
                String response = request("DELETE", "/api/v1/tunnels/" + tunnelId, null).getString("state");
                facts.put("revoke_response_state", response);
                check(response.equals("revoke_pending") || response.equals("revoked"));
            }
            status("waiting_revoke_ack");
            for (int i=0; i<100; i++) {
                JSONObject state = request("GET", "/api/v1/tunnels/" + tunnelId, null);
                facts.put("last_tunnel_state", state.getString("state"));
                if (state.getString("state").equals("revoked")) break;
                SystemClock.sleep(200);
            }
            check(request("GET", "/api/v1/tunnels/" + tunnelId, null).getString("state").equals("revoked"));
            status("native_state_at_cutoff");
            facts.put("native_up_at_cutoff", up());
            check(facts.getBoolean("native_up_at_cutoff"));
            status("traffic_cutoff");
            check(egress().isEmpty());
            status("cutoff_before_expiry");
            if (!expiry) check(System.currentTimeMillis() + 1000 < java.time.Instant.parse(config.getString("expires_at")).toEpochMilli());
            facts.put(expiry ? "lease_expiry_stops_traffic" : restart ? "agent_restart_stops_traffic" : "api_revoke_stops_traffic", true);
            status("quota_settlement");
            JSONObject quota = request("GET", "/api/v1/quota", null);
            check(quota.getLong("reserved_bytes") == 0 && quota.getLong("used_bytes") > 0);
            facts.put("quota_settled_once", true);
            status("native_disconnect");
            app.worker.submit(() -> { app.provider.disconnect(); return null; }).get(10, TimeUnit.SECONDS);
            status("network_restore");
            check(awaitNetworkRestore());
            facts.put("disconnect_restores_network", true);
            passed = true; status("complete");
        } catch (Throwable ignored) {
            try { facts.put("error", "lifecycle_acceptance_failed"); status(stage); } catch (Exception suppressed) { }
        } finally {
            try { if (app != null && app.provider != null) app.worker.submit(() -> {
                app.provider.disconnect(); return null; }).get(10, TimeUnit.SECONDS); } catch (Exception ignored) { }
            try { if (tunnelId != null) request("DELETE", "/api/v1/tunnels/" + tunnelId, null); } catch (Exception ignored) { }
            token = null;
        }
        Bundle result = new Bundle(); result.putString("lifecycle_facts", facts.toString());
        host.finish(passed ? Activity.RESULT_OK : Activity.RESULT_CANCELED, result);
    }
}
