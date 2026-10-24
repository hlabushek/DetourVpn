import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

class SplitAppsScreen extends StatefulWidget {
  const SplitAppsScreen({super.key});
  @override
  State<SplitAppsScreen> createState() => _SplitAppsState();
}

class _SplitAppsState extends State<SplitAppsScreen> {
  static const channel = MethodChannel('project.vpn/tunnel.v1');
  List<String> apps = [];
  Set<String> excluded = {};
  bool loading = true;
  String? error;
  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final listed = await channel.invokeListMethod<String>('apps') ?? [];
      final selected =
          await channel.invokeListMethod<String>('splitRead') ?? [];
      if (mounted)
        setState(() {
          apps = {...listed, ...selected}.toList()..sort();
          excluded = selected.toSet();
          loading = false;
        });
    } catch (_) {
      if (mounted)
        setState(() {
          loading = false;
          error = 'Не удалось прочитать настройки';
        });
    }
  }

  Future<void> save() async {
    setState(() {
      loading = true;
      error = null;
    });
    try {
      await channel.invokeMethod<void>('splitWrite', {
        'packages': excluded.toList()..sort(),
      });
      if (mounted) Navigator.pop(context);
    } catch (_) {
      if (mounted)
        setState(() {
          loading = false;
          error =
              'Сначала отключите VPN. Проверьте, что выбранные приложения установлены.';
        });
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Приложения вне VPN')),
    body: ListView(
      padding: const EdgeInsets.all(24),
      children: [
        const Text(
          'Отмеченные приложения используют обычную сеть, включая DNS и IPv6. '
          'Они не защищены VPN. Список хранится только на телефоне. Изменения доступны при отключённом VPN. '
          'Служебные запросы DetourVpn идут напрямую для продления доступа.',
        ),
        const SizedBox(height: 16),
        if (loading) const LinearProgressIndicator(),
        if (error != null) Text(error!),
        for (final name in apps)
          CheckboxListTile(
            title: Text(name),
            value: excluded.contains(name),
            onChanged: loading
                ? null
                : (value) => setState(() {
                    if (value == true) {
                      excluded.add(name);
                    } else {
                      excluded.remove(name);
                    }
                  }),
          ),
        FilledButton(
          onPressed: loading ? null : save,
          child: const Text('Сохранить'),
        ),
      ],
    ),
  );
}
