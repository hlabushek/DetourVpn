package org.example.vpnpoc;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.VpnService;
import android.os.PowerManager;
import android.os.SystemClock;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

/**
 * ADR-097: renews the current access window from the app process without the Flutter engine, so
 * Doze pausing Dart timers no longer ends the tunnel. It holds only a connection-scoped renew
 * token (never the refresh or access token), talks only to the API origin the app already uses,
 * and stops on any refusal. Doze wake-ups come from allow-while-idle alarms; a short wake lock
 * covers each attempt only.
 */
final class NativeRenewer {
    static final String ACTION = "org.example.vpnpoc.action.RENEW_WINDOW";
    private static final int TIMEOUT_MS = 10000;
    private static final int PENDING_POLLS = 15;
    private static final long PENDING_POLL_MS = 2000;
    /** No new pending poll this long after a wake-up began, so attempts fit the 60 s lock. */
    private static final long PENDING_BUDGET_MS = 40000;
    private static final long LOCK_MS = 60000;

    private final ClientApplication app;
    private final ScheduledExecutorService io = Executors.newSingleThreadScheduledExecutor();
    private volatile State state;
    private ScheduledFuture<?> next;
    /** Last refusal code, shown in diagnostics; never contains the token. */
    volatile String stoppedReason = "";

    private static final class State {
        final NativeRenewal plan;
        final URI origin;
        final String token;
        final long appGeneration;
        State(NativeRenewal plan, URI origin, String token, long appGeneration) {
            this.plan = plan; this.origin = origin; this.token = token;
            this.appGeneration = appGeneration;
        }
    }

    NativeRenewer(ClientApplication app) { this.app = app; }

    boolean active() { return state != null; }

    /** Called on the app worker thread right after connectWindow succeeded. */
    void start(Map<String, Object> args, long appGeneration) {
        URI origin = URI.create((String) args.get("origin"));
        if (!"https".equals(origin.getScheme()) || origin.getHost() == null
                || origin.getRawUserInfo() != null || origin.getRawQuery() != null
                || origin.getRawFragment() != null
                || (origin.getRawPath() != null && !origin.getRawPath().isEmpty()
                        && !origin.getRawPath().equals("/")))
            throw new IllegalArgumentException();
        String token = (String) args.get("token");
        if (token == null || token.length() < 32 || token.length() > 128
                || !token.matches("[A-Za-z0-9_-]+"))
            throw new IllegalArgumentException();
        var plan = new NativeRenewal((String) args.get("connection"), (String) args.get("runtime"),
                (String) args.get("windowId"), ((Number) args.get("generation")).longValue(),
                ((Number) args.get("budgetBytes")).longValue(),
                ((Number) args.get("windowSeconds")).intValue());
        stop();
        stoppedReason = "";
        State started = new State(plan, origin, token, appGeneration);
        state = started;
        io.execute(() -> tick(started));
    }

    void stop() {
        state = null;
        io.execute(() -> {
            if (next != null) next.cancel(false);
            next = null;
        });
        alarms().cancel(alarmIntent());
    }

    /**
     * Alarm receiver entry point. The wake lock is taken before the broadcast finishes, so the
     * CPU stays up for the attempt without holding the broadcast past its time limit.
     */
    void wake(Runnable done) {
        PowerManager.WakeLock lock = acquire();
        done.run();
        io.execute(() -> {
            try {
                State current = state;
                if (current != null) run(current);
            } finally {
                release(lock);
            }
        });
    }

    private void tick(State s) {
        if (state != s) return;
        PowerManager.WakeLock lock = acquire();
        try {
            run(s);
        } finally {
            release(lock);
        }
    }

