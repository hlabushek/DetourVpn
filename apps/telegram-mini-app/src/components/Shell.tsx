import React, { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import type { Tab } from "../types";

const TABS = [
    ["home", "home", "Главная"],
    ["rewards", "gift", "Бонусы"],
    ["profile", "user", "Профиль"],
] as const;

/** Header with wordmark and theme switch, and the floating glass navigation. */
export function Shell({
    light,
    onTheme,
    tab,
    setTab,
    children,
}: {
    light: boolean;
    onTheme: () => void;
    tab: Tab | null;
    setTab: (tab: Tab) => void;
    children: React.ReactNode;
}) {
    const index = Math.max(0, TABS.findIndex(([id]) => id === tab));
    // Re-keying the lens body replays the short "liquid" stretch on every move.
    const moves = useRef(0);
    const [move, setMove] = useState(0);
    useEffect(() => {
        moves.current += 1;
        setMove(moves.current);
    }, [index]);
    return (
        <div className={light ? "app light" : "app"}>
            <main className={tab ? "with-nav" : ""}>
                <header>
                    <a
                        className="logo"
                        href="#"
                        onClick={(e) => {
                            e.preventDefault();
                            if (tab) setTab("home");
                        }}
                        aria-label="DetourVpn, главная"
                    >
                        Detour<span>Vpn</span>
                    </a>
                    <button className="icon-button" aria-label="Сменить тему" onClick={onTheme}>
                        <span className="theme-icon" key={light ? "moon" : "sun"}>
                            <Icon name={light ? "moon" : "sun"} />
                        </span>
                    </button>
                </header>
                {children}
            </main>
            {tab && (
                <nav aria-label="Разделы кабинета" className="glass-nav">
                    <span className="nav-lens" style={{ transform: `translateX(${index * 100}%)` }} aria-hidden="true">
                        <span className={move > 1 ? "lens-body moving" : "lens-body"} key={move} />
                    </span>
                    {TABS.map(([id, icon, label]) => (
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
            )}
        </div>
    );
}
