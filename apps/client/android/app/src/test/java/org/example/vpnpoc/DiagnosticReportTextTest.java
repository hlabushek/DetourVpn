package org.example.vpnpoc;

import org.junit.Test;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

public class DiagnosticReportTextTest {
    @Test public void formatsOnlyAllowlistedShapeFields() {
        assertEquals("DetourVpn — отчёт диагностики\nКод: invalid_response\nЭтап: lease_create\n"
            + GENERAL, DiagnosticReportText.format("invalid_response", "lease_create"));
        assertEquals("DetourVpn — отчёт диагностики\nКод: unknown_error\nЭтап: unknown\n"
            + GENERAL, DiagnosticReportText.format("unknown_error", "unknown"));
    }

    private static final String GENERAL = "Причина по этому коду не определена. Что сделать: отправьте этот отчёт и напишите, что делали перед сбоем.";

    @Test public void newStagesAndCoarseContextAreAccepted() {
        for (String stage : new String[] {"window_renewal", "window_advance", "tunnel_restart",
                "tunnel_monitor", "network_change", "session_resume", "panel_profile"}) {
            String text = DiagnosticReportText.format("renewal_too_late", stage, "confirmed",
                    "m5_to_30", "open", "recent", "renewal_failed");
            assertEquals(true, text.contains("Этап: " + stage));
            assertEquals(true, text.contains("\nВремя с handshake: m5_to_30"));
            assertEquals(true, text.contains("\nОкно: open"));
            assertEquals(true, text.contains("\nСмена сети: recent"));
            assertEquals(true, text.contains("окно не удалось продлить"));
            assertEquals(false, text.contains("unknown"));
        }
    }

    @Test public void rejectsFreeTextContextAndHints() {
        assertThrows(IllegalArgumentException.class, () -> DiagnosticReportText.format(
                "native_down", "tunnel_monitor", null, "12345s", null, null, "general"));
        assertThrows(IllegalArgumentException.class, () -> DiagnosticReportText.format(
                "native_down", "tunnel_monitor", null, null, "https://host", null, "general"));
        assertThrows(IllegalArgumentException.class, () -> DiagnosticReportText.format(
                "native_down", "tunnel_monitor", null, null, null, "wifi-home", "general"));
        assertThrows(IllegalArgumentException.class, () -> DiagnosticReportText.format(
                "native_down", "tunnel_monitor", null, null, null, null, "custom text"));
    }

    @Test public void handshakeAgeIsRoundedIntoRanges() {
        assertEquals("never", DiagnosticReportText.handshakeAgeBucket(-1));
        assertEquals("under_1m", DiagnosticReportText.handshakeAgeBucket(59));
        assertEquals("m1_to_5", DiagnosticReportText.handshakeAgeBucket(60));
        assertEquals("m1_to_5", DiagnosticReportText.handshakeAgeBucket(299));
        assertEquals("m5_to_30", DiagnosticReportText.handshakeAgeBucket(300));
        assertEquals("m30_to_120", DiagnosticReportText.handshakeAgeBucket(1800));
        assertEquals("over_2h", DiagnosticReportText.handshakeAgeBucket(7200));
    }

    @Test public void deviceLineIsBooleansOnly() {
        assertEquals("\nУстройство: экран=выкл, doze=да", DiagnosticReportText.deviceLine(false, true));
        assertEquals("\nУстройство: экран=вкл, doze=нет", DiagnosticReportText.deviceLine(true, false));
    }

    @Test public void rejectsUnlistedValuesEvenWhenTheyMatchTheIdentifierShape() {
        assertThrows(IllegalArgumentException.class,
            () -> DiagnosticReportText.format("secret", "lease_create"));
        assertThrows(IllegalArgumentException.class,
            () -> DiagnosticReportText.format("invalid_response", "user_email"));
    }

    @Test public void rejectsNullNewlinesUrlsAndOversizedFields() {
        assertThrows(IllegalArgumentException.class,
            () -> DiagnosticReportText.format(null, "lease_create"));
        assertThrows(IllegalArgumentException.class,
            () -> DiagnosticReportText.format("invalid_response\nsecret", "lease_create"));
        assertThrows(IllegalArgumentException.class,
            () -> DiagnosticReportText.format("invalid_response", "https://host/id"));
        assertThrows(IllegalArgumentException.class,
            () -> DiagnosticReportText.format("a".repeat(65), "lease_create"));
    }

    @Test public void nativeLineUsesOnlyFixedCodes() {
        assertEquals("\nVPN: degraded, core_running, transient, intent=on, validated=no,"
                + " recoveries=2, restarts=3",
                DiagnosticReportText.nativeLine("degraded", "core_running", "transient", true,
                        false, 2, 3));
        assertThrows(IllegalArgumentException.class, () -> DiagnosticReportText.nativeLine(
                "10.0.0.1", "none", "none", false, false, 0, 0));
        assertThrows(IllegalArgumentException.class, () -> DiagnosticReportText.nativeLine(
                "connected", "end_to_end_verified", "none", false, false, 0, 0));
    }
}
