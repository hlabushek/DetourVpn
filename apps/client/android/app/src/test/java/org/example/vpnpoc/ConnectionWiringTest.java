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

    /** ADR-155: a failed native init is retried before UI calls and before a restore engine. */
    @Test public void aFailedNativeInitIsRetriedBeforeItIsNeeded() throws Exception {
        String app = source("ClientApplication.java");
        assertTrue(body(app, "public void onCreate()").contains("worker.execute(this::initNative)"));
        String ensure = body(app, "boolean ensureNative()");
        assertTrue(ensure.contains("ConnectionSupervisor.nativeInitRetryDue(initFailedAt"));
        assertTrue(ensure.contains("initNative()"));
        String init = body(app, "private void initNative()");
        assertTrue("the device key is never replaced", init.contains("if (key == null) key = vault.device()"));
        assertTrue(init.contains("initFailedAt = SystemClock.elapsedRealtime()"));
        String restore = body(app, "void startAlwaysOnRecovery(VpnService service)");
        assertTrue(restore.indexOf("ensureNative()") >= 0
                && restore.indexOf("ensureNative()") < restore.indexOf("provider == null"));
        String activity = source("ClientActivity.java");
        int retry = activity.indexOf("if (!storage && !preference) app.ensureNative();");
        int check = activity.indexOf("if (!storage && !preference && app.provider == null)");
        assertTrue("retried before the provider check", retry >= 0 && check > retry);
    }
}
