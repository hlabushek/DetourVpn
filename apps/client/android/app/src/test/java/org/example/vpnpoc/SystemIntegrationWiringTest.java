package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import org.junit.Test;

/**
 * ADR-149 static guards (like {@link RestartInPlacePatchTest}): the wiring in ClientApplication,
 * the reviewed GoBackend patch, its pinned hash and the manifest. Run with the working directory
 * {@code apps/client/android/app} (Gradle's default for unit tests). Behaviour needs a device.
 */
public class SystemIntegrationWiringTest {
    private static String read(String... parts) throws Exception {
        Path path = Paths.get("", parts);
        return new String(Files.readAllBytes(path), StandardCharsets.UTF_8);
    }

    private static String app() throws Exception {
        return read("src", "main", "java", "org", "example", "vpnpoc", "ClientApplication.java");
    }

    private static String body(String text, String signature) {
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

    /** R6: Lockdown is never read before establish(); it is read in beginWindow after it. */
    @Test public void lockdownIsReadAfterEstablishOnly() throws Exception {
        String text = app();
        String restoreStart = body(text, "void startAlwaysOnRecovery(VpnService service)");
        assertFalse("no Lockdown read on a restore start (before establish)",
                restoreStart.contains("isLockdownEnabled"));
        assertFalse("lockdown is not assigned from a raw platform read",
                text.contains("lockdown = service.isLockdownEnabled()"));
        String begin = body(text, "void beginWindow()");
        int up = begin.indexOf("supervisor.tunnelUp(");
        int read = begin.indexOf("observeAlwaysOn(protector, tunnelUpNow())");
        assertTrue("beginWindow reads Always-on/Lockdown after the tunnel is up", up >= 0 && read > up);
        String observe = body(text, "private void observeAlwaysOn(VpnService service, boolean vpnEstablished)");
        assertTrue("the read goes through the shared rule",
                observe.contains("lockdown = AlwaysOnStatus.lockdownAfterRead(value, lockdown)"));
        assertTrue("the flag is updated before the unchanged-display early return",
                observe.indexOf("lockdownAfterRead") < observe.indexOf("value.equals(alwaysOn)"));
    }

    /** 6.1: every service instance, including a UI-started one, is reported and remembered. */
    @Test public void uiStartedServicesGetAProtector() throws Exception {
        String text = app();
        assertTrue(text.contains("@Override public void serviceCreated(GoBackend.VpnService service)"));
        String created = body(text, "public void serviceCreated(GoBackend.VpnService service)");
        assertTrue(created.contains("protector = ServiceProtector.created(protector, service)"));
        String stopped = body(text, "public void alwaysOnStopped(GoBackend.VpnService service)");
        assertTrue(stopped.contains("protector = ServiceProtector.stopped(protector, service)"));

        String patch = read("..", "..", "..", "android", "patches", "go-only.patch");
        assertTrue("callback declared", patch.contains("+        void serviceCreated(VpnService service);"));
        int onCreate = patch.indexOf("         public void onCreate() {");
        int call = patch.indexOf("+                alwaysOnCallback.serviceCreated(this);", onCreate);
        int complete = patch.indexOf("+            vpnService.complete(this);", onCreate);
        int end = patch.indexOf("public void onDestroy()", onCreate);
        assertTrue("reported in onCreate before the service future completes",
                onCreate >= 0 && call > onCreate && complete > call && end > complete);
    }

    /** The reviewed patch and the hash pinned for the prepared SDK tree stay in sync. */
    @Test public void pinnedPatchHashMatchesThePatch() throws Exception {
        byte[] patch = Files.readAllBytes(Paths.get("..", "..", "..", "android", "patches", "go-only.patch"));
        StringBuilder hex = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(patch))
            hex.append(String.format("%02x", b));
        String gradle = read("..", "..", "..", "android", "tunnel", "build.gradle.kts");
        assertTrue("build.gradle.kts pins " + hex,
                gradle.contains("val expectedPatchHash = \"" + hex + "\""));
    }

    /** 6.2(a) and 6.3: boot and update use one receiver; nothing is direct-boot aware. */
    @Test public void manifestRestoresOnBootAndUpdateOnlyAfterUnlock() throws Exception {
        String manifest = read("src", "main", "AndroidManifest.xml");
        int receiver = manifest.indexOf("<receiver android:name=\"org.example.vpnpoc.BootReceiver\"");
        int close = manifest.indexOf("</receiver>", receiver);
        assertTrue(receiver >= 0 && close > receiver);
        String filter = manifest.substring(receiver, close);
        assertTrue(filter.contains("android.intent.action.BOOT_COMPLETED"));
        assertTrue(filter.contains("android.intent.action.MY_PACKAGE_REPLACED"));
        assertEquals("exactly two actions", 2, filter.split("<action ", -1).length - 1);
        assertFalse(manifest.contains("android:directBootAware"));
        assertFalse(manifest.contains("LOCKED_BOOT_COMPLETED\" />"));
        assertFalse(manifest.contains("android:defaultToDeviceProtectedStorage"));
    }
}
