import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { CabinetAPI, ApiError, bytes, loginAttemptFromFragment } from "./api.mjs";
import "./style.css";

type Rule = {
    id: string;
    reward_type: string;
    reward_bytes: string;
    eligible: boolean;
    claimed_today: string;
    daily_cap_claims: string;
    next_claim_at: string;
    ends_at: string;
};
type Quota = {
    available_bytes: string;
    granted_bytes: string;
    used_bytes: string;
    reserved_bytes: string;
};
type Entry = {
    id: string;
    kind: string;
    delta_bytes: string;
    source_type: string;
    created_at: string;
};
type Device = { id: string; platform: string; created_at: string };
type ReferralState = {
    code: string | null; bound: boolean; invited: string; rewarded: string;
    campaigns: {id: string; reward_bytes: string; inviter_cap: string; ends_at: string}[];
    items: {id: string; created_at: string; rewarded: boolean; reward_bytes: string}[];
    next_before_id: string | null;
};
type Snapshot = {
    referrals: ReferralState;
    quota: Quota;
    rules: Rule[];
    history: Entry[];
    devices: Device[];
    time: string;
};
type WebApp = {
    initData: string;
    colorScheme: string;
    ready: () => void;
    expand: () => void;
    onEvent: (event: string, handler: () => void) => void;
    offEvent: (event: string, handler: () => void) => void;
};
declare global {
    interface Window {
        Telegram?: { WebApp: WebApp };
    }
}

const icons: Record<string, React.ReactNode> = {
    home: (
        <>
            <path d="m3 11 9-8 9 8" />
            <path d="M5 10v11h5v-7h4v7h5V10" />
        </>
    ),
    gift: (
        <>
            <rect x="3" y="8" width="18" height="13" rx="2" />
            <path d="M12 8v13M3 12h18" />
            <path d="M12 8C3 8 6 0 10 4l2 4c9 0 6-8 2-4z" />
        </>
    ),
    user: (
        <>
            <circle cx="12" cy="7" r="4" />
            <path d="M4 22v-3a8 8 0 0 1 16 0v3z" />
        </>
    ),
    arrow: <path d="m9 5 7 7-7 7" />,
    phone: (
        <>
            <rect x="6" y="2" width="12" height="20" rx="3" />
            <path d="M10 18h4" />
        </>
    ),
    wave: (
        <>
            <path d="M4 15v6M10 8v13M16 3v18M22 11v10" />
        </>
    ),
};
function Icon({ name }: { name: string }) {
    return (
        <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
        >
            {icons[name]}
        </svg>
    );
}
const date = (v: string) => new Date(v).toLocaleString("ru-RU");
function message(error: unknown) {
    if (error instanceof ApiError) {
        const messages: Record<string, string> = {
            referral_self_invitation: "Свой код использовать нельзя.",
            referral_binding_conflict: "Пригласивший уже закреплён. Сменить его нельзя.",
            referral_binding_ineligible: "Код доступен новым аккаунтам до первого подключения, в течение 7 суток. Пригласивший должен зарегистрироваться раньше.",
            referral_code_unavailable: "Код недоступен. Проверьте его у пригласившего.",
            referral_campaign_unavailable: "Кампания завершена или приостановлена.",
            referral_not_qualified: "Приглашение ещё не подходит для бонуса. Нужен завершённый VPN-сеанс приглашённого с подтверждённым трафиком.",
            referral_inviter_limit: "Лимит бонусов этой кампании исчерпан.",
            referral_budget_exhausted: "Бюджет кампании исчерпан. Обратитесь в поддержку при несогласии.",
            referral_idempotency_conflict: "Повтор запроса конфликтует с предыдущим. Обновите историю.",
            invalid_init_data:
                "Не удалось подтвердить вход. Закройте и откройте Mini App заново.",
            init_data_replayed:
                "Этот вход уже использован. Откройте Mini App заново.",
            invalid_login_attempt:
                "Попытка входа недействительна. Начните вход заново в Android-приложении.",
            mini_app_disabled: "Кабинет ещё не включён на сервере.",
            unauthorized: "Сессия истекла. Откройте Mini App заново.",
            invalid_refresh: "Сессия истекла. Откройте Mini App заново.",
            refresh_reused: "Сессия отозвана. Откройте Mini App заново.",
            reward_cooldown:
                "Награда пока недоступна. Обновите кабинет чуть позже.",
            reward_limit_reached:
                "Лимит награды достигнут. Следующее обновление — по UTC.",
            rate_limited: "Слишком много запросов. Попробуйте через минуту.",
        };
        return (
            messages[error.message] ??
            "Сервер не смог завершить запрос. Попробуйте ещё раз."
        );
    }
    return "Ответ не получен. Повтор награды использует тот же ключ, пока кабинет открыт.";
}

