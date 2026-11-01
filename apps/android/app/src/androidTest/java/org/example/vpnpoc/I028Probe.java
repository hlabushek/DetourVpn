package org.example.vpnpoc;

import android.app.Activity;
import android.app.Instrumentation;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Bundle;
import java.io.ByteArrayOutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.Inet6Address;
import java.net.InetSocketAddress;
import java.net.Proxy;
import java.net.Socket;
import java.net.UnknownHostException;
import java.net.URL;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

/** POC TEST APK ONLY. Fixed network probes; no arbitrary URLs or raw IP/DNS logs. */
public final class I028Probe extends Instrumentation {
    private static final long REQUEST_DEADLINE_SECONDS = 8;
    private static final int DNS_PROBE_SOURCE_PORT = 42428;
    private static final int IPV6_HTTPS_PROBE_SOURCE_PORT = 42429;
    private static final int BASELINE_IPV6_HTTPS_PROBE_SOURCE_PORT = 42430;
    private static final byte[] I019_IPV4_TRIGGER_ADDRESS = {1, 1, 1, 1};
    // Cloudflare's public IPv6 DNS endpoint. Used only as a DNS-free TCP route probe.
    private static final byte[] IPV6_LITERAL_PROBE_ADDRESS = {
        0x26, 0x06, 0x47, 0x00, 0x47, 0x00, 0, 0,
        0, 0, 0, 0, 0, 0, 0x11, 0x11
    };
    private String mode;
    private String failure = "none";
    private String dnsFailure = "none";

    private static final class ProbeResult {
        final String address;
        final String failure;
        final boolean connectAttempted;
        ProbeResult(String address, String failure) {
            this(address, failure, false);
        }
        ProbeResult(String address, String failure, boolean connectAttempted) {
            this.address = address;
            this.failure = failure;
            this.connectAttempted = connectAttempted;
        }
    }

    private void stage(String marker) {
        Bundle status = new Bundle();
        status.putString("i028_stage", marker);
        sendStatus(Activity.RESULT_OK, status);
    }
    private String category(Exception error) {
        Throwable cause = error;
        for (int depth = 0; cause != null && depth < 4; depth++, cause = cause.getCause()) {
            if (cause instanceof android.system.ErrnoException value) {
                if (value.errno == android.system.OsConstants.EACCES || value.errno == android.system.OsConstants.EPERM)
                    return "permission";
                if (value.errno == android.system.OsConstants.ENETUNREACH || value.errno == android.system.OsConstants.EHOSTUNREACH)
                    return "route";
            }
            if (cause instanceof java.net.SocketException) {
                String message = cause.getMessage();
                if (message != null) {
                    String normalized = message.toLowerCase(java.util.Locale.ROOT);
                    if (normalized.contains("network is unreachable")
                        || normalized.contains("no route to host")
                        || normalized.contains("enetunreach")
                        || normalized.contains("ehostunreach")) return "route";
                    if (normalized.contains("permission denied") || normalized.contains("eacces")
                        || normalized.contains("eperm")) return "permission";
                }
            }
        }
        return error instanceof java.net.UnknownHostException ? "dns"
            : error instanceof javax.net.ssl.SSLException ? "tls"
            : error instanceof java.net.SocketTimeoutException ? "timeout"
            : error instanceof java.net.ConnectException ? "connect"
            : error instanceof SecurityException ? "permission" : "io";
    }
    @Override public void onCreate(Bundle args) {
        super.onCreate(args); mode = args.getString("mode", "ipv4"); start();
    }
    private ProbeResult request(String host) {
        return request(host, 0);
    }

