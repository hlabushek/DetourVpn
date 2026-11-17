package org.example.vpnpoc;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** ADR-145 (2.1): one-time prompts of the connect consent step. */
public final class ConnectPromptsTest {
    @Test public void notificationsAreAskedOnceOnAndroid13AndLater() {
        assertTrue(ConnectPrompts.askNotifications(33, false, false));
        assertTrue(ConnectPrompts.askNotifications(36, false, false));
        assertFalse("asked before", ConnectPrompts.askNotifications(36, false, true));
        assertFalse("already granted", ConnectPrompts.askNotifications(36, true, false));
        assertFalse("no runtime permission before 13", ConnectPrompts.askNotifications(32, false, false));
    }

    @Test public void batteryIsExplainedOnceWhileNotExempt() {
        assertTrue(ConnectPrompts.explainBattery(false, 0));
        assertFalse("already exempt", ConnectPrompts.explainBattery(true, 0));
        assertFalse("asked at an earlier connect", ConnectPrompts.explainBattery(false, 1));
        assertFalse("asked twice by older builds", ConnectPrompts.explainBattery(false, 2));
    }
}
