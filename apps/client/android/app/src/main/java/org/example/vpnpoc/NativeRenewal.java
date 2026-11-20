package org.example.vpnpoc;

import java.util.Calendar;
import java.util.TimeZone;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Pure timing and validation for ADR-097 native window renewal. No I/O, no Android types, so the
 * decisions are unit-tested on the JVM. Mirrors the Dart controller: renew once a third of a long
 * window has elapsed (55 s before expiry for 90 s windows) or after half the byte budget, and
 * accept only the next generation of the same connection and runtime.
 */
final class NativeRenewal {
    static final long SAFETY_MS = 6000;
    /** ADR-123: how far the device clock may differ from the server's. */
    static final long MAX_CLOCK_SKEW_MS = 300000;
    static final long MIN_BACKOFF_MS = 2000;
    static final long MAX_BACKOFF_MS = 60000;
    static final long AWAKE_CHECK_MS = 30000;
    /** ADR-118: attempts inside one wake-up, and the time from wake-up after which none starts. */
    static final int BURST_ATTEMPTS = 4;
    static final long BURST_BUDGET_MS = 30000;
    /**
     * ADR-152: when the app worker did not answer the renewal view in time (it was busy with a
     * long call, such as listing the installed apps), look again after this. Bounded by the
     * window itself: once it ends, the view reports no time left.
     */
    static final long WORKER_BUSY_RETRY_MS = 5000;

    /**
     * ADR-152: whether a failed renewal view means "the worker is busy, try again" rather than
     * "the tunnel this renewal belongs to stopped". Only a timeout is busy; every other failure
     * (a stopped tunnel, a new generation after a disconnect) still ends the renewal.
     */
    static boolean viewBusy(Throwable failure) {
        return failure instanceof java.util.concurrent.TimeoutException;
    }

    final String connection;
    final String runtime;
    final long budgetBytes;
    final int windowSeconds;
    String windowId;
    long generation;
    /** Idempotency key of the renewal in flight; reused verbatim on every retry. */
    String intentKey;
    long intentAtElapsed;
    long trafficBaseline = -1;
    int failures;

    NativeRenewal(String connection, String runtime, String windowId, long generation,
            long budgetBytes, int windowSeconds) {
        if (!uuid(connection) || !uuid(runtime) || !uuid(windowId) || generation < 1
                || budgetBytes < 65536 || budgetBytes > 67108864
                || windowSeconds < 30 || windowSeconds > 3600)
            throw new IllegalArgumentException();
        this.connection = connection;
        this.runtime = runtime;
        this.windowId = windowId;
        this.generation = generation;
        this.budgetBytes = budgetBytes;
        this.windowSeconds = windowSeconds;
    }

    /**
     * ADR-142: renew about 2 min after each grant (was 5 min). In deep Doze one allow-while-idle
     * alarm can arrive ~9 min late; with 900 s windows this leaves another alarm before expiry.
     */
    static final long RENEW_AFTER_GRANT_MS = 120000;

    long renewBeforeMs() {
        return Math.max(55000, windowSeconds * 1000L - RENEW_AFTER_GRANT_MS);
    }

    /**
     * ADR-147 (3.2): with an exact allow-while-idle renewal alarm, renew this long before expiry
     * instead of 2 min after the grant. 10 min still leaves room for one retry alarm that Doze
     * throttles (about 9 min apart for allow-while-idle alarms before Android 12).
     */
    static final long EXACT_RENEW_BEFORE_MS = 600000;

    /**
     * ADR-147: the renewal margin for the alarm kind in use. Exact alarms use the later, tighter
     * {@link #EXACT_RENEW_BEFORE_MS}; without the permission the inexact fallback keeps the
     * earlier ADR-142 margin ({@link #renewBeforeMs()}). Never below the 55 s floor.
     */
    long renewBeforeMs(boolean exactAlarm) {
        long inexact = renewBeforeMs();
        return exactAlarm ? Math.max(55000, Math.min(inexact, EXACT_RENEW_BEFORE_MS)) : inexact;
    }

    boolean due(long remainingMs, long trafficBytes) { return due(remainingMs, trafficBytes, false); }

    boolean due(long remainingMs, long trafficBytes, boolean exactAlarm) {
        if (intentKey != null) return true;
        if (remainingMs <= renewBeforeMs(exactAlarm)) return true;
        if (trafficBytes < 0) return false;
        if (trafficBaseline < 0 || trafficBytes < trafficBaseline) {
            trafficBaseline = trafficBytes; // Missing or reset counters start a fresh baseline.
            return false;
        }
        return trafficBytes - trafficBaseline >= budgetBytes / 2;
    }

    /** Delay until the next check while nothing is due; the alarm uses the time-based part. */
    long idleDelayMs(long remainingMs) { return idleDelayMs(remainingMs, false); }

    long idleDelayMs(long remainingMs, boolean exactAlarm) {
        return Math.max(1000, remainingMs - renewBeforeMs(exactAlarm));
    }

