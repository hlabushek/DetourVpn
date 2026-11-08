# Очистка закрытых I-028 архивов для staging-сборки

Область: только staging A `45.10.166.106`, файловая система `/`. B `150.241.99.227` и production не проверять и не менять. Sudo вводится владельцем только в локальной интерактивной консоли; пароль не передавать агенту.

## 1. Read-only baseline

Сначала проверить IPv4 адрес `ens3`, затем снять baseline на точном A:

```bash
df -Pk /
awk '/MemAvailable:/ {print "MemAvailable_kB=" $2}' /proc/meminfo
for u in detourvpn-i028-agent.service detourvpn-i028-core1.service detourvpn-i028-core2.service detourvpn-i028-timeout.service detourvpn-i028-timeout.timer; do
  systemctl show --property=Id,LoadState,ActiveState "$u"
done
for d in /opt/detourvpn-i028 /opt/detourvpn-i028-archives; do
  if test -L "$d"; then echo "SYMLINK $d"
  elif test -d "$d"; then du -x -k -s -- "$d"
  else echo "ABSENT $d"; fi
done
```

Сверить актуальные проверки в `infra/staging-v3/install.sh`, не ослаблять их. Сейчас установщик требует строго больше `1,500,000 KiB` свободного места на `/` и больше `350,000 KiB` `MemAvailable`. Это разные ресурсы. Любой активный I-028 service или timer блокирует очистку.

## 2. Отбор архива

Только если `/opt/detourvpn-i028-archives` и архив — настоящие каталоги, а не symlink, просмотреть закрытые архивы по времени создания. Для каждого кандидата прочитать `archive.json` и принять его только при всех условиях:

- `closed_resources_verified`, `private_journal_preserved` и `timeout_timer_disarmed` равны `true`;
- `sources` содержит `release`, `node`, `state`, `run`; `missing_sources` пуст;
- `archive.json` — обычный файл; каталог архива принадлежит root;
- путь `archive/release/python` существует как каталог, каждый компонент — не symlink, владелец root; canonical path остаётся внутри archive root.

На выбранной цели измерить выделенное место командой `du -x -k -s -- <точный-target>`. Пропустить повреждённые/неполные архивы и записать причину. До удаления показать владельцу точный target, возраст и объём. Выбрать самый старый минимальный набор, достаточный для прохода дискового порога с разумным запасом.

## 3. Узкая очистка

Перед удалением повторно проверить адрес A, inactive/not-found состояния всех I-028 units, metadata, отсутствие symlink, расположение target внутри archive root и неизменность его размера. Удалять только точные `release/python` каталог(и) прошедших проверку архивов через `shutil.rmtree(target)` с повторной валидацией; не применять широкие `rm -rf`.

Оставить без изменений корень архива, `archive.json`, журнал, `release` кроме `python`, `node`, `state`, `run`. Не очищать логи, БД, системные/пакетные кэши или данные других контуров.

## 4. Post-check и запуск

Повторить read-only baseline и проверить, что удалены только выбранные `release/python`, а `archive.json`, `node`, `state`, `run` остались. Установку запускать только если оба порога проходят. Если Install завершился ошибкой, не запускать Test: сначала изучить диагностику и подтвердить cleanup. Успешный Install начинает 30-минутное окно I-028; начинать его, когда телефон и требуемая сеть готовы к физическому тесту.

## Выполненная очистка — 2026-11-02

Read-only pre-check: A подтверждён; свободно `1,358,164 KiB`, `MemAvailable=1,529,200 KiB`; I-028 units и timer inactive/not-found; `/opt/detourvpn-i028` отсутствовал. Из 100 каталогов архива 36 прошли условия, 64 без читаемого `archive.json` были пропущены.

После показа владельцу удалены только:

- `closed-yk3u3riq/release/python` — `108,436 KiB`;
- `closed-7ej373xk/release/python` — `108,436 KiB`;
- `closed-wbacsh0m/release/python` — `108,436 KiB`.

Всего по `du`: `325,308 KiB`. Архивы от 2026-10-28, metadata и `node/state/run` сохранены. Post-check: `/` свободно `1,683,428 KiB`, `MemAvailable=1,502,572 KiB`; все I-028 services/timer inactive/not-found; archive root `14,289,896 KiB`. Install и Test после очистки ещё не запускались. Receipt: `../evidence/i-028-archive-python-prune-20261102T180634Z.json`.