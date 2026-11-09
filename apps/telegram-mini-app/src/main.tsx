import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { CabinetAPI, ApiError, bytes, loginAttemptFromFragment } from "./api.mjs";
import { Devices } from "./Devices";
import { Icon } from "./components/Icon";
import { Shell } from "./components/Shell";
import { Skeleton, StateView } from "./components/Cards";
import { Orbit } from "./components/Orbit";
import { Bonuses, type ReferralActions } from "./screens/Bonuses";
import { Home } from "./screens/Home";
import { Profile } from "./screens/Profile";
import { isNoAccess, viewerId } from "./access.mjs";
import { message, SESSION_GONE } from "./messages";
import { date, type Rule, type Snapshot, type Tab } from "./types";
import "./style.css";

/** First name from Telegram for the greeting only; never sent to the backend. */
function displayName(): string | null {
    const raw = window.Telegram?.WebApp.initDataUnsafe?.user?.first_name;
    if (typeof raw !== "string") return null;
    const name = raw.trim().slice(0, 32);
    return name || null;
}

/** The viewer's own Telegram ID with a copy button, so a tester can send it to whoever invites them. */
function MyId() {
    const id = viewerId(window.Telegram?.WebApp.initDataUnsafe?.user);
    const [copied, setCopied] = useState(false);
    if (!id) return null;
    async function copy() {
        try {
            await navigator.clipboard.writeText(id as string);
            setCopied(true);
        } catch {
            setCopied(false);
        }
    }
    return (
        <section className="card id-card">
            <p className="muted">Ваш Telegram ID</p>
            <p className="id-value" aria-label="Ваш Telegram ID">
                {id}
            </p>
            <button className="primary" onClick={() => void copy()}>
                {copied ? "Скопировано" : "Скопировать ID"}
            </button>
        </section>
    );
}

