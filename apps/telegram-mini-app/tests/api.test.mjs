import { test } from "node:test";
import assert from "node:assert/strict";
import { CabinetAPI, exactJSON, bytes, gigabytesToBytes, loginAttemptFromFragment } from "../src/api.mjs";

test("int64 remains exact, strings and escaped quotes are not rewritten", () => {
    const result = exactJSON(
        '{"n":9223372036854775807,"s":"12 \\\"3","a":[-17,true,null,1.5]}',
    );
    assert.equal(result.n, "9223372036854775807");
    assert.equal(result.s, '12 \\"3'.replace('\\"', '"'));
    assert.deepEqual(result.a, ["-17", true, null, 1.5]);
    assert.equal(bytes(result.n), "9223372036,8 ГБ");
});

test("login attempt is accepted only from a 32-hex URL fragment", () => {
    const id = "0123456789abcdef0123456789abcdef";
    assert.deepEqual(loginAttemptFromFragment(`#login_attempt=${id}`), {
        present: true, requestId: id,
    });
    assert.deepEqual(loginAttemptFromFragment("?login_attempt=" + id), {
        present: false, requestId: null,
    });
    assert.deepEqual(loginAttemptFromFragment("#login_attempt=bad"), {
        present: true, requestId: null,
    });
});

test("Android OTP request sends initData and challenge without Mini App session", async () => {
    let sent;
    const api = new CabinetAPI(async (path, options) => {
        sent = { path, options };
        return Response.json({ status: "processed" });
    });
    const response = await api.requestLoginCode("SYNTHETIC_TEST_INIT_DATA", "0123456789abcdef0123456789abcdef");
    assert.equal(response.status, "processed");
    assert.equal(sent.path, "/api/v1/auth/mini-app/request-code");
    assert.equal(sent.options.credentials, "omit");
    assert.equal(sent.options.headers.Authorization, undefined);
    assert.deepEqual(JSON.parse(sent.options.body), {
        init_data: "SYNTHETIC_TEST_INIT_DATA",
        request_id: "0123456789abcdef0123456789abcdef",
    });
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

test("rejected OTP request is represented as a non-delivery result", async () => {
    const api = new CabinetAPI(async () => Response.json({ status: "rejected" }));
    assert.deepEqual(
        await api.requestLoginCode("SYNTHETIC_TEST_INIT_DATA", "0123456789abcdef0123456789abcdef"),
        { status: "rejected" },
    );
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

function signedIn(fetcher) {
    const api = new CabinetAPI(fetcher);
    api.tokens = {
        access_token: "a".repeat(43),
        refresh_token: "r".repeat(43),
        access_expires_at: new Date(Date.now() + 600000).toISOString(),
    };
    return api;
}

test("device management uses the right verbs and paths and never reuses a code key", async () => {
    const calls = [];
    const api = signedIn(async (path, options) => {
        calls.push({ path, method: options.method, body: options.body, headers: options.headers });
        return options.method === "DELETE" ? new Response(null, { status: 204 }) : Response.json({ ok: true });
    });
    await api.createLinkCode({ label: "Phone", trafficCapBytes: 5000000000 });
    await api.createLinkCode();
    await api.listLinkCodes();
    await api.cancelLinkCode("11111111-1111-4111-8111-111111111111");
    await api.patchDevice("22222222-2222-4222-8222-222222222222", { label: null });
    await api.revokeDevice("22222222-2222-4222-8222-222222222222");
    assert.deepEqual(
        calls.map((c) => `${c.method} ${c.path}`),
        [
            "POST /api/v1/devices/link-codes",
            "POST /api/v1/devices/link-codes",
            "GET /api/v1/devices/link-codes",
            "DELETE /api/v1/devices/link-codes/11111111-1111-4111-8111-111111111111",
            "PATCH /api/v1/devices/22222222-2222-4222-8222-222222222222",
            "DELETE /api/v1/devices/22222222-2222-4222-8222-222222222222",
        ],
    );
    const [first, second] = [JSON.parse(calls[0].body), JSON.parse(calls[1].body)];
    assert.notEqual(first.idempotency_key, second.idempotency_key);
    assert.equal(first.label, "Phone");
    assert.equal(first.traffic_cap_bytes, 5000000000);
    assert.equal(second.label, undefined);
    assert.equal(second.traffic_cap_bytes, undefined);
    assert.deepEqual(JSON.parse(calls[4].body), { label: null });
    assert.equal(calls[3].body, undefined); // DELETE carries no body and no content type.
    assert.equal(calls[3].headers["Content-Type"], undefined);
});

test("device ids are encoded, never concatenated raw into a path", async () => {
    let seen;
    const api = signedIn(async (path) => {
        seen = path;
        return new Response(null, { status: 204 });
    });
    await api.revokeDevice("../quota");
    assert.equal(seen, "/api/v1/devices/..%2Fquota");
});

test("API errors from device calls surface the stable error code", async () => {
    const api = signedIn(async () =>
        Response.json({ error: { code: "device_limit_reached" } }, { status: 409 }),
    );
    await assert.rejects(api.createLinkCode(), (e) => e.message === "device_limit_reached" && e.status === 409);
});

test("traffic cap text converts to integer bytes or is rejected", () => {
    assert.equal(gigabytesToBytes(""), null);
    assert.equal(gigabytesToBytes("  "), null);
    assert.equal(gigabytesToBytes("5"), 5000000000);
    assert.equal(gigabytesToBytes("1,5"), 1500000000);
    assert.equal(gigabytesToBytes("0.001"), 1000000);
    for (const bad of ["0", "-1", "abc", "1.2345", "1e3", "9999999", "0.0"]) {
        assert.equal(gigabytesToBytes(bad), undefined, bad);
    }
});