    private PowerManager.WakeLock acquire() {
        try {
            PowerManager power = (PowerManager) app.getSystemService(Context.POWER_SERVICE);
            PowerManager.WakeLock lock =
                    power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "detourvpn:renew");
            lock.setReferenceCounted(false);
            lock.acquire(LOCK_MS);
            return lock;
        } catch (RuntimeException ignored) {
            return null; // Best effort; the attempt still runs while the CPU happens to be awake.
        }
    }

    private static void release(PowerManager.WakeLock lock) {
        try { if (lock != null && lock.isHeld()) lock.release(); }
        catch (RuntimeException ignored) { /* Released by its timeout. */ }
    }

    /** One wake-up: up to {@link NativeRenewal#BURST_ATTEMPTS} attempts (ADR-118), then the alarm. */
    private void run(State s) {
        long wakeStart = SystemClock.elapsedRealtime();
        for (int attempts = 1; ; attempts++) {
            long remaining = attempt(s, wakeStart);
            if (remaining < 0 || state != s) return;
            long delay = NativeRenewal.burstDelayMs(attempts,
                    SystemClock.elapsedRealtime() - wakeStart, remaining);
            if (delay < 0) { retry(s, remaining); return; }
            try {
                Thread.sleep(delay);
            } catch (InterruptedException stopped) {
                Thread.currentThread().interrupt();
                return;
            }
            if (state != s) return;
        }
    }

    /** Returns -1 when this wake-up is settled (renewed, idle, stopped), else the window time left. */
    private long attempt(State s, long wakeStart) {
        long remaining;
        long traffic;
        try {
            long[] live = app.renewalView(s.appGeneration);
            remaining = live[0];
            traffic = live[1];
        } catch (Exception gone) {
            finish(s, "tunnel_stopped");
            return -1;
        }
        if (remaining <= 0) { finish(s, "lease_expired"); return -1; }
        NativeRenewal plan = s.plan;
        boolean exact = exactAlarmAllowed(); // ADR-147: the margin follows the alarm kind.
        if (!plan.due(remaining, traffic, exact)) {
            schedule(s, Math.min(plan.idleDelayMs(remaining, exact), NativeRenewal.AWAKE_CHECK_MS),
                    plan.idleDelayMs(remaining, exact), plan.idleDelayMs(remaining, false));
            return -1;
        }
        String key = plan.beginIntent(SystemClock.elapsedRealtime());
        try {
            JSONObject body = new JSONObject()
                    .put("previous_window_id", plan.windowId)
                    .put("idempotency_key", key)
                    .put("budget_bytes", plan.budgetBytes)
                    .put("window_seconds", plan.windowSeconds);
            Response posted = call(s, "POST", "/native-renew", body);
            if (refused(posted.status)) { finish(s, posted.code()); return -1; }
            if (posted.status != 202) return remaining;
            for (int poll = 0; poll < PENDING_POLLS && state == s
                    && SystemClock.elapsedRealtime() - wakeStart < PENDING_BUDGET_MS; poll++) {
                Response status = call(s, "GET", "/native", null);
                if (refused(status.status)) { finish(s, status.code()); return -1; }
                if (status.status == 200 && "active".equals(status.json.optString("state"))) {
                    JSONObject window = status.json.getJSONObject("window");
                    long generation = window.getLong("generation");
                    if (generation == plan.generation + 1) {
                        long end = plan.adopt(window.getString("connection_id"),
                                window.getString("runtime_id"), window.getString("window_id"),
                                generation, window.getString("issued_at"),
                                window.getString("not_after"), SystemClock.elapsedRealtime(),
                                System.currentTimeMillis());
                        app.advanceNative(s.appGeneration, plan.connection, plan.runtime,
                                generation, end);
                        plan.adopted(generation, window.getString("window_id"), traffic);
                        run(s);
                        return -1;
                    }
                    if (generation != plan.generation) {
                        finish(s, "window_generation_conflict");
                        return -1;
                    }
                } else if (status.status != 200) {
                    break;
                }
                Thread.sleep(PENDING_POLL_MS);
            }
            // Accepted but not yet acknowledged by the node: the same key is re-sent next time.
            return remaining;
        } catch (InterruptedException stopped) {
            Thread.currentThread().interrupt();
            return -1;
        } catch (IllegalStateException conflict) {
            finish(s, "window_validation_failed");
            return -1;
        } catch (Exception transientFailure) {
            // Network loss, DNS, TLS or malformed JSON: retry inside the window.
            return remaining;
        }
    }

    private static boolean refused(int status) {
        return status == 401 || status == 403 || status == 404 || status == 409 || status == 422;
    }

    private void retry(State s, long remaining) {
        long delay = s.plan.backoffMs(remaining, ThreadLocalRandom.current().nextDouble());
        schedule(s, delay, delay, delay);
    }

    private void finish(State s, String reason) {
        if (state != s) return;
        stoppedReason = reason;
        app.log("native_renewal_stopped", reason); // ADR-144: a fixed reason code.
        // The window's renewal failed; a tunnel that stopped under us is not a renewal failure.
        if (!"tunnel_stopped".equals(reason)) app.failedAt("native_renewal");
        stop();
    }

    /**
     * ADR-147 (3.2): whether RENEW_WINDOW may use an exact alarm now. Read on every scheduling,
     * because the user can grant or revoke SCHEDULE_EXACT_ALARM at any time.
     */
    private boolean exactAlarmAllowed() {
        try {
            int sdk = android.os.Build.VERSION.SDK_INT;
            return DozeSurvival.exactAlarmAllowed(sdk,
                    sdk >= DozeSurvival.EXACT_ALARM_PERMISSION_SDK && alarms().canScheduleExactAlarms());
        } catch (RuntimeException unavailable) {
            return false;
        }
    }

    /**
     * Schedules the next renewal check: in process after {@code awakeDelayMs}, and by alarm.
     * ADR-147 (3.2): RENEW_WINDOW (and only it) uses {@code setExactAndAllowWhileIdle} when exact
     * alarms are allowed, after {@code exactAlarmDelayMs}. Otherwise, or if the exact call is
     * refused, the inexact {@code setAndAllowWhileIdle} fallback fires after the earlier-margin
     * {@code inexactAlarmDelayMs}.
     *
     * <p>TODO(owner, before any Play Store release): verify the Google Play policy for
     * SCHEDULE_EXACT_ALARM / USE_EXACT_ALARM for a VPN app. Only SCHEDULE_EXACT_ALARM is declared;
     * USE_EXACT_ALARM is restricted by Play to alarm/calendar apps and is deliberately not used.
     */
    private void schedule(State s, long awakeDelayMs, long exactAlarmDelayMs,
            long inexactAlarmDelayMs) {
        if (state != s) return;
        if (next != null) next.cancel(false);
        next = io.schedule(() -> tick(s), awakeDelayMs, TimeUnit.MILLISECONDS);
        long now = SystemClock.elapsedRealtime();
        if (exactAlarmAllowed()) {
            try {
                alarms().setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP,
                        now + exactAlarmDelayMs, alarmIntent());
                return;
            } catch (RuntimeException refused) {
                // SecurityException: the permission was revoked since the check. Fall back below.
                app.log("renew_alarm_fallback", "exact_refused");
            }
        }
        try {
            alarms().setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP,
                    now + inexactAlarmDelayMs, alarmIntent());
        } catch (RuntimeException ignored) {
            // The in-process schedule still runs while the CPU is awake.
        }
    }

    private AlarmManager alarms() {
        return (AlarmManager) app.getSystemService(Context.ALARM_SERVICE);
    }

    private PendingIntent alarmIntent() {
        Intent intent = new Intent(app, RenewAlarmReceiver.class).setAction(ACTION);
        return PendingIntent.getBroadcast(app, 0, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static final class Response {
        final int status;
        final JSONObject json;
        Response(int status, JSONObject json) { this.status = status; this.json = json; }
        String code() {
            String code = json.optJSONObject("error") != null
                    ? json.optJSONObject("error").optString("code", "")
                    : json.optString("code", json.optString("detail", ""));
            return code.matches("[a-z_]{1,64}") ? code : "renewal_refused";
        }
    }

    private Response call(State s, String method, String suffix, JSONObject body) throws Exception {
        String path = "/api/v1/window-tunnels/" + s.plan.connection + suffix;
        // ADR-140: under Lockdown only a protect()ed socket leaves; otherwise the app's own
        // package is outside its VPN and plain platform HTTPS uses the physical network.
        VpnService protector = app.protector;
        switch (RenewalOwner.transport(app.lockdown, protector != null)) {
            case PROTECTED: {
                String host = ProtectedApiRelay.validateApiOrigin(s.origin);
                byte[] bytes = body == null ? null : body.toString().getBytes(StandardCharsets.UTF_8);
                ProtectedHttps.Result result = ProtectedHttps.exchange(protector::protect, host,
                        method, path, s.token, bytes, TIMEOUT_MS);
                return new Response(result.status,
                        result.body.isEmpty() ? new JSONObject() : new JSONObject(result.body));
            }
            case NONE:
                throw new java.io.IOException("no_protected_path"); // Transient; window decides.
            default:
                break;
        }
        String base = s.origin.toString().replaceAll("/+$", "");
        URL url = new URL(base + path);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        try {
            connection.setInstanceFollowRedirects(false);
            connection.setUseCaches(false);
            connection.setConnectTimeout(TIMEOUT_MS);
            connection.setReadTimeout(TIMEOUT_MS);
            connection.setRequestMethod(method);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("X-Window-Renew-Token", s.token);
            if (body != null) {
                byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setFixedLengthStreamingMode(bytes.length);
                try (OutputStream out = connection.getOutputStream()) { out.write(bytes); }
            }
            int status = connection.getResponseCode();
            InputStream stream = status >= 400 ? connection.getErrorStream()
                    : connection.getInputStream();
            String text = stream == null ? "" : read(stream);
            return new Response(status, text.isEmpty() ? new JSONObject() : new JSONObject(text));
        } finally {
            connection.disconnect();
        }
    }

    private static String read(InputStream stream) throws Exception {
        try (InputStream in = stream) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int total = 0;
            for (int n; (n = in.read(buffer)) > 0; ) {
                total += n;
                if (total > 65536) throw new IllegalArgumentException();
                out.write(buffer, 0, n);
            }
            return out.toString("UTF-8");
        }
    }
}
