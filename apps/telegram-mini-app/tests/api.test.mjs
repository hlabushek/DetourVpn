import { test } from "node:test";
import assert from "node:assert/strict";
import { CabinetAPI, exactJSON, bytes } from "../src/api.mjs";

test("int64 remains exact, strings and escaped quotes are not rewritten", () => {
    const result = exactJSON(
        '{"n":9223372036854775807,"s":"12 \\\"3","a":[-17,true,null,1.5]}',
    );
    assert.equal(result.n, "9223372036854775807");
    assert.equal(result.s, '12 \\"3'.replace('\\"', '"'));
    assert.deepEqual(result.a, ["-17", true, null, 1.5]);
    assert.equal(bytes(result.n), "9223372036,8 ГБ");
});

test("unknown claim result retries the same key; success starts a fresh claim", async () => {
    const sent = [];
    let fail = true;
    const api = new CabinetAPI(async (path, options) => {
        sent.push(JSON.parse(options.body));
        assert.equal(options.redirect, "error");
        assert.equal(options.credentials, "omit");
        if (fail) {
            fail = false;
            throw new Error("network");
        }
        return new Response('{"reward_bytes":17}', { status: 200 });
    });
    await assert.rejects(api.claim("rule"));
    await api.claim("rule");
    await api.claim("rule");
    assert.equal(sent[0].idempotency_key, sent[1].idempotency_key);
    assert.notEqual(sent[1].idempotency_key, sent[2].idempotency_key);
});

test("concurrent reads share one refresh and logout clears credentials on network failure", async () => {
    let refreshes = 0;
    const api = new CabinetAPI(async (path) => {
        if (path.endsWith("/refresh")) {
            refreshes++;
            await new Promise((resolve) => setTimeout(resolve, 10));
            return Response.json({
                access_token: "new",
                refresh_token: "next",
                access_expires_at: "2099-01-01",
            });
        }
        if (path.endsWith("/logout")) throw new Error("offline");
        return Response.json({ available_bytes: 17 });
    });
    api.tokens = {
        access_token: "old",
        refresh_token: "refresh",
        access_expires_at: "2000-01-01",
    };
    await Promise.all([
        api.request("/api/v1/quota"),
        api.request("/api/v1/quota"),
    ]);
    assert.equal(refreshes, 1);
    await assert.rejects(api.logout());
    assert.equal(api.tokens, null);
    assert.equal(api.closed, true);
    await assert.rejects(api.request("/api/v1/quota"));
});

test("definitive rejection clears claim retry; unavailable storage keeps retry", async () => {
    let status = 503;
    const sent = [];
    const api = new CabinetAPI(async (path, options) => {
        sent.push(JSON.parse(options.body).idempotency_key);
        return Response.json({ error: { code: "test_error" } }, { status });
    });
    await assert.rejects(api.claim("rule"));
    status = 409;
    await assert.rejects(api.claim("rule"));
    await assert.rejects(api.claim("rule"));
    assert.equal(sent[0], sent[1]);
    assert.notEqual(sent[1], sent[2]);
});


test("referral retries preserve the key and stay separate from other rewards", async () => {
    const requests = [];
    let fail = true;
    const api = new CabinetAPI(async (path, options) => {
        requests.push({path, ...JSON.parse(options.body)});
        if (fail) { fail = false; throw new Error("offline"); }
        return Response.json({reward_bytes: 17});
    });
    await assert.rejects(api.referral("claim", {referral_id: "ref-one"}, "ref-one"));
    await api.claim("ref-one");
    await api.referral("claim", {referral_id: "ref-two"}, "ref-two");
    await api.referral("claim", {referral_id: "ref-one"}, "ref-one");
    assert.equal(requests[0].idempotency_key, requests[3].idempotency_key);
    assert.notEqual(requests[0].idempotency_key, requests[1].idempotency_key);
    assert.notEqual(requests[0].idempotency_key, requests[2].idempotency_key);
    assert.equal(requests[3].path, "/api/v1/referrals/claim");
    assert.equal(api.pending.size, 0);
});
