// DEV ONLY preview of the Soft Orbit screens on MOCK data. Served by `npm run dev`
// at /mini-app/preview.html; not a Vite build input, so it never reaches dist/.
// Names, amounts, codes and devices below are demo values, not real accounts.
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import type { CabinetAPI } from "../api.mjs";
import { Devices, type Device } from "../Devices";
import { Skeleton, StateView } from "../components/Cards";
import { Orbit } from "../components/Orbit";
import { Shell } from "../components/Shell";
import { Bonuses } from "../screens/Bonuses";
import { Home } from "../screens/Home";
import { Profile } from "../screens/Profile";
import type { Snapshot, Tab } from "../types";
import "../style.css";

const GB = 1_000_000_000;
const now = Date.now();
const iso = (offsetMs: number) => new Date(now + offsetMs).toISOString();

const MOCK_DEVICES: Device[] = [
    {
        id: "mock-0001-device",
        platform: "android",
        created_at: iso(-27 * 864e5),
        role: "owner",
        label: "Pixel (mock)",
        traffic_cap_bytes: String(50 * GB),
        used_bytes_total: String(12.4 * GB),
        used_bytes_30d: String(3.4 * GB),
        last_connected_at: iso(-2 * 36e5),
        last_country_code: "FI",
    },
    {
        id: "mock-0002-device",
        platform: "android",
        created_at: iso(-11 * 864e5),
        role: "guest",
        label: "Телефон друга (mock)",
        traffic_cap_bytes: String(5 * GB),
        used_bytes_total: String(1.2 * GB),
        used_bytes_30d: String(1.2 * GB),
        last_connected_at: iso(-26 * 36e5),
        last_country_code: "DE",
    },
];

const MOCK: Snapshot = {
    quota: {
        available_bytes: String(12.4 * GB),
        granted_bytes: String(50 * GB),
        used_bytes: String(37.6 * GB),
        reserved_bytes: "0",
    },
    rules: [
        {
            id: "mock-daily",
            reward_type: "daily",
            reward_bytes: String(300_000_000),
            eligible: true,
            claimed_today: "0",
            daily_cap_claims: "1",
            next_claim_at: iso(0),
            ends_at: iso(30 * 864e5),
        },
        {
            id: "mock-clicker",
            reward_type: "clicker",
            reward_bytes: String(20_000_000),
            eligible: false,
            claimed_today: "2",
            daily_cap_claims: "5",
            next_claim_at: iso(15 * 6e4),
            ends_at: iso(30 * 864e5),
        },
    ],
    history: [
        { id: "h3", kind: "credit", delta_bytes: String(10 * GB), source_type: "internal", created_at: iso(-3 * 864e5) },
        { id: "h2", kind: "debit", delta_bytes: String(-2.3 * GB), source_type: "peer_window", created_at: iso(-4 * 864e5) },
        { id: "h1", kind: "credit", delta_bytes: String(1 * GB), source_type: "reward", created_at: iso(-5 * 864e5) },
    ],
    devices: MOCK_DEVICES,
    referrals: {
        code: "MOCKMOCKMOCKMOCKMOCKMOCK",
        bound: true,
        invited: "3",
        rewarded: "1",
        campaigns: [],
        items: [{ id: "r1", created_at: iso(-2 * 864e5), rewarded: false, reward_bytes: String(1 * GB) }],
        next_before_id: null,
    },
    time: iso(0),
};

const mockApi = {
    listLinkCodes: async () => [
        { id: "c1", label: "Ноутбук (mock)", traffic_cap_bytes: String(10 * GB), created_at: iso(0), expires_at: iso(6e5) },
    ],
    createLinkCode: async () => ({
        id: "c2",
        code: "MOCK-CODE",
        label: null,
        traffic_cap_bytes: null,
        created_at: iso(0),
        expires_at: new Date(Date.now() + 581_000).toISOString(),
    }),
} as unknown as CabinetAPI;

const run = async (op: () => Promise<void>) => op();
const noop = () => undefined;

function Frame({ title, light, children }: { title: string; light: boolean; children: (tab: Tab) => React.ReactNode }) {
    const [tab, setTab] = useState<Tab>("home");
    return (
        <figure className="preview-frame">
            <div className="preview-screen">
                <Shell light={light} onTheme={noop} tab={tab} setTab={setTab}>
                    {children(tab)}
                </Shell>
            </div>
            <figcaption>{title}</figcaption>
        </figure>
    );
}

function Gallery() {
    const light = new URLSearchParams(window.location.search).has("light");
    const devices = <Devices api={mockApi} devices={MOCK_DEVICES} busy={false} run={run} reload={async () => {}} notify={noop} />;
    const referral = { createCode: noop, bind: noop, claim: noop, more: noop };
    return (
        <div className="preview-gallery">
            <Frame title="Главная" light={light}>
                {() => <Home name="Алексей" quota={MOCK.quota} daily={MOCK.rules[0]} devicesCount={2} go={noop} />}
            </Frame>
            <Frame title="Бонусы" light={light}>
                {() => <Bonuses snapshot={MOCK} busy={false} onClaim={noop} referral={referral} />}
            </Frame>
            <Frame title="Профиль · устройства" light={light}>
                {() => <Profile history={MOCK.history} devices={devices} />}
            </Frame>
            <Frame title="Загрузка" light={light}>
                {() => <Skeleton />}
            </Frame>
            <Frame title="Сессия истекла" light={light}>
                {() => (
                    <StateView
                        icon="alert"
                        tone="error"
                        title="Сессия истекла"
                        text="Откройте Mini App заново."
                        action={<button className="primary">Закрыть Mini App</button>}
                    />
                )}
            </Frame>
            <Frame title="Пусто" light={light}>
                {() => <StateView icon="folder" title="Здесь пока пусто" text="Активных наград пока нет. Загляните позже." />}
            </Frame>
            <Frame title="Код входа" light={light}>
                {() => (
                    <Orbit>
                        <h1 className="orbit-title">Код входа</h1>
                    </Orbit>
                )}
            </Frame>
        </div>
    );
}

const root = document.getElementById("preview-root");
if (root) createRoot(root).render(<Gallery />);
