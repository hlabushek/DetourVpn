package org.example.vpnpoc;

/** Passive authenticated data-plane evidence; no Android UID-bound probe sockets. */
final class TunnelHealthProbe {
    static boolean confirmed(long deliveredDataAgeMs) {
        return deliveredDataAgeMs >= 0 && deliveredDataAgeMs <= 60_000;
    }
}
