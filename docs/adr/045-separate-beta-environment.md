# ADR-045 — отдельный control plane для бесплатной закрытой beta

Статус: принято 2026-10-25; частичный deploy на новые beta VMs выполнен 2026-10-26.

Бета-контур должен иметь отдельные host, Compose project, PostgreSQL volume и
roles, Linux user, systemd units, каталоги, секреты, API origin и VPN node.
Конфиги `infra/closed` нельзя запускать на B или переиспользовать для друзей:
они закрепляют B IP, имена и системные пути. `infra/beta` сделан отдельным
набором на тех же уже используемых PostgreSQL 17, Compose, systemd, Caddy и
Certbot компонентах; зависимости не добавляются.

API и PostgreSQL доступны только через loopback; DB/API используют порты
25433/28000 и beta-only идентичности. Public ingress требует явный IP и
короткоживущий доверенный HTTPS сертификат. Bootstrap ingress обслуживает только
HTTP-01 challenge и не проксирует API. После TLS-ready Caddy скрывает
`/api/v1/ready` и все internal routes, кроме трёх v3 window-agent POST путей,
которые ограничены source IP beta node и проверяются API credentials/nonce по
ADR-047.

Beta invite policy реализована локально в ADR-046. Локальный HTTPS ingress
шаблон из ADR-047 не устанавливает VPN node, не provision-ит inventory/credential,
не задаёт firewall allowlist и не запускает persistent v3 slot services. Также
не включены BotFather menu, Mini App build или independent external monitor.
Поэтому это ещё не deployable VPN beta. API HTTPS proxy, Telegram account,
BotFather, node credential/firewall ingress, off-host backup/monitoring и friend
traffic не включены. Новые внешние действия за пределами уже подготовленных
beta VM по-прежнему требуют отдельного явного разрешения. Публичный запуск не
подразумевается.

На 2026-10-26 новая API VM получила Caddy 2.11.7, HTTP-01 challenge-only
bootstrap, Let's Encrypt shortlived IP certificate, deploy hook и собственный
renewal timer; Certbot Snap timer отключён во избежание дублирования. Внешний
HTTPS проверен с обычной валидацией TLS: root, readiness и Mini App paths
отдают 404. API/DB остаются на loopback. Renewal dry-run и ручной timer service
прошли; независимый expiry monitor/alerting всё ещё не настроен. Подробности:
`docs/evidence/i-030-beta-api-tls-20261026.json`.

Основания для IP TLS: [Let's Encrypt: IP certificates generally available](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)
и [Certbot IP/webroot support](https://letsencrypt.org/2026/03/11/shorter-certs-certbot).
IP certificates используют shortlived профиль (160 часов); это требует
проверенной автоматизации renewal и независимого контроля отказа.
