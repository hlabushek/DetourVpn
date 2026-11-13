# Connection rewrite (run 2) — phone tests for the owner

Plain English. None of these were run overnight: there was no phone. Build a signed APK from
`claude/connection-reliability` with your usual release script (`-WindowSeconds 900`), install
it, and sign in. Keep the phone on a charger only where a step says so.

**For every test, record:** the time it started, what the app showed (screenshot of the main
screen), whether websites opened, and the diagnostic report («Поделиться диагностикой») at the
end. The report now also carries the native state (`transport`, `evidence`, `failureCategory`,
`desiredConnected`); those are codes only, no addresses.

If a test fails, write down the minute it failed and run the read-only server steps in
`RELIABILITY_OWNER_LOGS.md` for that minute.

## A. The most important test: Wi-Fi ↔ mobile switch (ADR-127)
Before this build, the "restart" after a network switch did nothing (verified in code).
1. Connect on Wi-Fi. Open a website.
2. Turn Wi-Fi off. Wait 20 seconds. Open another website.
   - **Expected:** it opens within about 20 seconds, without touching the app or airplane mode.
3. Turn Wi-Fi back on. Wait 20 seconds. Open a website.
   - **Expected:** same.
4. Repeat steps 2–3 five times quickly (every ~10 s).
   - **Expected:** after you stop, websites open within about 30 seconds; the app does not show
     an error; it never needs a manual reconnect.

## B. Screen off and Doze
1. Connect. Lock the phone. Leave it unplugged for 1 hour, not touched.
2. Unlock and open a website **before** opening the app.
   - **Expected:** it opens. Then open the app: it shows «Подключено» or «проверяется связь»
     within a few seconds, and no error.
3. Repeat overnight (8 hours, unplugged).
   - **Expected:** same. If websites do not open, open the app and wait 30 s: it should
     reconnect by itself (status «Восстанавливается подключение»), without a tap.

## C. App closed (swiped from Recents)
1. Connect. Swipe the app away from Recents. Wait 30 minutes (screen on or off).
2. Open a website. **Expected:** it opens (native renewal keeps the window).
3. Open the app. **Expected:** it shows the running VPN; no «Подключить» needed.

## D. Window ends while the app is closed (ADR-124/125)
This is hard to force; do it with mobile data off for longer than a window.
1. Connect on Wi-Fi. Swipe the app away.
2. Turn Wi-Fi **and** mobile data off for 20 minutes (longer than one 15-minute window).
3. Turn mobile data on. Do not open the app. Wait up to 2 minutes.
   - **Expected:** the VPN key icon comes back by itself and websites open.
   - If it does not come back: open the app. **Expected:** it reconnects by itself within
     about 10 seconds (no tap). Record which of the two happened.
4. Check Settings → Apps → DetourVPN → Battery is "Unrestricted" before this test; without it
   Android may refuse the background restart (expected limitation).

## E. Explicit disconnect wins
1. Connect. Press «Отключить». Lock the phone for 30 minutes. Toggle Wi-Fi off/on once.
   - **Expected:** the VPN stays off. It must never come back by itself.
2. Reboot the phone. **Expected:** the VPN stays off.

## F. Reboot while connected
1. Connect. Reboot. Unlock. Do not open the app. Wait 1 minute.
   - **Expected:** the VPN comes back by itself.

## G. Another VPN app takes over
1. Connect. Start another VPN app (any). **Expected:** DetourVPN stops and never comes back by
   itself, even after Wi-Fi toggles or a reboot. Opening DetourVPN shows the "permission
   revoked" text.

## H. Captive portal / Wi-Fi without internet
1. Connect on mobile data. Join a Wi-Fi with a sign-in page (or a router with the WAN cable
   unplugged). **Expected:** the app does not loop or flash errors; when you leave that Wi-Fi,
   traffic returns within about 30 s.

## I. Split tunnelling unchanged
1. Exclude one app (e.g. a bank app) in the split settings, connect.
   **Expected:** that app works without the VPN, a browser goes through the VPN (check your IP
   on any "what is my IP" page from the browser).

## J. Battery
After test B (overnight), Settings → Battery → DetourVPN usage. **Expected:** low single-digit
percent. Record the number.

## What counts as proof
Only these phone results. Unit tests and the build prove the logic and that it compiles, not
that Android behaves as expected on your phone.