function App() {
    const api = useRef(new CabinetAPI());
    const [loginAttempt] = useState(() => loginAttemptFromFragment(window.location.hash));
    const [tab, setTab] = useState("home");
    const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [busy, setBusy] = useState(true);
    const [signedIn, setSignedIn] = useState(false);
    const [closed, setClosed] = useState(false);
    const [light, setLight] = useState(
        window.Telegram?.WebApp.colorScheme === "light",
    );
    const active = useRef(true);
    const lock = useRef(false);
    useEffect(() => {
        window.scrollTo({ top: 0 });
    }, [tab]);

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
                    window.history.replaceState(
                        null,
                        "",
                        window.location.pathname + window.location.search,
                    );
                    if (!loginAttempt.requestId) {
                        throw new ApiError("invalid_login_attempt", 400);
                    }
                    const result = await api.current.requestLoginCode(
                        tg.initData,
                        loginAttempt.requestId,
                    );
                    if (active.current) {
                        setNotice(
                            result.status === "rejected"
                                ? "Код не выдан. Проверьте вход и приглашение в закрытый тест; при необходимости начните новую попытку в Android-приложении."
                                : result.status === "delivery_unknown"
                                ? "Запрос обработан, но доставку кода подтвердить не удалось. Проверьте чат с ботом; если кода нет, начните новый вход в Android позже."
                                : "Проверьте личный чат с ботом и введите код в Android-приложении. Не пересылайте его и не вводите в Mini App.",
                        );
                    }
                    return;
                }
                await api.current.login(tg.initData);
                if (active.current) setSignedIn(true);
                await load();
            } catch (e) {
                if (active.current) setError(message(e));
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
            if (active.current) setError(message(e));
        } finally {
            lock.current = false;
            if (active.current) setBusy(false);
        }
    }
    const [inviteCode, setInviteCode] = useState("");
    const [campaignId, setCampaignId] = useState("");
    const quota = snapshot?.quota;
    const daily = snapshot?.rules.find((r) => r.reward_type === "daily");
    async function claim(rule: Rule) {
        await action(async () => {
            const receipt = await api.current.claim(rule.id);
            setNotice(
                `Начислено ${bytes(receipt.reward_bytes)}. Награда подтверждена сервером.`,
            );
            await load();
        });
    }
    const reward = (r: Rule) => (
        <section className="reward card" key={r.id}>
            <span className="tile">
                <Icon name="gift" />
            </span>
            <div className="reward-copy">
                <p>
                    {r.reward_type === "daily"
                        ? "Ежедневный бонус"
                        : "Орбитальный кликер"}
                </p>
                <strong>+{bytes(r.reward_bytes)}</strong>
                <small>
                    {r.claimed_today} из {r.daily_cap_claims} сегодня · UTC
                </small>
                {!r.eligible && (
                    <small>Доступность: {date(r.next_claim_at)}</small>
                )}
            </div>
            <button
                className="primary"
                disabled={busy || !r.eligible}
                onClick={() => void claim(r)}
            >
                {r.eligible ? "Забрать" : "Позже"}
            </button>
        </section>
    );
    if (loginAttempt.present) {
        return (
            <div className={light ? "app light" : "app"}>
                <div className="orbit orbit-one" aria-hidden="true" />
                <div className="orbit orbit-two" aria-hidden="true" />
                <main>
                    <header>Detour<span>Vpn</span></header>
                    <div className="eyebrow">ВХОД В ANDROID-ПРИЛОЖЕНИЕ</div>
                    <section className="card">
                        <h1>Код входа</h1>
                        {busy && <p role="status">Запрашиваем код у сервера…</p>}
                        {error && <div className="status error" role="alert">{error}</div>}
                        {notice && <div className="status" role="status">{notice}</div>}
                        <p>Код приходит в личный чат с ботом. Введите его только в Android-приложении.</p>
                        <p>Mini App не получает Android-сессию и не управляет VPN.</p>
                    </section>
                    <footer>DetourVpn · Безопасный вход</footer>
                </main>
            </div>
        );
    }
    return (
        <div className={light ? "app light" : "app"}>
            <div className="orbit orbit-one" aria-hidden="true" />
            <div className="orbit orbit-two" aria-hidden="true" />
            <main>
                <header>
                    <a
                        href="#"
                        onClick={(e) => {
                            e.preventDefault();
                            setTab("home");
                        }}
                        aria-label="DetourVpn, главная"
                    >
                        Detour<span>Vpn</span>
                    </a>
                    <button
                        className="icon-button"
                        aria-label="Сменить тему"
                        onClick={() => setLight(!light)}
                    >
                        {light ? "☾" : "☼"}
                    </button>
                </header>
                <div className="eyebrow">ВАШ ЛИЧНЫЙ КАБИНЕТ</div>
                <div className="greeting">
                    <span className="avatar">
                        <Icon name="user" />
                    </span>
                    <div>
                        <h1>
                            {tab === "home"
                                ? "Рады видеть вас"
                                : tab === "rewards"
                                  ? "Больше свободы"
                                  : "Ваш профиль"}
                        </h1>
                        <p>
                            {tab === "rewards"
                                ? "Награды за активность"
                                : "Всё важное — на одной орбите"}
                        </p>
                    </div>
                </div>
                {error && (
                    <div className="status error" role="alert">
                        {error}
                        {snapshot && (
                            <small>
                                Показан предыдущий снимок. Данные могли
                                измениться.
                            </small>
                        )}
                    </div>
                )}
                {notice && (
                    <div className="status" role="status">
                        {notice}
                    </div>
                )}
                {busy && (
                    <p role="status" className="loading">
                        Сверяем данные с сервером…
                    </p>
                )}
                {closed && (
                    <section className="card">
                        <h2>Вы вышли из кабинета</h2>
                        <p>Закройте и откройте Mini App для нового входа.</p>
                    </section>
                )}
                {!closed && (
                    <>
                        <section className="balance card">
                            <div className="balance-top">
                                <p>Доступный трафик</p>
                                <Icon name="wave" />
                            </div>
                            <strong className="amount">
                                {quota ? bytes(quota.available_bytes) : "—"}
                            </strong>
                            <p className="exact">
                                {quota
                                    ? `${BigInt(quota.available_bytes).toLocaleString("ru-RU")} байт`
                                    : "Баланс появится после подтверждения входа"}
                            </p>
                            <div
                                className="meter"
                                role="img"
                                aria-label={
                                    quota
                                        ? `Доступно ${bytes(quota.available_bytes)} из начисленных ${bytes(quota.granted_bytes)}`
                                        : "Баланс не загружен"
                                }
                            >
                                <span
                                    style={{
                                        width:
                                            quota &&
                                            BigInt(quota.granted_bytes) > 0n
                                                ? `${(BigInt(quota.available_bytes) * 100n) / BigInt(quota.granted_bytes)}%`
                                                : "0%",
                                    }}
                                />
                            </div>
                            {quota && (
                                <div className="balance-bottom">
                                    <span>
                                        Начислено {bytes(quota.granted_bytes)}
                                    </span>
                                    <span>
                                        Резерв {bytes(quota.reserved_bytes)}
                                    </span>
                                </div>
                            )}
                        </section>
                        {tab === "home" && (
                            <>
                                {daily
                                    ? reward(daily)
                                    : snapshot && (
                                          <section className="card">
                                              <h2>Бонусы готовятся</h2>
                                              <p>
                                                  Сейчас сервер не предлагает
                                                  ежедневную награду.
                                              </p>
                                          </section>
                                      )}
                                <section className="card menu">
                                    <button onClick={() => setTab("rewards")}>
                                        <span className="tile">
                                            <Icon name="gift" />
                                        </span>
                                        <span>
                                            <b>Бонусы и кликер</b>
                                            <small>
                                                Трафик по правилам сервера
                                            </small>
                                        </span>
                                        <Icon name="arrow" />
                                    </button>
                                    <button onClick={() => setTab("profile")}>
                                        <span className="tile">
                                            <Icon name="phone" />
                                        </span>
                                        <span>
                                            <b>Мои устройства</b>
                                            <small>
                                                {snapshot
                                                    ? `${snapshot.devices.length}${snapshot.devices.length === 100 ? "+" : ""} активных устройств`
                                                    : "После входа"}
                                            </small>
                                        </span>
                                        <Icon name="arrow" />
                                    </button>
                                </section>
                                <section className="quiet">
                                    <span className="dot" />
                                    VPN управляется в Android-приложении
                                </section>
                            </>
                        )}
                        {tab === "rewards" && (
                            <>
                                <h2>Ваша следующая награда</h2>
                                <p className="muted">
                                    Баланс увеличится после подтверждения
                                    сервера. Лимиты обновляются по UTC.
                                </p>
                                {snapshot?.rules.map(reward)}
                                {snapshot && (
                                    <section className="card referral-panel">
                                        <h2>Пригласить друга</h2>
                                        <p>
                                            Бонус получает пригласивший после завершённого VPN-сеанса
                                            друга с подтверждённым трафиком. Нажмите «Проверить бонус»
                                            после сеанса. Это не автоматическое начисление.
                                        </p>
                                        <p>
                                            Приглашено: {snapshot.referrals.invited} · Бонусов:{" "}
                                            {snapshot.referrals.rewarded}
                                        </p>
                                        {snapshot.referrals.code ? (
                                            <p>Ваш код: <code className="invite-code">{snapshot.referrals.code}</code></p>
                                        ) : (
                                            <button disabled={busy} onClick={() => void action(async () => {
                                                await api.current.request("/api/v1/referrals/code", {});
                                                await load();
                                            })}>Получить свой код</button>
                                        )}
                                        {!snapshot.referrals.campaigns.length && (
                                            <p>Активных реферальных кампаний пока нет.</p>
                                        )}
                                        {snapshot.referrals.campaigns.map(c => (
                                            <p key={c.id}>
                                                Бонус: {bytes(c.reward_bytes)} · до {c.inviter_cap}{" "}
                                                бонусов пригласившему · до {date(c.ends_at)}
                                            </p>
                                        ))}
                                        {!snapshot.referrals.bound && snapshot.referrals.campaigns.length > 0 && (
                                            <form onSubmit={e => {
                                                e.preventDefault();
                                                void action(async () => {
                                                    const campaign = campaignId || snapshot.referrals.campaigns[0].id;
                                                    const code = inviteCode.trim();
                                                    await api.current.referral("resolve", {
                                                        code, campaign_id: campaign,
                                                    }, `${campaign}:${code}`);
                                                    setInviteCode("");
                                                    setNotice("Приглашение закреплено. Бонус получит пригласивший после выполнения условий.");
                                                    await load();
                                                });
                                            }}>
                                                <label>
                                                    Кампания
                                                    <select
                                                        value={campaignId || snapshot.referrals.campaigns[0].id}
                                                        onChange={e => setCampaignId(e.target.value)}
                                                        disabled={busy}
                                                    >
                                                        {snapshot.referrals.campaigns.map(c => (
                                                            <option key={c.id} value={c.id}>
                                                                {bytes(c.reward_bytes)} · до {date(c.ends_at)}
                                                            </option>
                                                        ))}
                                                    </select>
                                                </label>
                                                <label>
                                                    Код пригласившего
                                                    <input
                                                        value={inviteCode}
                                                        onChange={e => setInviteCode(e.target.value)}
                                                        pattern="[A-Za-z0-9_-]{24}"
                                                        maxLength={24}
                                                        required
                                                        autoComplete="off"
                                                        disabled={busy}
                                                    />
                                                </label>
                                                <p>
                                                    Только в первые 7 суток и до первого VPN-подключения.
                                                    Пригласивший должен зарегистрироваться раньше.
                                                    Код закрепляется навсегда.
                                                </p>
                                                <button type="submit" disabled={busy}>Закрепить приглашение</button>
                                            </form>
                                        )}
                                        {snapshot.referrals.bound && <p>Ваше приглашение уже закреплено.</p>}
                                        {snapshot.referrals.items.map(r => (
                                            <div className="referral-item" key={r.id}>
                                                <span>
                                                    Приглашение от {date(r.created_at)} · {bytes(r.reward_bytes)} ·{" "}
                                                    {r.rewarded ? "Бонус получен" : "Ожидает проверки"}
                                                </span>
                                                {!r.rewarded && (
                                                    <button disabled={busy} onClick={() => void action(async () => {
                                                        await api.current.referral("claim", {referral_id: r.id}, r.id);
                                                        setNotice("Реферальный бонус начислен.");
                                                        await load();
                                                    })}>Проверить бонус</button>
                                                )}
                                            </div>
                                        ))}
                                        {snapshot.referrals.next_before_id && (
                                            <button disabled={busy} onClick={() => void action(async () => {
                                                const page = await api.current.request(
                                                    `/api/v1/referrals?before_id=${snapshot.referrals.next_before_id}`,
                                                );
                                                if (active.current) setSnapshot(previous => previous ? {
                                                    ...previous,
                                                    referrals: {
                                                        ...page,
                                                        items: [...previous.referrals.items, ...page.items],
                                                    },
                                                } : previous);
                                            })}>Ещё приглашения</button>
                                        )}
                                        <small>
                                            Общий бюджет кампании ограничен. Если проверка отклонена
                                            и вы не согласны, обратитесь в поддержку. Личные данные
                                            друзей здесь не отображаются.
                                        </small>
                                    </section>
                                )}
                                {snapshot && !snapshot.rules.length && (
                                    <section className="card">
                                        Активных наград пока нет.
                                    </section>
                                )}
                            </>
                        )}
                        {tab === "profile" && (
                            <>
                                <h2>Мои устройства</h2>
                                <section className="card">
                                    {snapshot?.devices.map((d) => (
                                        <div className="device" key={d.id}>
                                            <Icon name="phone" />
                                            <div>
                                                <b>
                                                    Android · {d.id.slice(0, 8)}
                                                </b>
                                                <small>
                                                    Добавлено{" "}
                                                    {date(d.created_at)}
                                                </small>
                                            </div>
                                        </div>
                                    ))}
                                    {snapshot && !snapshot.devices.length && (
                                        <p>
                                            Устройств пока нет. Добавьте
                                            устройство через Android-приложение.
                                        </p>
                                    )}
                                </section>
                                <h2>Последние операции</h2>
                                <section className="card history">
                                    {snapshot?.history.map((e) => (
                                        <div className="entry" key={e.id}>
                                            <div>
                                                <b>
                                                    {e.source_type === "reward"
                                                        ? "Бонус"
                                                        : e.source_type ===
                                                            "registration"
                                                          ? "Стартовый трафик"
                                                          : e.kind === "debit"
                                                            ? "Списание"
                                                            : "Начисление"}
                                                </b>
                                                <small>
                                                    {date(e.created_at)}
                                                </small>
                                            </div>
                                            <strong>
                                                {e.kind === "credit" ? "+" : ""}
                                                {bytes(e.delta_bytes)}
                                            </strong>
                                        </div>
                                    ))}
                                    {snapshot && !snapshot.history.length && (
                                        <p>Операций пока нет.</p>
                                    )}
                                    <small>
                                        Последние 20 операций. Полная история —
                                        в приложении.
                                    </small>
                                </section>
                            </>
                        )}
                        {snapshot && (
                            <p className="timestamp">
                                Снимок · {date(snapshot.time)}
                            </p>
                        )}
                        {signedIn && (
                            <div className="actions">
                                <button
                                    className="secondary"
                                    disabled={busy}
                                    onClick={() => void action(load)}
                                >
                                    Обновить
                                </button>
                                <button
                                    className="text-button"
                                    disabled={busy}
                                    onClick={() =>
                                        void action(async () => {
                                            try {
                                                await api.current.logout();
                                                setNotice(
                                                    "Сессия отозвана сервером.",
                                                );
                                            } catch {
                                                setError(
                                                    "Данные кабинета очищены. Отзыв сессии не подтверждён из-за ошибки сети. Серверная сессия истечёт в течение часа.",
                                                );
                                            } finally {
                                                active.current = false;
                                                setSnapshot(null);
                                                setInviteCode("");
                                                setCampaignId("");
                                                setSignedIn(false);
                                                setClosed(true);
                                                setBusy(false);
                                            }
                                        })
                                    }
                                >
                                    Выйти
                                </button>
                            </div>
                        )}
                    </>
                )}
                <footer>DetourVpn · Пространство для вашего интернета</footer>
            </main>
            <nav aria-label="Разделы кабинета">
                {[
                    ["home", "home", "Главная"],
                    ["rewards", "gift", "Бонусы"],
                    ["profile", "user", "Профиль"],
                ].map(([id, icon, label]) => (
                    <button
                        key={id}
                        aria-current={tab === id ? "page" : undefined}
                        className={tab === id ? "selected" : ""}
                        onClick={() => setTab(id)}
                    >
                        <Icon name={icon} />
                        <span>{label}</span>
                    </button>
                ))}
            </nav>
        </div>
    );
}
createRoot(document.getElementById("root")!).render(<App />);
