package org.example.vpnpoc;

import org.junit.Test;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

public class LibboxStartErrorCategoryTest {
    @Test public void classifiesSafeUdpAndContextFailures() {
        assertEquals("udp_socket_permission",
            LibboxStartErrorCategory.classify("listen udp: operation not permitted"));
        assertEquals("udp_route_unavailable",
            LibboxStartErrorCategory.classify("dial udp: network is unreachable"));
        assertEquals("dial_context_deadline",
            LibboxStartErrorCategory.classify("context deadline exceeded"));
        assertEquals("go_nil_pointer",
            LibboxStartErrorCategory.classify("invalid memory address or nil pointer dereference"));
    }

    @Test public void classifiesAdditionalSafeSocketOpenFailures() {
        assertEquals("udp_no_physical_interface",
            LibboxStartErrorCategory.classify("create ipv4 connection: no available network interface"));
        assertEquals("udp_bind_address_unavailable",
            LibboxStartErrorCategory.classify("listen udp: bind: cannot assign requested address"));
        assertEquals("udp_socket_resources_exhausted",
            LibboxStartErrorCategory.classify("listen udp: no buffer space available"));
        assertEquals("udp_socket_unsupported",
            LibboxStartErrorCategory.classify("listen udp: operation not supported"));
        assertEquals("udp_network_down",
            LibboxStartErrorCategory.classify("listen udp: network is down"));
    }

    @Test public void unknownAndOversizedMessagesProduceNoCategory() {
        assertNull(LibboxStartErrorCategory.classify(null));
        assertNull(LibboxStartErrorCategory.classify("unclassified native failure"));
        assertNull(LibboxStartErrorCategory.classify("x".repeat(4097)));
    }

    @Test public void classifiesAwgBindOpenStageWithoutReturningRawText() {
        assertEquals("awg_bind_ipv4_open",
            LibboxStartErrorCategory.classify("Unable to update bind: create ipv4 connection: hidden"));
        assertEquals("awg_bind_ipv6_open",
            LibboxStartErrorCategory.classify("Unable to update bind: create ipv6 connection: hidden"));
        assertEquals("awg_bind_open",
            LibboxStartErrorCategory.classify("Unable to update bind: unspecified"));
        assertNull(LibboxStartErrorCategory.classifyBindFailure("endpoint contains no bind marker"));
    }

    @Test public void engineBindLogPrefersSpecificSafeCause() {
        assertEquals("udp_no_physical_interface",
            LibboxStartErrorCategory.classifyEngineBindFailure(
                "create ipv4 connection: no available network interface"));
        assertEquals("udp_socket_permission",
            LibboxStartErrorCategory.classifyEngineBindFailure(
                "create ipv4 connection: listen udp: operation not permitted"));
        assertEquals("awg_bind_ipv4_open",
            LibboxStartErrorCategory.classifyEngineBindFailure("create ipv4 connection: hidden"));
        assertNull(LibboxStartErrorCategory.classifyEngineBindFailure("other engine message"));
    }
}
