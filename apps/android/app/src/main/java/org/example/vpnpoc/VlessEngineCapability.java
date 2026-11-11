package org.example.vpnpoc;

/** Immutable result of a VLESS engine capability check. Reason codes are stable strings for logs and tests. */
final class VlessEngineCapability {
    static final String REASON_OK = "ok";
    static final String REASON_FEATURE_DISABLED = "feature_disabled";
    static final String REASON_ENGINE_NOT_BUNDLED = "engine_not_bundled";
    static final String REASON_ADAPTER_VERSION_MISMATCH = "adapter_version_mismatch";

    final boolean available;
    final String reasonCode;
    final int adapterVersion;

    private VlessEngineCapability(boolean available, String reasonCode, int adapterVersion) {
        this.available = available;
        this.reasonCode = reasonCode;
        this.adapterVersion = adapterVersion;
    }

    static VlessEngineCapability available(int adapterVersion) {
        return new VlessEngineCapability(true, REASON_OK, adapterVersion);
    }

    static VlessEngineCapability unavailable(String reasonCode) {
        if (reasonCode == null || reasonCode.isEmpty() || REASON_OK.equals(reasonCode))
            throw new IllegalArgumentException("reason_code_invalid");
        return new VlessEngineCapability(false, reasonCode, 0);
    }
}
