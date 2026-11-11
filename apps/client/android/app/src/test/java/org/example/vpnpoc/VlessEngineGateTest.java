package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** Capability gate for the VLESS + Reality adapter (ADR-114). Covers the disabled default and refusals. */
public class VlessEngineGateTest {

    /** Fake engine: reports whatever it was given. Test-only, not a real core. */
    private static final class FakeEngine implements VlessEngine {
        private final VlessEngineCapability reply;

        FakeEngine(VlessEngineCapability reply) {
            this.reply = reply;
        }

        @Override
        public VlessEngineCapability probe() {
            return reply;
        }
    }

    @Test
    public void featureIsDisabledByDefault() {
        assertFalse(VlessEngineGate.FEATURE_ENABLED);
    }

    @Test
    public void currentBuildIsUnavailableWithFeatureDisabled() {
        VlessEngineCapability capability = VlessEngineGate.current();
        assertFalse(capability.available);
        assertEquals(VlessEngineCapability.REASON_FEATURE_DISABLED, capability.reasonCode);
    }

    @Test
    public void disabledFlagRefusesEvenAFullyWorkingEngine() {
        FakeEngine working = new FakeEngine(VlessEngineCapability.available(VlessEngine.ADAPTER_VERSION));
        VlessEngineCapability capability = VlessEngineGate.evaluate(false, working);
        assertFalse(capability.available);
        assertEquals(VlessEngineCapability.REASON_FEATURE_DISABLED, capability.reasonCode);
    }

    @Test
    public void enabledWithoutEngineIsNotBundled() {
        VlessEngineCapability capability = VlessEngineGate.evaluate(true, null);
        assertFalse(capability.available);
        assertEquals(VlessEngineCapability.REASON_ENGINE_NOT_BUNDLED, capability.reasonCode);
    }

    @Test
    public void placeholderEngineReportsNotBundled() {
        VlessEngineCapability capability = VlessEngineGate.NoVlessEngine.INSTANCE.probe();
        assertFalse(capability.available);
        assertEquals(VlessEngineCapability.REASON_ENGINE_NOT_BUNDLED, capability.reasonCode);
    }

    @Test
    public void enabledWithPlaceholderEngineStaysUnavailable() {
        VlessEngineCapability capability = VlessEngineGate.evaluate(true, VlessEngineGate.NoVlessEngine.INSTANCE);
        assertFalse(capability.available);
        assertEquals(VlessEngineCapability.REASON_ENGINE_NOT_BUNDLED, capability.reasonCode);
    }

    @Test
    public void nullProbeResultIsTreatedAsNotBundled() {
        VlessEngineCapability capability = VlessEngineGate.evaluate(true, new FakeEngine(null));
        assertFalse(capability.available);
        assertEquals(VlessEngineCapability.REASON_ENGINE_NOT_BUNDLED, capability.reasonCode);
    }

    @Test
    public void mismatchedAdapterVersionIsRefused() {
        FakeEngine stale = new FakeEngine(VlessEngineCapability.available(VlessEngine.ADAPTER_VERSION + 1));
        VlessEngineCapability capability = VlessEngineGate.evaluate(true, stale);
        assertFalse(capability.available);
        assertEquals(VlessEngineCapability.REASON_ADAPTER_VERSION_MISMATCH, capability.reasonCode);
    }

    @Test
    public void engineUnavailableReasonIsPassedThrough() {
        FakeEngine noUtls = new FakeEngine(VlessEngineCapability.unavailable("utls_not_compiled"));
        VlessEngineCapability capability = VlessEngineGate.evaluate(true, noUtls);
        assertFalse(capability.available);
        assertEquals("utls_not_compiled", capability.reasonCode);
    }

    @Test
    public void matchingAdapterVersionWhenEnabledIsAvailable() {
        FakeEngine ok = new FakeEngine(VlessEngineCapability.available(VlessEngine.ADAPTER_VERSION));
        VlessEngineCapability capability = VlessEngineGate.evaluate(true, ok);
        assertTrue(capability.available);
        assertEquals(VlessEngineCapability.REASON_OK, capability.reasonCode);
        assertEquals(VlessEngine.ADAPTER_VERSION, capability.adapterVersion);
    }

    @Test
    public void unavailableRequiresNonEmptyReason() {
        assertNotNull(VlessEngineCapability.unavailable("x"));
        try {
            VlessEngineCapability.unavailable("");
            throw new AssertionError("empty reason accepted");
        } catch (IllegalArgumentException expected) {
            assertEquals("reason_code_invalid", expected.getMessage());
        }
        try {
            VlessEngineCapability.unavailable(VlessEngineCapability.REASON_OK);
            throw new AssertionError("ok accepted as unavailable");
        } catch (IllegalArgumentException expected) {
            assertEquals("reason_code_invalid", expected.getMessage());
        }
    }
}
