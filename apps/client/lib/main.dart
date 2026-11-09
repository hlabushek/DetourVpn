import 'dart:async';

import 'package:flutter/material.dart';

import 'api.dart';
import 'connection.dart';
import 'home_screen.dart';
import 'login_screen.dart';
import 'native_tunnel.dart';
import 'quota_screen.dart';
import 'orbit_theme.dart';
import 'orbit_widgets.dart';
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
      if (selected != null) value.selectNode(selected);
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
        ),
      },
    );
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
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