    private ProbeResult request(String host, int sourcePort) {
        FutureTask<ProbeResult> task = sourcePort == 0
            ? new FutureTask<>(() -> requestBlocking(host))
            : new FutureTask<>(() -> requestIpv6Bound(host, sourcePort));
        Thread worker = new Thread(task, "i028-fixed-probe");
        worker.setDaemon(true);
        worker.start();
        try {
            return task.get(REQUEST_DEADLINE_SECONDS, TimeUnit.SECONDS);
        } catch (TimeoutException error) {
            task.cancel(true);
            return new ProbeResult(null, "timeout");
        } catch (InterruptedException error) {
            task.cancel(true);
            Thread.currentThread().interrupt();
            return new ProbeResult(null, "timeout");
        } catch (ExecutionException error) {
            return new ProbeResult(null, "io");
        }
    }

    private ProbeResult requestBlocking(String host) {
        HttpsURLConnection connection = null;
        try {
            connection = (HttpsURLConnection) new URL("https://" + host + "/").openConnection(Proxy.NO_PROXY);
            connection.setConnectTimeout(4000); connection.setReadTimeout(4000);
            connection.setInstanceFollowRedirects(false); connection.setRequestProperty("Connection", "close");
            int status = connection.getResponseCode();
            if (status != 200) { return new ProbeResult(null, "http_" + status); }
            try (var input = connection.getInputStream(); var output = new ByteArrayOutputStream()) {
                int value;
                while ((value = input.read()) != -1) {
                    if (output.size() >= 128) return new ProbeResult(null, "io");
                    output.write(value);
                }
                String address = output.toString(java.nio.charset.StandardCharsets.US_ASCII).trim();
                if (!address.matches("[0-9a-fA-F:.]{3,64}")) return new ProbeResult(null, "io");
                return new ProbeResult(InetAddress.getByName(address).getHostAddress(), "none");
            }
        } catch (Exception error) {
            return new ProbeResult(null, category(error));
        }
        finally { if (connection != null) connection.disconnect(); }
    }

    /** Fixed IPv6 HTTPS probe with a test-only source port for AP-station attribution. */
    private ProbeResult requestIpv6Bound(String host, int sourcePort) {
        Socket tcp = null;
        SSLSocket tls = null;
        try {
            Inet6Address destination = resolveIpv6OnActiveNetwork(host);
            if (destination == null) return new ProbeResult(null, "route");

            tcp = new Socket();
            tcp.setReuseAddress(true);
            tcp.bind(new InetSocketAddress(InetAddress.getByAddress(new byte[16]), sourcePort));
            tcp.connect(new InetSocketAddress(destination, 443), 4000);
            SSLSocketFactory factory = (SSLSocketFactory) SSLSocketFactory.getDefault();
            tls = (SSLSocket) factory.createSocket(tcp, host, 443, true);
            tls.setSoTimeout(4000);
            SSLParameters parameters = tls.getSSLParameters();
            parameters.setEndpointIdentificationAlgorithm("HTTPS");
            tls.setSSLParameters(parameters);
            tls.startHandshake();

            var output = tls.getOutputStream();
            output.write(("GET / HTTP/1.0\r\nHost: " + host
                + "\r\nConnection: close\r\nAccept: text/plain\r\n\r\n")
                .getBytes(java.nio.charset.StandardCharsets.US_ASCII));
            output.flush();
            ByteArrayOutputStream received = new ByteArrayOutputStream();
            var input = tls.getInputStream();
            int value;
            while ((value = input.read()) != -1) {
                if (received.size() >= 2048) return new ProbeResult(null, "io");
                received.write(value);
            }
            String response = received.toString(java.nio.charset.StandardCharsets.US_ASCII);
            int split = response.indexOf("\r\n\r\n");
            int statusEnd = response.indexOf("\r\n");
            if (split < 0 || statusEnd < 0
                || !response.substring(0, statusEnd).matches("HTTP/1\\.[01] 200(?: .*)?")) {
                return new ProbeResult(null, "io");
            }
            String headers = response.substring(0, split).toLowerCase(java.util.Locale.ROOT);
            String body = response.substring(split + 4).trim();
            if (headers.contains("transfer-encoding: chunked")) body = decodeChunkedBody(body);
            if (body.matches("[0-9a-fA-F:.]{3,64}")) return new ProbeResult(body, "none");
            return new ProbeResult(null, "io");
        } catch (Exception error) {
            return new ProbeResult(null, category(error));
        } finally {
            try { if (tls != null) tls.close(); else if (tcp != null) tcp.close(); }
            catch (Exception ignored) { }
        }
    }

