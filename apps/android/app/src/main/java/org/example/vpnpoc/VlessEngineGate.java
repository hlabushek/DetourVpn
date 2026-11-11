package org.example.vpnpoc;

/**
 * Decides whether a VLESS + Reality outbound may be offered on this device. Disabled by default:
 * {@link #FEATURE_ENABLED} is {@code false} and the bundled engine is {@link NoVlessEngine}, so
 * panel servers still show the Russian "not supported on Android" text (ADR-113).
 */
final class VlessEngineGate {
    /** Off until a real engine passes a physical-device test (ADR-114). Do not flip in this issue. */
    static final boolean FEATURE_ENABLED = false;

    private VlessEngineGate() {}

    /** Pure decision, so tests can supply any engine and flag value. */
    static VlessEngineCapability evaluate(boolean featureEnabled, VlessEngine engine) {
        if (!featureEnabled) return VlessEngineCapability.unavailable(VlessEngineCapability.REASON_FEATURE_DISABLED);
        if (engine == null) return VlessEngineCapability.unavailable(VlessEngineCapability.REASON_ENGINE_NOT_BUNDLED);
        VlessEngineCapability capability = engine.probe();
        if (capability == null) return VlessEngineCapability.unavailable(VlessEngineCapability.REASON_ENGINE_NOT_BUNDLED);
        if (!capability.available) return capability;
        if (capability.adapterVersion != VlessEngine.ADAPTER_VERSION)
            return VlessEngineCapability.unavailable(VlessEngineCapability.REASON_ADAPTER_VERSION_MISMATCH);
        return capability;
    }

    /**
     * The capability of this build: always unavailable while {@link #FEATURE_ENABLED} is false.
     * The engine is the libbox probe (ADR-114); it is never even probed while the flag is off.
     */
    static VlessEngineCapability current() {
        return evaluate(FEATURE_ENABLED, LibboxVlessEngine.INSTANCE);
    }

    /** Placeholder engine: no VLESS core is bundled. */
    static final class NoVlessEngine implements VlessEngine {
        static final NoVlessEngine INSTANCE = new NoVlessEngine();

        @Override
        public VlessEngineCapability probe() {
            return VlessEngineCapability.unavailable(VlessEngineCapability.REASON_ENGINE_NOT_BUNDLED);
        }
    }
}
