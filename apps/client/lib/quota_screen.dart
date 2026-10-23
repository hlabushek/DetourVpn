import 'package:flutter/material.dart';

import 'quota.dart';
import 'orbit_theme.dart';
import 'orbit_widgets.dart';

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

  Widget _buildContent(BuildContext context) => Scaffold(
    appBar: embedded ? null : AppBar(title: const Text('Трафик и история')),
    body: SafeArea(
      child: ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final c = controller;
          final q = c.snapshot;
          return ListView(
            padding: const EdgeInsets.all(24),
            children: [
              Text(
                'Ваш трафик',
                style: Theme.of(context).textTheme.headlineMedium,
              ),
              const SizedBox(height: 16),
              if (c.loading) const LinearProgressIndicator(),
              if (q == null) const Text('Данные о трафике пока недоступны'),
              if (q != null) ...[
                OrbitPanel(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('Доступно для подключения'),
                      const SizedBox(height: 8),
                      Text(
                        compactBytes(q.available),
                        style: Theme.of(context).textTheme.headlineLarge,
                      ),
                      const SizedBox(height: 12),
                      LinearProgressIndicator(
                        value: q.granted == 0 ? 0 : q.available / q.granted,
                        minHeight: 8,
                        borderRadius: BorderRadius.circular(8),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 20),
                Text(
                  'Доступно: ${byteText(q.available)}',
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
                const SizedBox(height: 12),
                Text('Всего начислено: ${byteText(q.granted)}'),
                Text('Списано: ${byteText(q.used)}'),
                Text('Зарезервировано: ${byteText(q.reserved)}'),
                if (q.unavailable > 0)
                  Text(
                    'Недоступно по сроку или отзыву: ${byteText(q.unavailable)}',
                  ),
                const SizedBox(height: 12),
                Text(
                  '${c.stale ? 'Данные устарели' : 'Снимок данных'}: ${c.updatedAt?.toLocal().toString().substring(0, 19)}',
                  style: c.stale
                      ? TextStyle(
                          color: orbitWarning(Theme.of(context).brightness),
                        )
                      : null,
                ),
              ],
              if (c.error != null)
                Semantics(
                  liveRegion: true,
                  child: Text(
                    {
                          'unauthorized',
                          'session_expired',
                          'device_revoked',
                        }.contains(c.error)
                        ? 'Сессия завершена. Вернитесь и войдите снова.'
                        : 'Не удалось загрузить данные. Проверьте сеть и повторите запрос.',
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                ),
              TextButton(
                onPressed: c.loading ? null : c.refresh,
                child: const Text('Обновить трафик и историю'),
              ),
              const SizedBox(height: 16),
              Text(
                'История операций',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 12),
              if (q != null && c.entries.isEmpty)
                const Text('Операций пока нет'),
              for (final entry in c.entries)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: OrbitPanel(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          entry.label,
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                        const SizedBox(height: 8),
                        Text(
                          '${entry.kind == 'credit' ? '+' : ''}${byteText(entry.delta)}',
                          style: TextStyle(
                            color: entry.kind == 'credit'
                                ? Theme.of(context).colorScheme.primary
                                : null,
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text(
                          'Баланс после операции: ${byteText(entry.balance)}',
                        ),
                        Text(
                          '№ ${entry.sequence} · ${entry.createdAt.toLocal().toString().substring(0, 19)}',
                        ),
                      ],
                    ),
                  ),
                ),
              if (c.nextBefore != null && !c.capped)
                OutlinedButton(
                  onPressed: c.loading || c.stale ? null : c.more,
                  child: const Text('Показать ещё'),
                ),
              if (c.capped)
                const Text(
                  'Показаны последние 500 операций. Обновите данные для нового снимка.',
                ),
              const SizedBox(height: 16),
              const ExpansionTile(
                tilePadding: EdgeInsets.zero,
                title: Text('Как считается трафик'),
                children: [
                  Padding(
                    padding: EdgeInsets.only(bottom: 16),
                    child: Text(
                      'Резерв временно недоступен для нового подключения. После расчёта неиспользованный резерв освобождается. Расход появляется после серверного учёта, это не счётчик в реальном времени.\n\nИстория идёт от новых операций к старым. Резерв не является списанием. Баланс после операции указан до вычета текущего резерва. Единицы КБ, МБ и ГБ — десятичные; точные суммы указаны в байтах.',
                    ),
                  ),
                ],
              ),
            ],
          );
        },
      ),
    ),
  );
}
