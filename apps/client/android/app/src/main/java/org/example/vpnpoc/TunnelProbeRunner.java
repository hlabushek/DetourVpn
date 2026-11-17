package org.example.vpnpoc;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import java.io.IOException;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.Executor;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * ADR-148: one active probe through the tunnel. The request is sent on the VPN network
 * ({@link Network#openConnection}), not on the app's default network, and is bounded by
 * {@link DeadPeerWatch#PROBE_TIMEOUT_MS} in total. Only the HTTP status is read; nothing is
 * logged here (the caller logs a result code, never the URL).
 *
 * <p>This app excludes its own package from its VPN (ADR-132), and Android lets only UIDs inside a
 * VPN bind sockets to it. If binding is refused, the result is
 * {@link DeadPeerWatch.ProbeResult#UNAVAILABLE}, which never escalates. Whether a probe can run at
 * all on a phone is not verified (ADR-148).
 */
final class TunnelProbeRunner {
    interface Done { void result(DeadPeerWatch.ProbeResult result, String code); }

    private TunnelProbeRunner() {}

    /**
     * Runs the probe on {@code io} and reports exactly once through {@code done} (on the
     * {@code io} or {@code timer} thread). {@code timer} enforces the total timeout.
     */
    static void run(Context context, String url, Executor io, ScheduledExecutorService timer,
            Done done) {
        AtomicBoolean finished = new AtomicBoolean();
        AtomicReference<HttpURLConnection> open = new AtomicReference<>();
        Done once = (result, code) -> {
            if (finished.compareAndSet(false, true)) done.result(result, code);
        };
        try {
            timer.schedule(() -> {
                HttpURLConnection connection = open.get();
                if (connection != null) connection.disconnect();
                once.result(DeadPeerWatch.ProbeResult.FAIL, "timeout");
            }, DeadPeerWatch.PROBE_TIMEOUT_MS, TimeUnit.MILLISECONDS);
            io.execute(() -> probe(context, url, open, once));
        } catch (RuntimeException refused) {
            once.result(DeadPeerWatch.ProbeResult.UNAVAILABLE, "not_started");
        }
    }

    private static void probe(Context context, String url,
            AtomicReference<HttpURLConnection> open, Done once) {
        Network vpn = vpnNetwork(context);
        if (vpn == null) {
            once.result(DeadPeerWatch.ProbeResult.UNAVAILABLE, "no_vpn_network");
            return;
        }
        // A throwaway bind first: a refused bind (this UID is outside the VPN) is "unavailable",
        // not a dead tunnel.
        try (DatagramSocket check = new DatagramSocket()) {
            vpn.bindSocket(check);
        } catch (IOException | RuntimeException refused) {
            once.result(DeadPeerWatch.ProbeResult.UNAVAILABLE, "bind_refused");
            return;
        }
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) vpn.openConnection(new URL(url));
            open.set(connection);
            int timeout = (int) DeadPeerWatch.PROBE_TIMEOUT_MS;
            connection.setConnectTimeout(timeout);
            connection.setReadTimeout(timeout);
            connection.setUseCaches(false);
            connection.setInstanceFollowRedirects(false);
            connection.setRequestMethod("GET");
            connection.setRequestProperty("User-Agent", DeadPeerProbe.USER_AGENT);
            connection.setRequestProperty("Accept", "*/*");
            connection.setRequestProperty("Connection", "close");
            int status = connection.getResponseCode();
            DeadPeerWatch.ProbeResult result = DeadPeerProbe.classify(status);
            once.result(result, result == DeadPeerWatch.ProbeResult.PASS ? "pass" : "status");
        } catch (IOException | RuntimeException failed) {
            once.result(DeadPeerWatch.ProbeResult.FAIL, "io");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    /** This app's VPN network (only one VPN runs at a time), or null. Never throws. */
    private static Network vpnNetwork(Context context) {
        try {
            ConnectivityManager manager =
                    (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
            if (manager == null) return null;
            for (Network network : manager.getAllNetworks()) {
                NetworkCapabilities caps = manager.getNetworkCapabilities(network);
                if (caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN))
                    return network;
            }
        } catch (RuntimeException unavailable) {
            // Treated as no VPN network.
        }
        return null;
    }
}
