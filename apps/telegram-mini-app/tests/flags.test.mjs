import { test } from "node:test";
import assert from "node:assert/strict";
import { FLAG_HEIGHT, FLAG_WIDTH, flagShapes, parseCity, parseCountryCode } from "../src/flags.mjs";

test("country code parsing is strict and never throws", () => {
    assert.equal(parseCountryCode("FI"), "FI");
    for (const bad of ["fi", "FIN", "", "F1", null, undefined, 7, {}]) {
        assert.equal(parseCountryCode(bad), null);
    }
    assert.equal(parseCity("Helsinki"), "Helsinki");
    assert.equal(parseCity(""), null);
    assert.equal(parseCity("x".repeat(65)), null);
});

test("known countries draw inside the canvas, unknown ones return null", () => {
    for (const code of ["FI", "SE", "DK", "NO", "DE", "NL", "AT", "PL", "EE", "LT", "FR", "IT"]) {
        const shapes = flagShapes(code);
        assert.ok(shapes && shapes.length >= 2, code);
        for (const s of shapes) {
            assert.ok(s.x >= 0 && s.y >= 0, code);
            assert.ok(s.x + s.width <= FLAG_WIDTH + 1e-9, code);
            assert.ok(s.y + s.height <= FLAG_HEIGHT + 1e-9, code);
            assert.match(s.fill, /^#[0-9a-f]{6}$/, code);
        }
    }
    for (const unknown of ["ZZ", null, undefined, "constructor", "__proto__", "toString"]) {
        assert.equal(flagShapes(unknown), null);
    }
});
