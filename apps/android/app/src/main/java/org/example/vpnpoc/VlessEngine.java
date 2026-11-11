package org.example.vpnpoc;

/**
 * Versioned adapter contract for a VLESS + Reality tunnel engine (AGENTS.md: tunnel engines
 * implement versioned adapter interfaces with capability checks). See docs/adr/114-android-vless-core.md.
 *
 * <p>Preparation only: no engine implements a working tunnel yet, and the feature is off
 * ({@link VlessEngineGate#FEATURE_ENABLED}).
 */
interface VlessEngine {
    /** Version of this contract. Bump when the meaning of a method changes. */
    int ADAPTER_VERSION = 1;

    /**
     * Reports what this build can do. Must not start a tunnel, open sockets or touch the network.
     *
     * @return capability; {@code null} is treated as unavailable by {@link VlessEngineGate}.
     */
    VlessEngineCapability probe();
}
