package org.example.vpnpoc;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.Button;
import android.widget.LinearLayout;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import javax.net.ssl.HttpsURLConnection;
import java.net.URL;
import org.amnezia.awg.config.Config;
import org.json.JSONObject;

/** Explicit real-network instrumentation; never packaged in the product APK.
 * Profile enters only debug target-private storage over an authorized ADB pipe.
 * No private device key, profile, raw exception or remote response is emitted.
 */
@android.annotation.TargetApi(33)
public final class PocNetworkTests {
    private final Instrumentation host;
    private PocApplication app;
    private MainActivity activity;
    private File directory;
    private final Map<String, Object> facts = new LinkedHashMap<>();
    private String stage = "starting";

    PocNetworkTests(Instrumentation host) { this.host = host; }

    private File file(String name) { return new File(directory, "i011-" + name); }

    private void status(String value) throws Exception {
        stage = value;
        JSONObject row = new JSONObject(facts);
        row.put("stage", stage);
        File temporary = file("result-next.json");
        Files.write(temporary.toPath(), row.toString().getBytes(StandardCharsets.UTF_8));
        Files.move(temporary.toPath(), file("result.json").toPath(),
            java.nio.file.StandardCopyOption.REPLACE_EXISTING);
    }

    private String ip(boolean ipv6) {
        HttpsURLConnection connection = null;
        try {
            connection = (HttpsURLConnection) new URL(
                ipv6 ? "https://api6.ipify.org" : "https://api.ipify.org").openConnection();
            connection.setConnectTimeout(4000);
            connection.setReadTimeout(4000);
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            connection.setRequestProperty("Connection", "close");
            if (connection.getResponseCode() != 200) return "";
            try (var stream = connection.getInputStream()) {
                String value = new String(stream.readNBytes(80), StandardCharsets.US_ASCII).trim();
                return value.matches(ipv6 ? "[0-9a-fA-F:]+" : "[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+")
                    ? value : "";
            }
        } catch (Exception ignored) { return ""; }
        finally { if (connection != null) connection.disconnect(); }
    }

    private boolean up() throws Exception {
        return app.worker.submit(() -> app.provider.isUp()).get(5, TimeUnit.SECONDS);
    }

