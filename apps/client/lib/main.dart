import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';
import 'app_update.dart';
import 'connection.dart';
import 'home_screen.dart';
import 'login_screen.dart';
import 'native_tunnel.dart';
import 'quota_screen.dart';
import 'orbit_theme.dart';
import 'orbit_widgets.dart';
import 'platform_info.dart';
import 'profile_screen.dart';
import 'session_store.dart';
import 'settings_screen.dart';
import 'ui/components.dart';
import 'ui/glass_nav.dart';
import 'ui/motion.dart';
import 'window_connection.dart';
import 'always_on_recovery.dart';
export 'home_screen.dart' show ConnectionScreen;
export 'login_screen.dart' show LoginScreen;

@pragma('vm:entry-point')
Future<void> alwaysOnRecoveryMain() => runAlwaysOnRecovery();

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  const origin = String.fromEnvironment('VPN_API_ORIGIN');
  Api? api;
  try {
    api = Api(Uri.parse(origin), store: PlatformSessionStore());
  } catch (_) {
    /* Explicit setup screen. */
  }
  // ADR-107: the in-app updater exists only in the Android client.
  runApp(
    VpnApp(
      api: api,
      native: PlatformTunnel(),
      // Window-tunnel builds (the signed beta) are Android too: only Windows lacks the updater.
      updates: isWindowsClient ? null : const PlatformUpdateNative(),
    ),
  );
}

class VpnApp extends StatefulWidget {
  const VpnApp({
    super.key,
    required this.api,
    required this.native,
    this.updates,
  });
  final Api? api;
  final NativeTunnel native;

  /// Null disables the start-up update check (tests, Windows).
  final UpdateNative? updates;
  @override
  State<VpnApp> createState() => _VpnAppState();
}

