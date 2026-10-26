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
  Set<String> selected = {};
  String mode = 'exclude';
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
      final saved = await channel.invokeListMethod<String>('splitRead') ?? [];
      final savedMode =
          await channel.invokeMethod<String>('splitModeRead') ?? 'exclude';
      if (mounted)
        setState(() {
          apps = {...listed, ...saved}.toList()..sort();
          selected = saved.toSet();
          mode = savedMode;
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
        'packages': selected.toList()..sort(),
        'mode': mode,
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
    appBar: AppBar(title: const Text('Разделение трафика')),
    body: ListView(
      padding: const EdgeInsets.all(24),
      children: [
        IgnorePointer(
          ignoring: loading,
          child: RadioGroup<String>(
            groupValue: mode,
            onChanged: (value) {
              if (!loading && value != null) setState(() => mode = value);
            },
            child: const Column(
              children: [
                RadioListTile<String>(
                  value: 'exclude',
                  title: Text('Исключить выбранные приложения'),
                  subtitle: Text('Они используют обычную сеть вне VPN.'),
                ),
                RadioListTile<String>(
                  value: 'include',
                  title: Text('Через VPN только выбранные'),
                  subtitle: Text(
                    'Остальные приложения используют обычную сеть.',
                  ),
                ),
              ],
            ),
          ),
        ),
        const Text(
          'Исключённые приложения, включая их DNS и IPv6, не защищены VPN. '
          'Сам DetourVpn остаётся вне туннеля для управления подключением. '
          'Android Lockdown может заблокировать приложения вне списка. '
          'Настройки хранятся только на телефоне и меняются при отключённом VPN.',
        ),
        const SizedBox(height: 16),
        if (loading) const LinearProgressIndicator(),
        if (error != null) Text(error!),
        if (mode == 'include' && selected.isEmpty)
          const Text(
            'Для режима «только выбранные» отметь хотя бы одно приложение.',
          ),
        for (final name in apps)
          CheckboxListTile(
            title: Text(name),
            value: selected.contains(name),
            onChanged: loading
                ? null
                : (value) => setState(() {
                    if (value == true) {
                      selected.add(name);
                    } else {
                      selected.remove(name);
                    }
                  }),
          ),
        FilledButton(
          onPressed: loading || (mode == 'include' && selected.isEmpty)
              ? null
              : save,
          child: const Text('Сохранить'),
        ),
      ],
    ),
  );
}
