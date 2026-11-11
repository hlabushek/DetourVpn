package org.example.vpnpoc;

import org.junit.Test;
import static org.junit.Assert.*;

public class DnsChoiceTest {
    @Test public void onlyListedResolversAreAccepted() {
        assertEquals(java.util.Arrays.asList("1.1.1.1", "8.8.8.8", "94.140.14.14", "9.9.9.9"), DnsChoice.SERVERS);
        assertNull(DnsChoice.canonicalOrNull(""));
        assertNull(DnsChoice.canonicalOrNull(null));
        assertEquals("9.9.9.9", DnsChoice.canonicalOrNull("9.9.9.9"));
        try { DnsChoice.canonicalOrNull("10.0.0.1"); fail(); } catch (IllegalArgumentException expected) { }
    }

    @Test public void readPathFallsBackToDefaultForUnknownValues() {
        assertNull(DnsChoice.storedOrNull("203.0.113.7"));
        assertEquals("1.1.1.1", DnsChoice.storedOrNull("1.1.1.1"));
    }

    @Test public void nullServerLeavesProfileUnchanged() {
        String text = "[Interface]\nPrivateKey = x\nDNS = 10.8.0.1\n[Peer]\nEndpoint = 203.0.113.9:51820\n";
        assertEquals(text, DnsChoice.applyToConfigText(text, null));
    }

    @Test public void replacesInterfaceDnsAndKeepsOtherSections() {
        String text = "[Interface]\nPrivateKey = x\nDNS = 10.8.0.1\n[Peer]\nEndpoint = 203.0.113.9:51820\n";
        String out = DnsChoice.applyToConfigText(text, "1.1.1.1");
        assertEquals("[Interface]\nPrivateKey = x\nDNS = 1.1.1.1\n[Peer]\nEndpoint = 203.0.113.9:51820\n", out);
    }

    @Test public void insertsDnsWhenProfileHasNone() {
        String text = "[Interface]\nPrivateKey = x\n[Peer]\nEndpoint = 203.0.113.9:51820";
        String out = DnsChoice.applyToConfigText(text, "8.8.8.8");
        assertEquals("[Interface]\nPrivateKey = x\nDNS = 8.8.8.8\n[Peer]\nEndpoint = 203.0.113.9:51820", out);
    }

    @Test public void dropsExtraDnsLinesAndIgnoresPeerDnsKey() {
        String text = "[Interface]\nDNS = 10.8.0.1\nDNS = 10.8.0.2\n[Peer]\nDNS = keepme\n";
        String out = DnsChoice.applyToConfigText(text, "9.9.9.9");
        assertEquals("[Interface]\nDNS = 9.9.9.9\n[Peer]\nDNS = keepme\n", out);
    }

    @Test public void missingInterfaceIsRejected() {
        try { DnsChoice.applyToConfigText("[Peer]\nEndpoint = a:1\n", "1.1.1.1"); fail(); }
        catch (IllegalArgumentException expected) { assertEquals("profile_invalid", expected.getMessage()); }
    }
}