class _VpnAppState extends State<VpnApp> with WidgetsBindingObserver {
  ConnectionController? connection;
  bool showLogin = true;
  bool guest = false;
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
    if (widget.api != null && widget.updates != null) {
      _updates = UpdateScheduler(widget.api!, widget.updates!);
      unawaited(_runUpdateCheck(manual: false));
      unawaited(_loadInstalledVersion(widget.updates!));
    }
  }

  UpdateScheduler? _updates;

  /// Shown in Settings next to «Проверить обновления», e.g. "0.1.0-beta.31 (код 32)".
  String? _installedVersion;

  Future<void> _loadInstalledVersion(UpdateNative native) async {
    final label = installedVersionLabel(
      await native.installedVersionName(),
      await native.installedVersionCode(),
    );
    if (mounted && label != null) setState(() => _installedVersion = label);
  }

  final _navigator = GlobalKey<NavigatorState>();

  /// ADR-107: at start, on return to the screen (at most every 6 h) and from Settings.
  /// Automatic checks are silent on failure and never block the app.
  Future<UpdateCheck?> _runUpdateCheck({required bool manual}) async {
    final scheduler = _updates;
    if (scheduler == null) return null;
    return scheduler.run(manual: manual, show: _showUpdate);
  }

  Future<void> _showUpdate(UpdateOffer offer) async {
    final scheduler = _updates;
    final context = _navigator.currentContext;
    if (scheduler == null || !mounted || context == null || !context.mounted) {
      return;
    }
    await showUpdateDialog(context, offer, scheduler.native);
  }

  /// The home-screen banner opens the same dialog and download flow as Settings.
  void _openUpdateBanner(UpdateOffer offer) =>
      unawaited(_updates?.present(offer, _showUpdate));

  /// ADR-100: no network at startup must not sign the user out. Temporary failures are retried
  /// (about 30 s in total) and the saved session is kept for the next resume or launch.
  /// ADR-158: only a confirmed "no session" (nothing stored, or a record for another origin,
  /// both handled inside [Api.restoreSession]) or the server's refusal (`session_expired`, which
  /// [Api] raises after it already erased the record) ends the session. Every other failure —
  /// a storage read error at a cold start, a 5xx, an unexpected exception — keeps it.
  bool _sessionKept = false;
  static const _restoreDelays = [1, 2, 4, 8, 15];
  Future<void> restore() async {
    try {
      for (var attempt = 0; ; attempt++) {
        try {
          await widget.api!.restoreSession();
          _sessionKept = false;
          if (mounted && widget.api!.authenticated) signedIn();
          return;
        } catch (e) {
          if (e is ClientError && e.code == 'session_expired') rethrow;
          if (attempt >= _restoreDelays.length || !mounted) {
            _sessionKept = true;
            return;
          }
          await Future<void>.delayed(
            Duration(seconds: _restoreDelays[attempt]),
          );
        }
      }
    } catch (_) {
      // `session_expired`: the server refused the session and Api erased it already.
      _sessionKept = false;
    } finally {
      if (mounted) setState(() => restoring = false);
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // ADR-142: matches the Activity's onStart/onStop, where native takes over recovery.
    // `inactive` (shade, dialogs) is still in the foreground.
    connection?.appForegroundChanged(
      state == AppLifecycleState.resumed || state == AppLifecycleState.inactive,
    );
    if (state == AppLifecycleState.resumed &&
        showLogin &&
        _sessionKept &&
        !restoring) {
      setState(() => restoring = true);
      unawaited(restore());
    }
    if (state == AppLifecycleState.resumed && !showLogin) {
      unawaited(connection?.refresh());
      unawaited(connection?.refreshQuota());
    }
    // The VPN keeps the app alive for days, so a check only at start is not enough.
    if (state == AppLifecycleState.resumed) {
      unawaited(_runUpdateCheck(manual: false));
    }
  }

  /// Guest devices (ADR-093) get a reduced profile. The server enforces the limits either way;
  /// a failed lookup just leaves the owner layout, never grants anything.
  Future<void> _loadRole() async {
    try {
      final me = await widget.api!.call('GET', '/api/v1/me');
      if (mounted) setState(() => guest = me['guest'] == true);
    } catch (_) {
      /* Owner layout stays. */
    }
  }

  void signedIn() {
    unawaited(_loadRole());
    final value =
        connection ??
        (windows
            ? WindowConnectionController(
                widget.api!,
                widget.native as WindowNative,
                // ADR-142: on Android native owns recovery while the app is in the background.
                nativeOwnsBackgroundRecovery: !isWindowsClient,
              )
            : ConnectionController(widget.api!, widget.native));
    if (value is WindowConnectionController) {
      value.onAuthenticatedSessionChanged();
    }
    setState(() {
      connection = value;
      showLogin = false;
      tab = 0;
    });
    if (value is WindowConnectionController &&
        widget.native is NodePreferenceStore) {
      unawaited(_initializeWindowController(value));
    } else {
      value.startPolling();
      unawaited(value.refresh());
      unawaited(value.refreshQuota());
      unawaited(value.refreshNodes());
    }
  }

  Future<void> _initializeWindowController(
    WindowConnectionController value,
  ) async {
    await value.refreshNodes();
    if (!mounted || showLogin || !identical(connection, value)) return;
    try {
      final selected = await (widget.native as NodePreferenceStore)
          .readSelectedNode();
      // ADR-145 (2.3): kept even if the node list failed to load; applied after a refresh.
      if (selected != null) value.preferNode(selected);
    } catch (_) {
      // Node selection remains manual if the local preference cannot be read.
    }
    if (!mounted || showLogin || !identical(connection, value)) return;
    value.startPolling();
    unawaited(value.refresh());
    unawaited(value.refreshQuota());
  }

  /// A guest removes its own device (frees the slot, closes its tunnel) and then signs out.
  Future<void> leaveAccount() async {
    final value = connection!;
    await value.disconnect();
    if (value.error == 'native_disconnect_failed') return;
    try {
      // The device list endpoint returns a JSON array, which Api.call does not decode;
      // /me carries the caller's own device id instead.
      final me = await widget.api!.call('GET', '/api/v1/me');
      final id = me['device_id'];
      if (id is String) {
        await widget.api!.call('DELETE', '/api/v1/devices/$id');
      }
    } on ClientError catch (e) {
      value.error = e.code == 'unauthorized'
          ? 'unauthorized'
          : 'leave_device_failed';
      if (mounted) setState(() {});
      return;
    } catch (_) {
      value.error = 'leave_device_failed';
      if (mounted) setState(() {});
      return;
    }
    await _eraseLocalSession(value);
    if (mounted) setState(() => guest = false);
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
    await _eraseLocalSession(value);
  }

  /// ADR-091: deletes the server account first. Local state is erased only after the
  /// server confirmed, so a failed request never looks like a completed deletion.
  Future<void> deleteAccount() async {
    final value = connection!;
    await value.disconnect();
    if (value.error == 'native_disconnect_failed') return;
    try {
      await widget.api!.call('POST', '/api/v1/me/delete', {
        'confirm': 'delete-my-account',
      });
    } on ClientError catch (e) {
      value.error = e.code == 'unauthorized'
          ? 'unauthorized'
          : 'account_delete_failed';
      if (mounted) setState(() {});
      return;
    } catch (_) {
      value.error = 'account_delete_failed';
      if (mounted) setState(() {});
      return;
    }
    await _eraseLocalSession(value);
  }

  Future<void> _eraseLocalSession(ConnectionController value) async {
    try {
      await widget.api!.forgetSession();
    } catch (_) {
      value.error = 'session_erasure_failed';
      if (mounted) setState(() {});
      return;
    } // Do not claim durable logout if erasure failed.
    value.quota.clear();
    value.pausePolling();
    if (widget.native is NodePreferenceStore) {
      try {
        await (widget.native as NodePreferenceStore).writeSelectedNode(null);
      } catch (_) {
        // The preference is non-secret and is revalidated against API nodes.
      }
    }
    // Keep unresolved revoke intent in RAM so reauthentication can finish it.
    if (mounted) setState(() => showLogin = true);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    connection?.dispose();
    _updates?.bannerOffer.dispose();
    widget.api?.close();
    super.dispose();
  }

  void _toggleTheme() => setState(
    () => theme = theme == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark,
  );

  void _openSettings(BuildContext context) => Navigator.of(context).push(
    MaterialPageRoute<void>(
      builder: (_) => SettingsScreen(
        onTheme: _toggleTheme,
        controller: showLogin ? null : connection,
        onCheckUpdates: _updates == null
            ? null
            : () => _runUpdateCheck(manual: true),
        installedVersion: _installedVersion,
      ),
    ),
  );

  static const _navItems = [
    GlassNavItem(
      icon: Icons.home_outlined,
      selectedIcon: Icons.home_rounded,
      label: 'VPN',
    ),
    GlassNavItem(
      icon: Icons.bar_chart_rounded,
      selectedIcon: Icons.insert_chart_rounded,
      label: 'Трафик',
    ),
    GlassNavItem(
      icon: Icons.person_outline_rounded,
      selectedIcon: Icons.person_rounded,
      label: 'Профиль',
    ),
  ];

  void _selectTab(int value) {
    if (value == tab) return;
    setState(() => tab = value);
    if (value == 1) unawaited(connection!.refreshQuota());
  }

  Widget _page() {
    if (restoring) {
      return const Center(
        key: ValueKey('restoring'),
        child: CircularProgressIndicator(),
      );
    }
    if (widget.api == null) {
      return const Padding(
        key: ValueKey('no-api'),
        padding: EdgeInsets.all(24),
        child: Text(
          'API не настроен. Соберите приложение с VPN_API_ORIGIN согласно инструкции I-013.',
        ),
      );
    }
    if (showLogin) {
      return LoginScreen(
        key: const ValueKey('login'),
        api: widget.api!,
        native: widget.native,
        onSignedIn: signedIn,
      );
    }
    return KeyedSubtree(
      key: ValueKey('tab-$tab'),
      child: switch (tab) {
        1 => QuotaScreen(controller: connection!.quota, embedded: true),
        2 => ProfileScreen(
          onSignOut: signOut,
          onDeleteAccount: deleteAccount,
          guest: guest,
          onLeaveDevice: leaveAccount,
          controller: connection!,
        ),
        _ => ConnectionScreen(
          controller: connection!,
          onSignOut: signOut,
          onOpenTraffic: () => _selectTab(1),
          updateOffer: _updates?.bannerOffer,
          onOpenUpdate: _openUpdateBanner,
        ),
      },
    );
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    navigatorKey: _navigator,
    title: 'DetourVPN',
    debugShowCheckedModeBanner: false,
    themeMode: theme,
    themeAnimationDuration: const Duration(milliseconds: 450),
    themeAnimationCurve: Curves.easeInOut,
    theme: orbitTheme(Brightness.light),
    darkTheme: orbitTheme(Brightness.dark),
    home: Builder(
      builder: (context) {
        final nav = !showLogin && !restoring && widget.api != null;
        return Scaffold(
          extendBody: true,
          appBar: AppBar(
            title: const OrbitLogo(),
            actions: [
              IconButton(
                tooltip: 'Настройки',
                onPressed: () => _openSettings(context),
                icon: const Icon(Icons.settings_outlined),
              ),
              const SizedBox(width: 4),
            ],
          ),
          bottomNavigationBar: nav
              ? GlassNavBar(
                  items: _navItems,
                  selectedIndex: tab,
                  onSelected: _selectTab,
                )
              : null,
          body: OrbitBackdrop(
            child: SafeArea(
              bottom: !nav,
              child: GlassNavInset(
                inset: nav ? GlassNavBar.insetOf(context) : 0,
                child: Center(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 520),
                    child: OrbitSwitcher(child: _page()),
                  ),
                ),
              ),
            ),
          ),
        );
      },
    ),
  );
}
