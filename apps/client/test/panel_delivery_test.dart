import 'package:flutter_test/flutter_test.dart';
import 'package:vpn_client/api.dart';
import 'package:vpn_client/connection.dart';
import 'package:vpn_client/native_tunnel.dart';
import 'package:vpn_client/panel_servers.dart';
import 'package:vpn_client/window_connection.dart';

import 'window_connection_test.dart' show Native;

// TEST ONLY: ADR-115 panel delivery against a fake API and fake native side. Synthetic values,
// reserved test names; no panel, engine or network is used.
const vps = '11111111-1111-4111-8111-111111111111';
const panel = '44444444-4444-4444-8444-444444444444';
const device = '55555555-5555-4555-8555-555555555555';
const root = WindowConnectionController.root;

Map<String, Object> panelProfile([String id = panel]) => {
  'id': id,
  'protocol': 'vless_reality',
  'server': 'panel.example.test',
  'port': 443,
  'uuid': '00000000-0000-4000-8000-000000000001',
  'flow': 'xtls-rprx-vision',
  'sni': 'www.example.test',
  'public_key': '${'r' * 42}A',
  'short_id': '0a1b',
  'fingerprint': 'chrome',
};

class PanelApi implements TunnelApi {
  PanelApi({this.panelRow, this.profile});
  Map<String, Object?>? panelRow;
  Map<String, Object>? profile;
  final calls = <String>[];
  @override
  Future<Map<String, dynamic>> call(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    calls.add('$method $path');
    if (method == 'GET' && path == '$root/nodes?include=panel') {
      return {
        'nodes': <Map<String, Object?>>[
          {
            'id': vps,
            'name': 'TEST ONLY vps',
            'region': 'test',
            'available': true,
            'source': 'vps',
            'protocol': 'awg',
          },
          ?panelRow,
        ],
        'checked_at': DateTime.now().toUtc().toIso8601String(),
      };
    }
    if (method == 'POST' && path == '/api/v1/devices') return {'id': device};
    if (method == 'GET' && path == root) return {'items': <Object>[]};
    if (method == 'GET' && path == '$root/panel-servers/$panel/profile') {
      return profile ?? panelProfile();
    }
    throw const ClientError('api_unavailable');
  }
}

Map<String, Object?> panelRow({String protocol = 'vless_reality'}) => {
  'id': panel,
  'name': 'Тестовая панель',
  'region': '',
  'available': true,
  'source': 'panel',
  'protocol': protocol,
};

class VlessFake extends Native implements VlessNative {
  final vless = <Map<String, Object>>[];
  int? maxMs;
  @override
  Future<void> connectVless(Map<String, Object> profile, int maxMs) async {
    vless.add(profile);
    this.maxMs = maxMs;
    up = true;
  }
}

WindowConnectionController controller(
  PanelApi api,
  Native native, {
  required bool panelVless,
}) => WindowConnectionController(
  api,
  native,
  pollInterval: const Duration(hours: 1),
  autoReconnectDelay: null,
  panelVless: panelVless,
);

void main() {
  test(
    'Android lists a panel server as unavailable with the Russian reason',
    () async {
      final api = PanelApi(panelRow: panelRow());
      final c = controller(api, VlessFake(), panelVless: false);
      await c.refreshNodes();
      expect(c.nodesError, isNull);
      final row = c.nodes.singleWhere((n) => n.id == panel);
      expect(row.isPanel, isTrue);
      expect(row.reason, panelUnsupportedReason);
      expect(nodeMessage(row.reason), panelServerUnsupportedAndroid);
      c.selectNode(panel);
      expect(c.selectedNode, isNot(panel));
      expect(c.nodes.singleWhere((n) => n.id == vps).reason, 'available');
      c.dispose();
    },
  );

  test(
    'a catalog row with a mismatched source and protocol is refused',
    () async {
      final api = PanelApi(panelRow: panelRow(protocol: 'awg'));
      final c = controller(api, VlessFake(), panelVless: true);
      await c.refreshNodes();
      expect(c.nodesError, 'invalid_response');
      c.dispose();
    },
  );

  test(
    'Windows connects a panel server without a lease and drops it when the engine stops',
    () async {
      final api = PanelApi(panelRow: panelRow());
      final native = VlessFake();
      final c = controller(api, native, panelVless: true);
      await c.refreshNodes();
      expect(c.nodes.singleWhere((n) => n.id == panel).reason, 'available');
      c.selectNode(panel);
      expect(c.selectedNode, panel);
      await c.connect();
      expect(c.error, isNull);
      expect(c.phase, ConnectionPhase.connected);
      expect(c.panelConnected, isTrue);
      expect(c.hasLease, isFalse);
      expect(native.maxMs, panelSessionMaxMs);
      expect(native.vless.single, {
        'server': 'panel.example.test',
        'port': 443,
        'uuid': '00000000-0000-4000-8000-000000000001',
        'flow': 'xtls-rprx-vision',
        'sni': 'www.example.test',
        'publicKey': '${'r' * 42}A',
        'shortId': '0a1b',
        'fingerprint': 'chrome',
      });
      // No window was created: no POST to the window-tunnel root.
      expect(api.calls.where((call) => call == 'POST $root'), isEmpty);

      native.up = false;
      await c.refresh();
      expect(c.phase, ConnectionPhase.failed);
      expect(c.panelConnected, isFalse);
      c.dispose();
    },
  );

  test(
    'a profile for another server or with a missing field is never started',
    () async {
      for (final bad in [
        panelProfile(vps),
        {...panelProfile()}..remove('uuid'),
        {...panelProfile(), 'port': 0},
        {...panelProfile(), 'protocol': 'awg'},
      ]) {
        final api = PanelApi(panelRow: panelRow(), profile: bad);
        final native = VlessFake();
        final c = controller(api, native, panelVless: true);
        await c.refreshNodes();
        c.selectNode(panel);
        await c.connect();
        expect(c.error, 'invalid_response');
        expect(native.vless, isEmpty);
        c.dispose();
      }
    },
  );

  test('vlessProfileArgs maps API names to channel names', () {
    expect(
      vlessProfileArgs(panelProfile(), panel)?['publicKey'],
      '${'r' * 42}A',
    );
    expect(vlessProfileArgs('not a map', panel), isNull);
    expect(vlessProfileArgs(panelProfile(), vps), isNull);
  });
}
