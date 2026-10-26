package org.example.vpnpoc;

import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.net.Uri;
import android.net.VpnService;
import android.provider.Settings;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.WindowManager;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import io.flutter.embedding.android.FlutterActivity;
import io.flutter.embedding.engine.FlutterEngine;
import io.flutter.plugin.common.MethodCall;
import io.flutter.plugin.common.MethodChannel;

/** Allowlisted method channel v1. No shell, key export, file import or logging. */
public final class ClientActivity extends FlutterActivity {
    private static final java.util.Set<String> I028_TEST_STAGES = java.util.Set.of(
        "start", "restore_real_account", "baseline_probe", "natural_sleep_connect",
        "natural_sleep_wait", "natural_sleep_wake", "natural_sleep_recovery",
        "complete", "failed_start", "failed_restore_real_account", "failed_baseline_probe",
        "failed_natural_sleep_connect", "failed_natural_sleep_wait", "failed_natural_sleep_wake",
        "failed_natural_sleep_recovery", "failed_cleanup"
    );
    private static final String I028_STAGE_FILE = "i028-natural-sleep-stage.txt";
    private static final String I028_DIAGNOSTICS_FILE = "i028-natural-sleep-diagnostics.jsonl";
    private static final long I028_DIAGNOSTICS_LIMIT = 32768;
    private static volatile String i028CurrentStage = "unknown";
    private MethodChannel.Result permissionResult;
    private ClientApplication owner() { return (ClientApplication) getApplication(); }
    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        recordI028Lifecycle("activity_created");
    }
    @Override protected void onStart() {
        super.onStart();
        recordI028Lifecycle("activity_started");
    }
    @Override protected void onResume() {
        super.onResume();
        recordI028Lifecycle("activity_resumed");
    }
    @Override protected void onPause() {
        recordI028Lifecycle("activity_paused");
        super.onPause();
    }
    @Override protected void onStop() {
        recordI028Lifecycle("activity_stopped");
        super.onStop();
    }
    @Override protected void onDestroy() {
        recordI028Lifecycle("activity_destroyed");
        super.onDestroy();
    }
    @Override public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        recordI028Lifecycle(hasFocus ? "window_focused" : "window_unfocused");
    }
    @Override public void configureFlutterEngine(FlutterEngine engine) {
        super.configureFlutterEngine(engine);
        new MethodChannel(engine.getDartExecutor().getBinaryMessenger(), "project.vpn/tunnel.v1")
            .setMethodCallHandler(this::handle);
    }
    private void handle(MethodCall call, MethodChannel.Result result) {
        if (call.method.equals("i028StageWrite")) {
            if ((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) == 0) {
                result.notImplemented(); return;
            }
            String value = call.argument("value");
            if (value == null || !I028_TEST_STAGES.contains(value)) {
                result.error("test_stage_invalid", "Недопустимый этап теста", null); return;
            }
            i028CurrentStage = value;
            try (FileOutputStream marker = new FileOutputStream(i028StageFile(), false)) {
                marker.write(value.getBytes(StandardCharsets.US_ASCII));
                appendI028Diagnostic("stage", null);
                result.success(null);
            } catch (java.io.IOException ignored) {
                result.error("test_stage_unavailable", "Не удалось сохранить этап теста", null);
            }
            return;
        }
        if (call.method.equals("i028TunnelSnapshot")) {
            if ((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) == 0) {
                result.notImplemented(); return;
            }
            Boolean up = call.argument("up");
            Boolean network = call.argument("network");
            Integer handshakeAge = call.argument("handshakeAge");
            Integer remainingMs = call.argument("remainingMs");
            if (up == null || network == null || handshakeAge == null || remainingMs == null
                    || handshakeAge < -1 || handshakeAge > 86400
                    || remainingMs < 0 || remainingMs > 3600000) {
                result.error("test_snapshot_invalid", "Недопустимое состояние тестового туннеля", null);
                return;
            }
            var snapshot = new org.json.JSONObject();
            try {
                snapshot.put("up", up);
                snapshot.put("network", network);
                snapshot.put("handshake_age_seconds", handshakeAge);
                snapshot.put("remaining_ms", remainingMs);
                appendI028Diagnostic("native_snapshot", snapshot);
                result.success(null);
            } catch (org.json.JSONException ignored) {
                result.error("test_snapshot_unavailable", "Не удалось сохранить состояние тестового туннеля", null);
            }
            return;
        }
        if (call.method.equals("permission")) {
            if (permissionResult != null) { result.success(false); return; }
            Intent intent = VpnService.prepare(this);
            if (intent == null) result.success(true);
            else { permissionResult = result; startActivityForResult(intent, 1301); }
            return;
        }
        if (call.method.equals("openLogin")) {
            try {
                Uri uri = Uri.parse((String) call.argument("url"));
                if (!"https".equals(uri.getScheme()) || !"t.me".equals(uri.getHost())
                        || uri.getUserInfo() != null || uri.getPort() != -1)
                    throw new IllegalArgumentException();
                startActivity(new Intent(Intent.ACTION_VIEW, uri)); result.success(null);
            } catch (RuntimeException ignored) { result.error("login_unavailable", "Не удалось открыть Telegram", null); }
            return;
        }
        if (call.method.equals("openVpnSettings")) {
            try {
                startActivity(new Intent(Settings.ACTION_VPN_SETTINGS));
                result.success(null);
            } catch (RuntimeException ignored) {
                result.error("vpn_settings_unavailable", "Не удалось открыть настройки VPN Android", null);
            }
            return;
        }
        if (!java.util.Set.of("publicKey", "prove", "connect", "connectWindow", "advanceWindow",
                "disconnect", "snapshot", "sessionRead", "sessionWrite", "splitRead", "splitModeRead",
                "splitWrite", "apps", "nodeRead", "nodeWrite").contains(call.method)) {
            result.notImplemented(); return;
        }
        ClientApplication app = owner();
        if (call.method.equals("disconnect")) {
            app.generation.incrementAndGet();
            if (permissionResult != null) { permissionResult.success(false); permissionResult = null; }
        }
        final long generation = app.generation.get();
        final long submittedAt = SystemClock.elapsedRealtime();
        app.worker.execute(() -> {
            try {
                // Session erasure must remain available even when native SDK init fails.
                boolean storage = call.method.equals("sessionRead") || call.method.equals("sessionWrite");
                boolean preference = call.method.equals("nodeRead") || call.method.equals("nodeWrite");
                if (storage && app.vault == null) throw new IllegalStateException();
                if (!storage && !preference && app.provider == null) throw new IllegalStateException();
                Object value = null;
                switch (call.method) {
                    case "sessionRead":
                        String saved = app.vault.read("session");
                        value = saved == null ? null : jsonMap(new org.json.JSONObject(saved));
                        break;
                    case "sessionWrite":
                        java.util.Map<String, Object> record = call.argument("value");
                        app.vault.write("session", record == null ? null : new org.json.JSONObject(record).toString());
                        break;
                    case "nodeRead":
                        value = getSharedPreferences("connection", MODE_PRIVATE).getString("selected_node", null);
                        break;
                    case "nodeWrite":
                        String selectedNode = call.argument("id");
                        if (selectedNode != null && !selectedNode.matches(
                                "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))
                            throw new IllegalArgumentException();
                        var nodePreferences = getSharedPreferences("connection", MODE_PRIVATE).edit();
                        if (selectedNode == null) nodePreferences.remove("selected_node");
                        else nodePreferences.putString("selected_node", selectedNode);
                        if (!nodePreferences.commit()) throw new IllegalStateException();
                        break;
                    case "apps":
                        var intent = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
                        var names = new java.util.TreeSet<String>();
                        for (var entry : getPackageManager().queryIntentActivities(intent, 0))
                            if (!entry.activityInfo.packageName.equals(getPackageName())) names.add(entry.activityInfo.packageName);
                        value = new java.util.ArrayList<>(names); break;
                    case "splitRead": value = selectedPackages(); break;
                    case "splitModeRead": value = routingModeName(); break;
                    case "splitWrite":
                        if (app.provider.isUp() || app.deadline != 0) throw new IllegalStateException();
                        java.util.List<String> selected = call.argument("packages");
                        String mode = call.argument("mode");
                        if (mode == null) mode = "exclude";
                        if (selected == null || selected.size() > 63) throw new IllegalArgumentException();
                        if (!mode.equals("exclude") && !mode.equals("include")) throw new IllegalArgumentException();
                        if (mode.equals("include") && selected.isEmpty()) throw new IllegalArgumentException();
                        for (String name : selected) {
                            if (!name.matches("[a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z][a-zA-Z0-9_]*)+")
                                    || name.equals(getPackageName())) throw new IllegalArgumentException();
                            getPackageManager().getApplicationInfo(name, 0);
                        }
                        if (!getSharedPreferences("routing", MODE_PRIVATE).edit()
                                .putStringSet("selected", new java.util.HashSet<>(selected))
                                .putString("mode", mode).remove("excluded").commit()) throw new IllegalStateException();
                        break;
                    case "advanceWindow":
                        if (generation != app.generation.get() || !app.provider.isUp()) throw new IllegalStateException();
                        app.window.advance(call.argument("connection"), call.argument("runtime"),
                            ((Number) call.argument("generation")).longValue(),
                            submittedAt + ((Number) call.argument("remainingMs")).longValue(), SystemClock.elapsedRealtime());
                        app.deadline = app.window.deadline;
                        break;
                    case "publicKey": value = app.key.getPublicKey().toBase64(); break;
                    case "prove": value = KeyConfirmation.answer(app.key,
                        call.argument("ephemeralKey"), call.argument("transcript")); break;
                    case "snapshot": value = app.snapshot(); break;
                    case "disconnect": app.stop(); break;
                    case "connect":
                    case "connectWindow":
                        boolean renewable = call.method.equals("connectWindow");
                        Number duration = call.argument("remainingMs");
                        if (duration == null || duration.longValue() <= 0 || duration.longValue() > (renewable ? 90000 : 60000)
                                || generation != app.generation.get() || app.provider.isUp()
                                || VpnService.prepare(this) != null) throw new IllegalStateException();
                        long deadline = submittedAt + duration.longValue();
                        var packagesToRoute = selectedPackages();
                        var routeMode = routingMode();
                        if (routeMode == ClientProfile.RoutingMode.EXCLUDE_SELECTED)
                            packagesToRoute.add(getPackageName()); // Control-plane requests stay outside the tunnel.
                        var config = ClientProfile.parse(call.argument("config"), app.key, packagesToRoute, routeMode);
                        if (SystemClock.elapsedRealtime() >= deadline) throw new IllegalStateException();
                        if (renewable) app.window.start(call.argument("connection"), call.argument("runtime"),
                            ((Number) call.argument("generation")).longValue(), deadline, SystemClock.elapsedRealtime());
                        app.deadline = deadline;
                        try {
                            app.provider.connect(config);
                            if (generation != app.generation.get() || SystemClock.elapsedRealtime() >= deadline) {
                                app.stop(); throw new IllegalStateException();
                            }
                            app.reason = "";
                        } catch (Exception failure) { app.stop(); throw failure; }
                        break;
                    default: throw new IllegalArgumentException();
                }
                final Object answer = value;
                runOnUiThread(() -> result.success(answer));
            } catch (Exception | LinkageError failure) {
                if ((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
                    android.util.Log.e("DetourVpnNativeCall", "operation=" + call.method
                            + " failure=" + failure.getClass().getSimpleName());
                }
                runOnUiThread(() -> result.error("native_operation_failed", "Операция VPN не выполнена", null));
            }
        });
    }
    private java.io.File i028StageFile() {
        return new java.io.File(getNoBackupFilesDir(), I028_STAGE_FILE);
    }
    private void recordI028Lifecycle(String event) {
        if ((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) == 0) return;
        java.io.File diagnostics = new java.io.File(getNoBackupFilesDir(), I028_DIAGNOSTICS_FILE);
        if (!diagnostics.isFile()) return;
        try (java.io.FileInputStream input = new java.io.FileInputStream(i028StageFile())) {
            byte[] stageBytes = new byte[128];
            int count = input.read(stageBytes);
            String persisted = count <= 0 ? "" : new String(stageBytes, 0, count, StandardCharsets.US_ASCII);
            if (I028_TEST_STAGES.contains(persisted)) i028CurrentStage = persisted;
        } catch (java.io.IOException ignored) { return; }
        appendI028Diagnostic(event, null);
    }
    private void appendI028Diagnostic(String event, org.json.JSONObject details) {
        java.io.File diagnostics = new java.io.File(getNoBackupFilesDir(), I028_DIAGNOSTICS_FILE);
        if (diagnostics.length() >= I028_DIAGNOSTICS_LIMIT) return;
        try (FileOutputStream stream = new FileOutputStream(diagnostics, true)) {
            var item = new org.json.JSONObject();
            item.put("event", event);
            item.put("stage", i028CurrentStage);
            item.put("elapsed_realtime_ms", SystemClock.elapsedRealtime());
            item.put("uptime_ms", SystemClock.uptimeMillis());
            item.put("pid", android.os.Process.myPid());
            if (details != null) item.put("native", details);
            stream.write((item.toString() + "\n").getBytes(StandardCharsets.UTF_8));
            stream.flush();
        } catch (java.io.IOException | org.json.JSONException ignored) {
            // Diagnostics are best-effort and must never change VPN/test behavior.
        }
    }
    private java.util.ArrayList<String> selectedPackages() {
        var preferences = getSharedPreferences("routing", MODE_PRIVATE);
        var selected = preferences.getStringSet("selected", null);
        if (selected == null) selected = preferences.getStringSet("excluded", java.util.Collections.emptySet());
        return new java.util.ArrayList<>(selected);
    }
    private String routingModeName() {
        return getSharedPreferences("routing", MODE_PRIVATE).getString("mode", "exclude");
    }
    private ClientProfile.RoutingMode routingMode() {
        return switch (routingModeName()) {
            case "exclude" -> ClientProfile.RoutingMode.EXCLUDE_SELECTED;
            case "include" -> ClientProfile.RoutingMode.INCLUDE_SELECTED;
            default -> throw new IllegalArgumentException();
        };
    }
    private static java.util.Map<String, Object> jsonMap(org.json.JSONObject record) throws Exception {
        var value = new java.util.HashMap<String, Object>();
        for (String key : java.util.List.of("origin", "refresh_token")) value.put(key, record.getString(key));
        return value;
    }
    @Override protected void onActivityResult(int request, int response, Intent data) {
        super.onActivityResult(request, response, data);
        if (request == 1301 && permissionResult != null) {
            permissionResult.success(response == RESULT_OK && VpnService.prepare(this) == null);
            permissionResult = null;
        }
    }
    @Override public void cleanUpFlutterEngine(FlutterEngine engine) {
        if (permissionResult != null) { permissionResult.success(false); permissionResult = null; }
        super.cleanUpFlutterEngine(engine);
    }
}
