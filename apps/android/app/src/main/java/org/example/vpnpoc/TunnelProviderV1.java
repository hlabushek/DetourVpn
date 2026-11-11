package org.example.vpnpoc;

import org.amnezia.awg.config.Config;
import java.util.Set;

/** Native adapter contract v1. No private-key export or remote command surface. */
interface TunnelProviderV1 {
    int CONTRACT_VERSION = 1;
    Set<String> capabilities();
    void connect(Config config) throws Exception;
    void disconnect() throws Exception;
    boolean isUp();
    long lastHandshakeSeconds();
    // Optional advisory only; -1 means unavailable. Never use for settlement.
    default long transferBytes() { return -1; }
    // Optional inbound-only advisory; -1 means unavailable. Never use for settlement.
    default long receivedBytes() { return -1; }
    // ADR-099: recreate the running tunnel with a new socket; on failure the tunnel is down.
    default void restartInPlace() throws Exception { throw new UnsupportedOperationException(); }
}