    /** TEST ONLY: create IPv4 tunnel traffic without depending on DNS. */
    private String triggerI019Handshake() {
        try (Socket tcp = new Socket()) {
            InetAddress destination = InetAddress.getByAddress(I019_IPV4_TRIGGER_ADDRESS);
            tcp.connect(new InetSocketAddress(destination, 443), 2000);
            return "connected";
        } catch (Exception error) {
            return category(error);
        }
    }

    /** DNS-free IPv6 TCP control; reports only connect outcome and a bounded category. */
    private ProbeResult requestIpv6LiteralBound(int sourcePort) {
        Socket tcp = null;
        boolean connectAttempted = false;
        try {
            Inet6Address destination = (Inet6Address) InetAddress.getByAddress(IPV6_LITERAL_PROBE_ADDRESS);
            tcp = new Socket();
            tcp.setReuseAddress(true);
            tcp.bind(new InetSocketAddress(InetAddress.getByAddress(new byte[16]), sourcePort));
            connectAttempted = true;
            tcp.connect(new InetSocketAddress(destination, 443), 4000);
            return new ProbeResult("tcp_connected", "none", true);
        } catch (Exception error) {
            return new ProbeResult(null, category(error), connectAttempted);
        } finally {
            try { if (tcp != null) tcp.close(); }
            catch (Exception ignored) { }
        }
    }

    /** Resolve only AAAA on the active Android network; never retain or report the address. */
    private Inet6Address resolveIpv6OnActiveNetwork(String host) throws Exception {
        ConnectivityManager manager = getTargetContext().getSystemService(ConnectivityManager.class);
        Network network = null;
        if (manager != null) {
            boolean adopted = false;
            try {
                getUiAutomation().adoptShellPermissionIdentity(
                    "android.permission.ACCESS_NETWORK_STATE");
                adopted = true;
                network = manager.getActiveNetwork();
            } finally {
                if (adopted) getUiAutomation().dropShellPermissionIdentity();
            }
        }
        if (network == null) throw new UnknownHostException();
        for (InetAddress address : network.getAllByName(host)) {
            if (address instanceof Inet6Address value && !value.isLinkLocalAddress()) return value;
        }
        return null;
    }

