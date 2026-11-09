import 'package:flutter/material.dart';

import 'quota.dart';
import 'orbit_theme.dart';
import 'orbit_widgets.dart';
import 'ui/components.dart';
import 'ui/glass_nav.dart';
import 'ui/motion.dart';

String _when(DateTime value) {
  final v = value.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(v.day)}.${two(v.month)}.${v.year}, ${two(v.hour)}:${two(v.minute)}';
}

/// Traffic tab: the one detailed place for balance and history.
class QuotaScreen extends StatelessWidget {
  const QuotaScreen({
    super.key,
    required this.controller,
    this.embedded = false,
  });
  final QuotaController controller;
  final bool embedded;

  @override
  Widget build(BuildContext context) => Theme(
    data: orbitTheme(Theme.of(context).brightness),
    child: Builder(builder: _buildContent),
  );

  Widget _buildContent(BuildContext context) {
    final body = SafeArea(
      top: !embedded,
      child: ListenableBuilder(
        listenable: controller,
        builder: (context, _) => _list(context),
      ),
    );
    return Scaffold(
      backgroundColor: embedded ? Colors.transparent : null,
      appBar: embedded ? null : AppBar(title: const Text('Трафик и история')),
      body: embedded ? body : OrbitBackdrop(child: body),
    );
  }

