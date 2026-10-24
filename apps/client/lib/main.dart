import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';
import 'connection.dart';
import 'native_tunnel.dart';
import 'quota_screen.dart';
import 'connection_screen.dart';
import 'orbit_theme.dart';
import 'orbit_widgets.dart';
import 'session_store.dart';
import 'window_connection.dart';
export 'connection_screen.dart' show ConnectionScreen;

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  const origin = String.fromEnvironment('VPN_API_ORIGIN');
  Api? api;
  try {
    api = Api(Uri.parse(origin), store: PlatformSessionStore());
  } catch (_) {
    /* Explicit setup screen. */
  }
  runApp(VpnApp(api: api, native: PlatformTunnel()));
}

class VpnApp extends StatefulWidget {
  const VpnApp({super.key, required this.api, required this.native});
  final Api? api;
  final NativeTunnel native;
  @override
  State<VpnApp> createState() => _VpnAppState();
}

class _VpnAppState extends State<VpnApp> with WidgetsBindingObserver {
  ConnectionController? connection;
  bool showLogin = true;
  ThemeMode theme = ThemeMode.dark;
  int tab = 0;
  bool restoring = false;
  static const windows = bool.fromEnvironment('VPN_WINDOW_TUNNELS');
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    if (widget.api?.store != null) {
      restoring = true;
      unawaited(restore());
    }
  }

  Future<void> restore() async {
    try {
      await widget.api!.restoreSession();
      if (mounted && widget.api!.authenticated) signedIn();
    } catch (_) {
      try {
        await widget.api!.forgetSession();
      } catch (_) {
        /* Login remains closed. */
      }
    } finally {
      if (mounted) setState(() => restoring = false);
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && !showLogin) {
      unawaited(connection?.refresh());
      unawaited(connection?.refreshQuota());
    }
  }

  void signedIn() {
    final value =
        connection ??
        (windows
            ? WindowConnectionController(
                widget.api!,
                widget.native as WindowNative,
              )
            : ConnectionController(widget.api!, widget.native));
    value.startPolling();
    setState(() {
      connection = value;
      showLogin = false;
      tab = 0;
    });
    unawaited(value.refresh());
    unawaited(value.refreshQuota());
    unawaited(value.refreshNodes());
  }

  Future<void> signOut() async {
    final value = connection!;
    await value.disconnect();
    if (value.error == 'native_disconnect_failed') return;
    try {
      await widget.api!.call('POST', '/api/v1/auth/logout');
    } catch (_) {
      /* Local session still erased. */
    }
    try {
      await widget.api!.forgetSession();
    } catch (_) {
      value.error = 'session_erasure_failed';
      if (mounted) setState(() {});
      return;
    } // Do not claim durable logout if erasure failed.
    value.quota.clear();
    value.pausePolling();
    // Keep unresolved revoke intent in RAM so reauthentication can finish it.
    if (mounted) setState(() => showLogin = true);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    connection?.dispose();
    widget.api?.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'DetourVpn · тестовый клиент',
    debugShowCheckedModeBanner: false,
    themeMode: theme,
    theme: orbitTheme(Brightness.light),
    darkTheme: orbitTheme(Brightness.dark),
    home: Scaffold(
      appBar: AppBar(
        title: const Text.rich(
          TextSpan(
            children: [
              TextSpan(
                text: 'Detour',
                style: TextStyle(fontWeight: FontWeight.w700),
              ),
              TextSpan(
                text: 'Vpn',
                style: TextStyle(color: Color(0xffbc93ed)),
              ),
            ],
          ),
        ),
        actions: [
          IconButton(
            tooltip: 'Сменить тему',
            onPressed: () => setState(
              () => theme = theme == ThemeMode.dark
                  ? ThemeMode.light
                  : ThemeMode.dark,
            ),
            icon: const Icon(Icons.contrast),
          ),
        ],
      ),
      bottomNavigationBar: showLogin
          ? null
          : NavigationBar(
              selectedIndex: tab,
              onDestinationSelected: (value) {
                setState(() => tab = value);
                if (value == 1) unawaited(connection!.refreshQuota());
              },
              destinations: const [
                NavigationDestination(
                  icon: Icon(Icons.home_outlined),
                  selectedIcon: Icon(Icons.home_rounded),
                  label: 'VPN',
                ),
                NavigationDestination(
                  icon: Icon(Icons.bar_chart_rounded),
                  label: 'Трафик',
                ),
                NavigationDestination(
                  icon: Icon(Icons.person_outline),
                  selectedIcon: Icon(Icons.person),
                  label: 'Профиль',
                ),
              ],
            ),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 520),
            child: restoring
                ? const CircularProgressIndicator()
                : widget.api == null
                ? const Padding(
                    padding: EdgeInsets.all(24),
                    child: Text(
                      'API не настроен. Соберите приложение с VPN_API_ORIGIN согласно инструкции I-013.',
                    ),
                  )
                : showLogin
                ? LoginScreen(
                    api: widget.api!,
                    native: widget.native,
                    onSignedIn: signedIn,
                  )
                : switch (tab) {
                    1 => QuotaScreen(
                      controller: connection!.quota,
                      embedded: true,
                    ),
                    2 => ProfileScreen(
                      onSignOut: signOut,
                      onTheme: () => setState(
                        () => theme = theme == ThemeMode.dark
                            ? ThemeMode.light
                            : ThemeMode.dark,
                      ),
                      controller: connection!,
                    ),
                    _ => ConnectionScreen(
                      controller: connection!,
                      onSignOut: signOut,
                    ),
                  },
          ),
        ),
      ),
    ),
  );
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({
    super.key,
    required this.api,
    required this.native,
    required this.onSignedIn,
  });
  final Api api;
  final NativeTunnel native;
  final VoidCallback onSignedIn;
  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final code = TextEditingController();
  Map<String, dynamic>? challenge;
  bool working = false;
  String? error;
  Future<void> perform(Future<void> Function() action) async {
    if (working) return;
    setState(() {
      working = true;
      error = null;
    });
    try {
      await action();
    } catch (e) {
      if (mounted)
        setState(
          () => error = messageFor(e is ClientError ? e.code : 'login_failed'),
        );
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  @override
  void dispose() {
    code.clear();
    code.dispose();
    challenge = null;
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(24),
    children: [
      const OrbitArtwork(),
      Text(
        'Ваш маршрут\nк свободному интернету',
        style: Theme.of(context).textTheme.headlineMedium,
      ),
      const SizedBox(height: 16),
      const Text(
        'Войдите через Telegram. Ключ устройства создаётся на телефоне. '
        'Android запросит разрешение VPN только после нажатия «Подключить». '
        'Доступ зависит от подписки, квоты и готовности узла.',
      ),
      const SizedBox(height: 24),
      FilledButton(
        onPressed: working
            ? null
            : () => perform(() async {
                final value = await widget.api.call(
                  'POST',
                  '/api/v1/auth/start',
                );
                if (mounted) setState(() => challenge = value);
              }),
        child: Text(
          challenge == null
              ? 'Войти через Telegram'
              : 'Получить новый код входа',
        ),
      ),
      if (challenge != null) ...[
        const SizedBox(height: 12),
        OutlinedButton(
          onPressed: working
              ? null
              : () => perform(
                  () =>
                      widget.native.openLogin(challenge!['bot_url'] as String),
                ),
          child: const Text('Открыть бота в Telegram'),
        ),
        const Text(
          'Откройте бота, нажмите Start и введите полученный код. Не отправляйте его в чат поддержки.',
        ),
        TextField(
          controller: code,
          obscureText: true,
          autocorrect: false,
          enableSuggestions: false,
          maxLength: 128,
          decoration: const InputDecoration(labelText: 'Код из Telegram'),
        ),
        FilledButton(
          onPressed: working
              ? null
              : () => perform(() async {
                  final value = await widget.api
                      .call('POST', '/api/v1/auth/verify', {
                        'request_id': challenge!['request_id'],
                        'verifier': challenge!['verifier'],
                        'code': code.text.trim(),
                      });
                  await widget.api.acceptSession(value);
                  code.clear();
                  challenge = null;
                  widget.onSignedIn();
                }),
          child: const Text('Подтвердить вход'),
        ),
      ],
      if (working)
        const Padding(
          padding: EdgeInsets.all(16),
          child: LinearProgressIndicator(),
        ),
      if (error != null)
        Padding(
          padding: const EdgeInsets.only(top: 16),
          child: Semantics(liveRegion: true, child: Text(error!)),
        ),
      const SizedBox(height: 24),
      const Text(
        'Ключ устройства и сессия зашифрованы на телефоне с помощью Android Keystore. После перезапуска приложения VPN подключается вручную. '
        'Просмотренные сайты и DNS-запросы не отправляются в API приложения.',
      ),
    ],
  );
}
