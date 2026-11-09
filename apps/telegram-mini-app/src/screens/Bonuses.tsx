import React, { useState } from "react";
import { bytes } from "../api.mjs";
import { RewardCard, StateView } from "../components/Cards";
import { Icon } from "../components/Icon";
import { date, type Rule, type Snapshot } from "../types";

export type ReferralActions = {
    createCode: () => void;
    bind: (code: string, campaignId: string, done: () => void) => void;
    claim: (referralId: string) => void;
    more: () => void;
};

/** The one place where rewards and referral bonuses are claimed. */
export function Bonuses({
    snapshot,
    busy,
    onClaim,
    referral,
}: {
    snapshot: Snapshot | null;
    busy: boolean;
    onClaim: (rule: Rule) => void;
    referral: ReferralActions;
}) {
    const [inviteCode, setInviteCode] = useState("");
    const [campaignId, setCampaignId] = useState("");
    const refs = snapshot?.referrals;
    return (
        <>
            <div className="screen-header">
                <span className="header-icon">
                    <Icon name="gift" />
                </span>
                <div>
                    <h1>Бонусы</h1>
                    <p className="muted">Дополнительный трафик и награды</p>
                </div>
            </div>
            {snapshot?.rules.map((r) => (
                <RewardCard key={r.id} rule={r} busy={busy} onClaim={onClaim} />
            ))}
            {snapshot && !snapshot.rules.length && (
                <StateView icon="folder" title="Здесь пока пусто" text="Активных наград пока нет. Загляните позже." />
            )}
            {snapshot && (
                <p className="muted note">Баланс увеличится после подтверждения сервера. Лимиты обновляются по UTC.</p>
            )}
            {refs && (
                <>
                    <h2 className="section-title">Пригласите друзей</h2>
                    <section className="card referral-panel">
                        <p className="muted">
                            Бонус получает пригласивший после завершённого VPN-сеанса друга с подтверждённым
                            трафиком. Нажмите «Проверить бонус» после сеанса — это не автоматическое начисление.
                        </p>
                        {refs.code ? (
                            <div className="code-field">
                                <small className="muted">Ваш код</small>
                                <code className="invite-code">{refs.code}</code>
                            </div>
                        ) : (
                            <button className="secondary" disabled={busy} onClick={referral.createCode}>
                                Получить свой код
                            </button>
                        )}
                        <p className="stats">
                            <Icon name="users" />
                            Приглашено {refs.invited} · Награждено {refs.rewarded}
                        </p>
                        {!refs.campaigns.length && <p className="muted">Активных реферальных кампаний пока нет.</p>}
                        {refs.campaigns.map((c) => (
                            <p className="muted" key={c.id}>
                                Бонус: {bytes(c.reward_bytes)} · до {c.inviter_cap} бонусов пригласившему · до{" "}
                                {date(c.ends_at)}
                            </p>
                        ))}
                        {!refs.bound && refs.campaigns.length > 0 && (
                            <form
                                onSubmit={(e) => {
                                    e.preventDefault();
                                    referral.bind(inviteCode.trim(), campaignId || refs.campaigns[0].id, () => {
                                        setInviteCode("");
                                    });
                                }}
                            >
                                <label>
                                    Кампания
                                    <select
                                        value={campaignId || refs.campaigns[0].id}
                                        onChange={(e) => setCampaignId(e.target.value)}
                                        disabled={busy}
                                    >
                                        {refs.campaigns.map((c) => (
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
                                        onChange={(e) => setInviteCode(e.target.value)}
                                        pattern="[A-Za-z0-9_-]{24}"
                                        maxLength={24}
                                        required
                                        autoComplete="off"
                                        disabled={busy}
                                    />
                                </label>
                                <small className="muted">
                                    Только в первые 7 суток и до первого VPN-подключения. Пригласивший должен
                                    зарегистрироваться раньше. Код закрепляется навсегда.
                                </small>
                                <button type="submit" className="secondary" disabled={busy}>
                                    Закрепить приглашение
                                </button>
                            </form>
                        )}
                        {refs.bound && <p className="muted">Ваше приглашение уже закреплено.</p>}
                        {refs.items.map((r) => (
                            <div className="referral-item" key={r.id}>
                                <span>
                                    <b>{r.rewarded ? "Бонус получен" : "Ожидает проверки"}</b>
                                    <small className="muted">
                                        {date(r.created_at)} · {bytes(r.reward_bytes)}
                                    </small>
                                </span>
                                {!r.rewarded && (
                                    <button className="primary small" disabled={busy} onClick={() => referral.claim(r.id)}>
                                        Проверить бонус
                                    </button>
                                )}
                            </div>
                        ))}
                        {refs.next_before_id && (
                            <button className="text-button" disabled={busy} onClick={referral.more}>
                                Ещё приглашения
                            </button>
                        )}
                        <small className="muted">
                            Общий бюджет кампании ограничен. Если проверка отклонена и вы не согласны, обратитесь в
                            поддержку. Личные данные друзей здесь не отображаются.
                        </small>
                    </section>
                </>
            )}
        </>
    );
}
