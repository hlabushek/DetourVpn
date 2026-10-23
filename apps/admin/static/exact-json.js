// Parse integer JSON tokens as strings before JSON.parse can round int64 byte amounts.
export function exactJSON(text) {
    let output = "",
        quoted = false,
        escaped = false;
    for (let i = 0; i < text.length; i++) {
        const c = text[i];
        if (quoted) {
            output += c;
            if (escaped) escaped = false;
            else if (c === "\\") escaped = true;
            else if (c === '"') quoted = false;
        } else if (c === '"') {
            quoted = true;
            output += c;
        } else if (c === "-" || /[0-9]/.test(c)) {
            const match = text
                .slice(i)
                .match(/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/);
            if (!match) throw new Error("invalid_json");
            const value = match[0];
            output += /^-?[0-9]+$/.test(value) ? JSON.stringify(value) : value;
            i += value.length - 1;
        } else output += c;
    }
    return JSON.parse(output);
}

