import 'dart:async';

import 'package:flutter/material.dart';

import 'connection.dart';
import 'country_flag.dart';
import 'native_tunnel.dart';
import 'orbit_widgets.dart';
import 'ui/components.dart';
import 'ui/motion.dart';
import 'ui/tokens.dart';
import 'window_connection.dart';

/// "Финляндия · Хельсинки" when the country is known, otherwise the operator
/// name and region. Display only; never used for routing. Inside a country
/// group ([withCountry] false) only the city is shown.
String nodeTitle(NodeOption node, {bool withCountry = true}) {
  final place = node.city ?? node.region;
  final country = countryNames[node.countryCode];
  if (country == null) return '${node.name} · $place';
  return withCountry ? '$country · $place' : place;
}

/// Titles for a whole catalog; equal titles get the operator name appended.
Map<String, String> nodeTitles(
  List<NodeOption> nodes, {
  bool withCountry = true,
}) {
  final base = {
    for (final n in nodes) n.id: nodeTitle(n, withCountry: withCountry),
  };
  final counts = <String, int>{};
  for (final title in base.values) {
    counts[title] = (counts[title] ?? 0) + 1;
  }
  return {
    for (final n in nodes)
      n.id: counts[base[n.id]]! > 1 ? '${base[n.id]} · ${n.name}' : base[n.id]!,
  };
}

/// Manual server choice (ADR-092: no automatic selection or failover).
class RegionPickerScreen extends StatefulWidget {
  const RegionPickerScreen({super.key, required this.controller});
  final ConnectionController controller;

  static Future<void> open(
    BuildContext context,
    ConnectionController controller,
  ) {
    unawaited(controller.refreshNodes());
    return Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => RegionPickerScreen(controller: controller),
      ),
    );
  }

  @override
  State<RegionPickerScreen> createState() => _RegionPickerScreenState();
}

class _RegionPickerScreenState extends State<RegionPickerScreen> {
  void _select(String? value) {
    final c = widget.controller;
    c.selectNode(value);
    if (c is WindowConnectionController &&
        c.selectedNode == value &&
        c.native is NodePreferenceStore) {
      unawaited(_save(c.native as NodePreferenceStore, value));
    }
    Navigator.pop(context);
  }

