import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'api.dart';
import 'country_flag.dart';
import 'connected_since.dart';
import 'connection.dart';
import 'diagnostic_report.dart';
import 'native_tunnel.dart';
import 'offline_grace.dart';
import 'panel_servers.dart';
import 'platform_info.dart';
import 'recovery_budget.dart';

/// Explicit v3 selection, never falls back to the bounded v2 protocol.
class WindowConnectionController extends ConnectionController {
  WindowConnectionController(
    super.api,
    WindowNative super.native, {
    super.pollInterval,
    this.renewBeforeMs = 55000,
    this.handshakeHintAfter = const Duration(seconds: 25),
    this.autoReconnectDelay = const Duration(seconds: 2),
    this.windowSeconds = defaultWindowSeconds,
    this.revokeWaitInterval = const Duration(seconds: 1),
    this.revokeWaitChecks = 10,
    this.dataPathFlowFor = const Duration(seconds: 120),
    this.handshakeTimeout = const Duration(seconds: 20),
    this.snapshotRetryDelay = const Duration(milliseconds: 500),
    this.snapshotAttempts = 3,
    this.nativeCallTimeout = const Duration(seconds: 20),
    this.nativeStartTimeout = const Duration(seconds: 60),
    Uri? renewalOrigin,
    bool? panelVless,
    this.nativeOwnsBackgroundRecovery = false,
    Duration offlineGrace = OfflineGrace.defaultLength,
    this.nativeRenewalRetry = const Duration(seconds: 60),
  }) : _renewalOrigin = renewalOrigin,
       _grace = OfflineGrace(length: offlineGrace),
       panelVless = panelVless ?? isWindowsClient {
    // ADR-144: the Android UI listens to native's pushed connection state.
    final source = native;
    if (nativeOwnsBackgroundRecovery && source is NativeStateSource) {
      _stateEvents = (source as NativeStateSource).stateEvents().listen(
        _onNativeState,
      );
    }
  }

  /// ADR-145 (2.4): a window this controller started with `connectWindow` must show a handshake
  /// within this time, or it fails with `handshake_not_established` (retried once on a new lease).
  final Duration handshakeTimeout;

  /// ADR-191 (Android UI only): while a held window has no native renewal (its hand-off failed,
  /// or native renewal stopped), a successful poll hands it to native again at most this often.
  /// Native renewal is what renews the window while Dart timers sleep in Doze.
  final Duration nativeRenewalRetry;
  int? _nativeRenewalTriedAt;
  bool _awaitingFirstHandshake = false;
  int _handshakeRetries = 0;
  static const maxHandshakeRetries = 1;

  /// ADR-145 (2.2): a failed `snapshot()` during reconcile is retried this many times, this far
  /// apart; then the native state is unknown and nothing is stopped.
  final Duration snapshotRetryDelay;
  final int snapshotAttempts;

  /// ADR-185: the longest this controller waits for one native channel call (snapshot, stop,
  /// window advance, key read) and for a tunnel start. A call that does not answer in time fails
  /// with `native_timeout`; its late answer is never applied (see [_bounded]).
  final Duration nativeCallTimeout;
  final Duration nativeStartTimeout;

  /// ADR-185: native calls that timed out and have not answered yet. While any is outstanding no
  /// new non-stop native call is sent (they would queue behind it on native's serial worker),
  /// connect is refused and the poll does only server-side revoke retries.
  int _lateNative = 0;

  /// ADR-185: a tunnel start timed out; it may still bring a tunnel up that nobody tracks. Cleared
  /// only by a stop that answered after every late call had settled.
  bool _lateStart = false;
  Future<void>? _lateStop;

  bool get _nativeStuck => _lateNative > 0 || _lateStart;

  @visibleForTesting
  bool get nativeStuck => _nativeStuck;

  /// ADR-185: one bounded native call. [kind] stop calls (disconnect, end of intent) are always
  /// sent, also while late calls are outstanding: stopping is the fail-closed direction, and on
  /// Android a disconnect bumps native's generation at once, which makes a late start stop itself.
  Future<T> _bounded<T>(
    Future<T> Function() call, {
    _NativeCall kind = _NativeCall.read,
  }) async {
    if (kind != _NativeCall.stop && _lateNative > 0)
      throw const ClientError('native_timeout');
    final pending = call();
    try {
      return await pending.timeout(
        kind == _NativeCall.start ? nativeStartTimeout : nativeCallTimeout,
      );
    } on TimeoutException {
      _lateNative++;
      if (kind == _NativeCall.start) _lateStart = true;
      unawaited(
        pending
            .then<void>((_) {}, onError: (Object _) {})
            .whenComplete(_lateSettled),
      );
      throw const ClientError('native_timeout');
    }
  }

  /// ADR-185: a timed-out call answered. Its result is dropped. Once nothing is outstanding, a
  /// timed-out start is followed by one more stop, so whatever it started is not left running.
  void _lateSettled() {
    _lateNative--;
    if (_dead) return;
    if (_lateNative == 0 && _lateStart) {
      unawaited(_stopLateStart());
    } else {
      _emit();
    }
  }

  Future<void> _stopLateStart() => _lateStop ??= () async {
    try {
      await _bounded(native.disconnect, kind: _NativeCall.stop);
      if (_lateNative == 0) {
        _lateStart = false;
        // Native's state is not what this controller last saw: the next poll or connect
        // reconciles with a fresh snapshot (and revokes a lease left from the failed start).
        _synced = false;
      }
    } catch (_) {
      // Still unconfirmed; the next poll stops it again.
    } finally {
      _lateStop = null;
      _emit();
    }
  }();

  /// ADR-145 (2.3): the node saved on this phone. Applied once the node list has it, so a node
  /// list that failed to load at startup does not lose the user's choice.
  String? _preferredNode;

  /// ADR-145 (2.3): select the saved node now if the list has it, otherwise after a refresh.
  void preferNode(String id) {
    _preferredNode = id;
    selectNode(id);
  }

  /// A node refresh may still provide a node to connect to.
  bool get _nodeMayAppear =>
      _preferredNode != null || nodes.isEmpty || nodesError != null;

  /// ADR-145: failures that never end the user's "stay connected" wish.
  static const _keepIntentCodes = {
    'window_node_required',
    'native_state_unknown',
    // ADR-185: native did not answer; its state is unknown, and ending the intent is one more
    // native call. Not retried automatically: the user taps again once native answers.
    'native_timeout',
  };

  /// ADR-142 (Android UI controller only): while the app is in the background, native owns
  /// recovery, so this controller's reconnect and resume timers do nothing until it is in the
  /// foreground again. Off for Windows and the headless restore engine.
  /// ADR-144: in the background this controller also never stops the tunnel, revokes the
  /// connection or reconciles; it only renews when native does not. The foreground re-syncs.
  final bool nativeOwnsBackgroundRecovery;
  bool _foreground = true;

  /// ADR-144: native's pushed state stream (Android UI only) and the last state native reported
  /// by event or snapshot. Null on Windows and older builds.
  StreamSubscription<NativeStateEvent>? _stateEvents;
  String? _nativeState;
  String? _nativeReason;

  /// ADR-144: native renews the window itself (last snapshot or event).
  bool _nativeRenewing = false;

  /// ADR-144: polling was paused for the background and resumes in the foreground.
  bool _pollWanted = false;

  /// ADR-144: a refresh failed in the background; the foreground re-syncs with native first.
  String? _backgroundFault;

  /// ADR-144: the last transient API failure while the tunnel kept working (diagnostics only).
  String? lastApiError;

  /// ADR-162: offline grace. Starts when a renewal first fails to reach any API origin, ends at
  /// the earlier of its length and the lease's own deadline; never extends the lease.
  final OfflineGrace _grace;

  /// ADR-162: restarts of a dropped tunnel on its still-valid lease while the API is unreachable.
  int _offlineResumes = 0;
  static const maxOfflineResumes = 3;

  /// ADR-162: an offline resume needs at least this much lease (and grace) left.
  static const minOfflineResumeMs = 30000;

  /// ADR-162: the held lease runs on offline grace (renewal cannot reach the API). Shown to the
  /// user as a degraded state with the time left.
  bool get offlineGrace => _grace.active && _grant != null;

  /// ADR-162: grace time left, or null when not on grace.
  Duration? get offlineGraceRemaining {
    if (!offlineGrace) return null;
    return Duration(
      milliseconds: _grace.remainingMs(_clock.elapsedMilliseconds, _deadline)!,
    );
  }

  void _resetGrace() {
    _grace.clear();
    _offlineResumes = 0;
  }

  @visibleForTesting
  String? get nativeState => _nativeState;

  @visibleForTesting
  String? get nativeReason => _nativeReason;

  @visibleForTesting
  bool get pollingActive => _poll != null;

  /// A reconnect or resume that came due while in the background; run when foreground again.
  Future<void> Function()? _deferredRecovery;

  /// ADR-142: native's restore engine is bringing a tunnel up; never disconnect it, adopt it.
  bool _restorePending = false;
  bool _awaitingRestore = false;

  @visibleForTesting
  bool get recoveryDeferred => _deferredRecovery != null;

  bool get _recoveryGated => nativeOwnsBackgroundRecovery && !_foreground;

  /// ADR-142: the app moved to the foreground (true) or the background (false). On return, a
  /// controller that holds no connection reconciles first (it adopts what native restored).
  @override
  void appForegroundChanged(bool foreground) {
    if (_foreground == foreground) return;
    _foreground = foreground;
    if (!nativeOwnsBackgroundRecovery || _dead) return;
    if (!foreground) {
      // ADR-144: no polling in the background unless this controller still renews the window.
      if (_poll != null && !_dartRenews) {
        pausePolling();
        _pollWanted = true;
      }
      return;
    }
    if (_backgroundFault != null) {
      // Native may have stopped, recovered or replaced the tunnel meanwhile: forget the local
      // view (no stop, no revoke) and reconcile with what native holds now.
      _backgroundFault = null;
      _forgetLocalLease();
    }
    if (_id == null) _synced = false;
    if (_pollWanted) {
      _pollWanted = false;
      startPolling();
    }
    final deferred = _deferredRecovery;
    _deferredRecovery = null;
    if (deferred != null) unawaited(deferred());
  }

