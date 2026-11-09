import assert from "node:assert/strict";
import test from "node:test";
import { isNoAccess, viewerId } from "../src/access.mjs";

test("viewerId accepts only a positive safe integer and returns text", () => {
    assert.equal(viewerId({ id: 987654321 }), "987654321");
    for (const bad of [undefined, null, {}, { id: 0 }, { id: -5 }, { id: 1.5 }, { id: "12" }, { id: 2 ** 53 }]) {
        assert.equal(viewerId(bad), null);
    }
});

test("isNoAccess matches only the invite-required code", () => {
    assert.equal(isNoAccess({ message: "beta_invite_required" }), true);
    assert.equal(isNoAccess({ message: "unauthorized" }), false);
    assert.equal(isNoAccess(undefined), false);
});
