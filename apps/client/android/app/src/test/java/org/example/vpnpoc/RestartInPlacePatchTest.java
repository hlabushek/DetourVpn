package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import org.junit.Test;

/**
 * ADR-127 static guard. GoBackend is a prepared upstream source tree (not on the JVM test
 * classpath), so this checks the reviewed patch text: restartInPlace must clear the live handle
 * before calling setStateInternal, which otherwise returns early ("already up") and does nothing.
 * The behaviour itself needs a device (see RELIABILITY2_DEVICE_TESTS.md).
 */
public class RestartInPlacePatchTest {
    @Test public void restartInPlaceClearsTheHandleBeforeBringingTheTunnelUp() throws Exception {
        Path patch = Paths.get("..", "..", "..", "android", "patches", "go-only.patch");
        String text = new String(Files.readAllBytes(patch), StandardCharsets.UTF_8);
        int method = text.indexOf("+    public synchronized void restartInPlace()");
        assertTrue("restartInPlace hunk present", method >= 0);
        int clear = text.indexOf("+            currentTunnelHandle = -1;", method);
        int up = text.indexOf("+            setStateInternal(tunnel, config, State.UP);", method);
        assertTrue("setStateInternal(UP) present", up > method);
        assertTrue("handle cleared before setStateInternal(UP)", clear > method && clear < up);
        assertTrue("early return still guards normal connects",
                text.contains(" if (currentTunnelHandle != -1) {"));
        assertTrue("old device closed after a successful restart",
                text.indexOf("+        if (currentTunnelHandle != old)", up) > up);
    }

    /**
     * ADR-138 (G): a restart replaces the interface through one establish() on the same service.
     * On success nothing is torn down before the new device is up; the old device is closed only
     * afterwards. Static check of the reviewed patch text; device proof is still missing
     * (RELIABILITY2_DEVICE_TESTS.md, test B/D).
     */
    @Test public void successfulRestartDoesNotTearDownBeforeTheNewInterfaceIsUp() throws Exception {
        String text = patchText();
        int method = text.indexOf("+    public synchronized void restartInPlace()");
        int end = text.indexOf("private void setStateInternal", method);
        assertTrue("restartInPlace hunk ends before setStateInternal", end > method);
        String body = text.substring(method, end);
        int tryStart = body.indexOf("try {");
        int catchStart = body.indexOf("} catch (final Exception e)");
        assertTrue("try block and catch present", tryStart > 0 && catchStart > tryStart);
        String success = body.substring(tryStart, catchStart);
        assertTrue("the success path brings the tunnel up", success.contains("setStateInternal(tunnel, config, State.UP)"));
        assertFalse("no DOWN transition on the success path", success.contains("State.DOWN"));
        assertFalse("no device close on the success path before the new one is up",
                success.contains("awgTurnOff"));
        assertFalse("no service stop on the success path", success.contains("stopSelf"));
        int closeOld = body.indexOf("if (currentTunnelHandle != old)", catchStart);
        assertTrue("old device is closed only after the successful bring-up", closeOld > catchStart);
        assertTrue("the old device close is the last step",
                body.indexOf("awgTurnOff(old)", closeOld) > closeOld);
    }

    /** ADR-138 (G): one interface creation per UP transition, in the same service. */
    @Test public void tunnelUpEstablishesTheInterfaceExactlyOnce() throws Exception {
        String text = patchText();
        int count = 0;
        for (int at = text.indexOf("builder.establish()"); at >= 0;
                at = text.indexOf("builder.establish()", at + 1)) count++;
        assertEquals("one establish() call in the restart path", 1, count);
    }

    private static String patchText() throws Exception {
        Path patch = Paths.get("..", "..", "..", "android", "patches", "go-only.patch");
        return new String(Files.readAllBytes(patch), StandardCharsets.UTF_8);
    }
}
