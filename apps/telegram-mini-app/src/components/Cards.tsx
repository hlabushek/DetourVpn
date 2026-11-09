import React from "react";
import { bytes } from "../api.mjs";
import { Icon } from "./Icon";
import { Orbit } from "./Orbit";
import { date, type Quota, type Rule } from "../types";

export function BalanceCard({ quota }: { quota?: Quota }) {
    const percent =
        quota && BigInt(quota.granted_bytes) > 0n
            ? `${(BigInt(quota.available_bytes) * 100n) / BigInt(quota.granted_bytes)}%`
            : "0%";
    return (
        <section className="card balance">
            <p className="muted">Ваш баланс</p>
            <div className="balance-row">
                <strong className="amount">{quota ? bytes(quota.available_bytes) : "—"}</strong>
                {quota && <span className="muted">из {bytes(quota.granted_bytes)}</span>}
            </div>
            <div
                className="meter"
                role="img"
                aria-label={
                    quota
                        ? `Доступно ${bytes(quota.available_bytes)} из начисленных ${bytes(quota.granted_bytes)}`
                        : "Баланс не загружен"
                }
            >
                <span style={{ width: percent }} />
            </div>
            <small className="muted">
                {quota
                    ? `${BigInt(quota.available_bytes).toLocaleString("ru-RU")} байт`
                    : "Баланс появится после подтверждения входа"}
            </small>
        </section>
    );
}

/** Card row: icon tile, title, subtitle, chevron. */
export function RowButton({
    icon,
    title,
    subtitle,
    onClick,
    badge,
}: {
    icon: string;
    title: string;
    subtitle: string;
    onClick?: () => void;
    badge?: boolean;
}) {
    return (
        <button className="row-card" onClick={onClick}>
            <span className="tile">
                <Icon name={icon} />
                {badge && <span className="tile-dot" aria-hidden="true" />}
            </span>
            <span className="row-text">
                <b>{title}</b>
                <small>{subtitle}</small>
            </span>
            <span className="chevron">
                <Icon name="arrow" />
            </span>
        </button>
    );
}

export function RewardCard({
    rule,
    busy,
    onClaim,
}: {
    rule: Rule;
    busy: boolean;
    onClaim: (rule: Rule) => void;
}) {
    const daily = rule.reward_type === "daily";
    return (
        <section className={`card reward ${daily ? "daily" : ""}`}>
            <span className="tile">
                <Icon name={daily ? "gift" : "rocket"} />
            </span>
            <div className="reward-copy">
                <b>{daily ? "Ежедневный бонус" : "Орбитальный кликер"}</b>
                <small>
                    +{bytes(rule.reward_bytes)} · {rule.claimed_today} из {rule.daily_cap_claims} сегодня · UTC
                </small>
                {!rule.eligible && <small>Доступно: {date(rule.next_claim_at)}</small>}
            </div>
            <button
                className={rule.eligible ? "primary small" : "secondary small"}
                disabled={busy || !rule.eligible}
                onClick={() => onClaim(rule)}
            >
                {rule.eligible ? "Забрать" : "Позже"}
            </button>
        </section>
    );
}

export function StateView({
    icon,
    title,
    text,
    tone = "idle",
    action,
}: {
    icon: string;
    title: string;
    text: string;
    tone?: "idle" | "error";
    action?: React.ReactNode;
}) {
    return (
        <section className="state-view">
            <Orbit tone={tone}>
                <span className={`state-icon ${tone}`}>
                    <Icon name={icon} />
                </span>
            </Orbit>
            <h2>{title}</h2>
            <p className="muted">{text}</p>
            {action}
        </section>
    );
}

export function Skeleton() {
    return (
        <div className="skeleton" role="status" aria-label="Сверяем данные с сервером…">
            <div className="sk-row">
                <span className="sk sk-circle" />
                <span className="sk-lines">
                    <span className="sk sk-line" />
                    <span className="sk sk-line short" />
                </span>
            </div>
            <span className="sk sk-card" />
            <span className="sk sk-row-card" />
            <span className="sk sk-row-card" />
            <span className="sk sk-row-card" />
        </div>
    );
}
