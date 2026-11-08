// Parse integer JSON tokens as strings before JSON.parse can round int64 byte amounts.
export function exactJSON(text) {
    let output = "",
        quoted = false,
        escaped = false;
    for (let i = 0; i < text.length; i++) {
        const c = text[i];
        if (quoted) {
            output += c;
            if (escaped) escaped = false;
            else if (c === "\\") escaped = true;
            else if (c === '"') quoted = false;
        } else if (c === '"') {
            quoted = true;
            output += c;
        } else if (c === "-" || /[0-9]/.test(c)) {
            const match = text
                .slice(i)
                .match(/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/);
            if (!match) throw new Error("invalid_json");
            const value = match[0];
            output += /^-?[0-9]+$/.test(value) ? JSON.stringify(value) : value;
            i += value.length - 1;
        } else output += c;
    }
    return JSON.parse(output);
}

export function bytes(value) {
    const n = BigInt(value);
    if (n < 0n) return `−${bytes(-n)}`;
    if (n < 1000000n) return `${n.toLocaleString("ru-RU")} Б`;
    const unit = n >= 1000000000n ? 1000000000n : 1000000n;
    return `${n / unit},${((n % unit) * 10n) / unit} ${unit === 1000000000n ? "ГБ" : "МБ"}`;
}

/** Decimal GB text ("1,5") to integer bytes; "" -> null (no cap); invalid or out of range -> undefined. */
export function gigabytesToBytes(text) {
    const value = String(text ?? "").trim().replace(",", ".");
    if (value === "") return null;
    if (!/^\d{1,6}(\.\d{1,3})?$/.test(value)) return undefined;
    const [whole, fraction = ""] = value.split(".");
    const result = BigInt(whole) * 1000000000n + BigInt(fraction.padEnd(9, "0"));
    return result >= 1n && result <= 2n ** 50n ? Number(result) : undefined;
}

export function loginAttemptFromFragment(fragment) {
    if (!fragment.startsWith("#")) return { present: false, requestId: null };
    const value = new URLSearchParams(fragment.slice(1)).get("login_attempt");
    if (value === null) return { present: false, requestId: null };
    return /^[0-9a-f]{32}$/.test(value)
        ? { present: true, requestId: value }
        : { present: true, requestId: null };
}

export class ApiError extends Error {
    constructor(code, status) {
        super(code);
        this.status = status;
    }
}

export class CabinetAPI {
    constructor(fetcher = globalThis.fetch.bind(globalThis)) {
        this.fetcher = fetcher;
        this.tokens = null;
        this.refreshing = null;
        this.pending = new Map();
        this.closed = false;
    }
    async request(path, body, authenticated = true, method) {
        if (!path.startsWith("/api/v1/")) throw new Error("invalid_path");
        if (this.closed) throw new Error("session_closed");
        if (
            authenticated &&
            this.tokens &&
            Date.parse(this.tokens.access_expires_at) <= Date.now() + 15000
        ) {
            this.refreshing ??= this.request(
                "/api/v1/auth/refresh",
                { refresh_token: this.tokens.refresh_token },
                false,
            )
                .then((tokens) => {
                    if (!this.closed) this.tokens = tokens;
                })
                .finally(() => {
                    this.refreshing = null;
                });
            await this.refreshing;
        }
        const response = await this.fetcher(path, {
            method: method ?? (body === undefined ? "GET" : "POST"),
            credentials: "omit",
            cache: "no-store",
            redirect: "error",
            referrerPolicy: "no-referrer",
            signal: AbortSignal.timeout(12000),
            headers: {
                ...(body === undefined
                    ? {}
                    : { "Content-Type": "application/json" }),
                ...(authenticated && this.tokens
                    ? { Authorization: `Bearer ${this.tokens.access_token}` }
                    : {}),
            },
            ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        });
        const result =
            response.status === 204 ? null : exactJSON(await response.text());
        if (!response.ok)
            throw new ApiError(
                result?.error?.code ?? "request_failed",
                response.status,
            );
        if (this.closed) throw new Error("session_closed");
        return result;
    }
    async login(initData) {
        this.tokens = await this.request(
            "/api/v1/auth/mini-app",
            { init_data: initData },
            false,
        );
    }
    async requestLoginCode(initData, requestId) {
        return this.request(
            "/api/v1/auth/mini-app/request-code",
            { init_data: initData, request_id: requestId },
            false,
        );
    }
    // Device management (ADR-093). A link code is shown once, so a lost response is never
    // retried with the same idempotency key (the server would refuse to reveal it again): each
    // attempt uses a fresh key and a stray unused code is simply cancelled in the list.
    async createLinkCode({ label, trafficCapBytes } = {}) {
        return this.request("/api/v1/devices/link-codes", {
            idempotency_key: crypto.randomUUID(),
            ...(label ? { label } : {}),
            ...(trafficCapBytes ? { traffic_cap_bytes: trafficCapBytes } : {}),
        });
    }
    async listLinkCodes() {
        return this.request("/api/v1/devices/link-codes");
    }
    async cancelLinkCode(id) {
        return this.request(`/api/v1/devices/link-codes/${encodeURIComponent(id)}`, undefined, true, "DELETE");
    }
    async patchDevice(id, patch) {
        return this.request(`/api/v1/devices/${encodeURIComponent(id)}`, patch, true, "PATCH");
    }
    async revokeDevice(id) {
        return this.request(`/api/v1/devices/${encodeURIComponent(id)}`, undefined, true, "DELETE");
    }
    async claim(rule) {
        const key = this.pending.get(rule) ?? crypto.randomUUID();
        this.pending.set(rule, key);
        try {
            const receipt = await this.request("/api/v1/rewards/claim", {
                rule_id: rule,
                idempotency_key: key,
            });
            this.pending.delete(rule);
            return receipt;
        } catch (error) {
            if (error instanceof ApiError && error.status < 500)
                this.pending.delete(rule);
            throw error;
        }
    }
    async referral(path, body, identity) {
        const slot = `referral:${path}:${identity}`;
        const key = this.pending.get(slot) ?? crypto.randomUUID();
        this.pending.set(slot, key);
        try {
            const receipt = await this.request(`/api/v1/referrals/${path}`, {
                ...body, idempotency_key: key,
            });
            this.pending.delete(slot);
            return receipt;
        } catch (error) {
            if (error instanceof ApiError && error.status < 500) this.pending.delete(slot);
            throw error;
        }
    }
    async logout() {
        try {
            await this.request("/api/v1/auth/logout", {});
        } finally {
            this.closed = true;
            this.tokens = null;
            this.pending.clear();
        }
    }
}
