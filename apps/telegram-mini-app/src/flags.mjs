// Country flags as plain geometry on a 20x14 canvas: no image files, no emoji (Telegram Desktop on
// Windows does not draw emoji flags) and no dependency. Unknown codes yield null; the caller draws a
// neutral globe. Mirror of apps/client/lib/country_flag.dart; keep both tables in sync.

export const FLAG_WIDTH = 20;
export const FLAG_HEIGHT = 14;

const NORDIC = {
    FI: ["#ffffff", "#003580"],
    SE: ["#006aa7", "#fecc00"],
    DK: ["#c8102e", "#ffffff"],
    NO: ["#ba0c2f", "#ffffff", "#00205b"],
};
const HORIZONTAL = {
    DE: ["#000000", "#dd0000", "#ffce00"],
    NL: ["#ae1c28", "#ffffff", "#21468b"],
    AT: ["#ed2939", "#ffffff", "#ed2939"],
    PL: ["#ffffff", "#dc143c"],
    EE: ["#0072ce", "#000000", "#ffffff"],
    LT: ["#fdb913", "#006a44", "#c1272d"],
};
const VERTICAL = {
    FR: ["#0055a4", "#ffffff", "#ef4135"],
    IT: ["#009246", "#ffffff", "#ce2b37"],
};

/** ISO 3166-1 alpha-2 in upper case, otherwise null (never throws on bad server data). */
export function parseCountryCode(value) {
    return typeof value === "string" && /^[A-Z]{2}$/.test(value) ? value : null;
}

/** Display city from the API, bounded; otherwise null. */
export function parseCity(value) {
    return typeof value === "string" && value.length > 0 && value.length <= 64 ? value : null;
}

/** Rectangles {x, y, width, height, fill} painted in order, or null for an unknown country. */
export function flagShapes(code) {
    const W = FLAG_WIDTH;
    const H = FLAG_HEIGHT;
    const rect = (x, y, width, height, fill) => ({ x, y, width, height, fill });
    if (Object.hasOwn(NORDIC, code ?? "")) {
        const [field, cross, inner] = NORDIC[code];
        const bar = H * 0.3;
        const x = W * 0.31;
        const y = (H - bar) / 2;
        const shapes = [rect(0, 0, W, H, field), rect(x, 0, bar, H, cross), rect(0, y, W, bar, cross)];
        if (inner) {
            const thin = bar / 2;
            const offset = (bar - thin) / 2;
            shapes.push(rect(x + offset, 0, thin, H, inner), rect(0, y + offset, W, thin, inner));
        }
        return shapes;
    }
    if (Object.hasOwn(HORIZONTAL, code ?? "")) {
        const colors = HORIZONTAL[code];
        return colors.map((fill, i) => rect(0, (H * i) / colors.length, W, H / colors.length, fill));
    }
    if (Object.hasOwn(VERTICAL, code ?? "")) {
        const colors = VERTICAL[code];
        return colors.map((fill, i) => rect((W * i) / colors.length, 0, W / colors.length, H, fill));
    }
    return null;
}
