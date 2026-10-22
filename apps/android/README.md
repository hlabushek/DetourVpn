# Android native POC — I-011

Исполняемая Java test harness поверх upstream userspace GoBackend/VpnService.
Собираются debug APK, unsigned release APK и GoBackend-only AAR для arm64/x86_64.
Native/parser/permission-denial checks проверены на физическом Pixel 8 Pro.
**Технический I-011 принят:** внешний endpoint, egress, DNS/IPv6 sample и
индивидуальный revoke измерены на Pixel/staging. Границы — ADR-022;
публичный выпуск остаётся закрыт release gates.

Private key генерируется на телефоне и живёт только в памяти процесса. Profile
принимается без PrivateKey; нет backend provisioning, Flutter или готового UI MVP.
Статус native UP не считается проверенным доступом в интернет. Build instructions,
hashes/ограничения и staging proposal: [runbook](../../docs/runbooks/android-poc.md),
[отчёт](../../docs/verification-i-011.md), [ADR-019](../../docs/adr/019-android-native-poc.md).
Публичный выпуск SDK/APK не разрешён этим spike; licensing/security gates открыты.
