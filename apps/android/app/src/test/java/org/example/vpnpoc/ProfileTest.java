package org.example.vpnpoc;

import org.amnezia.awg.crypto.KeyPair;
import org.junit.Test;
import static org.junit.Assert.*;

public class ProfileTest {
    private final KeyPair device = new KeyPair();
    private String profile() {
        // Synthetic documentation-only endpoint; no embedded private keys.
        return "[Interface]\nAddress = 10.203.71.2/30\nDNS = 192.0.2.53\n"
            + "Jc = 4\nJmin = 40\nJmax = 70\nS1 = 20\nS2 = 30\n"
            + "H1 = 1001\nH2 = 1002\nH3 = 1003\nH4 = 1004\n"
            + "[Peer]\nPublicKey = " + new KeyPair().getPublicKey().toBase64()
            + "\nEndpoint = 192.0.2.1:51820\nAllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n";
    }
    private void rejects(String text) {
        try { Profile.parse(text, device); fail("Unsafe profile accepted"); }
        catch (Exception expected) { /* Parser details must never reach logs/UI. */ }
    }
    @Test public void usesOnlyDeviceGeneratedKeyAndPreservesAwg() throws Exception {
        var parsed = Profile.parse(profile(), device);
        assertEquals(device.getPublicKey(), parsed.getInterface().getKeyPair().getPublicKey());
        assertTrue(parsed.toAwgUserspaceString().contains("jc=4"));
    }
    @Test public void importedPrivateKeyAndDuplicateInterfaceRejected() {
        rejects(profile().replace("Address =", "PrivateKey = ignored\nAddress ="));
        rejects(profile() + "\n[Interface]\n");
    }
    @Test public void splitRoutesDnsHostnamesAndAppBypassRejected() {
        rejects(profile().replace("0.0.0.0/0", "10.0.0.0/8"));
        rejects(profile().replace("192.0.2.53", "resolver.example"));
        rejects(profile().replace("DNS =", "ExcludedApplications = org.example.other\nDNS ="));
        rejects(profile().replace("192.0.2.1:51820", "node.example:51820"));
    }
    @Test public void unknownAwgAndIpv6ConfigurationRejected() {
        rejects(profile().replace("Jc = 4", "JunkUnknown = 4"));
        rejects(profile().replace("10.203.71.2/30", "2001:db8::2/64"));
        rejects(profile().replace("0.0.0.0/0", "0.0.0.0/0, ::/0"));
        rejects("a".repeat(16385));
    }
}
