package org.example.vpnpoc;

import java.util.Collection;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import org.amnezia.awg.config.Config;
import org.amnezia.awg.config.Interface;
import org.amnezia.awg.config.Peer;
import org.json.JSONArray;
import org.json.JSONObject;

/** Converts the already-validated beta AWG profile into a local libbox route config. */
final class LibboxConfigFactory {
    private LibboxConfigFactory() {}

    static String create(Config config, String mode, Collection<String> packages,
            Collection<String> rawDomains, String ownPackage) throws Exception {
        if (config == null || !Set.of("all", "exclude", "include").contains(mode)
                || packages == null || rawDomains == null || packages.size() > 63
                || rawDomains.size() > 128 || ownPackage == null
                || (mode.equals("include") && packages.isEmpty() && rawDomains.isEmpty()))
            throw new IllegalArgumentException("routing_policy_invalid");
        if (new HashSet<>(packages).size() != packages.size()
                || new HashSet<>(rawDomains).size() != rawDomains.size())
            throw new IllegalArgumentException("routing_policy_invalid");

        Interface source = config.getInterface();
        if (config.getPeers().size() != 1 || source.getDnsServers().isEmpty()
                || !source.getIncludedApplications().isEmpty() || !source.getExcludedApplications().isEmpty())
            throw new IllegalArgumentException("profile_invalid");
        Peer sourcePeer = config.getPeers().get(0);

        JSONObject awg = new JSONObject();
        awg.put("type", "awg").put("tag", "awg").put("useIntegratedTun", false);
        awg.put("private_key", source.getKeyPair().getPrivateKey().toBase64());
        JSONArray addresses = new JSONArray();
        for (var address : source.getAddresses()) addresses.put(address.toString());
        awg.put("address", addresses);
        int mtu = source.getMtu().orElse(1408);
        awg.put("mtu", mtu);
        source.getListenPort().ifPresent(value -> put(awg, "listen_port", value));
        putOptional(awg, "jc", source.getJunkPacketCount());
        putOptional(awg, "jmin", source.getJunkPacketMinSize());
        putOptional(awg, "jmax", source.getJunkPacketMaxSize());
        putOptional(awg, "s1", source.getInitPacketJunkSize());
        putOptional(awg, "s2", source.getResponsePacketJunkSize());
        putOptional(awg, "s3", source.getCookieReplyPacketJunkSize());
        putOptional(awg, "s4", source.getTransportPacketJunkSize());
        putOptional(awg, "h1", source.getInitPacketMagicHeader());
        putOptional(awg, "h2", source.getResponsePacketMagicHeader());
        putOptional(awg, "h3", source.getUnderloadPacketMagicHeader());
        putOptional(awg, "h4", source.getTransportPacketMagicHeader());
        putOptional(awg, "i1", source.getSpecialJunkI1());
        putOptional(awg, "i2", source.getSpecialJunkI2());
        putOptional(awg, "i3", source.getSpecialJunkI3());
        putOptional(awg, "i4", source.getSpecialJunkI4());
        putOptional(awg, "i5", source.getSpecialJunkI5());
        source.getHeaderProtectionKey().ifPresent(value -> put(awg, "header_protection_key", value.toBase64()));
        putOptional(awg, "content_padding_addition", source.getContentPaddingAddition());
        putOptional(awg, "rekey_after_time", source.getRekeyAfterTime());
        putOptional(awg, "rekey_timeout", source.getRekeyTimeout());
        putOptional(awg, "reject_after_time", source.getRejectAfterTime());
        putOptional(awg, "keepalive_timeout", source.getKeepaliveTimeout());
        putOptional(awg, "max_handshake_attempts", source.getMaxHandshakeAttempts());
        putBooleanString(awg, "random_trailers", source.getRandomTrailers());
        putBooleanString(awg, "disable_cookies", source.getDisableCookies());

        var endpoint = sourcePeer.getEndpoint().orElseThrow(() -> new IllegalArgumentException("profile_invalid"));
        JSONObject peer = new JSONObject().put("address", endpoint.getHost()).put("port", endpoint.getPort())
                .put("public_key", sourcePeer.getPublicKey().toBase64());
        sourcePeer.getPreSharedKey().ifPresent(value -> put(peer, "preshared_key", value.toBase64()));
        sourcePeer.getPersistentKeepalive().ifPresent(value -> {
            if (!value.matches("[0-9]{1,5}")) throw new IllegalArgumentException("profile_invalid");
            put(peer, "persistent_keepalive_interval", Integer.parseInt(value));
        });
        JSONArray allowed = new JSONArray();
        for (var prefix : sourcePeer.getAllowedIps()) allowed.put(prefix.toString());
        peer.put("allowed_ips", allowed);
        awg.put("peers", new JSONArray().put(peer));

        JSONObject tun = new JSONObject().put("type", "tun").put("tag", "tun-in")
                .put("address", addresses).put("mtu", mtu)
                .put("auto_route", true).put("strict_route", true).put("stack", "gvisor")
                .put("route_address", new JSONArray().put("0.0.0.0/0").put("::/0"));
        JSONArray rules = new JSONArray();
        rules.put(new JSONObject().put("protocol", "dns").put("action", "hijack-dns"));
        rules.put(new JSONObject().put("action", "sniff"));
        if (!mode.equals("all")) {
            String target = mode.equals("exclude") ? "direct" : "awg";
            if (!packages.isEmpty()) {
                JSONArray packageNames = new JSONArray();
                for (String name : packages) {
                    if (name == null || !name.matches("[a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z][a-zA-Z0-9_]*)+")
                            || name.equals(ownPackage))
                        throw new IllegalArgumentException("package_invalid");
                    packageNames.put(name);
                }
                rules.put(new JSONObject().put("package_name", packageNames).put("outbound", target));
            }
            var exactDomains = new JSONArray();
            var suffixDomains = new JSONArray();
            var canonical = new HashSet<String>();
            for (String raw : rawDomains) {
                DomainRule parsed = DomainRule.parse(raw);
                if (!canonical.add(parsed.canonical())) throw new IllegalArgumentException("domain_duplicate");
                (parsed.suffix ? suffixDomains : exactDomains).put(parsed.asciiHost);
            }
            if (exactDomains.length() > 0)
                rules.put(new JSONObject().put("domain", exactDomains).put("outbound", target));
            if (suffixDomains.length() > 0)
                rules.put(new JSONObject().put("domain_suffix", suffixDomains).put("outbound", target));
        }

        var dnsServer = source.getDnsServers().iterator().next();
        JSONArray dnsServers = new JSONArray().put(new JSONObject().put("type", "udp")
                .put("tag", "profile-dns").put("server", dnsServer.getHostAddress()).put("detour", "awg"));
        JSONObject dns = new JSONObject().put("servers", dnsServers).put("final", "profile-dns")
                .put("strategy", "ipv4_only");
        JSONArray endpoints = new JSONArray().put(awg);
        JSONArray outbounds = new JSONArray()
                .put(new JSONObject().put("type", "direct").put("tag", "direct"))
                .put(new JSONObject().put("type", "block").put("tag", "block"));
        JSONObject route = new JSONObject().put("rules", rules).put("final", mode.equals("include") ? "direct" : "awg")
                .put("find_process", mode.equals("all") ? false : !packages.isEmpty())
                .put("auto_detect_interface", true);
        JSONObject root = new JSONObject().put("log", new JSONObject().put("level", "error"))
                .put("dns", dns).put("inbounds", new JSONArray().put(tun))
                .put("endpoints", endpoints).put("outbounds", outbounds).put("route", route);
        return root.toString();
    }

    private static void putOptional(JSONObject object, String key, java.util.Optional<?> value) {
        value.ifPresent(item -> put(object, key, item));
    }

    private static void putBooleanString(JSONObject object, String key, java.util.Optional<String> value) {
        value.ifPresent(item -> put(object, key, Boolean.parseBoolean(item)));
    }

    private static void put(JSONObject object, String key, Object value) {
        try { object.put(key, value); }
        catch (org.json.JSONException invalid) { throw new IllegalArgumentException("profile_invalid"); }
    }
}
