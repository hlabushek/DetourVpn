package org.example.vpnpoc;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

/**
 * ADR-114 option A: the pinned Amnezia Box {@code libbox} as the VLESS + Reality core.
 *
 * <p>Probe only. It never starts a tunnel. It asks the bundled engine to parse a fixed,
 * obviously fake VLESS + Reality config ({@code Libbox.checkConfig}, no network). An engine
 * built without {@code with_utls} refuses it, so a stale AAR can never pass the gate. The class
 * is looked up by reflection: the live client does not bundle libbox (ADR-084), and then this
 * reports {@link VlessEngineCapability#REASON_ENGINE_NOT_BUNDLED}.
 */
final class LibboxVlessEngine implements VlessEngine {
    static final String LIBBOX_CLASS = "io.nekohasekai.libbox.Libbox";

    /** Same shape as the Windows self-test (ADR-112): reserved test names, zero UUID. */
    static final String SELF_TEST_CONFIG =
            "{\"log\":{\"level\":\"error\"},"
                    + "\"dns\":{\"servers\":[{\"type\":\"local\",\"tag\":\"local-dns\"}]},"
                    + "\"outbounds\":[{\"type\":\"direct\",\"tag\":\"direct\"},"
                    + "{\"type\":\"vless\",\"tag\":\"proxy\",\"server\":\"panel.example.test\","
                    + "\"server_port\":443,\"uuid\":\"00000000-0000-4000-8000-000000000001\","
                    + "\"flow\":\"xtls-rprx-vision\",\"packet_encoding\":\"xudp\","
                    + "\"domain_resolver\":\"local-dns\",\"tls\":{\"enabled\":true,"
                    + "\"server_name\":\"www.example.test\","
                    + "\"utls\":{\"enabled\":true,\"fingerprint\":\"chrome\"},"
                    + "\"reality\":{\"enabled\":true,"
                    + "\"public_key\":\"rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrA\","
                    + "\"short_id\":\"0a1b\"}}}],"
                    + "\"route\":{\"final\":\"proxy\"}}";

    /** Checks one engine config; throws when the engine refuses it. */
    interface ConfigChecker {
        void check(String config) throws Exception;
    }

    static final LibboxVlessEngine INSTANCE = new LibboxVlessEngine(reflectiveChecker());

    private final ConfigChecker checker;

    LibboxVlessEngine(ConfigChecker checker) {
        this.checker = checker;
    }

    @Override
    public VlessEngineCapability probe() {
        if (checker == null) return VlessEngineCapability.unavailable(VlessEngineCapability.REASON_ENGINE_NOT_BUNDLED);
        try {
            checker.check(SELF_TEST_CONFIG);
            return VlessEngineCapability.available(ADAPTER_VERSION);
        } catch (Exception failure) {
            // Only a fixed code leaves this method; the engine message is not logged or kept.
            String message = String.valueOf(failure.getMessage());
            return VlessEngineCapability.unavailable(message.contains("uTLS")
                    ? VlessEngineCapability.REASON_REALITY_UNSUPPORTED
                    : VlessEngineCapability.REASON_ENGINE_CHECK_FAILED);
        } catch (LinkageError failure) {
            // The Java binding is present but its native library is not (or the wrong ABI).
            return VlessEngineCapability.unavailable(VlessEngineCapability.REASON_ENGINE_NOT_BUNDLED);
        }
    }

    /** {@code Libbox.checkConfig(String)} when the binding is on the classpath, else {@code null}. */
    static ConfigChecker reflectiveChecker() {
        final Method method;
        try {
            method = Class.forName(LIBBOX_CLASS, false, LibboxVlessEngine.class.getClassLoader())
                    .getMethod("checkConfig", String.class);
        } catch (ClassNotFoundException | NoSuchMethodException | LinkageError absent) {
            return null;
        }
        return config -> {
            try {
                method.invoke(null, config);
            } catch (InvocationTargetException failure) {
                Throwable cause = failure.getCause();
                if (cause instanceof Exception) throw (Exception) cause;
                if (cause instanceof Error) throw (Error) cause;
                throw failure;
            }
        };
    }
}
