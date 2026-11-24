package org.example.vpnpoc;

import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.Drawable;
import android.net.Uri;
import android.net.VpnService;
import android.provider.Settings;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.WindowManager;
import java.io.ByteArrayOutputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import io.flutter.embedding.android.FlutterActivity;
import io.flutter.embedding.engine.FlutterEngine;
import io.flutter.plugin.common.MethodCall;
import io.flutter.plugin.common.MethodChannel;

/** Application-context bridge: no Activity references survive detachment. */
final class ClientBridge extends android.content.ContextWrapper {
    ClientBridge(ClientApplication app) { super(app); }
    private void runOnUiThread(Runnable action) {
        new android.os.Handler(android.os.Looper.getMainLooper()).post(action);
    }
    void handle(MethodCall call, MethodChannel.Result rawResult) {
        ClientApplication owner = (ClientApplication) getApplicationContext();
        if (owner.nativeTimedOut) {
            rawResult.error("native_operation_timeout", "VPN остановлен; перезапустите приложение", null);
            return;
        }
        final MethodChannel.Result result = owner.boundedResult(rawResult);
        if (java.util.Set.of("protectedRelayRead", "bootstrapComplete", "bootstrapFailed").contains(call.method)) {
            ((ClientApplication) getApplicationContext()).handleRecoveryCall(call, result); return;
        }
        if (call.method.equals("permission")) {
            result.success(VpnService.prepare(this) == null); return;
        }
        if (!java.util.Set.of("publicKey", "prove", "connect", "connectWindow", "advanceWindow",
                "sessionPulseAfter", "sessionIntent", "disconnect", "snapshot", "sessionRead", "sessionWrite", "pendingLoginRead",
                "pendingLoginWrite", "splitRead", "splitModeRead", "splitLastModeRead",
                "splitWrite", "splitDomainsRead", "apps", "nodeRead", "nodeWrite").contains(call.method)) {
            result.notImplemented(); return;
        }
        ClientApplication app = (ClientApplication) getApplicationContext();
        if (call.method.equals("sessionIntent") && Boolean.FALSE.equals(call.argument("desired"))) {
            app.cancelDesiredConnection();
        }
        if (call.method.equals("disconnect")) {
            app.cancelNative();
        }
        final long generation = app.generation.get();
        final long submittedAt = SystemClock.elapsedRealtime();
        app.worker.execute(() -> {
            try {
                // Session erasure must remain available even when native SDK init fails.
                boolean storage = call.method.equals("sessionRead") || call.method.equals("sessionWrite")
                        || call.method.equals("pendingLoginRead") || call.method.equals("pendingLoginWrite");
                boolean preference = call.method.equals("nodeRead") || call.method.equals("nodeWrite");
                if (storage && app.vault == null) throw new IllegalStateException();
                if (!storage && !preference && app.provider == null) throw new IllegalStateException();
                Object value = null;
                switch (call.method) {
                    case "sessionPulseAfter":
                        Number delay = call.argument("delayMs");
                        if (delay == null || delay.longValue() < 1000 || delay.longValue() > 30_000)
                            throw new IllegalArgumentException();
                        app.scheduleSessionPulse(delay.longValue());
                        break;
                    case "sessionIntent":
                        Boolean desired = call.argument("desired");
                        if (desired == null) throw new IllegalArgumentException();
                        app.setDesiredConnected(desired, generation);
                        break;
                    case "sessionRead":
                        String saved = app.vault.read("session");
                        value = saved == null ? null : jsonMap(
                                new org.json.JSONObject(saved), "origin", "refresh_token");
                        break;
                    case "sessionWrite":
                        java.util.Map<String, Object> record = call.argument("value");
                        app.vault.write("session", record == null ? null : new org.json.JSONObject(record).toString());
                        break;
                    case "pendingLoginRead":
                        String pendingLogin = app.vault.read("login");
                        value = pendingLogin == null ? null : jsonMap(
                                new org.json.JSONObject(pendingLogin),
                                "request_id", "verifier", "bot_url", "expires_at");
                        break;
                    case "pendingLoginWrite":
                        java.util.Map<String, Object> loginRecord = call.argument("value");
                        app.vault.write("login", loginRecord == null ? null : new org.json.JSONObject(loginRecord).toString());
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
                        value = installedLauncherApps(); break;
                    case "splitRead": value = selectedPackages(); break;
                    case "splitDomainsRead":
                        value = new java.util.ArrayList<>(getSharedPreferences("routing", MODE_PRIVATE)
                                .getStringSet("domains", java.util.Collections.emptySet()));
                        break;
                    case "splitModeRead": value = routingModeName(); break;
                    case "splitLastModeRead":
                        var prefs = getSharedPreferences("routing", MODE_PRIVATE);
                        String lastMode = prefs.getString("last_split_mode", null);
                        String currentMode = prefs.getString("mode", "exclude");
                        if (!"exclude".equals(lastMode) && !"include".equals(lastMode))
                            lastMode = "exclude".equals(currentMode) || "include".equals(currentMode)
                                    ? currentMode : "exclude";
                        value = lastMode;
                        break;
                    case "splitWrite":
                        if (app.provider.isUp() || app.deadline != 0) throw new IllegalStateException();
                        java.util.List<String> selected = call.argument("packages");
                        java.util.List<String> requestedDomains = call.argument("domains");
                        if (requestedDomains == null) requestedDomains = java.util.Collections.emptyList();
                        String mode = call.argument("mode");
                        if (mode == null) mode = "exclude";
                        if (selected == null || selected.size() > 63 || requestedDomains.size() > 128)
                            throw new IllegalArgumentException();
                        if (!mode.equals("all") && !mode.equals("exclude") && !mode.equals("include"))
                            throw new IllegalArgumentException();
                        for (String name : selected) {
                            if (!name.matches("[a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z][a-zA-Z0-9_]*)+")
                                    || name.equals(getPackageName())) throw new IllegalArgumentException();
                            getPackageManager().getApplicationInfo(name, 0);
                        }
                        var canonicalDomains = new java.util.TreeSet<String>();
                        // GoBackend has no domain routing: never persist rules it cannot enforce.
                        if (!app.provider.capabilities().contains("domain-routing.v1")) requestedDomains = java.util.Collections.emptyList();
                        for (String domain : requestedDomains) {
                            DomainRule rule = DomainRule.parse(domain);
                            if (!canonicalDomains.add(rule.canonical()))
                                throw new IllegalArgumentException("domain_duplicate");
                        }
                        var routingPrefs = getSharedPreferences("routing", MODE_PRIVATE);
                        String priorMode = routingPrefs.getString("mode", "exclude");
                        String lastSplitMode = routingPrefs.getString("last_split_mode", null);
                        if (mode.equals("exclude") || mode.equals("include")) {
                            lastSplitMode = mode;
                        } else if (!"exclude".equals(lastSplitMode) && !"include".equals(lastSplitMode)) {
                            lastSplitMode = "exclude".equals(priorMode) || "include".equals(priorMode)
                                    ? priorMode : "exclude";
                        }
                        if (!routingPrefs.edit()
                                .putStringSet("selected", new java.util.HashSet<>(selected))
                                .putStringSet("domains", new java.util.HashSet<>(canonicalDomains))
                                .putString("mode", mode).putString("last_split_mode", lastSplitMode)
                                .remove("excluded").commit()) throw new IllegalStateException();
                        value = new java.util.ArrayList<>(canonicalDomains);
                        break;
                    case "advanceWindow":
                        if (generation != app.generation.get() || !app.provider.isUp()) throw new IllegalStateException();
                        app.window.advance(call.argument("connection"), call.argument("runtime"),
                            ((Number) call.argument("generation")).longValue(),
                            submittedAt + ((Number) call.argument("remainingMs")).longValue(), SystemClock.elapsedRealtime());
                        app.deadline = app.window.deadline;
                        app.armDeadline();
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
                                || VpnService.prepare(app) != null) throw new IllegalStateException();
                        long deadline = submittedAt + duration.longValue();
                        var config = app.routedConfig(call.argument("config"));
                        if (SystemClock.elapsedRealtime() >= deadline) throw new IllegalStateException();
                        if (renewable) app.window.start(call.argument("connection"), call.argument("runtime"),
                            ((Number) call.argument("generation")).longValue(), deadline, SystemClock.elapsedRealtime());
                        app.deadline = deadline;
                        app.armDeadline();
                        try {
                            app.startAuthorized(config);
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
                boolean nativeStart = call.method.equals("connect")
                        || call.method.equals("connectWindow");
                if (nativeStart) {
                    // Release diagnostics contain exception types only. Never log
                    // exception messages, profiles, endpoint values, or keys.
                    android.util.Log.e("DetourVpnNativeCall", "operation=" + call.method
                            + " failure_types=" + safeFailureTypes(failure));
                } else if ((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
                    android.util.Log.e("DetourVpnNativeCall", "operation=" + call.method
                            + " failure=" + failure.getClass().getSimpleName());
                }
                String code;
                if (nativeStart && app.provider == null
                        && "native_unavailable".equals(app.reason)) {
                    // Key/proof calls succeeded before start; the missing provider
                    // at this point means backend initialization did not complete.
                    code = "native_engine_unavailable";
                } else if (nativeStart) {
                    code = ClientApplication.nativeStartFailureCode(failure);
                } else {
                    code = failure instanceof IllegalArgumentException
                            && ("domain_invalid".equals(failure.getMessage())
                                || "domain_duplicate".equals(failure.getMessage()))
                            ? failure.getMessage() : "native_operation_failed";
                }
                String message = "domain_invalid".equals(code)
                        ? "Укажи домен или шаблон *.ru без URL и IP-адреса"
                        : "domain_duplicate".equals(code) ? "Такой домен уже добавлен" : "Операция VPN не выполнена";
                runOnUiThread(() -> result.error(code, message, null));
            }
        });
    }
    private static String safeFailureTypes(Throwable failure) {
        var seen = java.util.Collections.newSetFromMap(
                new java.util.IdentityHashMap<Throwable, Boolean>());
        var types = new java.util.ArrayList<String>();
        Throwable current = failure;
        while (current != null && types.size() < 4 && seen.add(current)) {
            String name = current.getClass().getSimpleName();
            types.add(name.matches("[A-Za-z0-9_]{1,48}") ? name : "Other");
            current = current.getCause();
        }
        return String.join(",", types);
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

    private java.util.ArrayList<java.util.Map<String, Object>> installedLauncherApps() {
        PackageManager manager = getPackageManager();
        var intent = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        var apps = new java.util.TreeMap<String, java.util.Map<String, Object>>();
        for (var entry : manager.queryIntentActivities(intent, 0)) {
            String packageName = entry.activityInfo.packageName;
            if (packageName.equals(getPackageName())) continue;
            ApplicationInfo application = entry.activityInfo.applicationInfo;
            var item = new java.util.HashMap<String, Object>();
            CharSequence label = manager.getApplicationLabel(application);
            item.put("packageName", packageName);
            item.put("label", label == null ? packageName : label.toString());
            item.put("icon", applicationIconBytes(manager, application));
            apps.putIfAbsent(packageName, item);
        }
        return new java.util.ArrayList<>(apps.values());
    }

    private byte[] applicationIconBytes(PackageManager manager, ApplicationInfo application) {
        Bitmap bitmap = null;
        try {
            Drawable icon = manager.getApplicationIcon(application);
            bitmap = Bitmap.createBitmap(96, 96, Bitmap.Config.ARGB_8888);
            icon.setBounds(0, 0, bitmap.getWidth(), bitmap.getHeight());
            icon.draw(new Canvas(bitmap));
            var output = new ByteArrayOutputStream();
            if (!bitmap.compress(Bitmap.CompressFormat.PNG, 100, output)) return null;
            return output.toByteArray();
        } catch (RuntimeException unavailableIcon) {
            return null;
        } finally {
            if (bitmap != null) bitmap.recycle();
        }
    }
    private static java.util.Map<String, Object> jsonMap(
            org.json.JSONObject record, String... fields) throws Exception {
        var value = new java.util.HashMap<String, Object>();
        for (String key : fields) value.put(key, record.getString(key));
        return value;
    }
}
