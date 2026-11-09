import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';
import 'auth_replay_probe.dart';
import 'connection.dart';
import 'native_tunnel.dart';
import 'orbit_widgets.dart';
import 'ui/components.dart';
import 'ui/motion.dart';
import 'ui/tokens.dart';

enum _LoginMode { welcome, telegram, code }

/// Welcome → Telegram sign-in or a one-time device code (ADR-093).
/// The request logic is unchanged; only the layout is split into steps.
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
  final linkCode = TextEditingController();
  _LoginMode mode = _LoginMode.welcome;
  Map<String, dynamic>? challenge;
  bool working = false;
  bool accepted = false;
  bool restoringChallenge = true;
  String? error;

  @override
  void initState() {
    super.initState();
    unawaited(_restoreChallenge());
  }

  Map<String, dynamic>? _validChallenge(Map<String, dynamic>? value) {
    if (value == null) return null;
    final requestId = value['request_id'];
    final verifier = value['verifier'];
    final botUrl = value['bot_url'];
    final expiresAt = value['expires_at'];
    if (requestId is! String ||
        !RegExp(
          r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$',
        ).hasMatch(requestId) ||
        verifier is! String ||
        verifier.isEmpty ||
        verifier.length > 128 ||
        botUrl is! String ||
        expiresAt is! String) {
      return null;
    }
    final uri = Uri.tryParse(botUrl);
    final expiry = DateTime.tryParse(expiresAt)?.toUtc();
    if (uri == null ||
        uri.scheme != 'https' ||
        uri.host != 't.me' ||
        uri.userInfo.isNotEmpty ||
        uri.hasPort ||
        expiry == null ||
        !expiry.isAfter(DateTime.now().toUtc())) {
      return null;
    }
    return {
      'request_id': requestId,
      'verifier': verifier,
      'bot_url': botUrl,
      'expires_at': expiry.toIso8601String(),
    };
  }

  Future<void> _restoreChallenge() async {
    try {
      final saved = await widget.api.readPendingLogin();
      final restored = _validChallenge(saved);
      if (restored != null) {
        challenge = restored;
        mode = _LoginMode.telegram;
      } else if (saved != null) {
        await widget.api.writePendingLogin(null);
        error =
            'Сохранённая попытка входа больше недействительна. Запросите новый код.';
      }
    } catch (_) {
      error = 'Не удалось восстановить попытку входа. Запросите новый код.';
    } finally {
      if (mounted) setState(() => restoringChallenge = false);
    }
  }

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
    linkCode.clear();
    linkCode.dispose();
    challenge = null;
    super.dispose();
  }

  Future<void> _start() => perform(() async {
    final value = await widget.api.call('POST', '/api/v1/auth/start');
    final pending = _validChallenge(value);
    if (pending == null) throw const ClientError('invalid_response');
    await widget.api.writePendingLogin(pending);
    code.clear();
    if (mounted) {
      setState(() {
        challenge = pending;
        mode = _LoginMode.telegram;
      });
    }
  });

  Future<void> _verify() => perform(() async {
    final dialogContext = context;
    final request = <String, dynamic>{
      'request_id': challenge!['request_id'],
      'verifier': challenge!['verifier'],
      'code': code.text.trim(),
    };
    final value = await widget.api.call('POST', '/api/v1/auth/verify', request);
    final probeResult = i026DuplicateOtpProbeEnabled
        ? await probeDuplicateOtpReplay(widget.api, request, value)
        : null;
    await widget.api.writePendingLogin(null);
    await widget.api.acceptSession(value);
    code.clear();
    challenge = null;
    if (probeResult != null && dialogContext.mounted) {
      final message = switch (probeResult) {
        DuplicateOtpReplayResult.rejected =>
          'Проверка пройдена: сервер отклонил повторное использование OTP.',
        DuplicateOtpReplayResult.accepted =>
          'ОШИБКА БЕЗОПАСНОСТИ: сервер принял OTP повторно. Вторичная сессия отозвана; друзей пока не приглашать.',
        DuplicateOtpReplayResult.inconclusive =>
          'Проверка не завершена: ответ на повторный запрос не удалось подтвердить.',
        DuplicateOtpReplayResult.cleanupFailed =>
          'Проверка не завершена: не удалось подтвердить отзыв повторной сессии.',
      };
      await showDialog<void>(
        context: dialogContext,
        builder: (context) => AlertDialog(
          title: const Text('Проверка повторного OTP'),
          content: Text(message),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('Продолжить'),
            ),
          ],
        ),
      );
    }
    widget.onSignedIn();
  });

  Future<void> _redeem() => perform(() async {
    final value = await widget.api.call('POST', '/api/v1/auth/link/redeem', {
      'code': linkCode.text.trim(),
    });
    await widget.api.writePendingLogin(null);
    await widget.api.acceptSession(value);
    linkCode.clear();
    widget.onSignedIn();
  });

  Widget _step(BuildContext context, String title, String subtitle) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Align(
          alignment: Alignment.centerLeft,
          child: IconButton(
            tooltip: 'Назад',
            onPressed: working
                ? null
                : () => setState(() {
                    mode = _LoginMode.welcome;
                    error = null;
                  }),
            icon: const Icon(Icons.arrow_back_rounded),
          ),
        ),
        const SizedBox(height: 8),
        Text(title, textAlign: TextAlign.center, style: text.headlineSmall),
        const SizedBox(height: 8),
        Text(subtitle, textAlign: TextAlign.center, style: text.bodySmall),
        const SizedBox(height: 24),
      ],
    );
  }

  List<Widget> _welcome(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return [
      OrbitBlob(
        height: 260,
        child: Text(
          'Добро\nпожаловать',
          textAlign: TextAlign.center,
          style: text.headlineMedium,
        ),
      ),
      Text(
        'Войдите через Telegram. Ключ устройства создаётся на этом телефоне.',
        textAlign: TextAlign.center,
        style: text.bodyMedium,
      ),
      const SizedBox(height: 16),
      // Beta notice (docs/beta-privacy-notice.md): consent is per sign-in, nothing is stored.
      Material(
        type: MaterialType.transparency,
        child: InkWell(
          key: const Key('consent-check'),
          onTap: working ? null : () => setState(() => accepted = !accepted),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Checkbox(
                value: accepted,
                onChanged: working
                    ? null
                    : (value) => setState(() => accepted = value ?? false),
              ),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(
                    'Это закрытая бета без гарантий. Сервер хранит ваш Telegram-ID, '
                    'устройства и расход трафика, а адрес подключения может попасть в '
                    'технические журналы до 7 дней. Сайты и DNS не собираются.',
                    style: text.bodySmall,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
      const SizedBox(height: 8),
      ..._errorSlot(),
      FilledButton.icon(
        onPressed: working || restoringChallenge
            ? null
            : challenge != null
            ? () => setState(() => mode = _LoginMode.telegram)
            : accepted
            ? _start
            : null,
        icon: const Icon(Icons.send_rounded),
        label: Text(
          restoringChallenge
              ? 'Восстановление входа…'
              : challenge == null
              ? 'Войти через Telegram'
              : 'Продолжить вход',
        ),
      ),
      const SizedBox(height: 8),
      TextButton(
        key: const Key('link-code-open'),
        onPressed: working || !accepted
            ? null
            : () => setState(() {
                mode = _LoginMode.code;
                error = null;
              }),
        child: const Text('У меня есть код устройства'),
      ),
      const SizedBox(height: 24),
      Text(
        'Android запросит разрешение VPN только после нажатия «Подключить». '
        'Доступ зависит от подписки, квоты и готовности узла. '
        'Ключ устройства и сессия зашифрованы с помощью Android Keystore; '
        'просмотренные сайты и DNS-запросы не отправляются в API приложения.',
        textAlign: TextAlign.center,
        style: text.bodySmall,
      ),
    ];
  }

  List<Widget> _telegram(BuildContext context) {
    final t = OrbitTokens.of(context);
    final text = Theme.of(context).textTheme;
    return [
      _step(
        context,
        'Вход через Telegram',
        'Откройте бота, нажмите Start и введите полученный код.',
      ),
      OrbitPanel(
        selected: true,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        onTap: working
            ? null
            : () => perform(
                () => widget.native.openLogin(challenge!['bot_url'] as String),
              ),
        child: Row(
          children: [
            Container(
              width: 40,
              height: 40,
              decoration: const BoxDecoration(
                shape: BoxShape.circle,
                color: Color(0xff2aabee),
              ),
              child: const Icon(
                Icons.send_rounded,
                color: Colors.white,
                size: 20,
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Text('Открыть бота в Telegram', style: text.titleMedium),
            ),
            Icon(Icons.open_in_new_rounded, color: t.textMuted, size: 20),
          ],
        ),
      ),
      const SizedBox(height: 24),
      TextField(
        controller: code,
        obscureText: true,
        autocorrect: false,
        enableSuggestions: false,
        maxLength: 128,
        decoration: const InputDecoration(labelText: 'Код из Telegram'),
      ),
      const SizedBox(height: 8),
      ..._errorSlot(),
      FilledButton(
        onPressed: working ? null : _verify,
        child: const Text('Подтвердить вход'),
      ),
      const SizedBox(height: 16),
      Row(
        children: [
          Icon(Icons.info_outline, size: 18, color: t.textMuted),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'Не отправляйте код в чат поддержки.',
              style: text.bodySmall,
            ),
          ),
        ],
      ),
      const SizedBox(height: 8),
      TextButton(
        onPressed: working || restoringChallenge ? null : _start,
        child: const Text('Получить новый код входа'),
      ),
    ];
  }

  List<Widget> _code(BuildContext context) => [
    _step(
      context,
      'Вход по коду',
      'Введите одноразовый код, который передал владелец аккаунта. '
          'Устройство подключится как гостевое: оно пользуется VPN за счёт трафика владельца, '
          'но не управляет аккаунтом.',
    ),
    TextField(
      key: const Key('link-code-field'),
      controller: linkCode,
      autocorrect: false,
      enableSuggestions: false,
      textCapitalization: TextCapitalization.characters,
      maxLength: 32,
      style: Theme.of(context).textTheme.titleLarge?.copyWith(letterSpacing: 3),
      decoration: const InputDecoration(
        labelText: 'Код устройства',
        hintText: 'XXXX-XXXX',
      ),
    ),
    const SizedBox(height: 8),
    ..._errorSlot(),
    FilledButton(
      key: const Key('link-code-submit'),
      onPressed: working ? null : _redeem,
      child: const Text('Подключить это устройство'),
    ),
  ];

  @override
  Widget build(BuildContext context) {
    final shown = mode == _LoginMode.telegram && challenge == null
        ? _LoginMode.welcome
        : mode;
    return ListView(
      padding: const EdgeInsets.fromLTRB(24, 8, 24, 32),
      children: [
        OrbitSwitcher(
          offset: .04,
          child: Column(
            key: ValueKey(shown),
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: switch (shown) {
              _LoginMode.welcome => _welcome(context),
              _LoginMode.telegram => _telegram(context),
              _LoginMode.code => _code(context),
            },
          ),
        ),
        if (working || restoringChallenge)
          const Padding(
            padding: EdgeInsets.only(top: 16),
            child: LinearProgressIndicator(),
          ),
      ],
    );
  }

  /// Errors sit right above the step's main action so they stay in view.
  List<Widget> _errorSlot() => [
    if (error != null) ...[
      OrbitNotice(danger: true, text: error!),
      const SizedBox(height: 16),
    ],
  ];
}