  Widget _list(BuildContext context) {
    final c = controller;
    final q = c.snapshot;
    final t = OrbitTokens.of(context);
    final text = Theme.of(context).textTheme;

    Widget legend(Color color, String label, int value) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          StatusDot(color, size: 10),
          const SizedBox(width: 8),
          Expanded(child: Text(label, style: text.bodySmall)),
          Text(
            compactBytes(value),
            style: text.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
          ),
        ],
      ),
    );

    return RefreshIndicator(
      onRefresh: c.refresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: EdgeInsets.fromLTRB(16, 8, 16, 32 + GlassNavInset.of(context)),
        children: staggered([
          ScreenHeader(
            icon: Icons.bar_chart_rounded,
            title: 'Трафик',
            subtitle: 'Расход и пополнение трафика',
            trailing: IconButton(
              tooltip: 'Обновить трафик и историю',
              onPressed: c.loading ? null : c.refresh,
              icon: const Icon(Icons.refresh_rounded),
            ),
          ),
          if (c.loading)
            const Padding(
              padding: EdgeInsets.only(bottom: 16),
              child: LinearProgressIndicator(),
            ),
          if (q == null)
            OrbitPanel(
              child: Text(
                'Данные о трафике пока недоступны',
                style: text.bodyMedium,
              ),
            ),
          if (q != null)
            OrbitPanel(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  LayoutBuilder(
                    builder: (context, box) {
                      final gauge = DonutGauge(
                        size: 128,
                        value: q.granted == 0 ? 0 : q.available / q.granted,
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(
                              compactBytes(q.available),
                              style: text.titleLarge?.copyWith(
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                            Text(
                              'из ${compactBytes(q.granted)}',
                              style: text.bodySmall,
                            ),
                          ],
                        ),
                      );
                      final legends = Column(
                        children: [
                          legend(t.accent, 'Доступно', q.available),
                          legend(t.idle, 'Начислено', q.granted),
                          legend(t.cta, 'Списано', q.used),
                          if (q.reserved > 0)
                            legend(t.warning, 'Зарезервировано', q.reserved),
                          if (q.unavailable > 0)
                            legend(t.idle, 'Недоступно', q.unavailable),
                        ],
                      );
                      final wide =
                          box.maxWidth >= 300 &&
                          MediaQuery.textScalerOf(context).scale(10) <= 13;
                      return wide
                          ? Row(
                              children: [
                                gauge,
                                const SizedBox(width: 20),
                                Expanded(child: legends),
                              ],
                            )
                          : Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Center(child: gauge),
                                const SizedBox(height: 16),
                                legends,
                              ],
                            );
                    },
                  ),
                  const SizedBox(height: 16),
                  Text(
                    'Доступно: ${byteText(q.available)}',
                    style: text.bodySmall,
                  ),
                  if (q.unavailable > 0)
                    Text(
                      'Недоступно по сроку или отзыву: ${byteText(q.unavailable)}',
                      style: text.bodySmall,
                    ),
                  const SizedBox(height: 4),
                  Text(
                    '${c.stale ? 'Данные устарели' : 'Снимок данных'}: ${c.updatedAt?.toLocal().toString().substring(0, 19)}',
                    style: text.bodySmall?.copyWith(
                      color: c.stale
                          ? orbitWarning(Theme.of(context).brightness)
                          : null,
                    ),
                  ),
                ],
              ),
            ),
          if (c.error != null)
            Padding(
              padding: const EdgeInsets.only(top: 16),
              child: OrbitNotice(
                danger: true,
                text:
                    {
                      'unauthorized',
                      'session_expired',
                      'device_revoked',
                    }.contains(c.error)
                    ? 'Сессия завершена. Вернитесь и войдите снова.'
                    : 'Не удалось загрузить данные. Проверьте сеть и повторите запрос.',
              ),
            ),
          const SizedBox(height: 24),
          Text('История операций', style: text.titleMedium),
          const SizedBox(height: 12),
          if (q != null && c.entries.isEmpty)
            Text('Операций пока нет', style: text.bodySmall),
          if (c.entries.isNotEmpty)
            OrbitPanel(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              child: Column(
                children: [
                  for (final (i, entry) in c.entries.indexed) ...[
                    if (i > 0) const Divider(),
                    _EntryRow(entry: entry),
                  ],
                ],
              ),
            ),
          if (c.nextBefore != null && !c.capped) ...[
            const SizedBox(height: 12),
            OutlinedButton(
              onPressed: c.loading || c.stale ? null : c.more,
              child: const Text('Показать ещё'),
            ),
          ],
          if (c.capped)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(
                'Показаны последние 500 операций. Обновите данные для нового снимка.',
                style: text.bodySmall,
              ),
            ),
          const SizedBox(height: 16),
          OrbitPanel(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 0),
            child: ExpansionTile(
              tilePadding: EdgeInsets.zero,
              leading: Icon(Icons.info_outline, color: t.textMuted),
              title: Text('Как считается трафик', style: text.titleMedium),
              children: [
                Padding(
                  padding: const EdgeInsets.only(bottom: 16),
                  child: Text(
                    'Резерв временно недоступен для нового подключения. После расчёта неиспользованный резерв освобождается. Расход появляется после серверного учёта, это не счётчик в реальном времени.\n\nИстория идёт от новых операций к старым. Резерв не является списанием. Баланс после операции указан до вычета текущего резерва. Единицы КБ, МБ и ГБ — десятичные; точные суммы указаны в байтах.',
                    style: text.bodySmall,
                  ),
                ),
              ],
            ),
          ),
        ]),
      ),
    );
  }
}

class _EntryRow extends StatelessWidget {
  const _EntryRow({required this.entry});
  final QuotaEntry entry;
  @override
  Widget build(BuildContext context) {
    final t = OrbitTokens.of(context);
    final text = Theme.of(context).textTheme;
    final credit = entry.kind == 'credit';
    final color = entry.source == 'reward'
        ? t.accent
        : credit
        ? t.success
        : t.danger;
    final icon = entry.source == 'reward'
        ? Icons.card_giftcard_rounded
        : credit
        ? Icons.add_rounded
        : Icons.remove_rounded;
    return Semantics(
      label:
          '${entry.label}, ${credit ? '+' : ''}${byteText(entry.delta)}, '
          'баланс после операции ${byteText(entry.balance)}, № ${entry.sequence}',
      excludeSemantics: true,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Row(
          children: [
            IconTile(icon, color: color),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(entry.label, style: text.titleMedium),
                  const SizedBox(height: 2),
                  Text(_when(entry.createdAt), style: text.bodySmall),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Text(
              '${credit ? '+' : ''}${compactBytes(entry.delta)}',
              style: text.titleMedium?.copyWith(color: color),
            ),
          ],
        ),
      ),
    );
  }
}