    private String decodeChunkedBody(String encoded) {
        StringBuilder decoded = new StringBuilder();
        int cursor = 0;
        while (cursor < encoded.length()) {
            int lineEnd = encoded.indexOf("\r\n", cursor);
            if (lineEnd < 0) return "";
            int size;
            try { size = Integer.parseInt(encoded.substring(cursor, lineEnd).trim(), 16); }
            catch (NumberFormatException error) { return ""; }
            cursor = lineEnd + 2;
            if (size == 0) return decoded.toString().trim();
            if (size < 0 || cursor + size + 2 > encoded.length()) return "";
            decoded.append(encoded, cursor, cursor + size);
            cursor += size;
            if (!encoded.startsWith("\r\n", cursor)) return "";
            cursor += 2;
            if (decoded.length() > 128) return "";
        }
        return "";
    }
    private boolean dns() { return dns(false); }
    private boolean dns(boolean bindTestPort) {
        dnsFailure = "none";
        // One fixed example.com A question, standard DNS wire format; no browsing history.
        byte[] query = {0x49, 0x28, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0,
            7, 'e', 'x', 'a', 'm', 'p', 'l', 'e', 3, 'c', 'o', 'm', 0, 0, 1, 0, 1};
        try (DatagramSocket socket = new DatagramSocket(null)) {
            socket.bind(new InetSocketAddress(bindTestPort ? DNS_PROBE_SOURCE_PORT : 0));
            socket.setSoTimeout(3000); socket.connect(InetAddress.getByName("1.1.1.1"), 53);
            socket.send(new DatagramPacket(query, query.length));
            byte[] response = new byte[4096]; var packet = new DatagramPacket(response, response.length);
            socket.receive(packet);
            return packet.getLength() >= 12 && response[0] == query[0] && response[1] == query[1]
                && (response[2] & 0x80) != 0 && (response[3] & 15) == 0;
        } catch (Exception error) { dnsFailure = category(error); return false; }
    }
    private void diagnoseFixedPath(Bundle result) {
        // Separate, later observations. No resolved IPs, certificates, or errors are persisted.
        FutureTask<String> resolver = new FutureTask<>(() -> {
            try {
                return InetAddress.getAllByName("api.ipify.org").length > 0 ? "ok" : "dns";
            } catch (Exception error) { return category(error); }
        });
        Thread resolverThread = new Thread(resolver, "i028-fixed-resolver");
        resolverThread.setDaemon(true);
        resolverThread.start();
        String resolverResult;
        try { resolverResult = resolver.get(3, TimeUnit.SECONDS); }
        catch (TimeoutException error) { resolver.cancel(true); resolverResult = "timeout"; }
        catch (InterruptedException error) {
            resolver.cancel(true); Thread.currentThread().interrupt(); resolverResult = "interrupted";
        }
        catch (ExecutionException error) { resolverResult = "io"; }
        result.putString("fixed_resolver_result", resolverResult);

        AtomicBoolean tcpConnected = new AtomicBoolean(false);
        FutureTask<String[]> tlsTask = new FutureTask<>(() -> {
            try (Socket tcp = new Socket()) {
                byte[] address = {1, 1, 1, 1};
                tcp.connect(new InetSocketAddress(InetAddress.getByAddress(address), 443), 3000);
                tcpConnected.set(true);
                SSLSocketFactory factory = (SSLSocketFactory) SSLSocketFactory.getDefault();
                try (SSLSocket tls = (SSLSocket) factory.createSocket(tcp, "cloudflare-dns.com", 443, true)) {
                    tls.setSoTimeout(3000);
                    SSLParameters parameters = tls.getSSLParameters();
                    parameters.setEndpointIdentificationAlgorithm("HTTPS");
                    tls.setSSLParameters(parameters);
                    tls.startHandshake();
                    return new String[]{"ok", "ok"};
                }
            } catch (Exception error) {
                return tcpConnected.get() ? new String[]{"ok", category(error)}
                    : new String[]{category(error), "not_attempted"};
            }
        });
        Thread tlsThread = new Thread(tlsTask, "i028-fixed-tls");
        tlsThread.setDaemon(true);
        tlsThread.start();
        String[] outcomes;
        try { outcomes = tlsTask.get(7, TimeUnit.SECONDS); }
        catch (TimeoutException error) {
            tlsTask.cancel(true);
            outcomes = tcpConnected.get() ? new String[]{"ok", "timeout"}
                : new String[]{"timeout", "not_attempted"};
        } catch (InterruptedException error) {
            tlsTask.cancel(true); Thread.currentThread().interrupt();
            outcomes = tcpConnected.get() ? new String[]{"ok", "interrupted"}
                : new String[]{"interrupted", "not_attempted"};
        } catch (ExecutionException error) {
            outcomes = tcpConnected.get() ? new String[]{"ok", "io"}
                : new String[]{"io", "not_attempted"};
        }
        result.putString("fixed_tcp_result", outcomes[0]);
        result.putString("fixed_tls_result", outcomes[1]);
    }
    private void collectNetworkMetadata(Bundle result) {
        try {
            getUiAutomation().adoptShellPermissionIdentity("android.permission.ACCESS_NETWORK_STATE");
            ConnectivityManager manager = (ConnectivityManager) getTargetContext().getSystemService(android.content.Context.CONNECTIVITY_SERVICE);
            var network = manager.getActiveNetwork();
            var caps = manager.getNetworkCapabilities(network);
            var properties = manager.getLinkProperties(network);
            result.putBoolean("active_network_available", network != null);
            result.putBoolean("active_network_validated", caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED));
            result.putBoolean("active_network_is_vpn", caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN));
            result.putBoolean("active_transport_cellular", caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR));
            result.putBoolean("active_transport_wifi", caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI));
            result.putBoolean("vpn_dns_configured", properties != null && properties.getDnsServers().stream()
                .anyMatch(address -> "1.1.1.1".equals(address.getHostAddress())));
        } catch (Exception ignored) { result.putBoolean("network_metadata_unavailable", true); }
        finally { getUiAutomation().dropShellPermissionIdentity(); }
    }
    @Override public void onStart() {
        // onStart races Application.onCreate when started from onCreate.
        // Match the established POC runners: finish app/native initialization first.
        stage("started");
        waitForIdleSync();
        stage("idle");
        Bundle result = new Bundle();
        result.putBoolean("application_initialized", getTargetContext().getApplicationContext()
            instanceof PocApplication && ((PocApplication) getTargetContext().getApplicationContext()).provider != null);
        result.putBoolean("process_uid_matches_target", android.os.Process.myUid()
            == getTargetContext().getApplicationInfo().uid);
        result.putBoolean("test_internet_permission_declared", getContext().getPackageManager()
            .checkPermission("android.permission.INTERNET", getContext().getPackageName())
            == android.content.pm.PackageManager.PERMISSION_GRANTED);
        if (!java.util.Set.of("ipv4", "all", "egress", "dns", "i019").contains(mode)) { finish(Activity.RESULT_CANCELED, result); return; }
        if (mode.equals("dns")) {
            stage("udp_dns_started");
            result.putBoolean("fixed_udp_dns_response", dns(true));
            result.putString("dns_failure", dnsFailure);
            stage("udp_dns_done");
            result.putBoolean("probe_complete", true);
            stage("completed");
            finish(Activity.RESULT_OK, result);
            return;
        }
        ProbeResult earlyIpv6LiteralProbe = null;
        if (mode.equals("egress")) {
            // Check the included IPv6 route before ordinary requests can consume
            // the bounded staging quota. This probe uses a fixed literal, no DNS.
            stage("ipv6_literal_started");
            earlyIpv6LiteralProbe = requestIpv6LiteralBound(IPV6_HTTPS_PROBE_SOURCE_PORT);
            stage("ipv6_literal_done");
        }
        if (mode.equals("i019")) {
            stage("i019_literal_trigger_started");
            result.putString("i019_trigger_result", triggerI019Handshake());
            stage("i019_literal_trigger_done");
        }
        stage("ipv4_primary_started");
        ProbeResult primary = request("api.ipify.org");
        String ipv4 = primary.address;
        stage("ipv4_primary_done");
        if (mode.equals("i019")) {
            // The I-019 Flutter app is intentionally outside its own tunnel.
            // Run the fixed IPv4 oracle in this separate test-app UID and emit
            // only an allow-listed node label, never the address itself.
            String outcome;
            if (ipv4 != null) {
                outcome = ipv4.equals("45.10.166.106") ? "node_a"
                    : ipv4.equals("150.241.99.227") ? "node_b" : "other_address";
            } else {
                outcome = switch (primary.failure) {
                    case "dns", "timeout", "route", "tls", "connect", "permission", "io" ->
                        primary.failure;
                    default -> primary.failure.startsWith("http_") ? "http_error" : "io";
                };
            }
            result.putString("i019_egress_result", outcome);
            result.putBoolean("probe_complete", true);
            stage("completed");
            finish(Activity.RESULT_OK, result);
            return;
        }
        result.putString("ipv4_primary_failure", primary.failure);
        if (ipv4 == null) {
            // Fixed independent HTTPS fallback; default certificate/hostname checks stay enabled.
            stage("ipv4_fallback_started");
            ProbeResult fallback = request("checkip.amazonaws.com");
            ipv4 = fallback.address;
            failure = fallback.failure;
            stage("ipv4_fallback_done");
            result.putBoolean("ipv4_used_fallback", true);
        } else {
            failure = primary.failure;
        }
        stage("ipv4_done");
        result.putString("ipv4_failure", failure);
        result.putBoolean("ipv4_available", ipv4 != null && ipv4.indexOf(':') < 0);
        result.putBoolean("egress_is_staging", "45.10.166.106".equals(ipv4));
        if (mode.equals("ipv4") && ipv4 == null) {
            // Follow-up diagnostics only: these observations occur after the failed HTTPS probes.
            stage("udp_dns_started");
            result.putBoolean("fixed_udp_dns_response", dns());
            stage("udp_dns_done");
            result.putString("dns_failure", dnsFailure);
            stage("metadata_started");
            collectNetworkMetadata(result);
            stage("metadata_done");
        }
        if (mode.equals("all") || mode.equals("egress")) {
            int ipv6SourcePort = mode.equals("egress")
                ? IPV6_HTTPS_PROBE_SOURCE_PORT : BASELINE_IPV6_HTTPS_PROBE_SOURCE_PORT;
            ProbeResult ipv6LiteralProbe = mode.equals("egress")
                ? earlyIpv6LiteralProbe : requestIpv6LiteralBound(ipv6SourcePort);
            boolean ipv6Available;
            String ipv6Failure;
            if (mode.equals("egress")) {
                ipv6Available = ipv6LiteralProbe.address != null;
                ipv6Failure = ipv6LiteralProbe.failure;
            } else {
                stage("ipv6_started");
                ProbeResult ipv6Probe = request("api6.ipify.org", ipv6SourcePort);
                ipv6Available = ipv6Probe.address != null && ipv6Probe.address.indexOf(':') >= 0;
                ipv6Failure = ipv6Probe.failure;
            }
            stage("ipv6_done");
            result.putBoolean("ipv6_available", ipv6Available);
            result.putString("ipv6_failure", ipv6Failure);
            result.putBoolean("ipv6_literal_connected", ipv6LiteralProbe.address != null);
            result.putString("ipv6_literal_failure", ipv6LiteralProbe.failure);
            result.putBoolean("ipv6_literal_connect_attempted", ipv6LiteralProbe.connectAttempted);
            if (mode.equals("all")) {
                stage("ipv6_literal_egress_port_control_started");
                ProbeResult portControl = requestIpv6LiteralBound(IPV6_HTTPS_PROBE_SOURCE_PORT);
                result.putBoolean("ipv6_literal_egress_port_control_connected", portControl.address != null);
                result.putString("ipv6_literal_egress_port_control_failure", portControl.failure);
                stage("ipv6_literal_egress_port_control_done");
            }
            stage("udp_dns_started");
            result.putBoolean("fixed_udp_dns_response", dns(mode.equals("egress") || mode.equals("all")));
            stage("udp_dns_done");
            result.putString("dns_failure", dnsFailure);
            stage("metadata_started");
            collectNetworkMetadata(result);
            stage("metadata_done");
        }
        if (mode.equals("egress") && ipv4 == null) {
            stage("fixed_path_diagnostic_started");
            diagnoseFixedPath(result);
            stage("fixed_path_diagnostic_done");
        }
        result.putBoolean("probe_complete", true);
        stage("completed");
        finish(Activity.RESULT_OK, result);
    }
}