  /// ADR-144: this controller renews the window (native does not), so it must keep polling.
  bool get _dartRenews => _id != null && !_nativeRenewing && !_panelUp;

  /// ADR-144: native moved on (stopped, restored or replaced the tunnel) while this controller
  /// was not looking. Drop the local lease view without stopping native or revoking anything;
  /// the next reconcile adopts native's tunnel and revokes stale server rows.
  void _forgetLocalLease() {
    _id = null;
    _createIntent = null;
    _grant = null;
    _config = null;
    _renewIntent = null;
    _deadline = 0;
    _trafficBaseline = null;
    _lastTraffic = null;
    _renewTrafficAnchor = null;
    _inboundTraffic.reset();
    _dataPath.reset();
    _dataStalled = false;
    _awaitingFirstHandshake = false;
    remotePending = false;
    _synced = false;
    _resetGrace();
  }

  /// ADR-144: a state event from native. In the foreground it updates the status at once; when
  /// native's tunnel is not the one this controller holds, it re-syncs.
  void _onNativeState(NativeStateEvent event) {
    if (_dead) return;
    _nativeState = event.state;
    _nativeReason = event.reason;
    _nativeRenewing = event.nativeRenewal;
    if (_recoveryGated) return;
    final ours =
        _id != null &&
        event.up &&
        event.connectionId == _id &&
        !busy &&
        !remotePending;
    if (ours) {
      // ADR-151: render at once, also while a poll is in flight. Before, an event that arrived
      // during a poll was dropped and the status waited for the poll's API round trip.
      if (_transientApiCodes.contains(error)) error = null;
      _renderNative(event.state, event.reason);
      _emit();
      return;
    }
    if (_id == null && event.up && event.connectionId != null) _synced = false;
    unawaited(refresh());
  }

  /// ADR-144: the displayed status from native's state (Android). Only called while this
  /// controller holds a running tunnel; Windows keeps the Dart rule.
  void _renderNative(String state, String reason) {
    switch (state) {
      case 'connected':
        _hasEstablishedHandshake = true;
        _handshakeSeenNow();
        phase = ConnectionPhase.connected;
        detail = 'Связь через VPN подтверждена';
      case 'connecting':
        phase = ConnectionPhase.connecting;
        detail = 'Ожидается handshake';
      default:
        phase = ConnectionPhase.reconnecting;
        detail = recoveringText(reason);
    }
  }

  /// ADR-145 (2.4): the first handshake arrived; a later failure gets a fresh retry.
  void _handshakeSeenNow() {
    _awaitingFirstHandshake = false;
    _handshakeRetries = 0;
  }

  /// ADR-144: Russian text for a native `recovering` reason.
  static String recoveringText(String reason) => switch (reason) {
    'waiting_for_network' => 'Нет сети, VPN ждёт её возвращения',
    'network_changed' => 'Сеть сменилась, восстанавливается связь',
    'data_stalled' =>
      'Нет входящего трафика через VPN, соединение может быть нарушено',
    'handshake_stale' => 'Сервер давно не отвечал, связь проверяется',
    'no_handshake' =>
      'Handshake не получен. Нажмите «Отключить VPN» и подключитесь заново.',
    // ADR-148 (5.3): nothing reaches the server until the user signs in to the network.
    'captive_portal' =>
      'Сеть требует входа (страница авторизации Wi-Fi). Войдите в сеть — VPN подключится сам',
    // ADR-148 (5.2): the probe through the tunnel failed; native starts a new connection.
    'dead_peer' => 'Сервер не отвечает, подключение создаётся заново',
    _ => 'Восстанавливается подключение',
  };

  /// ADR-144: render [snapshot]'s native state; false when it has none (Windows, older builds).
  bool _applyNative(NativeSnapshot snapshot) {
    final state = snapshot.state;
    if (state == null) return false;
    _nativeState = state;
    _nativeReason = snapshot.stateReason;
    _renderNative(state, snapshot.stateReason ?? '');
    return true;
  }

  static const _transientApiCodes = {
    'network_unavailable',
    'api_unavailable',
    'rate_limited',
    // ADR-191: HTTP 503 codes of the window routes and the storage layer. The API answered but
    // could not decide now (database, node agent or clock briefly unavailable); nothing about
    // this lease changed. Before, a poll stopped the tunnel and ended the user's wish on them.
    // The lease deadline (and the offline grace, ADR-162) stays the boundary.
    'storage_unavailable',
    'dependencies_unavailable',
    'database_clock_unavailable',
    'window_tunnels_unavailable',
    'window_node_unavailable',
    'agent_unavailable',
    'quota_inconsistent',
    'window_inconsistent',
  };

  /// ADR-115: this engine can run panel servers (VLESS + Reality). Windows only today.
  final bool panelVless;
  bool _panelUp = false;
  @visibleForTesting
  bool get panelConnected => _panelUp;
  final Duration? autoReconnectDelay;

  /// ADR-119: before a new connection, how often and how many times to check that the previous
  /// one finished closing (the node confirms a revoke within a few seconds).
  final Duration revokeWaitInterval;
  final int revokeWaitChecks;

  /// ADR-119: a refresh failure qualified for automatic reconnect while the old connection was
  /// still closing; reconnect once that cleanup finishes.
  bool _reconnectAfterCleanup = false;

  /// ADR-119: connect-time activation refusals retried in a row (bounded; quota is reserved).
  static const maxActivationRetries = 3;
  int _activationRetries = 0;
  bool _switching = false;

  /// ADR-124: native still holds the user's wish to be connected, but the running tunnel could
  /// not be adopted (or none runs). The next refresh reconnects without a tap.
  bool _resumeWanted = false;

  /// ADR-139: native reported a spent recovery budget at startup; shown as the terminal state.
  bool _nativeExhausted = false;

  /// The native step that failed while no UI ran (from the startup snapshot); null when none.
  String? _nativeFailureStage;

  /// ADR-139/140: pace of automatic attempts in this failure streak (quick steps, then 60 s).
  final RecoveryBudget _recovery = RecoveryBudget();
  static const root = '/api/v1/window-tunnels';
  static const budgetBytes = int.fromEnvironment(
    'VPN_WINDOW_BUDGET_BYTES',
    defaultValue: 1048576,
  );

  /// Requested window length. 90 keeps the legacy request body (no field sent),
  /// so a build only asks for long windows once the API and nodes accept them.
  static const defaultWindowSeconds = int.fromEnvironment(
    'VPN_WINDOW_SECONDS',
    defaultValue: 90,
  );
  final int windowSeconds;
  final Uri? _renewalOrigin;
  // ADR-150: the server and nodes accept windows up to 3600 s (ADR-147 3.1).
  static const maxWindowMs = 3600000;

  /// Single-peer nodes meter the outer IP and use slots 1..16; pool nodes (ADR-101) meter the
  /// inner IP and use slots 1..250. Both come from the same API contract (`v3_contracts.py`).
  static const _meters = {'kernel-outer-ip.v1', 'kernel-inner-ip.v1'};
  static const maxSlotId = 250;
  final int renewBeforeMs;

  /// Long windows: ADR-142, renew about 2 min after each grant (was 5 min), the same rule as native
  /// NativeRenewal.renewBeforeMs, so one Doze-deferred alarm still leaves time before expiry.
  int get _renewBefore => max(renewBeforeMs, windowSeconds * 1000 - 120000);
  Map<String, int> get _windowRequest =>
      windowSeconds == 90 ? const {} : {'window_seconds': windowSeconds};

  /// After this long without any confirmed handshake, the status asks the user to reconnect.
  /// Advisory text only: no automatic reconnect, because every new window reserves quota.
  final Duration handshakeHintAfter;
  int _connectedAtMs = 0;
  final _clock = Stopwatch()..start();
  Map<String, dynamic>? _createIntent;
  Map<String, dynamic>? _renewIntent;
  Map<String, dynamic>? _grant;
  String? _id;
  String? _key;
  String? _deviceId;
  String? _config;
  int _deadline = 0;
  int _intentAt = 0;
  int? _trafficBaseline;
  int? _lastTraffic;
  int? _renewTrafficAnchor;
  final InboundTrafficFreshness _inboundTraffic = InboundTrafficFreshness();

  /// ADR-133: outbound bytes without inbound progress. Shown as unconfirmed; never reconnects.
  /// ADR-133: how long inbound silence may last before outbound-only traffic counts as a stall.
  final Duration dataPathFlowFor;
  late final DataPathHealth _dataPath = DataPathHealth(
    flowFor: dataPathFlowFor,
  );
  bool _dataStalled = false;
  bool _hasEstablishedHandshake = false;

  /// Task 7: the native side's own memory (NativeSnapshot.handshakeSeen). Diagnostics only:
  /// it never changes status, renewal or reconnect decisions.
  bool _nativeHandshakeSeen = false;
  bool _trafficRenewalRequested = false;
  @visibleForTesting
  bool get trafficRenewalRequested => _trafficRenewalRequested;
  @visibleForTesting
  bool get renewalPending => _renewIntent != null;
  int _version = 0;
  bool _closed = false;
  bool _working = false;
  bool _polling = false;
  bool _synced = false;
  bool _dead = false;
  Timer? _poll;
  Future<void>? _connectWork;
  Future<void>? _refreshWork;
  WindowNative get _native => native as WindowNative;
  @override
  bool get hasLease => _id != null || _createIntent != null;
  @override
  bool get busy => _working || _closed;
  @override
  bool get disconnecting => _closed;
  @override
  bool get canRecover => false; // Unknown holds require operator reconciliation.
  void _emit() {
    if (_dead) return;
    _trackConnectedSince();
    notifyListeners();
  }

  /// Home-screen timer only: follows the live connection id and keeps its start across restarts.
  late final ConnectedSinceTracker _connectedSince = ConnectedSinceTracker(
    native is ConnectedSinceStore ? native as ConnectedSinceStore : null,
  );

  @override
  int? get connectedSinceMs => _connectedSince.sinceMs;

  void _trackConnectedSince() {
    final live =
        phase == ConnectionPhase.connected ||
        phase == ConnectionPhase.reconnecting;
    final id = live ? (_id ?? (_panelUp ? 'panel' : null)) : null;
    unawaited(
      _connectedSince.observe(id).then((changed) {
        if (changed && !_dead) notifyListeners();
      }),
    );
  }

