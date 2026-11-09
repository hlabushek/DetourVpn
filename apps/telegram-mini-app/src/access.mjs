// Closed-beta access helpers. The Telegram ID is read in the browser only to show it to its owner.

/** The viewer's own Telegram numeric ID as a string, or null if Telegram did not provide a safe one. */
export function viewerId(user) {
    const id = user?.id;
    return Number.isSafeInteger(id) && id > 0 ? String(id) : null;
}

/** True when the server refused sign-in because the account has no beta invite yet. */
export function isNoAccess(error) {
    return error?.message === "beta_invite_required";
}
