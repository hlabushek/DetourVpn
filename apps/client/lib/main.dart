import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';
import 'connection.dart';
import 'native_tunnel.dart';
import 'quota_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  const origin = String.fromEnvironment('VPN_API_ORIGIN');
  Api? api;
  try {
    api = Api(Uri.parse(origin));
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
  ThemeMode theme = ThemeMode.system;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
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
        connection ?? ConnectionController(widget.api!, widget.native);
    value.startPolling();
    setState(() {
      connection = value;
      showLogin = false;
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
    widget.api!.forgetSession();
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
    theme: ThemeData(
      colorSchemeSeed: const Color(0xff206c60),
      useMaterial3: true,
    ),
    darkTheme: ThemeData(
      colorSchemeSeed: const Color(0xff67d7bc),
      brightness: Brightness.dark,
      useMaterial3: true,
    ),
    home: Scaffold(
      appBar: AppBar(
        title: const Text('DetourVpn'),
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
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 520),
            child: widget.api == null
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
                : ConnectionScreen(controller: connection!, onSignOut: signOut),
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
      Text(
        'Подключение под вашим контролем',
        style: Theme.of(context).textTheme.headlineMedium,
      ),
      const SizedBox(height: 16),
      const Text(
        'Войдите через Telegram. Ключ устройства создаётся на телефоне. '
        'Android запросит разрешение VPN только после нажатия «Подключить». '
        'Этот клиент использует тестовый узел и подключения до 60 секунд.',
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
                  widget.api.acceptSession(value);
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
        'Сессия и ключ хранятся только в памяти. После завершения процесса потребуется новый вход. '
        'Просмотренные сайты и DNS-запросы не отправляются в API приложения.',
      ),
    ],
  );
}

class ConnectionScreen extends StatelessWidget {
  const ConnectionScreen({
    super.key,
    required this.controller,
    required this.onSignOut,
  });
  final ConnectionController controller;
  final Future<void> Function() onSignOut;
  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: controller,
    builder: (context, _) {
      final c = controller;
      final connected = c.phase == ConnectionPhase.connected;
      final title = switch (c.phase) {
        ConnectionPhase.disconnected => 'Отключено',
        ConnectionPhase.connecting => 'Подключение',
        ConnectionPhase.connected => 'Подключено',
        ConnectionPhase.reconnecting => 'Восстановление связи',
        ConnectionPhase.failed => 'Требуется действие',
      };
      return ListView(
        padding: const EdgeInsets.all(24),
        children: [
          Semantics(
            liveRegion: true,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: Theme.of(context).textTheme.headlineLarge),
                const SizedBox(height: 8),
                Text(c.detail),
              ],
            ),
          ),
          const SizedBox(height: 24),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Row(
                    children: [
                      Icon(Icons.dns_outlined),
                      SizedBox(width: 12),
                      Expanded(child: Text('Тестовые узлы')),
                    ],
                  ),
                  const SizedBox(height: 12),
                  const Text('До 60 секунд · бюджет 1 048 576 байт'),
                  RadioGroup<String?>(
                    groupValue: c.selectedNode,
                    onChanged: c.selectNode,
                    child: Column(
                      children: [
                        RadioListTile<String?>(
                          value: null,
                          title: const Text('Первый доступный узел'),
                          enabled: !c.busy && !c.hasLease,
                        ),
                        for (final node in c.nodes)
                          RadioListTile<String?>(
                            value: node.id,
                            title: Text('${node.name} · ${node.region}'),
                            subtitle: Text(nodeMessage(node.reason)),
                            enabled:
                                !c.busy &&
                                !c.hasLease &&
                                c.nodesError == null &&
                                node.reason == 'available',
                          ),
                      ],
                    ),
                  ),
                  if (c.nodesError != null)
                    const Text(
                      'Каталог не обновлён; прежние статусы устарели.',
                    ),
                  if (c.nodesUpdatedAt != null)
                    Text(
                      'Проверка: ${c.nodesUpdatedAt!.toLocal().toString().substring(0, 19)}',
                    ),
                  TextButton(
                    onPressed: c.nodesLoading ? null : c.refreshNodes,
                    child: const Text('Обновить список узлов'),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    c.availableBytes == null
                        ? 'Остаток трафика неизвестен'
                        : 'Доступно: ${c.availableBytes} байт',
                  ),
                  if (c.quotaUpdatedAt != null)
                    Text(
                      '${c.quotaStale ? 'Данные устарели' : 'Обновлено'}: ${c.quotaUpdatedAt!.toLocal().toString().substring(0, 19)}',
                    ),
                  TextButton(
                    onPressed: c.busy ? null : c.refreshQuota,
                    child: const Text('Обновить остаток'),
                  ),
                  TextButton.icon(
                    icon: const Icon(Icons.receipt_long_outlined),
                    label: const Text('Трафик и история'),
                    onPressed: () {
                      unawaited(c.refreshQuota());
                      Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => QuotaScreen(controller: c.quota),
                        ),
                      );
                    },
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 24),
          FilledButton.icon(
            style: FilledButton.styleFrom(
              minimumSize: const Size.fromHeight(64),
            ),
            onPressed: c.busy || c.hasLease ? null : c.connect,
            icon: Icon(connected ? Icons.shield : Icons.power_settings_new),
            label: const Text('Подключить'),
          ),
          const SizedBox(height: 12),
          OutlinedButton(
            onPressed: c.disconnect,
            child: Text(c.busy ? 'Отменить подключение' : 'Отключить VPN'),
          ),
          if (c.error != null)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 16),
              child: Semantics(
                liveRegion: true,
                child: Text(
                  messageFor(c.error!),
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            ),
          if (c.remotePending)
            const Text('Ожидается подтверждение удаления peer на сервере.'),
          if (c.remotePending) ...[
            const Text(
              'Если узел потерян, после истечения lease можно завершить '
              'его с полным списанием резерва 1 048 576 байт. '
              'Это позволит выбрать другой узел.',
            ),
            TextButton(
              onPressed: c.canRecover ? c.recoverExpired : null,
              child: const Text(
                'Завершить истёкший lease со списанием резерва',
              ),
            ),
          ],
          const SizedBox(height: 24),
          const Text(
            'Статус подключения основан на системном VPN, свежем handshake и активном lease. '
            'Проверка внешнего IP и DNS выполняется отдельно. Автоматического продления нет.',
          ),
          TextButton(
            onPressed: c.busy ? null : onSignOut,
            child: const Text('Выйти из аккаунта'),
          ),
        ],
      );
    },
  );
}
