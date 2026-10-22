package org.example.vpnpoc;

import org.junit.Test;
import static org.junit.Assert.*;

public class TunnelStatusTest {
    @Test public void nativeUpWithoutHandshakeIsNotConnected() {
        assertTrue(TunnelStatus.describe(true, 0, 200).contains("ожидается"));
        assertTrue(TunnelStatus.describe(true, -2, 200).contains("ожидается"));
    }
    @Test public void staleAndFutureHandshakeAreNotHealthy() {
        assertTrue(TunnelStatus.describe(true, 1, 182).contains("устарел"));
        assertTrue(TunnelStatus.describe(true, 201, 200).contains("устарел"));
    }
    @Test public void handshakeDoesNotProveEgress() {
        assertTrue(TunnelStatus.describe(true, 199, 200).contains("требуют проверки"));
        assertEquals("Отключено", TunnelStatus.describe(false, 199, 200));
    }
}
