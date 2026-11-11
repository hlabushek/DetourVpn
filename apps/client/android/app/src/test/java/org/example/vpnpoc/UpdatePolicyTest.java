package org.example.vpnpoc;

import java.io.File;
import java.io.FileOutputStream;
import org.junit.Test;
import static org.junit.Assert.*;

public class UpdatePolicyTest {
    @Test public void decisionFollowsMinimumAndLatest() {
        assertEquals(UpdatePolicy.Decision.NONE, UpdatePolicy.decide(30, 30, 20));
        assertEquals(UpdatePolicy.Decision.NONE, UpdatePolicy.decide(31, 30, 20)); // no downgrade
        assertEquals(UpdatePolicy.Decision.OPTIONAL, UpdatePolicy.decide(25, 30, 20));
        assertEquals(UpdatePolicy.Decision.BLOCKING, UpdatePolicy.decide(19, 30, 20));
    }

    @Test public void invalidManifestIsRejected() {
        try { UpdatePolicy.decide(1, 10, 11); fail(); } catch (IllegalArgumentException expected) { }
        try { UpdatePolicy.decide(0, 10, 5); fail(); } catch (IllegalArgumentException expected) { }
    }

    @Test public void onlyHttpsUrlsAreAccepted() {
        assertTrue(UpdatePolicy.isAcceptableUrl("https://updates.example.test/d/app-1.2.3.apk"));
        assertFalse(UpdatePolicy.isAcceptableUrl("http://updates.example.test/app.apk"));
        assertFalse(UpdatePolicy.isAcceptableUrl("https://updates.example.test/a b.apk"));
        assertFalse(UpdatePolicy.isAcceptableUrl(null));
    }

    @Test public void hashMustMatchExactly() throws Exception {
        File apk = File.createTempFile("upd", ".apk");
        try (FileOutputStream out = new FileOutputStream(apk)) { out.write("abc".getBytes("UTF-8")); }
        String sha = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
        assertTrue(UpdatePolicy.sha256Matches(apk, sha));
        assertFalse(UpdatePolicy.sha256Matches(apk, sha.toUpperCase()));
        assertFalse(UpdatePolicy.sha256Matches(apk, "0" + sha.substring(1)));
        assertFalse(UpdatePolicy.sha256Matches(apk, null));
        apk.delete();
    }
}