    private String httpsText(String url, int limit) throws Exception {
        HttpsURLConnection connection = (HttpsURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(4000); connection.setReadTimeout(4000);
        connection.setInstanceFollowRedirects(false); connection.setUseCaches(false);
        connection.setRequestProperty("Connection", "close");
        try {
            if (connection.getResponseCode() != 200) throw new IllegalStateException();
            try (var stream = connection.getInputStream()) {
                return new String(stream.readNBytes(limit), StandardCharsets.UTF_8);
            }
        } finally { connection.disconnect(); }
    }

    private void dnsProbe() {
        facts.put("dns_probe_completed", false);
        facts.put("dns_expected_resolvers_only", false);
        try {
            // Public test protocol documented by macvk/dnsleaktest v1.4.
            // Only generated test names, no browsing history; emit no names/IPs.
            String id = httpsText("https://bash.ws/id", 64).trim();
            if (!id.matches("[A-Za-z0-9]{1,32}")) return;
            for (int i = 1; i <= 3; i++) {
                try { java.net.InetAddress.getAllByName(i + "." + id + ".bash.ws"); }
                catch (Exception expected) { /* NXDOMAIN still reaches the authoritative test. */ }
            }
            SystemClock.sleep(1000);
            var rows = new org.json.JSONArray(httpsText("https://bash.ws/dnsleak/test/" + id + "?json", 16384));
            int observed = 0;
            boolean expected = true;
            for (int i = 0; i < rows.length(); i++) {
                JSONObject row = rows.getJSONObject(i);
                if (!row.optString("type").equals("dns")) continue;
                observed++;
                String asn = row.optString("asn").toLowerCase(java.util.Locale.ROOT);
                expected &= asn.contains("cloudflare") || asn.contains("13335");
            }
            facts.put("dns_probe_completed", observed > 0);
            facts.put("dns_expected_resolvers_only", observed > 0 && expected);
        } catch (Exception ignored) { /* Unavailable external test is not a passing leak test. */ }
    }

    private void connect() throws Exception {
        File profileFile = file("profile.conf");
        String text = new String(Files.readAllBytes(profileFile.toPath()), StandardCharsets.UTF_8);
        profileFile.delete();
        final Config parsed = Profile.parse(text, app.key);
        host.waitForIdleSync();
        var pending = MainActivity.class.getDeclaredField("pending");
        pending.setAccessible(true);
        host.runOnMainSync(() -> {
            try { pending.set(activity, parsed); }
            catch (Exception ignored) { throw new AssertionError(); }
            LinearLayout layout = (LinearLayout) ((android.view.ViewGroup)
                activity.findViewById(android.R.id.content)).getChildAt(0);
            for (int i = 0; i < layout.getChildCount(); i++)
                if (layout.getChildAt(i) instanceof Button button
                    && button.getText().toString().equals("Подключить")) button.performClick();
        });
        for (int i = 0; i < 60; i++) {
            AccessibilityNodeInfo root = host.getUiAutomation().getRootInActiveWindow();
            if (root != null && "com.android.vpndialogs".contentEquals(root.getPackageName())) {
                var buttons = root.findAccessibilityNodeInfosByViewId("android:id/button1");
                for (var button : buttons)
                    if (button.isEnabled()) button.performAction(AccessibilityNodeInfo.ACTION_CLICK);
            }
            if (up()) break;
            SystemClock.sleep(250);
        }
        facts.put("native_up", up());
    }

    private void probe() throws Exception {
        facts.put("native_up", up());
        long handshake = app.worker.submit(() -> app.provider.lastHandshakeSeconds()).get(5, TimeUnit.SECONDS);
        long now = System.currentTimeMillis() / 1000;
        facts.put("fresh_handshake", handshake > 0 && now - handshake >= 0 && now - handshake < 180);
        String address = ip(false);
        facts.put("ipv4_probe_succeeded", !address.isEmpty());
        facts.put("egress_matches_staging", address.equals("45.10.166.106"));
        facts.put("ipv6_probe_succeeded", !ip(true).isEmpty());
        facts.put("dns_leak_verified", false);
    }

    public void run() {
        host.waitForIdleSync();
        boolean passed = false;
        try {
            app = (PocApplication) host.getTargetContext().getApplicationContext();
            directory = host.getTargetContext().getFilesDir();
            if (app.provider == null || up()) throw new IllegalStateException();
            activity = (MainActivity) host.startActivitySync(new Intent(
                host.getTargetContext(), MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            host.runOnMainSync(() -> activity.getWindow().addFlags(
                android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON));
            for (String stale : new String[]{"command", "profile.conf", "profile-next.conf", "result.json"})
                file(stale).delete();
            Files.write(file("public-key").toPath(), app.key.getPublicKey().toBase64().getBytes(StandardCharsets.US_ASCII));
            String baseline = ip(false);
            facts.put("baseline_ipv4_succeeded", !baseline.isEmpty());
            facts.put("baseline_matches_staging", baseline.equals("45.10.166.106"));
            facts.put("baseline_ipv6_succeeded", !ip(true).isEmpty());
            status("waiting_profile");
            long deadline = SystemClock.elapsedRealtime() + 900_000;
            while (SystemClock.elapsedRealtime() < deadline) {
                if (!file("command").exists()) { SystemClock.sleep(250); continue; }
                String command = new String(Files.readAllBytes(file("command").toPath()), StandardCharsets.US_ASCII).trim();
                file("command").delete();
                status("running_" + (command.matches("connect|probe|dns|disconnect|finish") ? command : "invalid"));
                switch (command) {
                    case "connect": connect(); probe(); status("connected_probe_complete"); break;
                    case "probe": probe(); status("probe_complete"); break;
                    case "dns": dnsProbe(); status("dns_probe_complete"); break;
                    case "disconnect":
                        app.worker.submit(() -> { app.provider.disconnect(); return null; }).get(10, TimeUnit.SECONDS);
                        facts.put("native_up", up());
                        facts.put("disconnect_native_down", !up());
                        SystemClock.sleep(3000); // Allow Android to publish the restored default network.
                        probe(); status("disconnected_probe_complete"); break;
                    case "finish": passed = true; status("complete"); return;
                    default: throw new IllegalArgumentException();
                }
            }
            throw new IllegalStateException();
        } catch (Throwable ignored) {
            try { facts.put("failed_at", stage); status("failed"); } catch (Exception cleanupIgnored) { }
        } finally {
            if (app != null && app.provider != null) {
                try { app.worker.submit(() -> { app.provider.disconnect(); return null; }).get(10, TimeUnit.SECONDS); }
                catch (Exception ignored) { passed = false; }
            }
            if (directory != null) {
                file("profile.conf").delete(); file("profile-next.conf").delete(); file("command").delete();
            }
            if (activity != null) host.runOnMainSync(activity::finish);
            Bundle result = new Bundle();
            result.putBoolean("network_harness_finished", passed);
            result.putString("stage", stage);
            host.finish(passed ? Activity.RESULT_OK : Activity.RESULT_CANCELED, result);
        }
    }
}
