/// Tunnel DNS chosen by the user (ADR-106). `null` means "as in the server profile",
/// which is the behavior before this setting existed. The list must match
/// `DnsChoice.SERVERS` in `DnsChoice.java`.
const List<String> dnsChoiceServers = [
  '1.1.1.1',
  '8.8.8.8',
  '94.140.14.14',
  '9.9.9.9',
];

/// Returns the canonical stored value: a listed server, or `null` for the default.
/// Empty input and any unlisted value both map to `null`, so no arbitrary address
/// can be persisted or sent to the native side.
String? parseDnsChoice(String? raw) {
  if (raw == null) return null;
  final value = raw.trim();
  return dnsChoiceServers.contains(value) ? value : null;
}

/// Wire form for the native side: the server, or an empty string for the default.
String dnsChoiceWire(String? choice) => parseDnsChoice(choice) ?? '';

/// Human label in Russian for the settings list.
String dnsChoiceLabel(String? choice) => switch (parseDnsChoice(choice)) {
  null => 'Как в профиле сервера',
  '1.1.1.1' => '1.1.1.1 (Cloudflare)',
  '8.8.8.8' => '8.8.8.8 (Google)',
  '94.140.14.14' => '94.140.14.14 (AdGuard)',
  '9.9.9.9' => '9.9.9.9 (Quad9)',
  _ => 'Как в профиле сервера',
};
