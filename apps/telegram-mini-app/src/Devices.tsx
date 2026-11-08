import React, { useEffect, useState } from "react";
import { CabinetAPI, bytes, gigabytesToBytes } from "./api.mjs";
import { Flag } from "./Flag";

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

export function Devices({ api, devices, busy, run, reload, notify }: Props) {
    const [codes, setCodes] = useState<LinkCode[]>([]);
    const [created, setCreated] = useState<Created | null>(null);
    const [label, setLabel] = useState("");
    const [cap, setCap] = useState("");
    const [capError, setCapError] = useState("");
    const [editing, setEditing] = useState<string | null>(null);
    const [editLabel, setEditLabel] = useState("");
    const [editCap, setEditCap] = useState("");

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
            <section className="card">
                {devices.map((d) => {
                    const capValue = d.traffic_cap_bytes ? Number(d.traffic_cap_bytes) : null;
                    const used = d.used_bytes_total ? Number(d.used_bytes_total) : 0;
                    return (
                        <div className="device device-row" key={d.id}>
                            <Flag code={d.last_country_code} />
                            <div className="device-body">
                                <b>
                                    {d.label ?? `Android · ${d.id.slice(0, 8)}`}
                                    {d.role === "guest" && <span className="badge">Гость</span>}
                                </b>
                                <small>Добавлено {when(d.created_at)}</small>
                                {d.last_connected_at && (
                                    <small>Последнее подключение {when(d.last_connected_at)}</small>
                                )}
                                <small>
                                    Трафик: {bytes(d.used_bytes_30d ?? "0")} за 30 дней ·{" "}
                                    {bytes(d.used_bytes_total ?? "0")} всего
                                </small>
                                {capValue !== null && (
                                    <>
                                        <small>Лимит устройства: {bytes(d.traffic_cap_bytes ?? "0")}</small>
                                        <progress max={capValue} value={Math.min(used, capValue)} />
                                    </>
                                )}
                                {editing === d.id ? (
                                    <div className="device-edit">
                                        <input
                                            value={editLabel}
                                            maxLength={40}
                                            placeholder="Название"
                                            aria-label="Название устройства"
                                            onChange={(e) => setEditLabel(e.target.value)}
                                        />
                                        <input
                                            value={editCap}
                                            inputMode="decimal"
                                            placeholder="Лимит, ГБ (пусто — без лимита)"
                                            aria-label="Лимит трафика устройства, ГБ"
                                            onChange={(e) => setEditCap(e.target.value)}
                                        />
                                        {capError && <small role="alert">{capError}</small>}
                                        <div className="device-actions">
                                            <button className="primary" disabled={busy} onClick={() => void saveEdit(d)}>
                                                Сохранить
                                            </button>
                                            <button disabled={busy} onClick={() => setEditing(null)}>
                                                Отмена
                                            </button>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="device-actions">
                                        <button disabled={busy} onClick={() => startEdit(d)}>
                                            Изменить
                                        </button>
                                        <button className="danger" disabled={busy} onClick={() => disconnect(d)}>
                                            Отключить
                                        </button>
                                    </div>
                                )}
                            </div>
                        </div>
                    );
                })}
                {!devices.length && <p>Устройств пока нет. Добавьте устройство через Android-приложение.</p>}
            </section>

            <h2>Подключить устройство</h2>
            <section className="card">
                <p>
                    Одноразовый код действует 10 минут. Устройство по коду получает доступ как гость: оно
                    пользуется VPN за счёт вашего трафика, но не может управлять аккаунтом и другими
                    устройствами. Отключить его можно в любой момент.
                </p>
                {created ? (
                    <div className="code-box" role="status">
                        <strong className="link-code">{created.code}</strong>
                        <small>Действует до {new Date(created.expires_at).toLocaleTimeString("ru-RU")}. Показан один раз.</small>
                        <div className="device-actions">
                            <button className="primary" onClick={() => void copy(created.code)}>
                                Скопировать
                            </button>
                            <button onClick={() => setCreated(null)}>Скрыть</button>
                        </div>
                    </div>
                ) : (
                    <div className="device-edit">
                        <input
                            value={label}
                            maxLength={40}
                            placeholder="Название, например «Телефон Пети»"
                            aria-label="Название нового устройства"
                            onChange={(e) => setLabel(e.target.value)}
                        />
                        <input
                            value={cap}
                            inputMode="decimal"
                            placeholder="Лимит трафика, ГБ (необязательно)"
                            aria-label="Лимит трафика нового устройства, ГБ"
                            onChange={(e) => setCap(e.target.value)}
                        />
                        {capError && <small role="alert">{capError}</small>}
                        <button className="primary" disabled={busy} onClick={() => void create()}>
                            Получить код
                        </button>
                    </div>
                )}
                {codes.length > 0 && (
                    <>
                        <h3>Неиспользованные коды</h3>
                        {codes.map((c) => (
                            <div className="device" key={c.id}>
                                <div className="device-body">
                                    <b>{c.label ?? "Без названия"}</b>
                                    <small>
                                        до {new Date(c.expires_at).toLocaleTimeString("ru-RU")}
                                        {c.traffic_cap_bytes ? ` · лимит ${bytes(c.traffic_cap_bytes)}` : ""}
                                    </small>
                                </div>
                                <button disabled={busy} onClick={() => cancel(c)}>
                                    Отменить
                                </button>
                            </div>
                        ))}
                    </>
                )}
            </section>
        </>
    );
}
