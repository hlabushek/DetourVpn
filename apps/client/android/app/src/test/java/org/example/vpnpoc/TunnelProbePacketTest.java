package org.example.vpnpoc;

import org.junit.Test;
import static org.junit.Assert.*;

public class TunnelProbePacketTest {
    private byte[] response(byte[] request) {
        byte[] answer = java.util.Arrays.copyOf(request, request.length + 16);
        answer[2] = (byte) 0x81; answer[3] = (byte) 0x80; answer[7] = 1;
        int offset = request.length;
        answer[offset] = (byte) 192; answer[offset + 1] = 12;
        answer[offset + 3] = 1; answer[offset + 5] = 1;
        answer[offset + 11] = 4;
        answer[offset + 12] = (byte) 192; answer[offset + 14] = 2; answer[offset + 15] = 1;
        return answer;
    }
    @Test public void responseMustMatchTransactionAndQuestion() {
        var query = TunnelProbePacket.query(12345);
        var answer = response(query);
        assertTrue(TunnelProbePacket.validResponse(query, answer, answer.length));
        answer[1]++;
        assertFalse(TunnelProbePacket.validResponse(query, answer, answer.length));
        answer = response(query); answer[15]++;
        assertFalse(TunnelProbePacket.validResponse(query, answer, answer.length));
    }
    @Test public void truncatedAndErrorRepliesNeverConfirmHealth() {
        var query = TunnelProbePacket.query(5);
        var answer = response(query);
        for (int size = 0; size <= query.length; size++)
            assertFalse(TunnelProbePacket.validResponse(query, answer, size));
        answer[3] = (byte) 0x83;
        assertFalse(TunnelProbePacket.validResponse(query, answer, answer.length));
        answer = response(query); answer[2] |= 2;
        assertFalse(TunnelProbePacket.validResponse(query, answer, answer.length));
        answer = response(query); answer[7] = 0;
        assertFalse(TunnelProbePacket.validResponse(query, answer, answer.length));
    }
}