  /// A new OTP session is a new server-side family even when this controller
  /// and the phone's locally held key survive a sign-out/sign-in cycle.
  void onAuthenticatedSessionChanged() {
    _deviceId = null;
    _synced = false;
    if (error == 'session_device_mismatch') {
      error = null;
      if (phase == ConnectionPhase.failed) phase = ConnectionPhase.disconnected;
    }
    _emit();
  }

  bool _cancelled(int version) => _dead || version != _version || _closed;

  @override
  void startPolling() {
    // ADR-144: in the background poll only while this controller renews the window itself.
    if (_recoveryGated && !_dartRenews) {
      _pollWanted = true;
      return;
    }
    _poll ??= Timer.periodic(pollInterval, (_) => unawaited(refresh()));
  }

  @override
  void pausePolling() {
    _poll?.cancel();
    _poll = null;
  }

  @override
  Future<void> refreshNodes() async {
    if (nodesLoading || _dead) return;
    nodesLoading = true;
    _emit();
    try {
      // ADR-115: panel rows come only on request; the server adds them when enabled.
      final value = await api.call('GET', '$root/nodes?include=panel');
      final rows = value['nodes'];
      if (rows is! List || rows.length > maxCatalogNodes + maxPanelNodes)
        throw const ClientError('invalid_response');
      final parsed = <NodeOption>[];
      for (final row in rows) {
        if (row is! Map ||
            row['available'] is! bool ||
            row['name'] is! String ||
            row['region'] is! String)
          throw const ClientError('invalid_response');
        final id = _uuid(row['id']);
        if (parsed.any((n) => n.id == id))
          throw const ClientError('invalid_response');
        // Absent fields = an older API: a VPS node. Any other pairing is refused.
        final source = row['source'] ?? 'vps';
        final protocol = row['protocol'] ?? 'awg';
        final panel = source == panelServerSource;
        if (!(source == 'vps' && protocol == 'awg') &&
            !(panel && protocol == panelServerProtocol))
          throw const ClientError('invalid_response');
        final available = row['available'] as bool;
        parsed.add(
          NodeOption(
            id,
            row['name'] as String,
            row['region'] as String,
            panel && !panelVless
                ? panelUnsupportedReason
                : available
                ? 'available'
                : 'capacity',
            source: source as String,
            protocol: protocol as String,
            countryCode: parseCountryCode(row['country_code']),
            city: parseCity(row['city']),
            probeHost: row['probe_host'] is String && row['probe_port'] is int
                ? row['probe_host'] as String
                : null,
            probePort: row['probe_host'] is String && row['probe_port'] is int
                ? row['probe_port'] as int
                : null,
          ),
        );
      }
      nodes = parsed;
      nodesUpdatedAt = DateTime.parse(value['checked_at'] as String);
      nodesError = null;
      // ADR-145 (2.3): the saved node, once the list has it.
      final preferred = _preferredNode;
      if (selectedNode == null && preferred != null) selectNode(preferred);
      unawaited(measureLatency());
    } catch (e) {
      nodesError = e is ClientError ? e.code : 'invalid_response';
    } finally {
      nodesLoading = false;
      _emit();
    }
  }

  static String _uuid(dynamic value) {
    if (value is! String ||
        !RegExp(
          r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        ).hasMatch(value))
      throw const ClientError('invalid_response');
    return value;
  }

  /// After process death, stop native and revoke old device connections before admission.
  Future<void> _ensureDevice() async {
    if (_deviceId != null) return;
    diagnosticStage = 'device_key_read';
    _key ??= await _bounded(native.publicKey);
    diagnosticStage = 'device_registration';
    final device = await api.call('POST', '/api/v1/devices', {
      'platform': devicePlatform,
      'key_type': 'x25519',
      'public_key': _key,
    });
    _deviceId = _uuid(device['id']);
  }

  /// After a UI restart: adopt the tunnel still running in this process, renewed natively
  /// (ADR-117) or by the restore engine (ADR-121). Otherwise stop native and revoke this device's
  /// old connections before admission. Returns true if a running tunnel was adopted.
  Future<bool> _reconcile() async {
    _restorePending = false;
    if (_synced) return false;
    // ADR-145 (2.2): a snapshot that fails is "unknown", never "nothing running". Retry it;
    // if it keeps failing, stop here without touching the tunnel and stay unsynced.
    final live = await _reconcileSnapshot();
    // ADR-142: native's restore engine is still bringing a tunnel up. Never disconnect it;
    // stay unsynced so the next refresh adopts it once it is up (ADR-121 hand-over).
    if (live.restoreActive && !live.up && native is RestoreHandover) {
      alwaysOnStatus = live.alwaysOn ?? alwaysOnStatus;
      _restorePending = true;
      return false;
    }
    alwaysOnStatus = live.alwaysOn ?? alwaysOnStatus;
    // ADR-139: native spent its background budget while no UI was open; say so once.
    _nativeExhausted =
        !live.up &&
        !live.desiredConnected &&
        live.stopCategory == 'recovery_exhausted';
    _nativeFailureStage = live.up ? null : live.failureStage;
    // ADR-191: on Android a running tunnel without native renewal (its hand-off failed, or
    // native renewal stopped) is adopted and renewed from here, not stopped. Its own native
    // deadline still bounds it. Windows keeps the ADR-117 fail-closed rule.
    final adoptable =
        live.up &&
        (live.nativeRenewal ||
            nativeOwnsBackgroundRecovery ||
            (live.restoreActive && native is RestoreHandover)) &&
        live.remainingMs > 0 &&
        live.connectionId != null &&
        live.windowGeneration != null;
    if (!adoptable) {
      _resumeWanted = live.desiredConnected;
      diagnosticStage = 'native_disconnect';
      await _bounded(native.disconnect, kind: _NativeCall.stop);
    }
    diagnosticStage = 'connection_reconcile';
    final result = await api.call('GET', root);
    final rows = result['items'];
    if (rows is! List) throw const ClientError('invalid_response');
    for (final row in rows) {
      if (row is! Map) throw const ClientError('invalid_response');
    }
    if (adoptable) {
      final match = rows
          .where((row) => row['id'] == live.connectionId)
          .firstOrNull;
      if (match is Map<String, dynamic> && await _adopt(match, live)) {
        for (final row in rows) {
          if (identical(row, match) || row['state'] == 'closed') continue;
          try {
            await api.call('DELETE', '$root/${_uuid(row['id'])}');
          } catch (_) {
            /* Best effort; the server refuses a second live connection anyway. */
          }
        }
        _synced = true;
        return true;
      }
      _resumeWanted = live.desiredConnected;
      diagnosticStage = 'native_disconnect';
      await _bounded(native.disconnect, kind: _NativeCall.stop);
      diagnosticStage = 'connection_reconcile';
    }
    // ADR-191: a close the server accepted (202 revoke_pending, ADR-186) is normal. Before, it
    // was thrown as `revoke_unconfirmed` here, which a poll treated as terminal (the user's
    // wish to stay connected was ended) and a restore attempt as a failure. Now the first row
    // still closing stays held: connect waits for it ([_closeOldLease]), a poll retries it.
    String? closing;
    for (final row in rows) {
      if (row['state'] != 'closed') {
        _id = _uuid(row['id']);
        remotePending = true;
        // Native was stopped above; only the server row is left to revoke.
        await _cleanup(stopNative: false);
        if (hasLease) closing ??= _id;
      }
    }
    if (closing != null) {
      _id = closing;
      remotePending = true;
    }
    _synced = true;
    return false;
  }

  /// ADR-145 (2.2): native's state for reconcile, retried; throws `native_state_unknown`.
  Future<NativeSnapshot> _reconcileSnapshot() async {
    for (var attempt = 1; ; attempt++) {
      try {
        return await _bounded(native.snapshot);
      } catch (_) {
        if (attempt >= snapshotAttempts || _dead) {
          diagnosticStage = 'connection_reconcile';
          throw const ClientError('native_state_unknown');
        }
        await Future<void>.delayed(snapshotRetryDelay);
      }
    }
  }

  /// ADR-117: take over a running tunnel only if the server's view of this device's connection
  /// matches what native holds; the same checks as [_validate] for a first grant.
  Future<bool> _adopt(Map<String, dynamic> value, NativeSnapshot live) async {
    final raw = value['window'];
    if (value['state'] != 'active' ||
        raw is! Map<String, dynamic> ||
        value['id'] != live.connectionId ||
        value['device_id'] != _deviceId ||
        _key == null ||
        !_meters.contains(value['meter']) ||
        value['client_config'] is! String ||
        (value['client_config'] as String).length > 16384)
      return false;
    try {
      _uuid(value['id']);
      _uuid(value['node_id']);
      for (final field in [
        'connection_id',
        'window_id',
        'node_id',
        'runtime_id',
      ]) {
        _uuid(raw[field]);
      }
    } on ClientError {
      return false;
    }
    final generation = raw['generation'];
    final budget = raw['budget_bytes'];
    final slot = raw['slot_id'];
    if (raw['contract_version'] != 3 ||
        raw['connection_id'] != value['id'] ||
        raw['public_key'] != _key ||
        raw['node_id'] != value['node_id'] ||
        slot != value['slot_id'] ||
        generation is! int ||
        generation < live.windowGeneration! ||
        budget is! int ||
        budget < 65536 ||
        budget > 67108864 ||
        slot is! int ||
        slot < 1 ||
        slot > maxSlotId)
      return false;
    // ADR-121: a restored tunnel is renewed by the restore engine; end that engine first. A
    // renewal it finished during the hand-over (one generation ahead) is applied below.
    final nativeGeneration = live.windowGeneration!;
    // ADR-144 (R3): end a live restore engine on every adoption. Before, an engine that had
    // already handed renewal to native was never ended, so it kept polling next to the UI and
    // blocked native recovery for good.
    final handover = live.restoreActive && native is RestoreHandover;
    // Dart renews only when native does not; a native renewal applies its own generations.
    // ADR-191: also for a plain running tunnel without native renewal (Android only, see
    // [_reconcile]); a renewal the server granted but native never applied is advanced below.
    final dartRenewal = !live.nativeRenewal;
    if (handover) {
      if (dartRenewal && generation > nativeGeneration + 1) return false;
      // ADR-185: bounded. On a timeout nothing is adopted; the connect path then stops native,
      // the poll stays unsynced and reconciles once native answers.
      if (!await _bounded((native as RestoreHandover).adoptRestore))
        return false;
    }
    final now = _clock.elapsedMilliseconds;
    _intentAt = now;
    var deadline = now + live.remainingMs;
    if (dartRenewal && generation == nativeGeneration + 1) {
      try {
        final end = _bound(raw, requested: false);
        if (end <= deadline) return false;
        await _bounded(
          () => _native.advanceWindow(raw, end - _clock.elapsedMilliseconds),
        );
        deadline = end;
      } catch (_) {
        return false;
      }
    }
    _id = value['id'] as String;
    _grant = raw;
    _config = value['client_config'] as String;
    selectedNode = value['node_id'] as String;
    _deadline = deadline;
    _renewIntent = null;
    _trafficBaseline = null;
    _lastTraffic = null;
    _renewTrafficAnchor = null;
    _inboundTraffic.reset();
    _dataPath.reset();
    _dataStalled = false;
    _hasEstablishedHandshake = false;
    // ADR-145: the 20 s handshake rule is for windows this controller started, not adopted ones.
    _awaitingFirstHandshake = false;
    _nativeHandshakeSeen = live.handshakeSeen;
    _connectedAtMs = now;
    _recovery.reset(); // ADR-139: a running tunnel is a successful window.
    remotePending = false;
    // A recorded failure keeps its stage until the next step sets a new one.
    if (error == null) diagnosticStage = null;
    error = null;
    _nativeRenewing = live.nativeRenewal;
    // ADR-144 (R2): a running tunnel keeps native's state; no forced «reconnecting» on adoption.
    if (!_applyNative(live)) {
      phase = ConnectionPhase.reconnecting;
      detail = 'VPN продолжает работать, проверяется связь';
    }
    // ADR-121: renewal moves from the ended restore engine to native renewal (or to this UI).
    if (dartRenewal) await _startNativeRenewal(raw);
    return true;
  }