    /** ADR-121: wake the restore engine every 4 minutes (Doze stretches it), never past the margin. */
    static final long RESTORE_WAKE_MS = 240000;

    /** Delay to the next restore-engine wake-up, or -1 when the window is about to end. */
    static long restoreWakeDelayMs(long remainingMs) {
        long delay = Math.min(RESTORE_WAKE_MS, remainingMs - SAFETY_MS);
        return delay < 1000 ? -1 : delay;
    }

    /**
     * ADR-118: delay before the next attempt in the same wake-up (2 s, 4 s, 8 s), or -1 once the
     * burst is spent. In Doze the next alarm can be about 9 minutes away, so a transient failure
     * at wake-up is retried while the wake lock is still held. {@code attempts} counts attempts
     * already made in this wake-up; {@code elapsedMs} is the time since it began.
     */
    static long burstDelayMs(int attempts, long elapsedMs, long remainingMs) {
        if (attempts < 1 || attempts >= BURST_ATTEMPTS || elapsedMs < 0) return -1;
        long delay = MIN_BACKOFF_MS << (attempts - 1);
        if (elapsedMs + delay >= BURST_BUDGET_MS) return -1;
        if (remainingMs - delay <= SAFETY_MS) return -1;
        return delay;
    }

    String beginIntent(long nowElapsed) {
        if (intentKey == null) {
            intentKey = UUID.randomUUID().toString();
            intentAtElapsed = nowElapsed;
        }
        return intentKey;
    }

    /** Exponential backoff with jitter in [0, 1); never past the safety margin of the window. */
    long backoffMs(long remainingMs, double jitter) {
        failures = Math.min(failures + 1, 16);
        long base = Math.min(MAX_BACKOFF_MS, MIN_BACKOFF_MS << Math.min(failures - 1, 10));
        long delay = base / 2 + (long) (base / 2 * Math.max(0, Math.min(jitter, 0.999)));
        return Math.max(500, Math.min(delay, remainingMs - SAFETY_MS));
    }

    /**
     * Validates the server window and returns the new native deadline in elapsed-realtime
     * milliseconds. Throws IllegalStateException for anything but this connection's next window.
     */
    long adopt(String connectionId, String runtimeId, String newWindowId, long newGeneration,
            String issuedAt, String notAfter, long nowElapsed, long nowWallMs) {
        if (intentKey == null || !connection.equals(connectionId) || !runtime.equals(runtimeId)
                || newGeneration != generation + 1 || !intentKey.equals(newWindowId))
            throw new IllegalStateException();
        long issued = parseUtcMillis(issuedAt);
        long expiry = parseUtcMillis(notAfter);
        long duration = expiry - issued;
        // ADR-123: this window was issued after the intent, so the elapsed-realtime deadline is
        // safe whatever the wall clock says; the wall clock only catches a clock minutes off.
        long sinceIntent = Math.max(0, nowElapsed - intentAtElapsed);
        if (duration <= SAFETY_MS || duration > windowSeconds * 1000L
                || issued > nowWallMs + MAX_CLOCK_SKEW_MS
                || issued < nowWallMs - sinceIntent - MAX_CLOCK_SKEW_MS)
            throw new IllegalStateException();
        long end = intentAtElapsed + duration - SAFETY_MS;
        if (end <= nowElapsed) throw new IllegalStateException();
        return end;
    }

    void adopted(long newGeneration, String newWindowId, long trafficBytes) {
        generation = newGeneration;
        windowId = newWindowId;
        intentKey = null;
        failures = 0;
        trafficBaseline = trafficBytes;
    }

    private static final Pattern UTC = Pattern.compile(
            "(\\d{4})-(\\d{2})-(\\d{2})T(\\d{2}):(\\d{2}):(\\d{2})(?:\\.(\\d{1,9}))?(?:Z|\\+00:00)");

    /** ISO-8601 UTC only (as the API emits); minSdk 24 has no java.time. */
    static long parseUtcMillis(String value) {
        Matcher m = value == null ? null : UTC.matcher(value);
        if (m == null || !m.matches()) throw new IllegalStateException();
        Calendar calendar = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        calendar.clear();
        calendar.setLenient(false);
        calendar.set(Integer.parseInt(m.group(1)), Integer.parseInt(m.group(2)) - 1,
                Integer.parseInt(m.group(3)), Integer.parseInt(m.group(4)),
                Integer.parseInt(m.group(5)), Integer.parseInt(m.group(6)));
        String fraction = m.group(7) == null ? "0" : (m.group(7) + "00").substring(0, 3);
        try {
            return calendar.getTimeInMillis() + Integer.parseInt(fraction);
        } catch (IllegalArgumentException invalid) {
            throw new IllegalStateException();
        }
    }

    static boolean uuid(String value) {
        if (value == null || value.length() != 36) return false;
        try {
            return UUID.fromString(value).toString().equals(value);
        } catch (IllegalArgumentException invalid) {
            return false;
        }
    }
}
