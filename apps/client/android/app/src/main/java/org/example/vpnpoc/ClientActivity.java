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
    @Override public FlutterEngine provideFlutterEngine(android.content.Context context) {
        return owner().uiEngine();
    }
    @Override public boolean shouldDestroyEngineWithHost() { return false; }
    @Override public void configureFlutterEngine(FlutterEngine engine) {
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
        if (call.method.equals("shareDiagnosticReport")) {
            String code = call.argument("code");
            String stage = call.argument("stage");
            final String reportText;
            try {
                reportText = DiagnosticReportText.format(code, stage);
            } catch (IllegalArgumentException invalidReport) {
                result.error("diagnostic_report_invalid", "Отчёт диагностики недоступен", null);
                return;
            }
            try {
                Intent report = new Intent(Intent.ACTION_SEND);
                report.setType("text/plain");
                report.putExtra(Intent.EXTRA_TEXT, reportText);
                startActivity(Intent.createChooser(report, "Поделиться диагностикой"));
                result.success(null);
            } catch (RuntimeException unavailable) {
                result.error("diagnostic_share_unavailable", "Не удалось открыть отправку отчёта", null);
            }
            return;
        }
        if ((call.method.equals("disconnect") || (call.method.equals("sessionIntent")
                && Boolean.FALSE.equals(call.argument("desired")))) && permissionResult != null) {
            permissionResult.success(false);
            permissionResult = null;
        }
        owner().bridge.handle(call, result);
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
    @Override protected void onActivityResult(int request, int response, Intent data) {
        super.onActivityResult(request, response, data);
        if (request == 1301 && permissionResult != null) {
            permissionResult.success(response == RESULT_OK && VpnService.prepare(this) == null);
            permissionResult = null;
        }
    }
    @Override public void cleanUpFlutterEngine(FlutterEngine engine) {
        if (permissionResult != null) { permissionResult.success(false); permissionResult = null; }
        new MethodChannel(engine.getDartExecutor().getBinaryMessenger(), "project.vpn/tunnel.v1")
            .setMethodCallHandler(owner().bridge::handle);
        super.cleanUpFlutterEngine(engine);
    }
}