  /// ADR-119: settle the previous connection and wait (bounded) until the server closed it.
  /// Returns false if the attempt was cancelled meanwhile.
  Future<bool> _closeOldLease(int version) async {
    diagnosticStage = 'old_lease_cleanup';
    await _cleanup();
    for (var i = 0; hasLease && i < revokeWaitChecks; i++) {
      if (_cancelled(version)) return false;
      await Future<void>.delayed(revokeWaitInterval);
      if (_cancelled(version)) return false;
      // ADR-145 (2.5): native is already stopped; only the server revoke is retried.
      await _cleanup(stopNative: false);
    }
    if (hasLease) throw const ClientError('revoke_unconfirmed');
    return !_cancelled(version);
  }

  @override
  Future<void> connect() async {
    if (busy || _dead) return;
    if (_polling) {
      // ADR-154: a tap while a poll runs (on return to the app it reconciles with native and
      // the server, up to one API timeout) waits for that poll instead of being dropped
      // silently. Busy meanwhile, so no new poll starts and the screen shows the attempt.
      _working = true;
      _emit();
      try {
        await _refreshWork;
      } finally {
        _working = false;
      }
      // The poll may have adopted native's tunnel or left a revoke pending: never replace it.
      if (busy || _polling || _dead || hasLease || remotePending) {
        _emit();
        return;
      }
    }
    if (_nativeStuck) {
      // ADR-185: an earlier native call has not answered (or a timed-out start is not yet
      // confirmed stopped). Nothing new is started until native answers and a fresh snapshot
      // reconciles; no API call, no quota. The stage of the call that timed out is kept.
      error = 'native_timeout';
      if (!hasLease) phase = ConnectionPhase.failed;
      _emit();
      return;
    }
    _working = true;
    final version = ++_version;
    _reconnectAfterCleanup = false;
    if (!_autoReconnecting) {
      _activationRetries = 0;
      _handshakeRetries =
          0; // ADR-145: a user tap gets a fresh handshake retry.
      // ADR-139: a user tap starts a fresh streak of automatic attempts.
      _recovery.reset();
    }
    // A recorded failure keeps its stage until the next step sets a new one.
    if (error == null) diagnosticStage = null;
    error = null;
    phase = ConnectionPhase.connecting;
    _emit();
    final work = _connect(version);
    _connectWork = work;
    try {
      await work;
    } finally {
      _connectWork = null;
      _working = false;
      _emit();
    }
  }

  Future<void> _connect(int version) async {
    try {
      diagnosticStage = 'selected_node';
      if (selectedNode == null) throw const ClientError('window_node_required');
      // ADR-145 (2.1): consent first (VPN, notifications on Android 13+, the one-time battery
      // explanation), before any API call, so no device or lease work starts for a refusal.
      diagnosticStage = 'android_vpn_permission';
      if (!await native.permission())
        throw const ClientError('permission_denied');
      if (_cancelled(version)) return;
      final chosen = nodes.where((n) => n.id == selectedNode).firstOrNull;
      if (chosen != null && chosen.isPanel) {
        await _connectPanel(version, chosen);
        return;
      }
      // A fresh OTP session has no device binding yet. Register this phone's
      // public key before asking the API to enumerate connections for it.
      await _ensureDevice();
      if (_cancelled(version)) return;
      final adopted = await _reconcile();
      _resumeWanted = false; // This connect is the resume.
      _nativeExhausted = false; // The user tapped: a fresh streak.
      if (_restorePending) {
        _waitForRestore();
        return;
      }
      if (adopted) {
        // ADR-117: the VPN was still running; keep it, the next refresh confirms it.
        startPolling();
        return;
      }
      if (_cancelled(version)) return;
      if (hasLease && !await _closeOldLease(version)) return;
      diagnosticStage = 'key_challenge';
      final proof = await api.call(
        'POST',
        '/api/v1/tunnels/key-challenge/$_deviceId',
      );
      diagnosticStage = 'key_proof';
      final answer = await _bounded(
        () => native.prove(
          proof['ephemeral_public_key'] as String,
          proof['transcript'] as String,
        ),
      );
      if (_cancelled(version)) return;
      _createIntent = {
        'device_id': _deviceId,
        'node_id': selectedNode,
        'idempotency_key': newId(),
        'proof_id': proof['id'],
        'proof_response': answer,
        'budget_bytes': budgetBytes,
        ..._windowRequest,
      };
      _intentAt = _clock.elapsedMilliseconds;
      diagnosticStage = 'lease_create';
      var value = await _create();
      for (var attempt = 0; value['state'] == 'pending'; attempt++) {
        if (_cancelled(version)) return;
        if (attempt >= 10) throw const ClientError('window_node_unavailable');
        await Future<void>.delayed(pollInterval);
        if (_cancelled(version)) return;
        diagnosticStage = 'lease_poll';
        value = await api.call('GET', '$root/$_id');
      }
      if (_cancelled(version)) return;
      diagnosticStage = 'lease_validation';
      final grant = _validate(value, initial: true);
      final deadline = _bound(grant);
      _config = value['client_config'] as String;
      diagnosticStage = 'native_tunnel_start';
      // ADR-185: bounded. On a timeout the catch below stops native at once (fail closed) and
      // the late answer is followed by one more stop; it is never taken as a running window.
      await _bounded(
        () => _native.connectWindow(
          _config!,
          grant,
          deadline - _clock.elapsedMilliseconds,
        ),
        kind: _NativeCall.start,
      );
      if (_cancelled(version)) {
        await _bounded(native.disconnect, kind: _NativeCall.stop);
        return;
      }
      _grant = grant;
      _deadline = deadline;
      _resetGrace(); // ADR-162: a fresh lease from a reachable API.
      // A newly connected backend starts a new cumulative statistics lifetime.
      _trafficBaseline = 0;
      _lastTraffic = 0;
      _renewTrafficAnchor = null;
      _inboundTraffic.reset();
      _dataPath.reset();
      _dataStalled = false;
      _hasEstablishedHandshake = false;
      _nativeHandshakeSeen = false;
      _connectedAtMs = _clock.elapsedMilliseconds;
      _awaitingFirstHandshake = true; // ADR-145 (2.4)
      _activationRetries = 0;
      // ADR-139: a new window is up; the next failure starts a fresh streak.
      _recovery.reset();
      if (error == null) diagnosticStage = null;
      diagnosticHandshake = null;
      diagnosticHandshakeAge = null;
      diagnosticWindow = null;
      diagnosticNetworkChange = null;
      _clearStatus();
      detail = 'Ожидается handshake';
      await _startNativeRenewal(grant);
      if (_cancelled(version)) return;
      startPolling();
    } catch (e) {
      if (!_cancelled(version)) {
        error = e is ClientError
            ? e.code
            : e is PlatformException && _nativeStartErrorCodes.contains(e.code)
            ? e.code
            : 'native_unavailable';
        phase = ConnectionPhase.failed;
        // ADR-145 (2.2): native's state is unknown; never stop a tunnel that may be running.
        await _cleanup(stopNative: error != 'native_state_unknown');
        // ADR-119: the server did not activate a new connection (revoked or not acknowledged
        // in time). Retried a few times in a row, also after a manual connect.
        final activation = error == 'window_activation_refused';
        if (activation) _activationRetries++;
        final bool retry;
        if (activation) {
          retry = _activationRetries <= maxActivationRetries;
        } else if (error == 'window_node_required') {
          // ADR-145 (2.3): the node list or the saved node may come back with the next refresh.
          retry = _nodeMayAppear;
        } else if (error == 'native_state_unknown') {
          retry = true;
        } else {
          // ADR-099: an automatic reconnect that hit a temporary failure keeps trying.
          retry = _autoReconnecting && _retryCodes.contains(error);
        }
        if (retry) {
          _scheduleAutoReconnect(version);
        } else if (_keepIntentCodes.contains(error)) {
          // ADR-145: not a reason to drop the user's wish to be connected.
        } else {
          // ADR-124: a failure nobody retries ends the native "stay connected" wish, so the
          // phone does not try again in the background behind the user's back.
          await _endUserIntent();
        }
      }
    }
  }

