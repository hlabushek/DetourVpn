package org.example.vpnpoc;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/**
 * ADR-144 (Phase 0): a bounded in-memory log of connection lifecycle events, so a failed phone
 * test explains itself instead of being inferred. Pure (no Android types, no clock): the caller
 * passes elapsed-realtime milliseconds.
 *
 * <p>Privacy: an entry is an event code and an optional detail code, both from {@link #CODE}
 * (lowercase letters, digits and underscores). Anything else is replaced by {@code invalid}, so no
 * address, endpoint, key, identifier or free text can be recorded. The log is never written to
 * disk; it is only exported with the user-shared diagnostic report.
 */
final class ConnectionEventLog {
    static final int CAPACITY = 200;
    static final Pattern CODE = Pattern.compile("[a-z0-9_]{1,48}");

    static final class Entry {
        final long at;
        final String event;
        final String detail;

        Entry(long at, String event, String detail) {
            this.at = at;
            this.event = event;
            this.detail = detail;
        }
    }

    private final ArrayDeque<Entry> entries = new ArrayDeque<>();
    private long dropped;

    static String sanitize(String code) {
        if (code == null || code.isEmpty()) return "";
        return CODE.matcher(code).matches() ? code : "invalid";
    }

    synchronized void record(long now, String event, String detail) {
        String safeEvent = sanitize(event);
        if (safeEvent.isEmpty()) safeEvent = "invalid";
        if (entries.size() >= CAPACITY) {
            entries.removeFirst();
            dropped++;
        }
        entries.addLast(new Entry(now, safeEvent, sanitize(detail)));
    }

    synchronized void record(long now, String event) {
        record(now, event, null);
    }

    synchronized int size() {
        return entries.size();
    }

    synchronized long dropped() {
        return dropped;
    }

    synchronized List<Entry> entries() {
        return new ArrayList<>(entries);
    }

    /**
     * Report text, newest last: one line per entry with its age in seconds relative to {@code now}
     * ({@code -123s event detail}). Ages instead of clock times: nothing ties the log to a moment.
     */
    synchronized String format(long now, int limit) {
        StringBuilder text = new StringBuilder();
        int skip = Math.max(0, entries.size() - Math.max(0, limit));
        int index = 0;
        for (Entry entry : entries) {
            if (index++ < skip) continue;
            long age = Math.max(0, now - entry.at) / 1000;
            text.append("\n-").append(age).append("s ").append(entry.event);
            if (!entry.detail.isEmpty()) text.append(' ').append(entry.detail);
        }
        return text.toString();
    }
}
