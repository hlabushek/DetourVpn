package org.example.vpnpoc;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;
import io.flutter.embedding.engine.FlutterEngine;
import java.util.concurrent.TimeUnit;

/** TEST APK ONLY: real JNI/Activity checks, no API calls, account or VPN peer. */
public final class ReliabilityDeviceTests extends Instrumentation {
    private final Bundle facts = new Bundle();
    @Override public void onCreate(Bundle args) { super.onCreate(args); start(); }
    private void check(boolean condition, String name) {
        if (!condition) throw new IllegalStateException(name);
        facts.putBoolean(name, true);
    }
    @Override public void onStart() {
        String stage = "native_load";
        try {
            waitForIdleSync();
            ClientApplication app = (ClientApplication) getTargetContext().getApplicationContext();
            app.worker.submit(() -> check(app.provider != null, "real_native_sdk_loaded"))
                    .get(20, TimeUnit.SECONDS);
            check(!org.amnezia.awg.GoBackend.awgSetDeadline(-1, 1), "invalid_native_handle_rejected");
            org.amnezia.awg.GoBackend.awgCancel(-1);
            check(!org.amnezia.awg.GoBackend.awgRouteReady(-1, true), "invalid_route_rejected");
            check(org.amnezia.awg.GoBackend.awgDataAge(-1) == -1, "missing_data_not_health");
            stage = "activity_lifetime";
            Intent launch = new Intent(app, ClientActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            Activity first = startActivitySync(launch);
            final FlutterEngine[] engine = new FlutterEngine[1];
            runOnMainSync(() -> engine[0] = app.uiEngine());
            runOnMainSync(first::finish);
            waitForIdleSync();
            Activity second = startActivitySync(launch);
            runOnMainSync(() -> check(engine[0] == app.uiEngine(), "activity_reuses_engine"));
            runOnMainSync(second::recreate);
            waitForIdleSync();
            runOnMainSync(() -> check(engine[0] == app.uiEngine(), "recreation_reuses_engine"));
            check(!app.worker.submit(() -> app.provider.isUp()).get(10, TimeUnit.SECONDS),
                    "ui_did_not_create_tunnel");
            stage = "blocked_worker_cancellation";
            java.util.concurrent.CountDownLatch entered = new java.util.concurrent.CountDownLatch(1);
            java.util.concurrent.CountDownLatch release = new java.util.concurrent.CountDownLatch(1);
            var blocked = app.worker.submit(() -> {
                entered.countDown();
                try { release.await(25, TimeUnit.SECONDS); }
                catch (InterruptedException stopped) { Thread.currentThread().interrupt(); }
            });
            try {
                check(entered.await(5, TimeUnit.SECONDS), "test_worker_blocked");
                java.util.concurrent.CountDownLatch answered = new java.util.concurrent.CountDownLatch(1);
                java.util.concurrent.atomic.AtomicInteger replies = new java.util.concurrent.atomic.AtomicInteger();
                final io.flutter.plugin.common.MethodChannel.Result[] bounded = new io.flutter.plugin.common.MethodChannel.Result[1];
                runOnMainSync(() -> bounded[0] = app.boundedResult(new io.flutter.plugin.common.MethodChannel.Result() {
                    public void success(Object value) { replies.incrementAndGet(); answered.countDown(); }
                    public void error(String code, String message, Object detail) {
                        if ("native_operation_timeout".equals(code)) replies.incrementAndGet();
                        answered.countDown();
                    }
                    public void notImplemented() { answered.countDown(); }
                }));
                check(answered.await(20, TimeUnit.SECONDS), "blocked_request_completed");
                check(app.nativeTimedOut && !app.desiredConnected, "timeout_cancels_intent");
                check(!app.getSharedPreferences("connection", 0).getBoolean("desired_connected", true),
                        "cancelled_intent_saved_without_worker");
                runOnMainSync(() -> bounded[0].success(null));
                check(replies.get() == 1, "late_native_result_ignored");
            } finally { release.countDown(); }
            blocked.get(5, TimeUnit.SECONDS);
            facts.putBoolean("vpn_or_physical_acceptance", false);
            facts.putBoolean("passed", true);
            finish(Activity.RESULT_OK, facts);
        } catch (Throwable failure) {
            facts.putBoolean("passed", false);
            facts.putString("failed_stage", stage);
            facts.putString("failure_type", failure.getClass().getSimpleName());
            finish(Activity.RESULT_CANCELED, facts);
        }
    }
}