  /// ADR-115: a panel server. No lease, quota window or renewal: the panel meters its own
  /// client. The API re-checks the platform and the native side re-checks every field.
  Future<void> _connectPanel(int version, NodeOption node) async {
    final engine = native;
    if (!panelVless || engine is! VlessNative)
      throw const ClientError('panel_platform_unsupported');
    await _ensureDevice();
    if (_cancelled(version)) return;
    await _reconcile();
    if (_cancelled(version)) return;
    if (hasLease && !await _closeOldLease(version)) return;
    // Consent was asked in _connect, before any API call (ADR-145).
    diagnosticStage = 'panel_profile';
    final args = vlessProfileArgs(
      await api.call('GET', '$root/panel-servers/${node.id}/profile'),
      node.id,
    );
    if (args == null) throw const ClientError('invalid_response');
    if (_cancelled(version)) return;
    diagnosticStage = 'native_tunnel_start';
    // VlessNative is a separate interface, so the check above cannot promote `engine`.
    await _bounded(
      () => (engine as VlessNative).connectVless(args, panelSessionMaxMs),
      kind: _NativeCall.start,
    );
    if (_cancelled(version)) {
      await _bounded(native.disconnect, kind: _NativeCall.stop);
      return;
    }
    _panelUp = true;
    _connectedAtMs = _clock.elapsedMilliseconds;
    if (error == null) diagnosticStage = null;
    diagnosticHandshake = null;
    diagnosticHandshakeAge = null;
    diagnosticWindow = null;
    diagnosticNetworkChange = null;
    _clearStatus();
    phase = ConnectionPhase.connected;
    detail = 'Подключено к серверу из панели';
    startPolling();
  }

  Future<Map<String, dynamic>> _create() async {
    try {
      final value = await api.call('POST', root, _createIntent);
      _id = _uuid(value['id']);
      _createIntent = null;
      return value;
    } on ClientError catch (e) {
      if (!{
        'network_unavailable',
        'api_unavailable',
        'invalid_response',
        'unauthorized',
        'session_expired',
      }.contains(e.code))
        _createIntent = null;
      rethrow;
    }
  }

  Map<String, dynamic> _validate(
    Map<String, dynamic> value, {
    bool initial = false,
    bool nativeRenewal = false,
  }) {
    final raw = value['window'];
    if (value['state'] != 'active' ||
        raw is! Map<String, dynamic> ||
        value['id'] != _id ||
        value['device_id'] != _deviceId ||
        !_meters.contains(value['meter']) ||
        value['client_config'] is! String ||
        (value['client_config'] as String).length > 16384)
      // ADR-119: a new connection the server did not activate is not a failed renewal.
      throw ClientError(
        initial ? 'window_activation_refused' : 'window_not_active',
      );
    for (final field in [
      'connection_id',
      'window_id',
      'node_id',
      'runtime_id',
    ]) {
      _uuid(raw[field]);
    }
    final generation = raw['generation'];
    if (raw['contract_version'] != 3 ||
        raw['connection_id'] != _id ||
        raw['public_key'] != _key ||
        raw['node_id'] != selectedNode ||
        raw['slot_id'] != value['slot_id'] ||
        value['node_id'] != selectedNode ||
        generation is! int ||
        raw['budget_bytes'] is! int ||
        (raw['budget_bytes'] as int) < 65536 ||
        (raw['budget_bytes'] as int) > 67108864 ||
        raw['slot_id'] is! int ||
        (raw['slot_id'] as int) < 1 ||
        (raw['slot_id'] as int) > maxSlotId)
      throw const ClientError('invalid_response');
    if (initial) {
      if (generation != 1) throw const ClientError('invalid_response');
    } else {
      final old = _grant!;
      if (raw['runtime_id'] != old['runtime_id'] ||
          raw['slot_id'] != old['slot_id'] ||
          value['client_config'] != _config ||
          (nativeRenewal
              ? generation < (old['generation'] as int)
              : generation != old['generation'] &&
                    generation != old['generation'] + 1))
        throw const ClientError('window_generation_conflict');
      if (generation == old['generation']) {
        if (!mapEquals(raw, old))
          throw const ClientError('window_generation_conflict');
      } else if (!nativeRenewal &&
          raw['window_id'] != _renewIntent?['idempotency_key']) {
        throw const ClientError('window_generation_conflict');
      }
    }
    return raw;
  }

  /// ADR-097: hand renewal to the native VPN process for long windows. Any failure (old API,
  /// platform without a renewer) silently keeps Dart-driven renewal; it never stops the tunnel.
  Future<void> _startNativeRenewal(Map<String, dynamic> grant) async {
    final tunnel = native;
    final host = tunnel is NativeRenewalHost
        ? tunnel as NativeRenewalHost
        : null;
    final client = api;
    final origin = _renewalOrigin ?? (client is Api ? client.origin : null);
    if (windowSeconds == 90 || host == null || origin == null) {
      return;
    }
    _nativeRenewalTriedAt = _clock.elapsedMilliseconds;
    try {
      final issued = await api.call('POST', '$root/$_id/renew-token');
      final token = issued['renew_token'];
      if (token is! String) return;
      // ADR-185: bounded. A late acceptance is the normal hand-off seen late: the poll follows
      // the snapshot's nativeRenewal flag and stops renewing from Dart.
      await _bounded(
        () => host.startNativeRenewal({
          'origin': origin.toString(),
          'token': token,
          'connection': grant['connection_id'],
          'runtime': grant['runtime_id'],
          'windowId': grant['window_id'],
          'generation': grant['generation'],
          'budgetBytes': budgetBytes,
          'windowSeconds': windowSeconds,
        }),
      );
    } catch (_) {
      // ADR-117: any failure keeps Dart renewal, including MissingPluginException from an
      // engine that does not offer native renewal. It must never fail a connect.
      return;
    }
  }

  /// ADR-123: how far the device clock may differ from the server's.
  static const maxClockSkew = Duration(minutes: 5);

  /// [requested]: this client asked for the window after [_intentAt] (initial connect, Dart
  /// renewal), so the monotonic deadline is safe whatever the device clock says. Otherwise
  /// (adopting a window someone else renewed) the wall clock also bounds it.
  int _bound(Map<String, dynamic> grant, {bool requested = true}) {
    final issued = DateTime.parse(grant['issued_at'] as String);
    final expiry = DateTime.parse(grant['not_after'] as String);
    final duration = expiry.difference(issued).inMilliseconds;
    if (!issued.isUtc ||
        !expiry.isUtc ||
        duration <= 6000 ||
        duration > maxWindowMs)
      throw const ClientError('invalid_response');
    final wallNow = DateTime.now().toUtc();
    final elapsed = _clock.elapsedMilliseconds;
    // ADR-123: a device clock that is minutes off is the user's setting to fix, not bad data.
    final sinceIntent = Duration(milliseconds: max(0, elapsed - _intentAt));
    if (issued.isAfter(wallNow.add(maxClockSkew)) ||
        (requested &&
            issued.isBefore(wallNow.subtract(sinceIntent + maxClockSkew))))
      throw const ClientError('device_clock_skew');
    final deadline = requested
        ? _intentAt + duration - 6000
        : min(
            _intentAt + duration - 6000,
            elapsed + expiry.difference(wallNow).inMilliseconds - 6000,
          );
    if (deadline <= _clock.elapsedMilliseconds)
      throw const ClientError('lease_expired');
    return deadline;
  }

  /// Last error code of a failed server revoke (DELETE/create settlement), for the diagnostic
  /// report. Cleared once the lease is gone. Never holds identifiers, only a fixed code.
  String? revokeErrorCode;

  void _recordRevokeFailure(String code) {
    revokeErrorCode = code;
    diagnosticStage = 'revoke_delete';
  }

  /// ADR-162: restart a dropped tunnel on the lease it already holds, only while the API is
  /// known to be unreachable (a transient failure since the last good answer) and the lease and
  /// grace leave at least [minOfflineResumeMs]. The native deadline is the end of the grace, which
  /// is never later than the lease. At most [maxOfflineResumes] per streak. No API call, no new
  /// key, no new lease. False when it does not apply or the start failed (the normal path runs).
  Future<bool> _offlineResume(int version) async {
    final grant = _grant;
    final config = _config;
    final apiUnreachable = lastApiError != null || _grace.active;
    if (!apiUnreachable ||
        grant == null ||
        config == null ||
        _id == null ||
        remotePending ||
        _offlineResumes >= maxOfflineResumes) {
      return false;
    }
    final now = _clock.elapsedMilliseconds;
    final end = _grace.endMs(_deadline) ?? _deadline;
    if (end - now < minOfflineResumeMs) return false;
    _offlineResumes++;
    try {
      diagnosticStage = 'native_tunnel_start';
      await _bounded(
        () => _native.connectWindow(config, grant, end - now),
        kind: _NativeCall.start,
      );
    } on ClientError catch (e) {
      // ADR-185: a start that did not answer may still come up; stop it now (fail closed). The
      // poll's normal failure path then stops native again and revokes as usual.
      if (e.code == 'native_timeout') {
        try {
          await _bounded(native.disconnect, kind: _NativeCall.stop);
        } catch (_) {
          /* The late-start stop and the next poll retry it. */
        }
      }
      return false;
    } catch (_) {
      return false;
    }
    if (_cancelled(version)) {
      // As in _connect: a disconnect arrived meanwhile; never leave this start running. That
      // disconnect's own cleanup stops native again, so a failure here must not escape the poll.
      try {
        await _bounded(native.disconnect, kind: _NativeCall.stop);
      } catch (_) {
        /* The native deadline and the disconnect's cleanup remain the boundary. */
      }
      return true;
    }
    _trafficBaseline = 0;
    _lastTraffic = 0;
    _renewTrafficAnchor = null;
    _inboundTraffic.reset();
    _dataPath.reset();
    _dataStalled = false;
    _hasEstablishedHandshake = false;
    _connectedAtMs = _clock.elapsedMilliseconds;
    _awaitingFirstHandshake = true;
    error = null;
    phase = ConnectionPhase.reconnecting;
    detail = 'Нет связи с сервером. VPN переподключён по текущему доступу';
    return true;
  }

