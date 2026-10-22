# API contracts

`openapi.json` генерируется из фактического FastAPI-приложения:

```text
uv run --frozen python scripts/export_openapi.py
uv run --frozen python scripts/export_openapi.py --check
```

Публичный контракт описывает health, auth, devices и quota read API. Редирект со
слешем выключен; ошибки имеют error.code/message/request_id. Agent/admin mutations
не перечисляются в public OpenAPI. Не весь будущий API из ТЗ реализован.

Внутренние `usage-sample.v1.json` и `usage-result.v1.json` описывают accounting
payload/result. `agent-usage.v1.json` описывает envelope внутреннего node endpoint.
Узел определяется credential, переданным по HTTPS, не полями JSON. Временной nonce
защищает от повторной отправки. Текущий endpoint принимает только usage samples;
daemon и удалённый TLS ingress не поставляются.

```text
uv run --frozen python scripts/export_usage_contracts.py
uv run --frozen python scripts/export_usage_contracts.py --check
```
