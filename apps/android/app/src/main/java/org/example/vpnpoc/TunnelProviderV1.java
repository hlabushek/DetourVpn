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
    default void setAuthorizationDeadline(long end) { throw new UnsupportedOperationException(); }
    default void cancelImmediately() { }
    default void suspendTransport() { }
    default long deliveredDataAge() { return -1; }
    default void bindNetwork(android.net.Network network, long revision) throws Exception {
        throw new UnsupportedOperationException("network_binding_unavailable");
    }
    long lastHandshakeSeconds();
    // Optional advisory only; -1 means unavailable. Never use for settlement.
    default long transferBytes() { return -1; }
    // Optional inbound-only advisory; -1 means unavailable. Never use for settlement.
    default long receivedBytes() { return -1; }
}