  /// [stopNative]: false where native is already stopped and only the server revoke is retried
  /// (ADR-145 2.5: a pending revoke no longer stops native on every poll), or where native's
  /// state is unknown (ADR-145 2.2).
  Future<void> _cleanup({bool stopNative = true}) async {
    if (stopNative) {
      try {
        await _bounded(native.disconnect, kind: _NativeCall.stop);
      } catch (_) {
        // ADR-164: as in ConnectionController._performCleanup. A failed stop keeps the lease
        // (nothing is revoked under a tunnel that may still run; the native deadline and Lockdown
        // stay the fail-closed boundary) and never escapes as an unhandled error from a poll
        // timer or a connect tap. The next poll stops it again.
        error = 'native_disconnect_failed';
        diagnosticStage = 'native_disconnect';
        phase = ConnectionPhase.failed;
        return;
      }
    }
    _awaitingFirstHandshake = false;
    _panelUp = false;
    _resetGrace();
    _grant = null;
    _config = null;
    _renewIntent = null;
    _trafficBaseline = null;
    _lastTraffic = null;
    _renewTrafficAnchor = null;
    _inboundTraffic.reset();
    _dataPath.reset();
    _dataStalled = false;
    _hasEstablishedHandshake = false;
    try {
      if (_createIntent != null) await _create();
      if (_id != null) {
        try {
          final value = await api.call('DELETE', '$root/$_id');
          if (value['state'] == 'closed') _id = null;
        } on ClientError catch (e) {
          // The server no longer knows this connection (already closed or revoked): nothing
          // is left to revoke, so drop the stale id instead of blocking a new connection.
          if (e.code == 'not_found') {
            _id = null;
          } else {
            _recordRevokeFailure(e.code);
          }
        }
      }
    } catch (e) {
      /* Retain the original admission intent until closure. */
      _recordRevokeFailure(e is ClientError ? e.code : 'revoke_failed');
    }
    remotePending = hasLease;
    if (remotePending) {
      // The server accepts a close with 202 (state revoke_pending) and the node confirms it on a
      // later poll. That is normal, not an error: only a failed DELETE is shown as a warning.
      if (revokeErrorCode != null) error ??= 'revoke_unconfirmed';
      startPolling();
    } else {
      revokeErrorCode = null;
      if (error == 'revoke_unconfirmed') {
        error = null;
        if (diagnosticStage == 'revoke_delete') diagnosticStage = null;
      }
    }
    unawaited(refreshQuota());
  }

  /// ADR-124: ends the native "stay connected" wish. Best effort; never throws.
  Future<void> _endUserIntent() async {
    final host = native;
    if (host is! UserIntentHost) return;
    try {
      // ADR-185: bounded, so a native worker that never answers cannot hold a disconnect tap.
      await _bounded(
        (host as UserIntentHost).endUserIntent,
        kind: _NativeCall.stop,
      );
    } catch (_) {
      /* The engine stop that follows still happens. */
    }
  }

  /// [user]: an explicit disconnect (button, sign-out). It also ends the native "stay
  /// connected" wish, which cancels any background recovery for good. A server switch is not.
  @override
  Future<void> disconnect({bool user = true}) async {
    if (_closed) return;
    _closed = true;
    ++_version;
    _reconnectAfterCleanup = false;
    _resumeWanted = false;
    _emit();
    try {
      if (user) await _endUserIntent();
      await _bounded(native.disconnect, kind: _NativeCall.stop);
      await _connectWork;
      await _refreshWork;
      await _cleanup();
      phase = ConnectionPhase.disconnected;
      detail = 'VPN на телефоне выключен';
      if (!remotePending) error = null;
    } catch (_) {
      error = 'native_disconnect_failed';
      diagnosticStage = 'native_disconnect';
      phase = ConnectionPhase.failed;
    } finally {
      _closed = false;
      _emit();
    }
  }

  /// ADR-145 (2.5): «Повторить закрытие» while a revoke is pending: one more server revoke now.
  Future<void> retryRevoke() async {
    if (!remotePending || _dead) return;
    await refresh();
  }

  /// ADR-145 (2.5): «Сбросить» while a revoke is pending. Ends the user's wish, makes sure
  /// native is stopped and forgets the local hold, so Connect and server choice work again. The
  /// server row is not forgotten: the next connect reconciles and revokes it first, and refuses
  /// to start a new connection while the server still holds the old one.
  Future<void> forceReset() async {
    if (!remotePending || busy || _dead) return;
    ++_version;
    _reconnectAfterCleanup = false;
    _resumeWanted = false;
    _deferredRecovery = null;
    await _refreshWork;
    pausePolling();
    _pollWanted = false;
    await _endUserIntent();
    try {
      await _bounded(native.disconnect, kind: _NativeCall.stop);
    } catch (_) {
      // The native deadline and Lockdown stay the fail-closed boundary.
    }
    _forgetLocalLease();
    revokeErrorCode = null;
    error = null;
    if (diagnosticStage == 'revoke_delete') diagnosticStage = null;
    phase = ConnectionPhase.disconnected;
    detail = 'VPN на телефоне выключен';
    _emit();
  }

  // ADR-185 (ADR-165 item 1): every native call this poll awaits is bounded by [_bounded], so
  // `_polling` is always released and a connect tap that waits for the poll (ADR-154) ends.
  @override
  Future<void> refresh() async {
    if (_polling || busy || _dead) return;
    _polling = true;
    final work = _refresh(_version);
    _refreshWork = work;
    try {
      await work;
    } finally {
      _refreshWork = null;
      _polling = false;
      _emit();
    }
  }

  bool get _handshakeOverdue =>
      !_hasEstablishedHandshake &&
      _clock.elapsedMilliseconds - _connectedAtMs >=
          handshakeHintAfter.inMilliseconds;

  /// Coarse, fixed-category context for the diagnostic report (no address, key or traffic).
  void _recordContext(NativeSnapshot snapshot) {
    alwaysOnStatus = snapshot.alwaysOn ?? alwaysOnStatus;
    // A tunnel that native stopped names the native step that failed (background failures never
    // reach Dart's own steps). A running tunnel keeps the Dart step.
    if (!snapshot.up && snapshot.failureStage != null) {
      diagnosticStage = snapshot.failureStage;
    }
    // Age unknown although a handshake was seen earlier: record nothing rather than guess.
    diagnosticHandshakeAge = snapshot.handshakeAge >= 0
        ? handshakeAgeBucket(snapshot.handshakeAge)
        : (snapshot.handshakeSeen || _hasEstablishedHandshake ? null : 'never');
    diagnosticWindow = windowBucket(snapshot.remainingMs);
    diagnosticNetworkChange = networkChangeBucket(snapshot.networkChangeAgeMs);
    statusHandshakeAgeSec = snapshot.handshakeAge;
    statusRemainingMs = snapshot.remainingMs;
    statusSampledAt = DateTime.now();
  }

  void _clearStatus() {
    statusHandshakeAgeSec = null;
    statusRemainingMs = null;
    statusSampledAt = null;
  }

  /// Same rule as native renewal (NativeRenewal.renewBeforeMs) and [_renewBefore] above.
  @override
  int? get renewLeadMs => _panelUp ? null : _renewBefore;

