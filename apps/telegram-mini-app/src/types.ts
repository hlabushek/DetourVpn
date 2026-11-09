import type { Device } from "./Devices";

// Integers arrive as decimal strings (exactJSON keeps int64 exact).
export type Rule = {
    id: string;
    reward_type: string;
    reward_bytes: string;
    eligible: boolean;
    claimed_today: string;
    daily_cap_claims: string;
    next_claim_at: string;
    ends_at: string;
};
export type Quota = {
    available_bytes: string;
    granted_bytes: string;
    used_bytes: string;
    reserved_bytes: string;
};
export type Entry = {
    id: string;
    kind: string;
    delta_bytes: string;
    source_type: string;
    created_at: string;
};
export type ReferralState = {
    code: string | null;
    bound: boolean;
    invited: string;
    rewarded: string;
    campaigns: { id: string; reward_bytes: string; inviter_cap: string; ends_at: string }[];
    items: { id: string; created_at: string; rewarded: boolean; reward_bytes: string }[];
    next_before_id: string | null;
};
export type Snapshot = {
    referrals: ReferralState;
    quota: Quota;
    rules: Rule[];
    history: Entry[];
    devices: Device[];
    time: string;
};
export type Tab = "home" | "rewards" | "profile";

type WebApp = {
    initData: string;
    /** Display only: the name is never sent to the backend. */
    initDataUnsafe?: { user?: { first_name?: string } };
    colorScheme: string;
    ready: () => void;
    expand: () => void;
    close?: () => void;
    onEvent: (event: string, handler: () => void) => void;
    offEvent: (event: string, handler: () => void) => void;
};
declare global {
    interface Window {
        Telegram?: { WebApp: WebApp };
    }
}

export const date = (v: string) => new Date(v).toLocaleString("ru-RU");
