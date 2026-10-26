package org.example.vpnpoc;
import org.amnezia.awg.crypto.KeyPair;
import org.junit.Test;
import java.util.List;
import static org.junit.Assert.*;

public class ClientProfileTest {
    final KeyPair key = new KeyPair();
    String text() {
        return "[Interface]\nAddress = 10.203.71.2/30\nDNS = 192.0.2.53\nMTU = 1280\n"
            + "[Peer]\nPublicKey = " + new KeyPair().getPublicKey().toBase64()
            + "\nEndpoint = 192.0.2.1:51901\nAllowedIPs = 0.0.0.0/0\n";
    }
    @Test public void onlyLocalExclusionsKeepFullIpv4DnsAndDeviceKey() throws Exception {
        var config = ClientProfile.parse(text(), key, List.of("org.example.other"));
        assertEquals(List.of("org.example.other"), List.copyOf(config.getInterface().getExcludedApplications()));
        assertEquals(key.getPublicKey(), config.getInterface().getKeyPair().getPublicKey());
        assertEquals(1, config.getPeers().get(0).getAllowedIps().size());
        assertEquals(1, config.getInterface().getDnsServers().size());
    }
    @Test public void onlyLocalInclusionsKeepFullIpv4DnsAndDeviceKey() throws Exception {
        var config = ClientProfile.parse(text(), key, List.of("org.example.selected"),
            ClientProfile.RoutingMode.INCLUDE_SELECTED);
        assertEquals(List.of("org.example.selected"), List.copyOf(config.getInterface().getIncludedApplications()));
        assertTrue(config.getInterface().getExcludedApplications().isEmpty());
        assertEquals(key.getPublicKey(), config.getInterface().getKeyPair().getPublicKey());
        assertEquals(1, config.getPeers().get(0).getAllowedIps().size());
        assertEquals(1, config.getInterface().getDnsServers().size());
    }
    @Test public void includedModeRejectsEmptySelectionToAvoidFailOpenRouting() {
        assertThrows(Exception.class, () -> ClientProfile.parse(text(), key, List.of(),
            ClientProfile.RoutingMode.INCLUDE_SELECTED));
    }
    @Test public void includedModeRejectsRemoteRoutingDirectivesAndOversizedLists() {
        String remoteDirective = text().replace("DNS =", "IncludedApplications = org.bad.app\nDNS =");
        assertThrows(Exception.class, () -> ClientProfile.parse(remoteDirective, key,
            List.of("org.example.selected"), ClientProfile.RoutingMode.INCLUDE_SELECTED));
        var many = java.util.stream.IntStream.range(0, 64)
            .mapToObj(i -> "org.example.app" + i).toList();
        assertThrows(Exception.class, () -> ClientProfile.parse(text(), key,
            many, ClientProfile.RoutingMode.INCLUDE_SELECTED));
    }
    @Test public void remoteBypassAndInjectedPackagesRejected() {
        assertThrows(Exception.class, () -> ClientProfile.parse(text().replace("DNS =", "ExcludedApplications = org.bad.app\nDNS ="), key, List.of()));
        assertThrows(Exception.class, () -> ClientProfile.parse(text(), key, List.of("org.good.app\nPrivateKey=x")));
        assertThrows(Exception.class, () -> ClientProfile.parse(text(), key, List.of("org.good.app", "org.good.app")));
    }
    @Test public void ipv6PassthroughAndEndpointHostnameRejected() {
        assertThrows(Exception.class, () -> ClientProfile.parse(text().replace("0.0.0.0/0", "0.0.0.0/0, ::/0"), key, List.of()));
        assertThrows(Exception.class, () -> ClientProfile.parse(text().replace("192.0.2.1:51901", "node.example:51901"), key, List.of()));
    }
}