  Future<void> _refresh(int version) async {
    try {
      await _ensureDevice();
      if (_cancelled(version)) return;
      // ADR-144: in the background never revoke or reconcile: both can stop the tunnel that
      // native now owns. The foreground does it.
      if (_recoveryGated && (remotePending || !_synced)) return;
      if (remotePending) {
        // ADR-145 (2.5): native was stopped when the revoke started; retry only the revoke.
        await _cleanup(stopNative: false);
        // ADR-119: the stopped connection finished closing; now the reconnect can start.
        if (!hasLease && _reconnectAfterCleanup && !_cancelled(version)) {
          _reconnectAfterCleanup = false;
          _scheduleAutoReconnect(version);
        } else if (!hasLease && _resumeWanted && !_cancelled(version)) {
          // ADR-191: reconcile left an old row closing while the user still wants the VPN.
          _resumeWanted = false;
          _scheduleResume(version);
        }
        return;
      }
      // ADR-185: an earlier native call has not answered. Send nothing more (it would queue
      // behind it); once every late call settled, a timed-out start is stopped first, and then
      // this poll reconciles with a fresh snapshot (the stop left the controller unsynced).
      if (_nativeStuck) {
        if (_lateNative == 0) await _stopLateStart();
        if (_cancelled(version)) return;
        if (_nativeStuck) {
          error = 'native_timeout';
          return;
        }
      }
      // ADR-115: a panel session has no lease to reconcile; only the engine's liveness.
      if (_panelUp) {
        final snapshot = await _bounded(native.snapshot);
        if (_cancelled(version)) return;
        if (!snapshot.up) throw ClientError(_stopCode(snapshot));
        return;
      }
      // Fresh OTP sessions are not bound to a device until this phone registers
      // its local public key. Bind first so the per-device reconciliation query
      // can safely return this phone's prior connections (if any).
      final adopted = await _reconcile();
      if (_cancelled(version)) return;
      if (_restorePending) {
        _waitForRestore();
        return;
      }
      if (remotePending) {
        // ADR-191: an old row is still closing (accepted, not yet confirmed by the node). The
        // next poll retries its revoke and then resumes; never monitor a closing connection.
        _awaitingRestore = false;
        if (_resumeWanted) {
          if (error == null) diagnosticStage = 'session_resume';
          phase = ConnectionPhase.reconnecting;
          detail = 'Восстанавливается подключение';
        } else if (phase != ConnectionPhase.failed) {
          phase = ConnectionPhase.disconnected;
          detail = 'VPN на телефоне выключен';
        }
        return;
      }
      if (_awaitingRestore) {
        // ADR-142: the restore ended. Adopted, resumed below, or nothing is wanted any more:
        // never leave the reconnecting state behind.
        _awaitingRestore = false;
        if (!adopted && !_resumeWanted && !hasLease) {
          phase = ConnectionPhase.disconnected;
          detail = 'VPN на телефоне выключен';
        }
      }
      if (_resumeWanted && !hasLease) {
        _resumeWanted = false;
        _scheduleResume(version);
        return;
      }
      if (_nativeExhausted && !hasLease) {
        _nativeExhausted = false;
        phase = ConnectionPhase.failed;
        // Native gave up its restore budget; name the native step it failed in last.
        diagnosticStage = _nativeFailureStage ?? 'native_restore';
        error = 'recovery_exhausted';
        return;
      }
      if (_id == null) return;
      diagnosticStage = 'tunnel_monitor';
      var snapshot = await _bounded(native.snapshot);
      if (_cancelled(version)) return;
      _recordContext(snapshot);
      _nativeRenewing = snapshot.nativeRenewal;
      if (snapshot.state != null) {
        _nativeState = snapshot.state;
        _nativeReason = snapshot.stateReason;
      }
      // ADR-144: native moved on without this controller (it restores, or runs another
      // connection it restored meanwhile). Never stop that tunnel: forget the local view and
      // let the next reconcile adopt it.
      if (snapshot.state != null &&
          ((!snapshot.up && snapshot.restoreActive) ||
              (snapshot.up &&
                  snapshot.connectionId != null &&
                  snapshot.connectionId != _id))) {
        _forgetLocalLease();
        if (!snapshot.up) {
          _waitForRestore();
        } else {
          _applyNative(snapshot);
        }
        return;
      }
      // Read before any stop check below, so a window that just ended still reports it.
      if (snapshot.handshakeSeen) _nativeHandshakeSeen = true;
      // While native renews, its deadline is authoritative: Dart timers may have slept.
      if (snapshot.nativeRenewal && snapshot.up && snapshot.remainingMs > 0)
        _deadline = _clock.elapsedMilliseconds + snapshot.remainingMs;
      if (_clock.elapsedMilliseconds >= _deadline ||
          !snapshot.up ||
          snapshot.remainingMs <= 0)
        throw ClientError(_stopCode(snapshot));
      // ADR-162: the offline grace is over (never later than the lease): stop as at expiry.
      if (_grace.ended(_clock.elapsedMilliseconds, _deadline))
        throw const ClientError('lease_expired');
      _inboundTraffic.observe(
        snapshot.receivedBytes,
        _clock.elapsedMilliseconds,
      );
      if (snapshot.confirmed) _hasEstablishedHandshake = true;
      // ADR-145 (2.4): a window started here must show a handshake within [handshakeTimeout].
      if (_awaitingFirstHandshake) {
        if (snapshot.handshakeSeen ||
            snapshot.handshakeAge >= 0 ||
            _hasEstablishedHandshake ||
            snapshot.state == 'connected') {
          _handshakeSeenNow();
        } else if (_clock.elapsedMilliseconds - _connectedAtMs >=
            handshakeTimeout.inMilliseconds) {
          throw const ClientError('handshake_not_established');
        }
      }
      // ADR-151: show native's state now, not only after the API round trip below (which can
      // take seconds through a fresh tunnel). The end of this poll renders it again.
      if (snapshot.state != null && error == null) {
        _renderNative(snapshot.state!, snapshot.stateReason ?? '');
        _emit();
      }
      final traffic = snapshot.trafficBytes;
      if (traffic != null && traffic >= 0) {
        if (_trafficBaseline == null ||
            (_lastTraffic != null && traffic < _lastTraffic!)) {
          // Missing/reset stats are advisory only; start a fresh baseline.
          _trafficBaseline = traffic;
          _renewTrafficAnchor = null;
        }
        _lastTraffic = traffic;
      }
      final trafficSuggestsRenewal =
          traffic != null &&
          traffic >= 0 &&
          _trafficBaseline != null &&
          traffic >= _trafficBaseline! &&
          traffic - _trafficBaseline! >= (_grant!['budget_bytes'] as int) ~/ 2;
      if (_renewIntent == null &&
          !snapshot.nativeRenewal &&
          (_deadline - _clock.elapsedMilliseconds <= _renewBefore ||
              trafficSuggestsRenewal)) {
        if (trafficSuggestsRenewal &&
            _deadline - _clock.elapsedMilliseconds > _renewBefore) {
          _trafficRenewalRequested = true;
        }
        _renewIntent = {
          'previous_window_id': _grant!['window_id'],
          'idempotency_key': newId(),
          'budget_bytes': budgetBytes,
          ..._windowRequest,
        };
        _intentAt = _clock.elapsedMilliseconds;
        // Capture before issuing the request: bytes sent during rotation are
        // counted conservatively in the next window's advisory estimate.
        _renewTrafficAnchor = traffic != null && traffic >= 0 ? traffic : null;
      }
      diagnosticStage = _renewIntent != null
          ? 'window_renewal'
          : 'tunnel_monitor';
      var value = await api.call('GET', '$root/$_id');
      if (_cancelled(version)) return;
      if (_renewIntent != null) {
        // Same immutable request on response loss. Only GET active ACK extends native.
        await api.call('POST', '$root/$_id/renew', _renewIntent);
        if (_cancelled(version)) return;
        value = await api.call('GET', '$root/$_id');
      }
      if (_cancelled(version)) return;
      if (_clock.elapsedMilliseconds >= _deadline)
        throw const ClientError('lease_expired');
      if (value['state'] == 'pending' &&
          (_renewIntent != null || snapshot.nativeRenewal)) {
        if (_applyNative(snapshot))
          return; // ADR-144: native's state, as below.
        final confirmed = _establishedStatusConfirmed(snapshot);
        phase = confirmed
            ? ConnectionPhase.connected
            : ConnectionPhase.reconnecting;
        detail = confirmed
            ? (snapshot.confirmed
                  ? 'Свежий handshake подтверждён'
                  : 'Входящий трафик через VPN подтверждён')
            : 'Ожидается подтверждение продления';
        return;
      }
      final grant = _validate(value, nativeRenewal: snapshot.nativeRenewal);
      if (grant['generation'] != _grant!['generation'] &&
          snapshot.nativeRenewal) {
        // Native already advanced its own deadline (possibly several generations while the app
        // slept); adopt the grant and keep mirroring the native deadline.
        _grant = grant;
        _trafficBaseline = traffic;
        _renewTrafficAnchor = null;
        _renewIntent = null;
      } else if (grant['generation'] != _grant!['generation']) {
        final end = _bound(grant);
        diagnosticStage = 'window_advance';
        if (end <= _deadline)
          throw const ClientError('window_generation_conflict');
        // ADR-185: bounded. A late advance is harmless: the next poll sends the same generation
        // again, which native accepts as a no-op (WindowDeadline.advance).
        await _bounded(
          () => _native.advanceWindow(grant, end - _clock.elapsedMilliseconds),
        );
        if (_cancelled(version)) return;
        _grant = grant;
        _deadline = end;
        _trafficBaseline =
            _renewTrafficAnchor != null &&
                traffic != null &&
                traffic >= _renewTrafficAnchor!
            ? _renewTrafficAnchor
            : traffic;
        _renewTrafficAnchor = null;
        _renewIntent = null;
      }
      diagnosticStage = 'tunnel_monitor';
      snapshot = await _bounded(native.snapshot);
      if (_cancelled(version)) return;
      _recordContext(snapshot);
      if (!snapshot.up ||
          snapshot.remainingMs <= 0 ||
          _clock.elapsedMilliseconds >= _deadline)
        throw ClientError(_stopCode(snapshot));
      error = null;
      lastApiError = null;
      _resetGrace(); // ADR-162: the API answered and the window is current again.
      // ADR-191: a held window without native renewal expires in Doze (Dart timers sleep), so
      // the hand-off is retried here, bounded by [nativeRenewalRetry]. Before, one failed
      // renew-token call at connect left the window to Dart for its whole life.
      final triedAt = _nativeRenewalTriedAt;
      if (nativeOwnsBackgroundRecovery &&
          !snapshot.nativeRenewal &&
          _renewIntent == null &&
          _grant != null &&
          (triedAt == null ||
              _clock.elapsedMilliseconds - triedAt >=
                  nativeRenewalRetry.inMilliseconds)) {
        await _startNativeRenewal(_grant!);
        if (_cancelled(version)) return;
      }
      // ADR-144: on Android native's state is the status (180 s handshake rule, inbound data,
      // network change, stall); the Dart rule below remains for Windows and older builds.
      if (_applyNative(snapshot)) return;
      final confirmed = _establishedStatusConfirmed(snapshot);
      phase = confirmed
          ? ConnectionPhase.connected
          : ConnectionPhase.reconnecting;
      detail = _dataStalled
          ? 'Нет входящего трафика через VPN, соединение может быть нарушено'
          : snapshot.confirmed
          ? 'Свежий handshake подтверждён'
          : confirmed
          ? 'Входящий трафик через VPN подтверждён'
          : snapshot.recentNetworkChange && _hasEstablishedHandshake
          ? 'Сеть сменилась, восстанавливается связь'
          : _handshakeOverdue
          ? 'Handshake не получен более ${handshakeHintAfter.inSeconds} с. '
                'Нажмите «Отключить VPN» и подключитесь заново.'
          : 'Ожидается сеть и handshake';
    } catch (e) {
      if (_cancelled(version)) return;
      // API shape failures are raised as ClientError('invalid_response'). Any
      // other exception here can come from the native bridge (for example,
      // reading the app-private device key) and must not be mislabeled as an
      // API response failure.
      final code = e is ClientError ? e.code : 'native_unavailable';
      // ADR-162: a renewal that could not reach the API starts the offline grace (first failure
      // of the streak only). The lease itself is never extended.
      if (_transientApiCodes.contains(code) &&
          _renewIntent != null &&
          _grant != null) {
        _grace.renewalFailed(_clock.elapsedMilliseconds);
      }
      // ADR-144: in the background never stop, revoke or reconnect from here; native owns the
      // tunnel. The foreground re-syncs with native first (appForegroundChanged).
      if (_recoveryGated) {
        _backgroundFault = code;
        return;
      }
      // ADR-145 (2.2): native's state is unknown. Stop nothing, revoke nothing; the next poll
      // reconciles again (still unsynced).
      // ADR-185: the same for a native call that did not answer in time: the tunnel this
      // controller holds may still run, so nothing is stopped or revoked from here.
      if (code == 'native_state_unknown' || code == 'native_timeout') {
        error = code;
        return;
      }
      final now = _clock.elapsedMilliseconds;
      // ADR-162: the end of the offline grace counts as the end of the lease.
      final deadlinePassed =
          _grant != null && (now >= _deadline || _grace.ended(now, _deadline));
      // ADR-144: an unreachable API does not mean a broken tunnel. While native still runs it,
      // native's state is the status and the API error is a note, not «Восстановление связи».
      if (_transientApiCodes.contains(code) &&
          !deadlinePassed &&
          _nativeState != null &&
          _id != null) {
        lastApiError = code;
        error = null;
        _renderNative(_nativeState!, _nativeReason ?? '');
        if (phase == ConnectionPhase.connected) {
          detail = offlineGrace
              ? offlineGraceText(_grace.remainingMs(now, _deadline)!)
              : 'VPN работает, сервер временно недоступен';
        }
        return;
      }
      // ADR-162: the tunnel dropped while the API is unreachable and the lease is still valid:
      // start it again on the same lease instead of a revoke and a new lease that cannot work.
      if (code == 'native_down' &&
          !deadlinePassed &&
          await _offlineResume(version)) {
        return;
      }
      error = code;
      if (diagnosticNetworkChange == 'recent' &&
          diagnosticStage == 'tunnel_monitor') {
        diagnosticStage = 'network_change';
      }
      // Task 7: recorded before cleanup resets it. The native memory also counts a handshake
      // that happened while the app was asleep (Android VPN process, Windows controller).
      diagnosticHandshake = _hasEstablishedHandshake || _nativeHandshakeSeen
          ? 'confirmed'
          : 'not_seen';
      phase = ConnectionPhase.reconnecting;
      if (offlineGrace && _transientApiCodes.contains(code)) {
        // ADR-162: visible degraded state (Windows and builds without native state).
        detail = offlineGraceText(_grace.remainingMs(now, _deadline)!);
      }
      if (!_transientApiCodes.contains(code) || deadlinePassed) {
        phase = ConnectionPhase.failed;
        await _cleanup();
        if (code == 'handshake_not_established') {
          // ADR-145 (2.4): one more try on a fresh lease, then a clear terminal error.
          if (_handshakeRetries < maxHandshakeRetries) {
            _handshakeRetries++;
            if (!hasLease) {
              _scheduleAutoReconnect(version);
            } else {
              _reconnectAfterCleanup = true;
            }
          } else {
            await _endUserIntent();
          }
        } else if (_autoReconnectCodes.contains(code)) {
          if (!hasLease) {
            _scheduleAutoReconnect(version);
          } else {
            _reconnectAfterCleanup = true;
          }
        } else {
          await _endUserIntent(); // ADR-124: terminal for this connection.
        }
      }
    }
  }

