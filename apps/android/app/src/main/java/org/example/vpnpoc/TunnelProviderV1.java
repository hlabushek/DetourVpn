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
}
