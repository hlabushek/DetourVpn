import React from "react";
import { bytes } from "../api.mjs";
import { Icon } from "../components/Icon";
import { date, type Entry } from "../types";

function entryView(e: Entry) {
    if (e.source_type === "reward") return { title: "Бонус", icon: "gift", tone: "accent" };
    if (e.source_type === "registration") return { title: "Стартовый трафик", icon: "plus", tone: "success" };
    return e.kind === "debit"
        ? { title: "Списание", icon: "minus", tone: "danger" }
        : { title: "Начисление", icon: "plus", tone: "success" };
}

/** Devices (slot), recent operations and session actions. */
export function Profile({
    history,
    devices,
}: {
    history: Entry[] | null;
    devices: React.ReactNode;
}) {
    return (
        <>
            <div className="screen-header">
                <span className="header-icon">
                    <Icon name="phone" />
                </span>
                <div>
                    <h1>Мои устройства</h1>
                    <p className="muted">Подключённые устройства и их трафик</p>
                </div>
            </div>
            {devices}
            <h2 className="section-title">Последние операции</h2>
            <section className="card history">
                {history?.map((e) => {
                    const v = entryView(e);
                    return (
                        <div className="entry" key={e.id}>
                            <span className={`tile tone-${v.tone}`}>
                                <Icon name={v.icon} />
                            </span>
                            <div>
                                <b>{v.title}</b>
                                <small className="muted">{date(e.created_at)}</small>
                            </div>
                            <strong className={`tone-${v.tone}`}>
                                {e.kind === "credit" ? "+" : ""}
                                {bytes(e.delta_bytes)}
                            </strong>
                        </div>
                    );
                })}
                {history && !history.length && <p className="muted">Операций пока нет.</p>}
                <small className="muted">Последние 20 операций. Полная история — в приложении.</small>
            </section>
        </>
    );
}