  /// Window ran out, the server no longer treats it as active (a late renewal), or the phone
  /// dropped the tunnel while time was left (failed in-place restart, killed service):
  /// reconnecting is the only recovery. Lost VPN consent ('vpn_revoked') is never undone.
  static const _autoReconnectCodes = {
    'lease_expired',
    'window_not_active',
    'native_down',
    // ADR-191: this lease cannot be renewed (for example the node agent restarted with a new
    // runtime) or its generation no longer matches. A new lease can work; before, both ended
    // the user's wish to stay connected.
    'window_not_renewable',
    'window_generation_conflict',
  };

  /// Temporary failures of an automatic reconnect attempt itself; it is retried with backoff.
  static const _retryCodes = {
    ..._autoReconnectCodes,
    'network_unavailable',
    'api_unavailable',
    'rate_limited',
    'window_node_unavailable',
    'window_activation_refused',
    // A create with an unknown outcome must be settled first; the attempt fails fast without
    // an API call until the background settlement finishes, then the next attempt connects.
    'revoke_unconfirmed',
    // ADR-145: retried only where _connect/_refresh decided so (one handshake retry, a node
    // that may come back, a native state that could not be read).
    'handshake_not_established',
    'window_node_required',
    'native_state_unknown',
  };
  bool _autoReconnecting = false;

  /// Why the tunnel is gone. Only a window that really ran out is 'lease_expired'; if the phone
  /// stopped the VPN while time was left (another VPN, system toggle, service restart) say so.
  String _stopCode(NativeSnapshot snapshot) =>
      !snapshot.up && snapshot.reason == 'vpn_revoked'
      ? 'vpn_revoked'
      : !snapshot.up &&
            snapshot.reason != 'lease_expired' &&
            _clock.elapsedMilliseconds < _deadline - 1000
      ? 'native_down'
      : 'lease_expired';

  /// The tunnel ended without the user pressing Disconnect: connect again on our own, with
  /// backoff (ADR-099). ADR-140: no attempt budget; after the quick steps it retries every
  /// 60 s while the cause is transient. A failed attempt reserves no quota; a user disconnect
  /// bumps the version and cancels every pending attempt.
  void _scheduleAutoReconnect(int version) {
    final base = autoReconnectDelay;
    if (base == null) return;
    final step = _recovery.take();
    final delay = Duration(microseconds: (base.inMicroseconds * step).round());
    Future<void> fire() async {
      if (_dead ||
          _version != version ||
          phase != ConnectionPhase.failed ||
          !_retryCodes.contains(error)) {
        return;
      }
      // ADR-142: in the background native owns recovery; try again only once in the foreground.
      if (_recoveryGated) {
        _deferredRecovery = fire;
        return;
      }
      // ADR-119: a refresh in flight would make connect() return without trying; wait for it.
      if (_working || _polling) {
        unawaited(Future<void>.delayed(base, fire));
        return;
      }
      if (error == 'window_node_required') {
        // ADR-145 (2.3): retry after the next node refresh, and only once a node is selected.
        await refreshNodes();
        if (_dead ||
            _version != version ||
            phase != ConnectionPhase.failed ||
            error != 'window_node_required') {
          return;
        }
        if (selectedNode == null) {
          if (_nodeMayAppear) _scheduleAutoReconnect(version);
          return;
        }
      }
      _autoReconnecting = true;
      try {
        await connect();
      } finally {
        _autoReconnecting = false;
      }
    }

    unawaited(Future<void>.delayed(delay, fire));
  }

  /// ADR-142: native's restore engine is connecting. Show it as reconnecting and let the poll
  /// reconcile again; the running tunnel is then adopted, a failed restore resumes normally.
  void _waitForRestore() {
    _awaitingRestore = true;
    error = null;
    diagnosticStage = 'session_resume';
    phase = ConnectionPhase.reconnecting;
    detail = 'Восстанавливается подключение';
    startPolling();
    _emit();
  }

  /// ADR-124: the app was reopened (or its UI engine recreated) while the user still wanted
  /// the VPN and nothing adoptable was running: connect again without a tap.
  void _scheduleResume(int version) {
    final base = autoReconnectDelay;
    if (base == null) return;
    error = null;
    diagnosticStage = 'session_resume';
    phase = ConnectionPhase.reconnecting;
    detail = 'Восстанавливается подключение';
    _emit();
    Future<void> fire() async {
      if (_dead || _version != version || hasLease) return;
      if (_recoveryGated) {
        _deferredRecovery =
            fire; // ADR-142: native owns recovery in the background.
        return;
      }
      if (_working || _polling) {
        unawaited(Future<void>.delayed(base, fire));
        return;
      }
      _autoReconnecting = true;
      try {
        await connect();
      } finally {
        _autoReconnecting = false;
      }
    }

    unawaited(Future<void>.delayed(base, fire));
  }

  /// ADR-119: choose another server. While connected or connecting: stop, settle the old
  /// connection (a pending create keeps its idempotency key), then connect to the new server.
  /// A switch while another one runs is ignored.
  Future<void> switchNode(String id) async {
    if (_dead || _switching) return;
    if (!nodes.any((n) => n.id == id && n.reason == 'available')) return;
    final live =
        hasLease ||
        busy ||
        phase == ConnectionPhase.connecting ||
        phase == ConnectionPhase.connected ||
        phase == ConnectionPhase.reconnecting;
    if (!live) {
      selectNode(id);
      return;
    }
    if (id == selectedNode && hasLease && phase != ConnectionPhase.failed)
      return;
    _switching = true;
    try {
      await disconnect(user: false);
      if (_dead) return;
      await _refreshWork;
      if (_dead) return;
      // The old connection may still be closing; connect() waits for that (bounded).
      selectedNode = id;
      _emit();
      await connect();
    } finally {
      _switching = false;
    }
  }

  /// ADR-187: restart the running tunnel on the same server so saved split rules take effect.
  /// Keeps the user's "stay connected" wish, unlike a manual disconnect.
  Future<void> restartTunnel() async {
    if (_dead || _switching || !hasLease) return;
    _switching = true;
    try {
      await disconnect(user: false);
      if (_dead) return;
      await _refreshWork;
      if (_dead) return;
      await connect();
    } finally {
      _switching = false;
    }
  }

  bool _establishedStatusConfirmed(NativeSnapshot snapshot) {
    if (snapshot.confirmed) _hasEstablishedHandshake = true;
    // ADR-133: a fresh handshake does not prove the return path while data goes out unanswered.
    _dataStalled =
        _dataPath.observe(
          snapshot.receivedBytes,
          snapshot.trafficBytes,
          _clock.elapsedMilliseconds,
        ) ==
        DataPathState.stalled;
    if (_dataStalled) return false;
    return snapshot.confirmed ||
        (_hasEstablishedHandshake &&
            snapshot.up &&
            snapshot.network &&
            snapshot.remainingMs > 0 &&
            _inboundTraffic.observe(
              snapshot.receivedBytes,
              _clock.elapsedMilliseconds,
            ) &&
            // ADR-122: inbound bytes from just before a network change prove nothing.
            !snapshot.recentNetworkChange);
  }

  @override
  void dispose() {
    _dead = true;
    ++_version;
    pausePolling();
    unawaited(_stateEvents?.cancel());
    _stateEvents = null;
    super.dispose();
  }
}

/// ADR-185: what a bounded native call does, which sets its timeout and whether it may be sent
/// while an earlier call is still unanswered.
enum _NativeCall { read, start, stop }

const _nativeStartErrorCodes = <String>{
  'native_config_invalid',
  'native_engine_unavailable',
  'native_io_error',
  'native_permission_error',
  'native_service_state_invalid',
  'native_start_failed',
  'kill_switch_failed', // ADR-196 (Windows)
};
