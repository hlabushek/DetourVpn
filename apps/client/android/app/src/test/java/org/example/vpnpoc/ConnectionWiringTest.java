package org.example.vpnpoc;

import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import org.junit.Test;

/**
 * Static guards for ClientApplication wiring that has no JVM-testable seam (ADR-153 onwards), in
 * the style of {@link SystemIntegrationWiringTest}. Run with the working directory
 * {@code apps/client/android/app} (Gradle's default for unit tests). Behaviour needs a device.
 */
public class ConnectionWiringTest {
    static String source(String name) throws Exception {
        return new String(Files.readAllBytes(Paths.get("src", "main", "java", "org", "example",
                "vpnpoc", name)), StandardCharsets.UTF_8);
    }

    static String body(String text, String signature) {
        int start = text.indexOf(signature);
        assertTrue(signature + " present", start >= 0);
        int brace = text.indexOf('{', start);
        int depth = 0;
        for (int i = brace; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c == '{') depth++;
            if (c == '}' && --depth == 0) return text.substring(brace, i + 1);
        }
        throw new AssertionError("unbalanced " + signature);
    }

    /** ADR-153: every stop and every new window resets the watchdog; a window starts its own. */
    @Test public void theWatchdogForgetsTheOldWindowOnStopAndOnANewWindow() throws Exception {
        String app = source("ClientApplication.java");
        String forget = body(app, "private void forgetWindowWatch()");
        assertTrue(forget.contains("watchdog.tunnelDown()"));
        assertTrue(forget.contains("pendingCheck.cancel(false)"));
        assertTrue(body(app, "void stop() throws Exception").contains("forgetWindowWatch()"));
        assertTrue(body(app, "void prepareWindow()").contains("forgetWindowWatch()"));
        String begin = body(app, "void beginWindow()");
        assertTrue(begin.indexOf("watchdog.tunnelUp(now)") > begin.indexOf("windowUpAt = now"));
    }
}
