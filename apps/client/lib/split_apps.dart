import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'native_tunnel.dart';
import 'platform_info.dart';

class SplitAppsScreen extends StatefulWidget {
  const SplitAppsScreen({super.key, this.onSaved});

  /// ADR-187: called after a change is stored. The running tunnel is not changed by it.
  final VoidCallback? onSaved;

  @override
  State<SplitAppsScreen> createState() => _SplitAppsState();
}

class _InstalledApp {
  const _InstalledApp({
    required this.packageName,
    required this.label,
    this.icon,
  });

  final String packageName;
  final String label;
  final Uint8List? icon;

  factory _InstalledApp.fromPlatform(Object? value) {
    if (value is String) {
      return _InstalledApp(packageName: value, label: value);
    }
    if (value is! Map) throw const FormatException('Invalid app entry');
    final entry = Map<String, Object?>.from(value);
    final packageName = entry['packageName'];
    final label = entry['label'];
    final icon = entry['icon'];
    if (packageName is! String || packageName.isEmpty) {
      throw const FormatException('Invalid app package');
    }
    return _InstalledApp(
      packageName: packageName,
      label: label is String && label.trim().isNotEmpty ? label : packageName,
      icon: icon is Uint8List ? icon : null,
    );
  }
}

class _SplitAppsState extends State<SplitAppsScreen>
    with SingleTickerProviderStateMixin {
  static const channel = MethodChannel('project.vpn/tunnel.v1');

  final searchController = TextEditingController();
  final domainSearchController = TextEditingController();
  final domainController = TextEditingController();
  late final TabController tabController;
  List<_InstalledApp> apps = [];
  Set<String> selected = {};
  List<String> domains = [];
  String mode = 'exclude';
  String lastSplitMode = 'exclude';
  String searchQuery = '';
  String domainSearchQuery = '';
  bool domainRouting = false;
  bool loading = true;
  bool saving = false;
  String? error;

  @override
  void initState() {
    super.initState();
    tabController = TabController(length: 2, vsync: this);
    load();
  }

  @override
  void dispose() {
    searchController.dispose();
    domainSearchController.dispose();
    domainController.dispose();
    tabController.dispose();
    super.dispose();
  }

  Future<void> load() async {
    try {
      final listed = await channel.invokeListMethod<Object?>('apps') ?? [];
      final saved = await channel.invokeListMethod<String>('splitRead') ?? [];
      // ADR-084 / ADR-095: domain rules exist only when the engine proves `domain-routing.v1`
      // (hidden on Android/GoBackend, available on the Windows engine).
      final supportsDomains = (await readNativeCapabilities()).contains(
        'domain-routing.v1',
      );
      final savedDomains = supportsDomains
          ? await channel.invokeListMethod<String>('splitDomainsRead') ??
                <String>[]
          : <String>[];
      final savedMode =
          await channel.invokeMethod<String>('splitModeRead') ?? 'exclude';
      final savedLastMode =
          await channel.invokeMethod<String>('splitLastModeRead') ?? 'exclude';
      final byPackage = <String, _InstalledApp>{};
      for (final item in listed) {
        final app = _InstalledApp.fromPlatform(item);
        byPackage[app.packageName] = app;
      }
      for (final packageName in saved) {
        byPackage.putIfAbsent(
          packageName,
          () => _InstalledApp(
            packageName: packageName,
            label: displayName(packageName),
          ),
        );
      }
      final sortedApps = byPackage.values.toList()
        ..sort((left, right) {
          final byLabel = left.label.toLowerCase().compareTo(
            right.label.toLowerCase(),
          );
          return byLabel != 0
              ? byLabel
              : left.packageName.compareTo(right.packageName);
        });
      if (!mounted) return;
      setState(() {
        apps = sortedApps;
        domainRouting = supportsDomains;
        selected = saved.toSet();
        domains = savedDomains;
        mode = const {'all', 'exclude', 'include'}.contains(savedMode)
            ? savedMode
            : 'exclude';
        lastSplitMode = const {'exclude', 'include'}.contains(savedMode)
            ? savedMode
            : const {'exclude', 'include'}.contains(savedLastMode)
            ? savedLastMode
            : 'exclude';
        error = const {'all', 'exclude', 'include'}.contains(savedMode)
            ? null
            : 'Неизвестный режим. Выбери режим заново.';
        loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        loading = false;
        error = 'Не удалось прочитать настройки $deviceWord';
      });
    }
  }

  Future<void> saveChange({
    required String nextMode,
    required Set<String> nextSelected,
    required List<String> nextDomains,
  }) async {
    if (loading || saving) return;
    final previousMode = mode;
    final previousLastMode = lastSplitMode;
    final previousSelected = Set<String>.of(selected);
    final previousDomains = List<String>.of(domains);
    setState(() {
      mode = nextMode;
      if (nextMode == 'exclude' || nextMode == 'include') {
        lastSplitMode = nextMode;
      }
      selected = Set<String>.of(nextSelected);
      domains = List<String>.of(nextDomains);
      saving = true;
      error = null;
    });

    try {
      final savedDomains = await channel
          .invokeListMethod<String>('splitWrite', {
            'packages': nextSelected.toList()..sort(),
            'domains': nextDomains,
            'mode': nextMode,
          });
      if (mounted) {
        setState(() {
          domains = savedDomains ?? nextDomains;
          saving = false;
        });
      }
      widget.onSaved?.call();
    } on PlatformException catch (failure) {
      if (!mounted) return;
      setState(() {
        mode = previousMode;
        lastSplitMode = previousLastMode;
        selected = previousSelected;
        domains = previousDomains;
        saving = false;
        error = failure.code == 'domain_invalid'
            ? 'Добавь домен без https:// и пути, например example.org или *.ru.'
            : failure.code == 'domain_duplicate'
            ? 'Этот домен уже есть в списке.'
            : 'Не удалось сохранить. Попробуй снова.';
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        mode = previousMode;
        lastSplitMode = previousLastMode;
        selected = previousSelected;
        domains = previousDomains;
        saving = false;
        error = 'Не удалось сохранить настройки $deviceWord.';
      });
    }
  }

  /// Windows entries are executable paths: show the file name without `.exe`.
  static String displayName(String id) {
    if (!isWindowsClient) return id;
    final name = id.split(RegExp(r'[\\/]')).last;
    return name.toLowerCase().endsWith('.exe')
        ? name.substring(0, name.length - 4)
        : name;
  }

  /// Windows only: choose a program that is not in the Start menu list.
  Future<void> pickApp() async {
    if (loading || saving) return;
    try {
      final path = await channel.invokeMethod<String>('pickApp');
      if (!mounted || path == null || path.isEmpty) return;
      final lower = path.toLowerCase();
      final known = apps.where((app) => app.packageName.toLowerCase() == lower);
      final key = known.isEmpty ? path : known.first.packageName;
      if (known.isEmpty) {
        setState(
          () => apps = [
            ...apps,
            _InstalledApp(packageName: path, label: displayName(path)),
          ],
        );
      }
      await saveChange(
        nextMode: mode,
        nextSelected: {...selected, key},
        nextDomains: domains,
      );
    } on PlatformException {
      if (!mounted) return;
      setState(() => error = 'Не удалось выбрать программу.');
    }
  }

  void selectMode(String? value) {
    if (value == null) return;
    saveChange(nextMode: value, nextSelected: selected, nextDomains: domains);
  }

  void setSplitEnabled(bool enabled) => saveChange(
    nextMode: enabled ? lastSplitMode : 'all',
    nextSelected: selected,
    nextDomains: domains,
  );

  void toggleApp(String packageName, bool? checked) {
    final nextSelected = Set<String>.of(selected);
    if (checked == true) {
      nextSelected.add(packageName);
    } else {
      nextSelected.remove(packageName);
    }
    saveChange(
      nextMode: mode,
      nextSelected: nextSelected,
      nextDomains: domains,
    );
  }

  void toggleDomain(String domain, bool checked) {
    final nextDomains = List<String>.of(domains);
    if (checked) {
      if (!nextDomains.contains(domain)) nextDomains.add(domain);
    } else {
      nextDomains.remove(domain);
    }
    saveChange(
      nextMode: mode,
      nextSelected: selected,
      nextDomains: nextDomains,
    );
  }

  Future<void> addDomain() async {
    domainController.clear();
    final value = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Добавить домен'),
        content: TextField(
          controller: domainController,
          autofocus: true,
          autocorrect: false,
          enableSuggestions: false,
          textInputAction: TextInputAction.done,
          onSubmitted: (value) => Navigator.of(context).pop(value),
          decoration: const InputDecoration(
            hintText: 'example.org или *.ru',
            helperText: 'Без https://, пути и порта',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Отмена'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(domainController.text),
            child: const Text('Добавить'),
          ),
        ],
      ),
    );
    final domain = value?.trim();
    if (!mounted || domain == null || domain.isEmpty) return;
    if (domains.any(
      (existing) => existing.toLowerCase() == domain.toLowerCase(),
    )) {
      setState(() => error = 'Этот домен уже есть в списке.');
      return;
    }
    await saveChange(
      nextMode: mode,
      nextSelected: selected,
      nextDomains: [...domains, domain],
    );
  }

  List<_InstalledApp> get visibleApps {
    final query = searchQuery.trim().toLowerCase();
    final filtered = query.isEmpty
        ? apps
        : apps.where(
            (app) =>
                app.label.toLowerCase().contains(query) ||
                app.packageName.toLowerCase().contains(query),
          );
    return filtered.toList()..sort((left, right) {
      final leftSelected = selected.contains(left.packageName);
      final rightSelected = selected.contains(right.packageName);
      if (leftSelected != rightSelected) return leftSelected ? -1 : 1;
      return left.label.toLowerCase().compareTo(right.label.toLowerCase());
    });
  }

  Widget appIcon(_InstalledApp app) {
    final bytes = app.icon;
    if (bytes == null) return const Icon(Icons.apps);
    return ClipRRect(
      borderRadius: BorderRadius.circular(8),
      child: Image.memory(
        bytes,
        width: 40,
        height: 40,
        fit: BoxFit.contain,
        errorBuilder: (_, __, ___) => const Icon(Icons.apps),
      ),
    );
  }

  Widget buildSaveStatus() {
    final currentError = error;
    final icon = saving
        ? Icons.sync
        : currentError == null
        ? Icons.check_circle_outline
        : Icons.error_outline;
    final message = saving
        ? 'Сохраняю $deviceWord…'
        : currentError ?? 'Настройки сохраняются автоматически $deviceWord.';
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 8, 24, 16),
        child: Row(
          children: [
            Icon(icon, size: 18),
            const SizedBox(width: 8),
            Expanded(child: Text(message)),
          ],
        ),
      ),
    );
  }

  Widget buildDomainTab() {
    final query = domainSearchQuery.trim().toLowerCase();
    final visibleDomains =
        domains.where((domain) => domain.toLowerCase().contains(query)).toList()
          ..sort(
            (left, right) => left.toLowerCase().compareTo(right.toLowerCase()),
          );
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 8),
          child: TextField(
            controller: domainSearchController,
            onChanged: (value) => setState(() => domainSearchQuery = value),
            decoration: InputDecoration(
              prefixIcon: const Icon(Icons.search),
              hintText: 'Поиск доменов',
              suffixIcon: domainSearchQuery.isEmpty
                  ? null
                  : IconButton(
                      tooltip: 'Очистить поиск',
                      onPressed: () {
                        domainSearchController.clear();
                        setState(() => domainSearchQuery = '');
                      },
                      icon: const Icon(Icons.clear),
                    ),
              border: const OutlineInputBorder(),
            ),
          ),
        ),
        Expanded(
          child: visibleDomains.isEmpty
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text(
                      domains.isEmpty
                          ? 'Доменов пока нет. Добавь адрес или шаблон, например *.ru.'
                          : 'Домены не найдены.',
                      textAlign: TextAlign.center,
                    ),
                  ),
                )
              : ListView.builder(
                  padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
                  itemCount: visibleDomains.length,
                  itemBuilder: (context, index) {
                    final domain = visibleDomains[index];
                    return Card(
                      child: ListTile(
                        leading: const Icon(Icons.language),
                        title: Text(domain),
                        subtitle: Text(
                          domain.startsWith('*.')
                              ? 'Домен и его поддомены'
                              : 'Точное имя домена',
                        ),
                        trailing: IconButton(
                          tooltip: 'Удалить домен',
                          onPressed: () => toggleDomain(domain, false),
                          icon: const Icon(Icons.delete_outline),
                        ),
                      ),
                    );
                  },
                ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 4, 20, 12),
          child: SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: addDomain,
              icon: const Icon(Icons.add),
              label: const Text('Добавить домен'),
            ),
          ),
        ),
      ],
    );
  }

  Widget buildAppsTab() => Column(
    children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 8),
        child: TextField(
          controller: searchController,
          onChanged: (value) => setState(() => searchQuery = value),
          decoration: InputDecoration(
            prefixIcon: const Icon(Icons.search),
            hintText: isWindowsClient
                ? 'Название или путь к программе'
                : 'Название или код Android',
            suffixIcon: searchQuery.isEmpty
                ? null
                : IconButton(
                    tooltip: 'Очистить поиск',
                    onPressed: () {
                      searchController.clear();
                      setState(() => searchQuery = '');
                    },
                    icon: const Icon(Icons.clear),
                  ),
            border: const OutlineInputBorder(),
          ),
        ),
      ),
      Expanded(
        child: visibleApps.isEmpty
            ? const Center(child: Text('Приложения не найдены.'))
            : ListView.builder(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
                itemCount: visibleApps.length,
                itemBuilder: (context, index) {
                  final app = visibleApps[index];
                  return CheckboxListTile(
                    key: ValueKey(app.packageName),
                    contentPadding: const EdgeInsets.symmetric(horizontal: 8),
                    controlAffinity: ListTileControlAffinity.trailing,
                    secondary: appIcon(app),
                    title: Text(app.label),
                    subtitle: Text(app.packageName),
                    value: selected.contains(app.packageName),
                    onChanged: (value) => toggleApp(app.packageName, value),
                  );
                },
              ),
      ),
      if (isWindowsClient)
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
          child: SizedBox(
            width: double.infinity,
            child: OutlinedButton.icon(
              key: const ValueKey('pick-app'),
              onPressed: pickApp,
              icon: const Icon(Icons.folder_open),
              label: const Text('Добавить программу…'),
            ),
          ),
        ),
      if (mode == 'include' && selected.isEmpty && domains.isEmpty)
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
          child: Text(
            domainRouting
                ? 'Добавь хотя бы одно приложение или домен, чтобы подключиться в этом режиме.'
                : 'Добавь хотя бы одно приложение, чтобы подключиться в этом режиме.',
          ),
        ),
    ],
  );

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Разделение трафика')),
    bottomNavigationBar: buildSaveStatus(),
    body: loading
        ? const Center(child: CircularProgressIndicator())
        : IgnorePointer(
            ignoring: saving,
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 4, 20, 0),
                  child: SwitchListTile.adaptive(
                    key: const ValueKey('split-enabled'),
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Разделение трафика'),
                    subtitle: Text(
                      mode == 'all'
                          ? 'Выключено: весь обычный трафик через VPN.'
                          : 'Включено: выбранные правила применяются ниже.',
                    ),
                    value: mode != 'all',
                    onChanged: setSplitEnabled,
                  ),
                ),
                if (mode != 'all')
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 20),
                    child: RadioGroup<String>(
                      groupValue: mode,
                      onChanged: selectMode,
                      child: const Column(
                        children: [
                          RadioListTile<String>(
                            contentPadding: EdgeInsets.zero,
                            value: 'exclude',
                            title: Text('Исключить выбранное из VPN'),
                          ),
                          RadioListTile<String>(
                            contentPadding: EdgeInsets.zero,
                            value: 'include',
                            title: Text('Через VPN только выбранные'),
                          ),
                        ],
                      ),
                    ),
                  ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 4, 20, 8),
                  child: Text(
                    domainRouting
                        ? 'Правила действуют для приложений и доменов. В режиме «только выбранное» выбери хотя бы одно приложение или домен. Домен определяется по имени в соединении и DNS-запросе: если программа использует зашифрованный DNS, ECH или кеш, правило может не сработать.'
                        : 'Правила действуют для приложений. В режиме «только выбранное» выбери хотя бы одно приложение. Маршрутизация по доменам в этой бете недоступна.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
                if (mode != 'all') ...[
                  // ADR-084 / ADR-095: the domain tab exists only with `domain-routing.v1`.
                  if (domainRouting)
                    TabBar(
                      controller: tabController,
                      tabs: const [
                        Tab(text: 'Домены'),
                        Tab(text: 'Приложения'),
                      ],
                    ),
                  Expanded(
                    child: domainRouting
                        ? TabBarView(
                            controller: tabController,
                            children: [buildDomainTab(), buildAppsTab()],
                          )
                        : buildAppsTab(),
                  ),
                ] else
                  const Expanded(
                    child: Center(
                      child: Padding(
                        padding: EdgeInsets.all(24),
                        child: Text(
                          'Список сохранён, но разделение выключено. Весь обычный трафик направляется через VPN.',
                          textAlign: TextAlign.center,
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
  );
}
