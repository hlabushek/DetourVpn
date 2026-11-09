import assert from "node:assert/strict";
import test from "node:test";
import { ago, errorText, formatBytes, inviteMessage, parseAmount } from "../static/format.js";

test("formatBytes is exact for int64 strings and uses binary units", () => {
    assert.equal(formatBytes("0"), "0 Б");
    assert.equal(formatBytes("1023"), "1023 Б");
    assert.equal(formatBytes("5368709120"), "5 ГиБ");
    assert.equal(formatBytes("1610612736"), "1,5 ГиБ");
    assert.equal(formatBytes("9223372036854775807"), "8388607,99 ТиБ");
    assert.equal(formatBytes("-1048576"), "-1 МиБ");
    assert.equal(formatBytes(null), "—");
    assert.equal(formatBytes("12.5"), "—");
});

test("parseAmount returns exact bytes and rejects bad input", () => {
    assert.equal(parseAmount("5", "gib"), "5368709120");
    assert.equal(parseAmount("1,5", "gib"), "1610612736");
    assert.equal(parseAmount("1.5", "gib"), "1610612736");
    assert.equal(parseAmount("100", "mib"), "104857600");
    for (const bad of ["", "0", "0,000", "-1", "1e3", "1,2345", "abc", "1 2", "9999999999"]) {
        assert.throws(() => parseAmount(bad, "gib"), /invalid_amount/, bad);
    }
    assert.throws(() => parseAmount("1", "tib"), /invalid_amount/);
});

test("ago never goes negative and handles missing time", () => {
    const now = Date.parse("2026-11-10T09:00:00Z");
    assert.equal(ago("2026-11-10T08:59:30Z", now), "30 с назад");
    assert.equal(ago("2026-11-10T08:30:00Z", now), "30 мин назад");
    assert.equal(ago("2026-11-10T06:00:00Z", now), "3 ч назад");
    assert.equal(ago("2026-11-07T09:00:00Z", now), "3 дн назад");
    assert.equal(ago("2026-11-10T09:05:00Z", now), "0 с назад");
    assert.equal(ago(null, now), "нет данных");
});

test("invite message links only a valid bot username and contains no personal data", () => {
    assert.match(inviteMessage("DetourVpnBot"), /https:\/\/t\.me\/DetourVpnBot/);
    assert.doesNotMatch(inviteMessage("bad name!"), /t\.me/);
    assert.doesNotMatch(inviteMessage(""), /t\.me/);
});

test("errors have plain Russian text with a fallback", () => {
    assert.match(errorText("beta_invite_exists"), /уже приглашён/);
    assert.match(errorText("something_new"), /something_new/);
});
