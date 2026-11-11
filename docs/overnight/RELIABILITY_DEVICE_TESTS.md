# Connection reliability — phone tests for the owner

Use an APK built from branch `claude/connection-reliability` with 15-minute windows (the release
scripts pass `-WindowSeconds 900`). Use the same phone as before. Nothing here has been run on a
phone yet.

## Before you start
1. Install the new build over the old one.
2. Android settings → Apps → DetourVPN → Battery → **Unrestricted** (or "Don't optimize"). On
   Xiaomi, Samsung, Huawei and similar phones also allow "Autostart" / "Run in background" if the
   setting exists. Write down which settings your phone has.
3. Sign in, pick a server, tap «Подключить», and wait until the status shows the connection is
   confirmed.
4. Keep a note open (paper is fine). For every test write the fields in "What to record" below.

## What to record for every test
- Phone model and Android version (once).
- Start time and end time (phone clock).
- Network: Wi-Fi or mobile data; did it change during the test?
- Screen off the whole time? Charger connected? (Doze does not start while charging.)
- Result: still connected / reconnected by itself / off.
- If something failed: open the diagnostic report and copy **only** these lines: `Код`, `Этап`,
  handshake line, app version. Do not copy anything else.
- Optional: open a web page right after the test to check the VPN really carries traffic.

## Test 1 — sleep for 30 minutes
1. Connected, unplug the charger, lock the screen, put the phone down.
2. After 30 minutes unlock it and open DetourVPN.
3. **Expected:** the VPN is still on, or the app reconnected by itself within about a minute.

## Test 2 — sleep for 2 hours
Same as test 1, for 2 hours (overnight is fine; note the times).
**Expected:** as test 1. If it is off, note the time you unlocked, then run the server steps in
`RELIABILITY_OWNER_LOGS.md` for that time.

## Test 3 — app closed for a long time
1. Connected. Swipe DetourVPN away from the recent-apps screen.
2. Check the VPN key icon in the status bar is still there.
3. Use the phone normally for at least 1 hour, or leave it locked.
4. Open DetourVPN.
5. **Expected:** the VPN stays on when the app opens. The app must **not** turn it off by itself.
   (This was a confirmed bug: opening the app turned the VPN off.)

## Test 4 — switch servers while connected
1. Connected to server A. Open the server list and pick server B.
2. **Expected:** the app disconnects from A and connects to B on its own, within about 15 s.
3. Repeat back to A.

## Test 5 — switch servers while connecting
1. Disconnected. Tap «Подключить» and, while it still says it is connecting, open the server list
   and pick another server.
2. **Expected:** it ends up connected to the server you picked last. No error that says to wait
   for the old connection.

## Test 6 — a failing server, then retry
Use a server you know is down, or turn its node off for this test (only if you control it).
1. Pick the failing server and tap «Подключить».
2. **Expected:** an error with a clear Russian text. Copy `Код` and `Этап`.
   - `window_node_unavailable`: the server says the node is not ready.
   - `window_activation_refused` at `lease_validation`: the server did not switch the new
     connection on. The app retries up to 3 times by itself.
3. Pick a working server and tap «Подключить».
4. **Expected:** it connects on the first try, with no `revoke_unconfirmed` error.

## Test 7 — retry right after a disconnect
1. Connected. Tap «Отключить», then tap «Подключить» immediately.
2. **Expected:** it connects (it may take a few seconds longer). No error.

## Test 8 — reboot with the VPN on
1. Connected. Restart the phone and unlock it. Do not open the app.
2. Wait 2 minutes and check whether the VPN key icon is shown.
3. **Expected:** the VPN comes back by itself.
4. Lock the phone for 30 minutes without opening the app. **Expected:** still on (ADR-121 wakes the
   background engine). This is the weakest case; note the result carefully.
5. Then open DetourVPN. **Expected:** the VPN stays on; the app does not turn it off or create a new
   connection (it takes over the restored one).

## What not to do
- Do not send screenshots that show IP addresses, server names you consider private, or links.
- Do not change anything on the servers for these tests, except turning off a node you control in
  test 6.
