import React from "react";
import { BalanceCard, RowButton } from "../components/Cards";
import { Icon } from "../components/Icon";
import type { Quota, Rule, Tab } from "../types";

function devicesText(count: number | null) {
    if (count === null) return "После входа";
    const n = count % 10;
    const word =
        n === 1 && count % 100 !== 11
            ? "активное устройство"
            : n >= 2 && n <= 4 && (count % 100 < 12 || count % 100 > 14)
              ? "активных устройства"
              : "активных устройств";
    return `${count}${count === 100 ? "+" : ""} ${word}`;
}

/** Home: greeting, the only balance card, and entry points. Claims live in Bonuses. */
export function Home({
    name,
    quota,
    daily,
    devicesCount,
    go,
}: {
    name: string | null;
    quota?: Quota;
    daily?: Rule;
    devicesCount: number | null;
    go: (tab: Tab) => void;
}) {
    return (
        <>
            <div className="greeting">
                <span className="avatar">
                    <Icon name="user" />
                </span>
                <div>
                    <h1>{name ? `Привет, ${name}!` : "Привет!"}</h1>
                    <p className="muted">Рады видеть вас снова</p>
                </div>
            </div>
            <BalanceCard quota={quota} />
            <div className="rows">
                <RowButton
                    icon="gift"
                    title="Бонусы и приглашения"
                    subtitle={daily?.eligible ? "Ежедневный бонус уже доступен" : "Дополнительный трафик и награды"}
                    badge={daily?.eligible}
                    onClick={() => go("rewards")}
                />
                <RowButton
                    icon="phone"
                    title="Мои устройства"
                    subtitle={devicesText(devicesCount)}
                    onClick={() => go("profile")}
                />
            </div>
            <p className="quiet">
                <span className="dot" />
                VPN включается в Android-приложении
            </p>
        </>
    );
}
