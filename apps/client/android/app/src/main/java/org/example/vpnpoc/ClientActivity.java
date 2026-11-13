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
        // FLAG_SECURE intentionally not set: screenshots and recents previews are allowed (ADR-103).
        // ADR-107: a cold start drops an update APK left by an earlier attempt.
        if (state == null) AppUpdateInstaller.discardStale(this);
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
        owner().uiEngine(true); // ADR-125: this engine's controller owns reconnects while attached.
        new MethodChannel(engine.getDartExecutor().getBinaryMessenger(), "project.vpn/tunnel.v1")
            .setMethodCallHandler(this::handle);
    }
    private void handle(MethodCall call, MethodChannel.Result result) {
        if (call.method.equals("i028PackageName")) {
            if ((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) == 0) {
                result.notImplemented(); return;
            }
            result.success(getPackageName());
            return;
        }
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
        if (call.method.equals("openBatterySettings")) {
            try {
                if (!requestBatteryExemption())
                    startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
                result.success(null);
            } catch (RuntimeException ignored) {
                result.error("battery_settings_unavailable", "Не удалось открыть настройки батареи", null);
            }
            return;
        }
        if (call.method.equals("appVersionCode")) {
            try { result.success(AppUpdateInstaller.installedVersionCode(this)); }
            catch (PackageManager.NameNotFoundException | RuntimeException unavailable) {
                result.error("app_version_unavailable", "Версия приложения недоступна", null);
            }
            return;
        }
        if (call.method.equals("appVersionName")) {
            try { result.success(AppUpdateInstaller.installedVersionName(this)); }
            catch (PackageManager.NameNotFoundException | RuntimeException unavailable) {
                result.error("app_version_unavailable", "Версия приложения недоступна", null);
            }
            return;
        }
        if (call.method.equals("appUpdateVerify")) {
            // ADR-107: the manifest counts only if its Ed25519 signature verifies here.
            result.success(verifiedUpdatePayload(call));
            return;
        }
        if (call.method.equals("appUpdateInstall")) {
            handleUpdateInstall(call, result);
            return;
        }
        if (call.method.equals("shareDiagnosticReport")) {
            String code = call.argument("code");
            String stage = call.argument("stage");
            String handshake = call.argument("handshake");
            String hint = call.argument("hint");
            final String reportText;
            try {
                reportText = DiagnosticReportText.format(code, stage, handshake,
                        call.argument("handshakeAge"), call.argument("window"),
                        call.argument("networkChange"), hint == null ? "general" : hint);
            } catch (IllegalArgumentException invalidReport) {
                result.error("diagnostic_report_invalid", "Отчёт диагностики недоступен", null);
                return;
            }
            // ADR-124: the native state line is read on the worker that owns it.
            ClientApplication shared = owner();
            shared.worker.execute(() -> {
                String nativeState = "";
                try { nativeState = shared.diagnosticLine(); }
                catch (RuntimeException unavailable) { /* Report without the native line. */ }
                String text = reportText + nativeState;
                runOnUiThread(() -> {
                    try {
                        Intent report = new Intent(Intent.ACTION_SEND);
                        report.setType("text/plain");
                        report.putExtra(Intent.EXTRA_TEXT, text);
                        startActivity(Intent.createChooser(report, "Поделиться диагностикой"));
                        result.success(null);
                    } catch (RuntimeException unavailable) {
                        result.error("diagnostic_share_unavailable", "Не удалось открыть отправку отчёта", null);
                    }
                });
            });
            return;
        }
        if (!java.util.Set.of("publicKey", "prove", "connect", "connectWindow", "advanceWindow",
                "disconnect", "snapshot", "sessionRead", "sessionWrite", "pendingLoginRead",
                "pendingLoginWrite", "splitRead", "splitModeRead", "splitLastModeRead",
                "splitWrite", "splitDomainsRead", "apps", "nodeRead", "nodeWrite",
                "startNativeRenewal", "adoptRestore", "dnsRead", "dnsWrite", "setIntent").contains(call.method)) {
            result.notImplemented(); return;
        }
        ClientApplication app = owner();
        // ADR-124: `disconnect` only stops the engine (also used by cleanups and switches); the
        // user's intent ends only with `setIntent {connected: false}` or a terminal failure.
        boolean userStop = call.method.equals("setIntent");
        if (userStop && !Boolean.FALSE.equals(call.argument("connected"))) {
            result.error("native_operation_failed", "Операция VPN не выполнена", null);
            return;
        }
        if (call.method.equals("disconnect") || userStop) {
            app.generation.incrementAndGet();
            if (permissionResult != null) { permissionResult.success(false); permissionResult = null; }
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
                    case "dnsRead": {
                        // "" = as in the server profile (default).
                        String storedDns = DnsChoice.storedOrNull(getSharedPreferences("routing", MODE_PRIVATE)
                                .getString("dns", null));
                        value = storedDns == null ? "" : storedDns;
                        break;
                    }
                    case "dnsWrite": {
                        // Like split settings: only while the tunnel is off; applied on next connect.
                        if (app.provider.isUp() || app.deadline != 0) throw new IllegalStateException();
                        String requestedDns = call.argument("server");
                        String canonicalDns = DnsChoice.canonicalOrNull(requestedDns);
                        if (!getSharedPreferences("routing", MODE_PRIVATE).edit()
                                .putString("dns", canonicalDns == null ? "" : canonicalDns)
                                .commit()) throw new IllegalStateException();
                        value = canonicalDns == null ? "" : canonicalDns;
                        break;
                    }
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
                        app.setDeadline(app.window.deadline);
                        break;
                    case "startNativeRenewal":
                        // ADR-097: only for the tunnel this call raced against; a disconnect in
                        // between bumps the generation and the renewer refuses to start.
                        if (generation != app.generation.get() || !app.provider.isUp()
                                || app.deadline <= 0) throw new IllegalStateException();
                        // ADR-136/140: an unadopted restore engine owns renewal; under Lockdown
                        // native needs a protected path. A refusal keeps Dart renewing.
                        if (!RenewalOwner.nativeMayStart(false, app.restoreEngineAlive(),
                                app.lockdown, app.protector != null)) {
                            value = false;
                            break;
                        }
                        app.renewer.start(call.arguments(), generation);
                        value = true;
                        break;
                    case "adoptRestore":
                        // ADR-121: UI channel only; the restore engine's channel never offers it.
                        value = app.detachRestoreForUi();
                        break;
                    case "publicKey": value = app.key.getPublicKey().toBase64(); break;
                    case "prove": value = KeyConfirmation.answer(app.key,
                        call.argument("ephemeralKey"), call.argument("transcript")); break;
                    case "snapshot": value = app.snapshot(); break;
                    case "disconnect": app.stop(); break;
                    case "setIntent": app.userDisconnect(); break;
                    case "connect":
                    case "connectWindow":
                        boolean renewable = call.method.equals("connectWindow");
                        Number duration = call.argument("remainingMs");
                        if (duration == null || duration.longValue() <= 0 || duration.longValue() > (renewable ? WindowDeadline.MAX_MS : 60000)
                                || generation != app.generation.get() || app.provider.isUp()
                                || VpnService.prepare(this) != null) throw new IllegalStateException();
                        long deadline = submittedAt + duration.longValue();
                        var config = app.routedConfig(call.argument("config"));
                        if (SystemClock.elapsedRealtime() >= deadline) throw new IllegalStateException();
                        if (renewable) app.window.start(call.argument("connection"), call.argument("runtime"),
                            ((Number) call.argument("generation")).longValue(), deadline, SystemClock.elapsedRealtime());
                        app.setDeadline(deadline);
                        try {
                            app.provider.connect(config);
                            if (generation != app.generation.get() || SystemClock.elapsedRealtime() >= deadline) {
                                app.stop(); throw new IllegalStateException();
                            }
                            app.reason = "";
                        } catch (Exception failure) { app.stop(); throw failure; }
                        if (renewable) {
                            ClientApplication.setWantsConnected(app, true);
                            app.supervisor.userConnected(); // ADR-124
                            runOnUiThread(this::askBatteryExemption);
                        }
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
    /**
     * ADR-107: downloads, verifies and opens the system installer. Answers a stable outcome code;
     * "install_permission_required" opens Android's "install unknown apps" screen first.
     */
    /** Verified manifest JSON text, or null (no key, old Android, bad or missing signature). */
    private static String verifiedUpdatePayload(MethodCall call) {
        if (android.os.Build.VERSION.SDK_INT < UpdateSignature.MIN_API) return null;
        return UpdateSignature.verify(UpdateSignature.PUBLIC_KEY_B64, call.argument("payload"),
                call.argument("signature"), call.argument("keyId"));
    }

    private void handleUpdateInstall(MethodCall call, MethodChannel.Result result) {
        // Every install value comes from the signed payload, re-verified here; Dart only relays it.
        String payload = verifiedUpdatePayload(call);
        if (payload == null) { result.success("manifest_unverified"); return; }
        final String url;
        final String sha256;
        final long size;
        final long version;
        final long minimum;
        try {
            org.json.JSONObject manifest = new org.json.JSONObject(payload);
            url = manifest.getString("apk_url");
            sha256 = manifest.getString("apk_sha256");
            size = manifest.getLong("apk_size_bytes");
            version = manifest.getLong("version_code");
            minimum = manifest.getLong("min_supported_version_code");
        } catch (org.json.JSONException invalid) {
            result.success("invalid_manifest"); return;
        }
        if (!AppUpdateInstaller.canInstall(this)) {
            try { startActivity(AppUpdateInstaller.unknownSourcesSettings(this)); }
            catch (RuntimeException unavailable) { /* The dialog explains the setting. */ }
            result.success("install_permission_required"); return;
        }
        AppUpdateInstaller.download(this, url, sha256, size, version,
                minimum, outcome -> runOnUiThread(() -> {
                    if (!"verified".equals(outcome)) { result.success(outcome); return; }
                    try {
                        startActivity(AppUpdateInstaller.installIntent(this));
                        result.success("installer_opened");
                    } catch (RuntimeException unavailable) {
                        result.success("installer_unavailable");
                    }
                }));
    }
    /** Shows Android's own "allow background activity" dialog; false if already allowed or unavailable. */
    private boolean requestBatteryExemption() {
        try {
            android.os.PowerManager manager = (android.os.PowerManager) getSystemService(POWER_SERVICE);
            if (manager == null || manager.isIgnoringBatteryOptimizations(getPackageName())) return true;
            startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:" + getPackageName())));
            return true;
        } catch (RuntimeException unavailable) {
            return false;
        }
    }

    /** At most twice per install: without the exemption Android sleeps the renewal timer and the 90 s window ends. */
    private void askBatteryExemption() {
        try {
            var prefs = getSharedPreferences("connection", MODE_PRIVATE);
            int asked = prefs.getInt("battery_prompted", 0);
            android.os.PowerManager manager = (android.os.PowerManager) getSystemService(POWER_SERVICE);
            if (asked >= 2 || manager == null || manager.isIgnoringBatteryOptimizations(getPackageName())) return;
            prefs.edit().putInt("battery_prompted", asked + 1).apply();
            requestBatteryExemption();
        } catch (RuntimeException ignored) {
            // Best effort only.
        }
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
    @Override protected void onActivityResult(int request, int response, Intent data) {
        super.onActivityResult(request, response, data);
        if (request == 1301 && permissionResult != null) {
            permissionResult.success(response == RESULT_OK && VpnService.prepare(this) == null);
            permissionResult = null;
        }
    }
    @Override public void cleanUpFlutterEngine(FlutterEngine engine) {
        if (permissionResult != null) { permissionResult.success(false); permissionResult = null; }
        owner().uiEngine(false); // ADR-125: not a disconnect; native may start recovery later.
        super.cleanUpFlutterEngine(engine);
    }
}
