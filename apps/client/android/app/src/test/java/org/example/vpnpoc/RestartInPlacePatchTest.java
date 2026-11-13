package org.example.vpnpoc;

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
}
