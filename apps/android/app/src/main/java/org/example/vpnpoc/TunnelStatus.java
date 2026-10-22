package org.example.vpnpoc;

/** Pure status policy. Native UP alone never proves a handshake or egress. */
final class TunnelStatus {
    static String describe(boolean up, long handshakeSeconds, long nowSeconds) {
        if (!up) return "Отключено";
        if (handshakeSeconds <= 0) return "Туннель создан · ожидается handshake";
        if (handshakeSeconds > nowSeconds || nowSeconds - handshakeSeconds > 180)
            return "Handshake устарел · связь не подтверждена";
        return "Handshake подтверждён · egress/DNS ещё требуют проверки";
    }
}
