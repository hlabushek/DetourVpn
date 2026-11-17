package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** ADR-148: probe configuration. TEST ONLY host names under the reserved .test domain. */
public class DeadPeerProbeTest {
    @Test public void theShippedPlaceholderKeepsTheProbeAndFailoverOff() {
        var config = DeadPeerProbe.fromConfig("none", false);
        assertFalse(config.enabled());
        assertFalse(config.failoverOptIn);
        var missing = DeadPeerProbe.fromConfig(null, null);
        assertFalse(missing.enabled());
        assertFalse("failover defaults to off", missing.failoverOptIn);
        assertFalse(DeadPeerProbe.off().enabled());
        assertFalse(DeadPeerProbe.off().failoverOptIn);
    }

    @Test public void failoverNeedsAnExplicitTrue() {
        assertTrue(DeadPeerProbe.fromConfig("none", true).failoverOptIn);
        assertTrue(DeadPeerProbe.fromConfig("none", "true").failoverOptIn);
        assertFalse(DeadPeerProbe.fromConfig("none", "TRUE").failoverOptIn);
        assertFalse(DeadPeerProbe.fromConfig("none", 1).failoverOptIn);
        assertFalse(DeadPeerProbe.fromConfig("none", "yes").failoverOptIn);
    }

    @Test public void aPlainHttpsHealthUrlIsAccepted() {
        assertEquals("https://health.example.test/healthz",
                DeadPeerProbe.validUrl("https://health.example.test/healthz"));
        assertEquals("https://health.example.test",
                DeadPeerProbe.validUrl("https://health.example.test"));
        assertEquals("https://health.example.test:8443/v1/ok",
                DeadPeerProbe.validUrl("https://health.example.test:8443/v1/ok"));
        assertTrue(DeadPeerProbe.fromConfig("https://health.example.test/healthz", false)
                .enabled());
    }

    @Test public void anythingThatCouldCarryDataOrIsNotProjectHttpsIsRefused() {
        for (String raw : new String[] {
                "", "none", "http://health.example.test/healthz",
                "https://user:secret@health.example.test/healthz",
                "https://health.example.test/healthz?device=1",
                "https://health.example.test/healthz#x",
                "https://198.51.100.7/healthz",
                "https://[2001:db8::1]/healthz",
                "https://localhost/healthz",
                "https://Health.Example.test/healthz",
                "https://health.example.test/a%20b",
                "https://health.example.test:0/healthz",
                "ftp://health.example.test/healthz",
                "https:health.example.test",
                "https://health.example.test/" + "a".repeat(300),
        }) {
            assertNull(raw, DeadPeerProbe.validUrl(raw));
        }
        assertNull(DeadPeerProbe.validUrl(null));
        assertFalse(DeadPeerProbe.fromConfig(42, false).enabled());
    }

    @Test public void onlyA2xxAnswerPasses() {
        assertEquals(DeadPeerWatch.ProbeResult.PASS, DeadPeerProbe.classify(200));
        assertEquals(DeadPeerWatch.ProbeResult.PASS, DeadPeerProbe.classify(204));
        assertEquals(DeadPeerWatch.ProbeResult.FAIL, DeadPeerProbe.classify(301));
        assertEquals(DeadPeerWatch.ProbeResult.FAIL, DeadPeerProbe.classify(404));
        assertEquals(DeadPeerWatch.ProbeResult.FAIL, DeadPeerProbe.classify(503));
        assertEquals(DeadPeerWatch.ProbeResult.FAIL, DeadPeerProbe.classify(-1));
    }
}