function App() {
    const api = useRef(new CabinetAPI());
    const [loginAttempt] = useState(() => loginAttemptFromFragment(window.location.hash));
    const [tab, setTab] = useState<Tab>("home");
    const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
    const [error, setError] = useState("");
    const [sessionGone, setSessionGone] = useState(false);
    const [noAccess, setNoAccess] = useState(false);
    const [notice, setNotice] = useState("");
    const [busy, setBusy] = useState(true);
    const [signedIn, setSignedIn] = useState(false);
    const [closed, setClosed] = useState(false);
    const [light, setLight] = useState(window.Telegram?.WebApp.colorScheme === "light");
    const [name] = useState(displayName);
    const active = useRef(true);
    const lock = useRef(false);
    useEffect(() => {
        window.scrollTo({ top: 0 });
    }, [tab]);

    function fail(e: unknown) {
        setError(message(e));
        if (isNoAccess(e)) setNoAccess(true);
        if (e instanceof ApiError && SESSION_GONE.has(e.message)) setSessionGone(true);
    }

    async function load() {
        const [quota, rules, history, devices, referrals] = await Promise.all([
            api.current.request("/api/v1/quota"),
            api.current.request("/api/v1/rewards/rules"),
            api.current.request("/api/v1/quota/history?limit=20"),
            api.current.request("/api/v1/devices?limit=100"),
            api.current.request("/api/v1/referrals"),
        ]);
        if (active.current)
            setSnapshot({
                quota,
                referrals,
                rules: rules.items,
                history: history.items,
                devices,
                time: new Date().toISOString(),
            });
    }
    useEffect(() => {
        const tg = window.Telegram?.WebApp;
        const theme = () => setLight(tg?.colorScheme === "light");
        tg?.ready();
        tg?.expand();
        tg?.onEvent("themeChanged", theme);
        async function start() {
            if (!tg?.initData) {
                setError("Откройте кабинет кнопкой Mini App в Telegram-боте.");
                setBusy(false);
                return;
            }
            try {
                if (loginAttempt.present) {
                    window.history.replaceState(null, "", window.location.pathname + window.location.search);
                    if (!loginAttempt.requestId) {
                        throw new ApiError("invalid_login_attempt", 400);
                    }
                    const result = await api.current.requestLoginCode(tg.initData, loginAttempt.requestId);
                    if (active.current) {
                        setNotice(
                            result.status === "rejected"
                                ? "Код не выдан. Проверьте вход и приглашение в закрытый тест; при необходимости начните новую попытку в Android-приложении."
                                : result.status === "delivery_unknown"
                                  ? "Запрос обработан, но доставку кода подтвердить не удалось. Проверьте чат с ботом; если кода нет, начните новый вход в Android позже."
                                  : "Проверьте личный чат с ботом и введите код в Android-приложении. Не пересылайте его и не вводите в Mini App.",
                        );
                    }
                    if (active.current && result.status === "rejected") setNoAccess(true);
                    return;
                }
                await api.current.login(tg.initData);
                if (active.current) setSignedIn(true);
                await load();
            } catch (e) {
                if (active.current) fail(e);
            } finally {
                if (active.current) setBusy(false);
            }
        }
        void start();
        return () => {
            active.current = false;
            tg?.offEvent("themeChanged", theme);
        };
    }, []);
    async function action(operation: () => Promise<void>) {
        if (lock.current) return;
        lock.current = true;
        setBusy(true);
        setError("");
        setNotice("");
        try {
            await operation();
        } catch (e) {
            if (active.current) fail(e);
        } finally {
            lock.current = false;
            if (active.current) setBusy(false);
        }
    }
    const quota = snapshot?.quota;
    const daily = snapshot?.rules.find((r) => r.reward_type === "daily");
    async function claim(rule: Rule) {
        await action(async () => {
            const receipt = await api.current.claim(rule.id);
            setNotice(`Начислено ${bytes(receipt.reward_bytes)}. Награда подтверждена сервером.`);
            await load();
        });
    }
    const referral: ReferralActions = {
        createCode: () =>
            void action(async () => {
                await api.current.request("/api/v1/referrals/code", {});
                await load();
            }),
        bind: (code, campaign, done) =>
            void action(async () => {
                await api.current.referral("resolve", { code, campaign_id: campaign }, `${campaign}:${code}`);
                done();
                setNotice("Приглашение закреплено. Бонус получит пригласивший после выполнения условий.");
                await load();
            }),
        claim: (id) =>
            void action(async () => {
                await api.current.referral("claim", { referral_id: id }, id);
                setNotice("Реферальный бонус начислен.");
                await load();
            }),
        more: () =>
            void action(async () => {
                const before = snapshot?.referrals.next_before_id;
                if (!before) return;
                const page = await api.current.request(`/api/v1/referrals?before_id=${before}`);
                if (active.current)
                    setSnapshot((previous) =>
                        previous
                            ? {
                                  ...previous,
                                  referrals: { ...page, items: [...previous.referrals.items, ...page.items] },
                              }
                            : previous,
                    );
            }),
    };
    function logout() {
        void action(async () => {
            try {
                await api.current.logout();
                setNotice("Сессия отозвана сервером.");
            } catch {
                setError(
                    "Данные кабинета очищены. Отзыв сессии не подтверждён из-за ошибки сети. Серверная сессия истечёт в течение часа.",
                );
            } finally {
                active.current = false;
                setSnapshot(null);
                setSignedIn(false);
                setClosed(true);
                setBusy(false);
            }
        });
    }

    const banners = (
        <>
            {error && !(sessionGone && !snapshot) && (
                <div className="status error" role="alert">
                    <Icon name="alert" />
                    <span>
                        {error}
                        {snapshot && <small>Показан предыдущий снимок. Данные могли измениться.</small>}
                    </span>
                </div>
            )}
            {notice && (
                <div className="status" role="status">
                    <Icon name="info" />
                    <span>{notice}</span>
                </div>
            )}
        </>
    );

    if (loginAttempt.present) {
        return (
            <Shell light={light} onTheme={() => setLight(!light)} tab={null} setTab={setTab}>
                <Orbit>
                    <h1 className="orbit-title">Код входа</h1>
                </Orbit>
                {busy && (
                    <p role="status" className="muted center">
                        Запрашиваем код у сервера…
                    </p>
                )}
                {banners}
                <section className="card">
                    <p>Код приходит в личный чат с ботом. Введите его только в Android-приложении.</p>
                    <p className="muted">Mini App не получает Android-сессию и не управляет VPN.</p>
                </section>
                {noAccess && (
                    <>
                        <p className="muted center">
                            Если вас ещё не добавили в закрытую бету, отправьте этот ID тому, кто вас пригласил.
                        </p>
                        <MyId />
                    </>
                )}
            </Shell>
        );
    }

    let content: React.ReactNode;
    if (noAccess && !snapshot) {
        content = (
            <>
                <StateView
                    icon="info"
                    title="Доступ пока не открыт"
                    text="DetourVPN сейчас работает как закрытый тест. Отправьте ваш Telegram ID тому, кто вас пригласил, и после добавления откройте Mini App снова."
                />
                <MyId />
            </>
        );
    } else if (closed) {
        content = (
            <StateView
                icon="logout"
                title="Вы вышли из кабинета"
                text="Закройте и откройте Mini App для нового входа."
            />
        );
    } else if (sessionGone && !snapshot) {
        const tg = window.Telegram?.WebApp;
        content = (
            <StateView
                icon="alert"
                tone="error"
                title="Сессия истекла"
                text={error || "Откройте Mini App заново."}
                action={
                    tg?.close && (
                        <button className="primary" onClick={() => tg.close?.()}>
                            Закрыть Mini App
                        </button>
                    )
                }
            />
        );
    } else if (busy && !snapshot && signedIn) {
        content = <Skeleton />;
    } else if (tab === "home") {
        content = (
            <Home
                name={name}
                quota={quota}
                daily={daily}
                devicesCount={snapshot ? snapshot.devices.length : null}
                go={setTab}
            />
        );
    } else if (tab === "rewards") {
        content = <Bonuses snapshot={snapshot} busy={busy} onClaim={(r) => void claim(r)} referral={referral} />;
    } else {
        content = (
            <Profile
                history={snapshot ? snapshot.history : null}
                devices={
                    snapshot && (
                        <Devices
                            api={api.current}
                            devices={snapshot.devices}
                            busy={busy}
                            run={action}
                            reload={load}
                            notify={setNotice}
                        />
                    )
                }
            />
        );
    }

    return (
        <Shell light={light} onTheme={() => setLight(!light)} tab={closed || (sessionGone && !snapshot) ? null : tab} setTab={setTab}>
            {banners}
            <div className="screen" key={closed ? "closed" : sessionGone && !snapshot ? "gone" : busy && !snapshot && signedIn ? "loading" : tab}>
                {content}
            </div>
            {!closed && snapshot && <p className="timestamp">Снимок · {date(snapshot.time)}</p>}
            {signedIn && !closed && (
                <div className="actions">
                    <button className="secondary" disabled={busy} onClick={() => void action(load)}>
                        <Icon name="refresh" />
                        Обновить
                    </button>
                    <button className="text-button" disabled={busy} onClick={logout}>
                        Выйти
                    </button>
                </div>
            )}
        </Shell>
    );
}

const root = document.getElementById("root");
if (root) createRoot(root).render(<App />);
