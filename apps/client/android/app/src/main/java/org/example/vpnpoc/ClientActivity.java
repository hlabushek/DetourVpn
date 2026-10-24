package org.example.vpnpoc;

import android.content.Intent;
import android.net.Uri;
import android.net.VpnService;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.WindowManager;
import io.flutter.embedding.android.FlutterActivity;
import io.flutter.embedding.engine.FlutterEngine;
import io.flutter.plugin.common.MethodCall;
import io.flutter.plugin.common.MethodChannel;

/** Allowlisted method channel v1. No shell, key export, file import or logging. */
public final class ClientActivity extends FlutterActivity {
    private MethodChannel.Result permissionResult;
    private ClientApplication owner() { return (ClientApplication) getApplication(); }
    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
    }
    @Override public void configureFlutterEngine(FlutterEngine engine) {
        super.configureFlutterEngine(engine);
        new MethodChannel(engine.getDartExecutor().getBinaryMessenger(), "project.vpn/tunnel.v1")
            .setMethodCallHandler(this::handle);
    }
    private void handle(MethodCall call, MethodChannel.Result result) {
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
        if (!java.util.Set.of("publicKey", "prove", "connect", "connectWindow", "advanceWindow",
                "disconnect", "snapshot", "sessionRead", "sessionWrite", "splitRead", "splitWrite", "apps").contains(call.method)) {
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
                if (storage ? app.vault == null : app.provider == null) throw new IllegalStateException();
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
                    case "apps":
                        var intent = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
                        var names = new java.util.TreeSet<String>();
                        for (var entry : getPackageManager().queryIntentActivities(intent, 0))
                            if (!entry.activityInfo.packageName.equals(getPackageName())) names.add(entry.activityInfo.packageName);
                        value = new java.util.ArrayList<>(names); break;
                    case "splitRead": value = exclusions(); break;
                    case "splitWrite":
                        if (app.provider.isUp() || app.deadline != 0) throw new IllegalStateException();
                        java.util.List<String> selected = call.argument("packages");
                        if (selected == null || selected.size() > 63) throw new IllegalArgumentException();
                        for (String name : selected) {
                            if (!name.matches("[a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z][a-zA-Z0-9_]*)+")
                                    || name.equals(getPackageName())) throw new IllegalArgumentException();
                            getPackageManager().getApplicationInfo(name, 0);
                        }
                        if (!getSharedPreferences("routing", MODE_PRIVATE).edit()
                                .putStringSet("excluded", new java.util.HashSet<>(selected)).commit()) throw new IllegalStateException();
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
                        var excluded = exclusions();
                        if (renewable) excluded.add(getPackageName()); // Control-plane renewal bypasses its own budgeted VPN.
                        var config = renewable ? ClientProfile.parse(call.argument("config"), app.key, excluded)
                            : Profile.parse(call.argument("config"), app.key);
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
            } catch (Exception | LinkageError ignored) {
                runOnUiThread(() -> result.error("native_operation_failed", "Операция VPN не выполнена", null));
            }
        });
    }
    private java.util.ArrayList<String> exclusions() {
        return new java.util.ArrayList<>(getSharedPreferences("routing", MODE_PRIVATE)
            .getStringSet("excluded", java.util.Collections.emptySet()));
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
