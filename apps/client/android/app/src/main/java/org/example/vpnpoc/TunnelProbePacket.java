package org.example.vpnpoc;

/** Fixed synthetic DNS request/response validation. No Android dependencies. */
final class TunnelProbePacket {
    static byte[] query(int id) {
        return new byte[] {(byte) (id >> 8), (byte) id, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0,
                7, 'e', 'x', 'a', 'm', 'p', 'l', 'e', 3, 'c', 'o', 'm', 0, 0, 1, 0, 1};
    }

    static boolean validResponse(byte[] query, byte[] answer, int length) {
        if (length < query.length || length > answer.length || answer[0] != query[0]
                || answer[1] != query[1] || (answer[2] & 0x80) == 0
                || (answer[2] & 0x7a) != 0 || (answer[3] & 15) != 0
                || answer[4] != 0 || answer[5] != 1
                || (answer[6] == 0 && answer[7] == 0)) return false;
        for (int i = 12; i < query.length; i++) if (query[i] != answer[i]) return false;
        // Accept only a complete first A/IN answer pointing to the exact question.
        int offset = query.length;
        return length >= offset + 16 && (answer[offset] & 255) == 192
                && answer[offset + 1] == 12 && answer[offset + 2] == 0
                && answer[offset + 3] == 1 && answer[offset + 4] == 0
                && answer[offset + 5] == 1 && answer[offset + 10] == 0
                && answer[offset + 11] == 4;
    }
}