  Future<void> _save(NodePreferenceStore store, String? selected) async {
    final messenger = ScaffoldMessenger.maybeOf(context);
    try {
      await store.writeSelectedNode(selected);
    } catch (_) {
      messenger?.showSnackBar(
        const SnackBar(
          content: Text('Выбор узла не сохранён на этом устройстве'),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Выберите сервер')),
    body: OrbitBackdrop(
      child: SafeArea(
        top: false,
        child: ListenableBuilder(
          listenable: widget.controller,
          builder: (context, _) => _body(context),
        ),
      ),
    ),
  );

  Widget _body(BuildContext context) {
    final c = widget.controller;
    final t = OrbitTokens.of(context);
    final locked = c.busy || c.hasLease;
    final byCountry = <String?, List<NodeOption>>{};
    for (final node in c.nodes) {
      byCountry.putIfAbsent(node.countryCode, () => []).add(node);
    }
    final grouped = byCountry.values.any((list) => list.length > 1);
    final titles = nodeTitles(c.nodes, withCountry: !grouped);
    final fullTitles = grouped ? nodeTitles(c.nodes) : titles;

    var order = 0;
    Widget card(NodeOption node, {bool compact = false}) {
      final state = serverState(node.reason);
      final selected = c.selectedNode == node.id;
      final enabled =
          !locked && c.nodesError == null && node.reason == 'available';
      return OrbitAppear(
        index: order++,
        child: Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Semantics(
            button: true,
            selected: selected,
            enabled: enabled,
            label: '${fullTitles[node.id]}, ${nodeMessage(node.reason)}',
            excludeSemantics: true,
            child: Opacity(
              opacity: enabled || selected ? 1 : .55,
              child: OrbitPanel(
                selected: selected,
                padding: EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: compact ? 12 : 16,
                ),
                onTap: enabled ? () => _select(node.id) : null,
                child: Row(
                  children: [
                    CountryFlag(node.countryCode, width: 36),
                    const SizedBox(width: 16),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            titles[node.id]!,
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          const SizedBox(height: 4),
                          StateLabel(label: state.label, color: state.color(t)),
                        ],
                      ),
                    ),
                    if (selected)
                      Container(
                        width: 26,
                        height: 26,
                        decoration: BoxDecoration(
                          color: t.accent,
                          shape: BoxShape.circle,
                        ),
                        child: Icon(Icons.check, size: 18, color: t.onAccent),
                      )
                    else if (enabled)
                      Icon(Icons.chevron_right_rounded, color: t.textMuted),
                  ],
                ),
              ),
            ),
          ),
        ),
      );
    }

    final empty = !c.nodesLoading && c.nodes.isEmpty && c.nodesError == null;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
      children: [
        if (!empty)
          Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: Text(
              'Подключение будет выполнено к выбранному серверу',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        if (c.hasLease)
          const Padding(
            padding: EdgeInsets.only(bottom: 16),
            child: OrbitNotice(
              text: 'Сначала отключите VPN и дождитесь завершения подключения.',
            ),
          ),
        if (c.nodesError != null)
          const Padding(
            padding: EdgeInsets.only(bottom: 16),
            child: OrbitNotice(
              text: 'Каталог не обновлён; прежние статусы устарели.',
            ),
          ),
        if (c is! WindowConnectionController)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: OrbitPanel(
              selected: c.selectedNode == null,
              onTap: locked ? null : () => _select(null),
              child: Row(
                children: [
                  Icon(Icons.public, color: t.accent, size: 28),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Text(
                      'Первый доступный узел',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ),
                ],
              ),
            ),
          ),
        if (grouped)
          for (final entry in byCountry.entries) ...[
            Padding(
              padding: const EdgeInsets.fromLTRB(4, 8, 4, 12),
              child: Text.rich(
                TextSpan(
                  text: countryNames[entry.key] ?? 'Другие',
                  style: Theme.of(context).textTheme.titleMedium,
                  children: [
                    TextSpan(
                      text: ' · ${_servers(entry.value.length)}',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
            ),
            for (final node in entry.value) card(node, compact: true),
          ]
        else
          for (final node in c.nodes) card(node),
        if (c.nodesLoading)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 16),
            child: LinearProgressIndicator(),
          ),
        if (empty) ...[
          const SizedBox(height: 16),
          OrbitBlob(
            height: 200,
            child: Icon(Icons.dns_outlined, size: 48, color: t.accent),
          ),
          const SizedBox(height: 16),
          Text(
            'Серверы пока не настроены.',
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 4),
          Text(
            'Обновите список позже',
            textAlign: TextAlign.center,
            style: Theme.of(
              context,
            ).textTheme.bodySmall?.copyWith(color: t.accent),
          ),
        ],
        const SizedBox(height: 16),
        Text(
          'Доступность по данным агента. Скорость здесь не измеряется.',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.bodySmall,
        ),
        if (c.nodesUpdatedAt != null)
          Text(
            'Проверка: ${c.nodesUpdatedAt!.toLocal().toString().substring(0, 19)}',
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.bodySmall,
          ),
        const SizedBox(height: 16),
        FilledButton.icon(
          onPressed: c.nodesLoading ? null : c.refreshNodes,
          icon: const Icon(Icons.refresh_rounded),
          label: const Text('Обновить список'),
        ),
      ],
    );
  }

  static String _servers(int n) {
    final mod10 = n % 10, mod100 = n % 100;
    if (mod10 == 1 && mod100 != 11) return '$n сервер';
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
      return '$n сервера';
    }
    return '$n серверов';
  }
}
