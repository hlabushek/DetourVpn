import React, { useEffect, useState } from "react";
import { CabinetAPI, bytes, gigabytesToBytes } from "./api.mjs";
import { Flag } from "./Flag";
import { Icon } from "./components/Icon";

// Integers arrive as decimal strings (exactJSON keeps int64 exact).
export type Device = {
    id: string;
    platform: string;
    created_at: string;
    role?: "owner" | "guest";
    label?: string | null;
    traffic_cap_bytes?: string | null;
    used_bytes_total?: string | null;
    used_bytes_30d?: string | null;
    last_connected_at?: string | null;
    last_country_code?: string | null;
};
type LinkCode = {
    id: string;
    label: string | null;
    traffic_cap_bytes: string | null;
    created_at: string;
    expires_at: string;
};
type Created = LinkCode & { code: string };

type Props = {
    api: CabinetAPI;
    devices: Device[];
    busy: boolean;
    run: (operation: () => Promise<void>) => Promise<void>;
    reload: () => Promise<void>;
    notify: (text: string) => void;
};

type Confirmer = { showConfirm?: (text: string, done: (ok: boolean) => void) => void };
function confirmAction(text: string, done: () => void) {
    const tg = window.Telegram?.WebApp as unknown as Confirmer | undefined;
    if (tg?.showConfirm) tg.showConfirm(text, (ok) => ok && done());
    else if (window.confirm(text)) done();
}

const when = (value: string) => new Date(value).toLocaleString("ru-RU");

/** "09:41" until the code expires; display only, the server enforces expiry. */
function useCountdown(expiresAt: string | null) {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!expiresAt) return;
        const timer = window.setInterval(() => setNow(Date.now()), 1000);
        return () => window.clearInterval(timer);
    }, [expiresAt]);
    if (!expiresAt) return null;
    const left = Math.max(0, Math.floor((new Date(expiresAt).getTime() - now) / 1000));
    if (left === 0) return null;
    return `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(left % 60).padStart(2, "0")}`;
}

