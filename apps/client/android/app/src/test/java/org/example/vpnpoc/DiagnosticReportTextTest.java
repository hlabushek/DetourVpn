package org.example.vpnpoc;

import org.junit.Test;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

public class DiagnosticReportTextTest {
    @Test public void formatsOnlyAllowlistedShapeFields() {
        assertEquals("DetourVpn — отчёт диагностики\nКод: invalid_response\nЭтап: lease_create",
            DiagnosticReportText.format("invalid_response", "lease_create"));
        assertEquals("DetourVpn — отчёт диагностики\nКод: unknown_error\nЭтап: unknown",
            DiagnosticReportText.format("unknown_error", "unknown"));
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
}
