import 'package:flutter/material.dart';

import 'quota.dart';
import 'orbit_theme.dart';

class QuotaScreen extends StatelessWidget {
  const QuotaScreen({super.key, required this.controller});
  final QuotaController controller;

  @override
  Widget build(BuildContext context) => Theme(
    data: orbitTheme(Theme.of(context).brightness),
    child: Builder(builder: _buildContent),
  );

  Widget _buildContent(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Трафик и история')),
    body: SafeArea(
      child: ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final c = controller;
          final q = c.snapshot;
          return ListView(
            padding: const EdgeInsets.all(24),
            children: [
              const OrbitRoute(),
              if (c.loading) const LinearProgressIndicator(),
              if (q == null) const Text('Данные о трафике пока недоступны'),
              if (q != null) ...[
                Text(
                  'Доступно: ${byteText(q.available)}',
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                const SizedBox(height: 12),
                Text('Всего начислено: ${byteText(q.granted)}'),
                Text('Списано: ${byteText(q.used)}'),
                Text('Зарезервировано: ${byteText(q.reserved)}'),
                const Text(
                  'Резерв временно недоступен для нового подключения. После расчёта подключения неиспользованный резерв освобождается. Расход появляется после серверного учёта, это не счётчик в реальном времени.',
                ),
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
              const Text(
                'Показаны учтённые операции от новых к старым. Резерв не является списанием. Баланс после операции указан до вычета текущего резерва.',
              ),
              if (q != null && c.entries.isEmpty)
                const Text('Операций пока нет'),
              for (final entry in c.entries)
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(entry.label),
                        Text(
                          '${entry.kind == 'credit' ? '+' : ''}${byteText(entry.delta)}',
                        ),
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
            ],
          );
        },
      ),
    ),
  );
}