export function Devices({ api, devices, busy, run, reload, notify }: Props) {
    const [codes, setCodes] = useState<LinkCode[]>([]);
    const [created, setCreated] = useState<Created | null>(null);
    const [label, setLabel] = useState("");
    const [cap, setCap] = useState("");
    const [capError, setCapError] = useState("");
    const [editing, setEditing] = useState<string | null>(null);
    const [editLabel, setEditLabel] = useState("");
    const [editCap, setEditCap] = useState("");
    const left = useCountdown(created?.expires_at ?? null);

    async function loadCodes() {
        try {
            setCodes(await api.listLinkCodes());
        } catch {
            setCodes([]); // The list is a convenience; the device list above still works.
        }
    }
    useEffect(() => {
        void loadCodes();
    }, []);

    function capBytes(text: string): number | null | undefined {
        const value = gigabytesToBytes(text);
        setCapError(
            value === undefined ? "Лимит: число ГБ от 0,001, не более трёх знаков после запятой." : "",
        );
        return value;
    }

    async function create() {
        const value = capBytes(cap);
        if (value === undefined) return;
        await run(async () => {
            const result = await api.createLinkCode({
                label: label.trim() || undefined,
                trafficCapBytes: value ?? undefined,
            });
            setCreated(result);
            setLabel("");
            setCap("");
            await loadCodes();
        });
    }
    async function copy(code: string) {
        try {
            await navigator.clipboard.writeText(code);
            notify("Код скопирован. Передайте его только тому, кому доверяете.");
        } catch {
            notify("Не удалось скопировать: выделите код вручную.");
        }
    }
    function startEdit(device: Device) {
        setEditing(device.id);
        setEditLabel(device.label ?? "");
        setEditCap(
            device.traffic_cap_bytes ? String(Number(device.traffic_cap_bytes) / 1e9).replace(".", ",") : "",
        );
        setCapError("");
    }
    async function saveEdit(device: Device) {
        const value = capBytes(editCap);
        if (value === undefined) return;
        await run(async () => {
            await api.patchDevice(device.id, {
                label: editLabel.trim() || null,
                traffic_cap_bytes: value,
            });
            setEditing(null);
            await reload();
        });
    }
    function disconnect(device: Device) {
        const name = device.label ?? `Android · ${device.id.slice(0, 8)}`;
        confirmAction(
            `Отключить «${name}» от аккаунта? Устройство потеряет доступ к VPN и аккаунту.`,
            () =>
                void run(async () => {
                    await api.revokeDevice(device.id);
                    notify("Устройство отключено. Туннель на нём закрывается.");
                    await reload();
                }),
        );
    }
    function cancel(code: LinkCode) {
        void run(async () => {
            await api.cancelLinkCode(code.id);
            if (created?.id === code.id) setCreated(null);
            await loadCodes();
        });
    }

    return (
        <>
            {devices.map((d) => {
                const capValue = d.traffic_cap_bytes ? Number(d.traffic_cap_bytes) : null;
                const used = d.used_bytes_total ? Number(d.used_bytes_total) : 0;
                return (
                    <section className="card device-card" key={d.id}>
                        <div className="device-head">
                            <Flag code={d.last_country_code} width={36} />
                            <b>{d.label ?? `Android · ${d.id.slice(0, 8)}`}</b>
                            {d.role === "guest" && <span className="badge">Гость</span>}
                        </div>
                        <div className="device-meta">
                            <small>Добавлено {when(d.created_at)}</small>
                            {d.last_connected_at && <small>Последнее подключение {when(d.last_connected_at)}</small>}
                            <small>
                                Трафик: {bytes(d.used_bytes_30d ?? "0")} за 30 дней · {bytes(d.used_bytes_total ?? "0")}{" "}
                                всего
                            </small>
                        </div>
                        {capValue !== null && (
                            <div className="cap">
                                <div className="cap-row">
                                    <small>Лимит устройства: {bytes(d.traffic_cap_bytes ?? "0")}</small>
                                    <small>{bytes(d.used_bytes_total ?? "0")}</small>
                                </div>
                                <div className="meter" role="img" aria-label="Расход относительно лимита устройства">
                                    <span style={{ width: `${Math.min(100, (used / capValue) * 100)}%` }} />
                                </div>
                            </div>
                        )}
                        {editing === d.id ? (
                            <div className="device-edit">
                                <label>
                                    Название
                                    <input
                                        value={editLabel}
                                        maxLength={40}
                                        placeholder="Например, Телефон Пети"
                                        aria-label="Название устройства"
                                        onChange={(e) => setEditLabel(e.target.value)}
                                    />
                                </label>
                                <label>
                                    Лимит трафика, ГБ
                                    <input
                                        value={editCap}
                                        inputMode="decimal"
                                        placeholder="Пусто — без лимита"
                                        aria-label="Лимит трафика устройства, ГБ"
                                        onChange={(e) => setEditCap(e.target.value)}
                                    />
                                </label>
                                {capError && (
                                    <small role="alert" className="field-error">
                                        {capError}
                                    </small>
                                )}
                                <div className="device-actions">
                                    <button className="primary" disabled={busy} onClick={() => void saveEdit(d)}>
                                        Сохранить
                                    </button>
                                    <button className="secondary" disabled={busy} onClick={() => setEditing(null)}>
                                        Отмена
                                    </button>
                                </div>
                            </div>
                        ) : (
                            <div className="device-actions">
                                <button className="secondary" disabled={busy} onClick={() => startEdit(d)}>
                                    Изменить
                                </button>
                                <button className="danger" disabled={busy} onClick={() => disconnect(d)}>
                                    Отключить
                                </button>
                            </div>
                        )}
                    </section>
                );
            })}
            {!devices.length && (
                <section className="card">
                    <p className="muted">Устройств пока нет. Добавьте устройство через Android-приложение.</p>
                </section>
            )}

            <h2 className="section-title">Подключить устройство</h2>
            <section className="card">
                {created ? (
                    <div className="code-box" role="status">
                        <small className="muted">Код для подключения</small>
                        <strong className="link-code">{created.code}</strong>
                        <p className="timer">
                            <Icon name="clock" />
                            {left ? (
                                <>
                                    Действует ещё <b>{left}</b>
                                </>
                            ) : (
                                "Срок действия истёк"
                            )}
                        </p>
                        <div className="device-actions">
                            <button className="primary" onClick={() => void copy(created.code)}>
                                <Icon name="copy" />
                                Скопировать
                            </button>
                            <button className="secondary" onClick={() => setCreated(null)}>
                                Скрыть
                            </button>
                        </div>
                        <p className="warn-note">
                            <Icon name="alert" />
                            Показан один раз. Передайте только тому, кому доверяете.
                        </p>
                    </div>
                ) : (
                    <div className="device-edit">
                        <label>
                            Название
                            <input
                                value={label}
                                maxLength={40}
                                placeholder="Например, Телефон Пети"
                                aria-label="Название нового устройства"
                                onChange={(e) => setLabel(e.target.value)}
                            />
                        </label>
                        <label>
                            Лимит трафика, ГБ (необязательно)
                            <input
                                value={cap}
                                inputMode="decimal"
                                placeholder="Например, 5"
                                aria-label="Лимит трафика нового устройства, ГБ"
                                onChange={(e) => setCap(e.target.value)}
                            />
                        </label>
                        {capError && (
                            <small role="alert" className="field-error">
                                {capError}
                            </small>
                        )}
                        <p className="info-note">
                            <Icon name="info" />
                            Одноразовый код действует 10 минут. Устройство по коду получает доступ как гость: пользуется
                            VPN за счёт вашего трафика, но не управляет аккаунтом. Отключить его можно в любой момент.
                        </p>
                        <button className="primary" disabled={busy} onClick={() => void create()}>
                            <Icon name="link" />
                            Получить код
                        </button>
                    </div>
                )}
            </section>
            {codes.length > 0 && (
                <>
                    <h2 className="section-title">Неиспользованные коды</h2>
                    <section className="card codes">
                        {codes.map((c) => (
                            <div className="code-row" key={c.id}>
                                <span className="tile">
                                    <Icon name="link" />
                                </span>
                                <span className="row-text">
                                    <b>{c.label ?? "Без названия"}</b>
                                    <small>
                                        до {new Date(c.expires_at).toLocaleTimeString("ru-RU")}
                                        {c.traffic_cap_bytes ? ` · лимит ${bytes(c.traffic_cap_bytes)}` : ""}
                                    </small>
                                </span>
                                <button className="danger small" disabled={busy} onClick={() => cancel(c)}>
                                    Отменить
                                </button>
                            </div>
                        ))}
                    </section>
                </>
            )}
        </>
    );
}
