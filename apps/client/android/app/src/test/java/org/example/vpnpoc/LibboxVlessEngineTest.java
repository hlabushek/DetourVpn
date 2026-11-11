package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.junit.Test;

/** TEST ONLY: ADR-114 libbox probe with fake checkers. No engine, tunnel or network is used. */
public class LibboxVlessEngineTest {
    @Test
    public void unitTestJvmHasNoLibboxBinding() {
        assertNull(LibboxVlessEngine.reflectiveChecker());
        VlessEngineCapability capability = LibboxVlessEngine.INSTANCE.probe();
        assertFalse(capability.available);
        assertEquals(VlessEngineCapability.REASON_ENGINE_NOT_BUNDLED, capability.reasonCode);
    }

    @Test
    public void engineThatAcceptsTheSelfTestIsAvailable() {
        List<String> seen = new ArrayList<>();
        VlessEngineCapability capability = new LibboxVlessEngine(seen::add).probe();
        assertTrue(capability.available);
        assertEquals(VlessEngine.ADAPTER_VERSION, capability.adapterVersion);
        assertEquals(1, seen.size());
        assertEquals(LibboxVlessEngine.SELF_TEST_CONFIG, seen.get(0));
    }

    @Test
    public void engineWithoutUtlsReportsRealityUnsupported() {
        VlessEngineCapability capability = new LibboxVlessEngine(config -> {
            throw new Exception("initialize outbound[1]: uTLS, which is required by reality is not included in this build");
        }).probe();
        assertFalse(capability.available);
        assertEquals(VlessEngineCapability.REASON_REALITY_UNSUPPORTED, capability.reasonCode);
    }

    @Test
    public void otherRefusalIsACheckFailure() {
        VlessEngineCapability capability = new LibboxVlessEngine(config -> {
            throw new IllegalStateException("decode config");
        }).probe();
        assertEquals(VlessEngineCapability.REASON_ENGINE_CHECK_FAILED, capability.reasonCode);
    }

    @Test
    public void missingNativeLibraryIsNotBundled() {
        VlessEngineCapability capability = new LibboxVlessEngine(config -> {
            throw new UnsatisfiedLinkError("TEST ONLY no gojni");
        }).probe();
        assertEquals(VlessEngineCapability.REASON_ENGINE_NOT_BUNDLED, capability.reasonCode);
    }

    @Test
    public void gateNeverProbesWhileTheFeatureIsOff() {
        List<String> seen = new ArrayList<>();
        VlessEngineCapability capability = VlessEngineGate.evaluate(false, new LibboxVlessEngine(seen::add));
        assertFalse(capability.available);
        assertEquals(VlessEngineCapability.REASON_FEATURE_DISABLED, capability.reasonCode);
        assertTrue(seen.isEmpty());
        assertEquals(VlessEngineCapability.REASON_FEATURE_DISABLED, VlessEngineGate.current().reasonCode);
    }

    @Test
    public void selfTestUsesOnlyFakeValuesAndAValidKeyLength() {
        String config = LibboxVlessEngine.SELF_TEST_CONFIG;
        Matcher key = Pattern.compile("\"public_key\":\"([A-Za-z0-9_-]+)\"").matcher(config);
        assertTrue(key.find());
        assertEquals(43, key.group(1).length());
        assertTrue(config.contains("\"server\":\"panel.example.test\""));
        assertTrue(config.contains("\"uuid\":\"00000000-0000-4000-8000-000000000001\""));
    }
}
