# ADR-055 — публичный IPv4 origin для закрытой beta Mini App

Статус: принят для локальной подготовки I-026, 2026-10-26.

## Контекст

У бесплатной закрытой beta есть постоянный публичный IPv4 API host, но нет
выбранного домена. Telegram `WebAppInfo` требует HTTPS URL. Telegram API описывает
поле как HTTPS URL и не устанавливает в контракте обязательное DNS-имя; поэтому
возможность открыть HTTPS Mini App по IP трактуется как допустимая гипотеза до
отдельного реального Telegram webview acceptance. Совместимость BotFather и
клиентов должна быть подтверждена live тестом.

## Решение

Serverless menu config может указать canonical globally-routable IPv4 HTTPS URL
только в `miniAppUrl`. Запрещены специальные/частные диапазоны, неканонические
октеты, userinfo, порты, query и fragment. Поддержка, privacy и download URLs
по-прежнему требуют DNS hostname. Backend получает только проверенный Telegram
`initData` и самостоятельно проверяет его подпись; IP origin не добавляет
отдельный серверный secret или доверие к клиентским данным.

Beta TLS может использовать уже предусмотренные IP-certificate templates;
сертификат короткоживущий и требует проверенной автоматической ротации и
независимого alerting. Этот ADR не включает TLS, firewall, DNS/BotFather
изменения, API ingress или публикацию. Реальный BotFather/webview запуск остаётся
открытым acceptance gate.

## Последствия и риски

- Проверка глобальных IPv4-диапазонов локальна и должна обновляться при изменении
  IANA special-purpose allocations.
- TLS доверие, доступность через Telegram-клиенты, свежесть `initData` и OTP
  доставки проверяются только hosted acceptance; локальный тест этого не доказывает.
- Никаких новых библиотек: используется переносимая проверка синтаксиса в уже
  существующем runtime-коде.
