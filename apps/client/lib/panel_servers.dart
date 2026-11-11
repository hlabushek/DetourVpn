/// Panel servers (3x-ui / Remnawave, VLESS + Reality), ADR-111..113.
///
/// Android has no VLESS core yet (ADR-113). When panel servers are delivered to clients, an
/// Android client must list such a server as unavailable with this text, never hide it.
/// Panel servers are not delivered to any device yet, so nothing calls this on the live path.
library;

/// Source value the API uses for a server imported from a panel.
const String panelServerSource = 'panel';

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
