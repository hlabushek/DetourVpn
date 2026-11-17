package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertSame;

import org.junit.Test;

/** ADR-149 (6.1): a protector for every instance of the app's own VPN service. */
public class ServiceProtectorTest {
    private final Object uiStarted = new Object();
    private final Object alwaysOnStarted = new Object();

    @Test public void aUiStartedServiceBecomesTheProtector() {
        Object protector = ServiceProtector.created(null, uiStarted);
        assertSame(uiStarted, protector);
        // Under Lockdown native renewal can now use a protected socket instead of refusing.
        assertEquals(RenewalOwner.Transport.PROTECTED,
                RenewalOwner.transport(true, protector != null));
        assertEquals(true, RenewalOwner.nativeMayStart(false, false, true, protector != null));
        // Without Lockdown nothing changes: the direct path is used, the protector is not.
        assertEquals(RenewalOwner.Transport.DIRECT, RenewalOwner.transport(false, true));
    }

    @Test public void withoutTheServiceUnderLockdownNativeRenewalStaysRefused() {
        Object protector = ServiceProtector.created(null, null);
        assertNull(protector);
        assertEquals(RenewalOwner.Transport.NONE, RenewalOwner.transport(true, false));
    }

    @Test public void aNewerInstanceReplacesAnOlderOne() {
        Object protector = ServiceProtector.created(uiStarted, alwaysOnStarted);
        assertSame(alwaysOnStarted, protector);
        // The same instance reported twice (onCreate, then the Always-on callback) is a no-op.
        assertSame(alwaysOnStarted, ServiceProtector.created(protector, alwaysOnStarted));
    }

    @Test public void aLateStopOfAnOlderInstanceKeepsTheCurrentOne() {
        Object protector = ServiceProtector.created(null, alwaysOnStarted);
        assertSame(alwaysOnStarted, ServiceProtector.stopped(protector, uiStarted));
    }

    @Test public void theStopOfTheCurrentInstanceClearsIt() {
        Object protector = ServiceProtector.created(null, uiStarted);
        protector = ServiceProtector.stopped(protector, uiStarted);
        assertNull(protector);
        assertEquals(RenewalOwner.Transport.NONE, RenewalOwner.transport(true, protector != null));
    }
}
