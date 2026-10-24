package org.example.vpnpoc;

import android.app.Activity;
import android.app.Instrumentation;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.os.Bundle;
import java.io.ByteArrayOutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.Proxy;
import java.net.URL;
import javax.net.ssl.HttpsURLConnection;

/** POC TEST APK ONLY. Fixed network probes; no arbitrary URLs or raw IP/DNS logs. */
public final class I028Probe extends Instrumentation {
    private String mode;
    private String failure = "none";
    private String dnsFailure = "none";
    private String category(Exception error) {
        Throwable cause = error;
        for (int depth = 0; cause != null && depth < 4; depth++, cause = cause.getCause()) {
            if (cause instanceof android.system.ErrnoException value) {
                if (value.errno == android.system.OsConstants.EACCES || value.errno == android.system.OsConstants.EPERM)
                    return "permission";
                if (value.errno == android.system.OsConstants.ENETUNREACH || value.errno == android.system.OsConstants.EHOSTUNREACH)
                    return "route";
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
    private String request(String host) {
        failure = "none";
        HttpsURLConnection connection = null;
        try {
            connection = (HttpsURLConnection) new URL("https://" + host + "/").openConnection(Proxy.NO_PROXY);
            connection.setConnectTimeout(4000); connection.setReadTimeout(4000);
            connection.setInstanceFollowRedirects(false); connection.setRequestProperty("Connection", "close");
            int status = connection.getResponseCode();
            if (status != 200) { failure = "http_" + status; return null; }
            try (var input = connection.getInputStream(); var output = new ByteArrayOutputStream()) {
                int value;
                while ((value = input.read()) != -1) {
                    if (output.size() >= 128) return null;
                    output.write(value);
                }
                String address = output.toString(java.nio.charset.StandardCharsets.US_ASCII).trim();
                if (!address.matches("[0-9a-fA-F:.]{3,64}")) return null;
                return InetAddress.getByName(address).getHostAddress();
            }
        } catch (Exception error) {
            failure = category(error);
            return null;
        }
        finally { if (connection != null) connection.disconnect(); }
    }
    private boolean dns() {
        dnsFailure = "none";
        // One fixed example.com A question, standard DNS wire format; no browsing history.
        byte[] query = {0x49, 0x28, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0,
            7, 'e', 'x', 'a', 'm', 'p', 'l', 'e', 3, 'c', 'o', 'm', 0, 0, 1, 0, 1};
        try (DatagramSocket socket = new DatagramSocket()) {
            socket.setSoTimeout(3000); socket.connect(InetAddress.getByName("1.1.1.1"), 53);
            socket.send(new DatagramPacket(query, query.length));
            byte[] response = new byte[4096]; var packet = new DatagramPacket(response, response.length);
            socket.receive(packet);
            return packet.getLength() >= 12 && response[0] == query[0] && response[1] == query[1]
                && (response[2] & 0x80) != 0 && (response[3] & 15) == 0;
        } catch (Exception error) { dnsFailure = category(error); return false; }
    }
    @Override public void onStart() {
        // onStart races Application.onCreate when started from onCreate.
        // Match the established POC runners: finish app/native initialization first.
        waitForIdleSync();
        Bundle result = new Bundle();
        result.putBoolean("application_initialized", getTargetContext().getApplicationContext()
            instanceof PocApplication && ((PocApplication) getTargetContext().getApplicationContext()).provider != null);
        result.putBoolean("process_uid_matches_target", android.os.Process.myUid()
            == getTargetContext().getApplicationInfo().uid);
        result.putBoolean("test_internet_permission_declared", getContext().getPackageManager()
            .checkPermission("android.permission.INTERNET", getContext().getPackageName())
            == android.content.pm.PackageManager.PERMISSION_GRANTED);
        if (!java.util.Set.of("ipv4", "all").contains(mode)) { finish(Activity.RESULT_CANCELED, result); return; }
        String ipv4 = request("api.ipify.org");
        result.putString("ipv4_primary_failure", failure);
        if (ipv4 == null) {
            // Fixed independent HTTPS fallback; default certificate/hostname checks stay enabled.
            ipv4 = request("checkip.amazonaws.com");
            result.putBoolean("ipv4_used_fallback", true);
        }
        result.putString("ipv4_failure", failure);
        result.putBoolean("ipv4_available", ipv4 != null && ipv4.indexOf(':') < 0);
        result.putBoolean("egress_is_staging", "45.10.166.106".equals(ipv4));
        if (mode.equals("all")) {
            String ipv6 = request("api6.ipify.org");
            result.putBoolean("ipv6_available", ipv6 != null && ipv6.indexOf(':') >= 0);
            result.putBoolean("fixed_udp_dns_response", dns());
            result.putString("dns_failure", dnsFailure);
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
        result.putBoolean("probe_complete", true);
        finish(Activity.RESULT_OK, result);
    }
}
