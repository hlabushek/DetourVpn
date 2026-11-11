/// Panel servers (3x-ui / Remnawave, VLESS + Reality), ADR-111..115.
///
/// The API lists them only when asked with `include=panel` and when the operator enabled
/// delivery (ADR-115). Windows can connect (engine built with `with_utls`, ADR-112). Android has
/// no VLESS core yet (ADR-113/114): it lists such a server as unavailable with the text below,
/// never hides it.
library;

/// Source value the API uses for a server imported from a panel.
const String panelServerSource = 'panel';

/// Only protocol a panel row may carry.
const String panelServerProtocol = 'vless_reality';

/// Upper bound shared with the API (`panel_delivery.MAX_PANEL_ROWS`).
const int maxPanelNodes = 20;

/// Node state for a panel server on a device whose engine cannot run VLESS.
const String panelUnsupportedReason = 'panel_unsupported';

/// Longest single panel session the Windows engine accepts (12 h, `kMaxVlessMs`). The panel
/// meters its own client; this only bounds one session.
const int panelSessionMaxMs = 12 * 60 * 60 * 1000;

/// User-facing source label (Russian) under a panel server row.
const String panelServerLabel = 'Сервер из панели (VLESS)';

/// User-facing text (Russian) for a panel server on Android.
const String panelServerUnsupportedAndroid =
    'Серверы из панели (VLESS) пока не поддерживаются на Android. '
    'Выберите другой сервер.';

/// Why a server from [source] cannot be used on this device, or `null` when nothing blocks it
/// here. Only the Android gap is decided on the client; other checks stay on the server.
String? serverUnavailableReason({
  required String source,
  required bool isAndroid,
}) => source == panelServerSource && isAndroid
    ? panelServerUnsupportedAndroid
    : null;

/// Strictly validated `connectVless` arguments from the profile response, or `null` when any
/// field is missing or has the wrong type. Values are re-checked natively; never log them.
Map<String, Object>? vlessProfileArgs(Object? value, String expectedId) {
  if (value is! Map) return null;
  final strings = <String, String>{};
  for (final key in const [
    'id',
    'protocol',
    'server',
    'uuid',
    'flow',
    'sni',
    'public_key',
    'short_id',
    'fingerprint',
  ]) {
    final field = value[key];
    if (field is! String) return null;
    strings[key] = field;
  }
  final port = value['port'];
  if (port is! int || port < 1 || port > 65535) return null;
  if (strings['id'] != expectedId || strings['protocol'] != panelServerProtocol)
    return null;
  return {
    'server': strings['server']!,
    'port': port,
    'uuid': strings['uuid']!,
    'flow': strings['flow']!,
    'sni': strings['sni']!,
    'publicKey': strings['public_key']!,
    'shortId': strings['short_id']!,
    'fingerprint': strings['fingerprint']!,
  };
}
