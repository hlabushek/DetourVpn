package org.example.vpnpoc;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** TEST ONLY: synthetic engine readings; no tunnel. */
public class HandshakeMemoryTest {
    private static final long NOW = 1_800_000_000L;

    @Test
    public void nothingSeenUntilAHandshakeIsObserved() {
        HandshakeMemory memory = new HandshakeMemory();
        assertFalse(memory.seen());
        memory.observe(0, NOW);
        memory.observe(-1, NOW);
        assertFalse(memory.seen());
        memory.observe(NOW - 3, NOW);
        assertTrue(memory.seen());
    }

    @Test
    public void staysSeenAfterTheTunnelStopsUntilTheNextConnect() {
        HandshakeMemory memory = new HandshakeMemory();
        memory.observe(NOW - 3, NOW);
        memory.observe(0, NOW + 900); // tunnel down: the engine reports no handshake any more
        assertTrue(memory.seen());
        memory.reset();
        assertFalse(memory.seen());
    }

    @Test
    public void bogusFutureReadingIsIgnored() {
        HandshakeMemory memory = new HandshakeMemory();
        memory.observe(NOW + 3600, NOW);
        assertFalse(memory.seen());
        memory.observe(NOW + 30, NOW); // small clock skew is tolerated
        assertTrue(memory.seen());
    }
}
