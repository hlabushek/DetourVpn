import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import message from '../tgcloud/handlers/message.js';
import callback from '../tgcloud/handlers/callback_query.js';
import { commands, pages, route, validateConfig, view } from '../tgcloud/lib/menu.js';
import { config } from '../tgcloud/lib/config.js';
import { admit } from '../tgcloud/lib/storage.js';
import { updates, limits } from '../tgcloud/schema.js';
import { reset, calls, db, failStorage, failDelivery, rows } from './sdk-test-double.mjs';

// TEST ONLY messages, IDs and SDK delivery. This suite does not prove Telegram hosting.
const now = () => Math.floor(Date.now() / 1000);
const input = (text = '/start', id = 1, user = 123) => {
  const payload = { message_id: 1, date: now(), from: { id: user, is_bot: false }, chat: { id: user, type: 'private' }, text };
  return [payload, { update: { update_id: id, message: payload } }];
};
const button = (data = 'detour:connect', id = 1, user = 123) => {
  const payload = { id: 'test-only-query', from: { id: user, is_bot: false }, data, message: { message_id: 1, date: now() - 86400, from: { id: 456, is_bot: true }, chat: { id: user, type: 'private' } } };
  return [payload, { update: { update_id: id, callback_query: payload } }];
};
beforeEach(reset);

test('start returns welcome and a usable menu without public links', async () => {
  assert.equal((await message(...input())).status, 'processed');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'sendMessage');
  assert.equal(calls[0].params.protect_content, true);
  assert.match(calls[0].params.text, /DetourVpn/);
  assert.equal(calls[0].params.reply_markup.inline_keyboard[0][0].callback_data, 'detour:cabinet');
  assert.equal(calls[0].params.parse_mode, undefined);
});

test('all commands and menu pages produce bounded content and back navigation', () => {
  assert.equal(validateConfig(config), config);
  for (const command of commands) assert.notEqual(route(`/${command.command}`, ''), null);
  for (const page of pages) {
    const content = view(page, config);
    assert.ok(content.text.length < 4096);
    for (const row of content.reply_markup.inline_keyboard) for (const b of row) {
      if (b.callback_data) assert.ok(Buffer.byteLength(b.callback_data) <= 64);
    }
    if (page !== 'home') assert.equal(content.reply_markup.inline_keyboard.at(-1)[0].callback_data, 'detour:home');
  }
});

test('configured links open the Mini App and published operator URLs', () => {
  const configured = validateConfig({ ...config, miniAppUrl: 'https://app123.tgcloud.ai/', androidUrl: 'https://downloads.detourvpn.org/android', supportUrl: 'https://t.me/DetourSupport', privacyUrl: 'https://detourvpn.org/privacy' });
  assert.equal(view('home', configured).reply_markup.inline_keyboard[0][0].web_app.url, configured.miniAppUrl);
  assert.equal(view('android', configured).reply_markup.inline_keyboard[0][0].url, configured.androidUrl);
  assert.equal(view('help', configured).reply_markup.inline_keyboard[0][0].url, configured.supportUrl);
  assert.equal(view('privacy', configured).reply_markup.inline_keyboard[0][0].url, configured.privacyUrl);
});

test('unset links have explicit unavailable text and no invented URLs or balances', () => {
  assert.match(view('cabinet', config).text, /адрес пока не настроен/);
  assert.match(view('android', config).text, /ссылка.*не опубликована/);
  assert.match(view('help', config).text, /не создают обращение/);
  assert.ok(!JSON.stringify(view('home', config)).includes('url'));
});

for (const bad of ['http://detourvpn.org/', 'https://user:pass@detourvpn.org/', 'https://127.0.0.1/', 'https://127.1/', 'https://0x7f.1/', 'https://localhost/', 'https://example.invalid/', 'https://example.org:8443/', 'https://example.org/#bad\n', 'javascript:alert(1)', 'https://-bad.example.org/', 'https://example.org/"']) {
  test(`reject unsafe operator URL: ${bad.replace(/\n/g, '')}`, () => {
    assert.throws(() => validateConfig({ ...config, miniAppUrl: bad }), /bot_configuration_invalid/);
  });
}

test('unsupported config fields and bot usernames fail closed', () => {
  assert.throws(() => validateConfig({ ...config, token: 'TEST ONLY' }), /bot_configuration_invalid/);
  assert.throws(() => validateConfig({ ...config, botUsername: 'not a bot' }), /bot_configuration_invalid/);
});

for (const kind of ['group', 'bot', 'mismatch', 'forward_origin', 'forward_date', 'via_bot', 'business_connection_id', 'sender_chat', 'old', 'future', 'edited', 'missing_context', 'unsafe_id', 'mismatched_payload']) {
  test(`untrusted update is ignored: ${kind}`, async () => {
    const [p, ctx] = input();
    if (kind === 'group') p.chat.type = 'group';
    else if (kind === 'bot') p.from.is_bot = true;
    else if (kind === 'mismatch') p.chat.id++;
    else if (kind === 'old') p.date -= 301;
    else if (kind === 'future') p.date += 61;
    else if (kind === 'edited') { ctx.update.edited_message = ctx.update.message; delete ctx.update.message; }
    else if (kind === 'missing_context') delete ctx.update;
    else if (kind === 'unsafe_id') p.from.id = 2 ** 53;
    else if (kind === 'mismatched_payload') ctx.update.message = { ...p, text: '/help' };
    else p[kind] = { untrusted: true };
    assert.equal((await message(p, ctx)).status, 'ignored');
    assert.equal(calls.length, 0);
    assert.equal(rows('bot_menu_updates').length, 0);
  });
}

test('private user text and code-like strings are never echoed or persisted', async () => {
  const text = '<b>TEST ONLY private message</b> 12345678';
  await message(...input(text));
  assert.ok(!JSON.stringify(calls).includes(text));
  assert.ok(!JSON.stringify(rows('bot_menu_updates')).includes(text));
  assert.ok(!JSON.stringify(rows('bot_menu_limits')).includes(text));
});

test('login deep links fail closed; no fake OTP or attempt UUID is echoed', async () => {
  const attempt = '0123456789abcdef0123456789abcdef';
  await message(...input(`/start ${attempt}`));
  assert.match(calls[0].params.text, /Код не выпущен/);
  assert.ok(!JSON.stringify(calls).includes(attempt));
});

test('commands addressed to another bot are ignored', async () => {
  await message(...input('/start@OtherBot'));
  assert.equal(calls.length, 0);
  assert.equal(route('/help@DetourBot', 'detourbot'), 'help');
});

test('callback acknowledges spinner and edits menu instead of sending new chat messages', async () => {
  assert.equal((await callback(...button())).status, 'processed');
  assert.deepEqual(calls.map((c) => c.method), ['answerCallbackQuery', 'editMessageText']);
  assert.match(calls[1].params.text, /Как подключиться/);
});

test('unknown or forged callback never edits content', async () => {
  assert.equal((await callback(...button('detour:admin'))).status, 'ignored');
  assert.equal(calls[0].method, 'answerCallbackQuery');
  const [p, ctx] = button(); p.message.chat.id = 999;
  assert.equal((await callback(p, ctx)).status, 'ignored');
  assert.equal(calls.length, 1);
});

test('duplicate callback only acknowledges; inaccessible messages are ignored', async () => {
  const args = button();
  assert.equal((await callback(...args)).status, 'processed');
  assert.equal((await callback(...args)).status, 'duplicate');
  assert.deepEqual(calls.map((c) => c.method), ['answerCallbackQuery', 'editMessageText', 'answerCallbackQuery']);
  const [p, ctx] = button('detour:home', 2);
  p.message.date = 0;
  assert.equal((await callback(p, ctx)).status, 'ignored');
  assert.equal(calls.length, 3);
});

test('concurrent redelivery results in one durable claim and one send attempt', async () => {
  const args = input();
  const results = await Promise.all(Array.from({ length: 10 }, () => message(...args)));
  assert.equal(results.filter((r) => r.status === 'processed').length, 1);
  assert.equal(results.filter((r) => r.status === 'duplicate').length, 9);
  assert.equal(calls.length, 1);
  assert.equal(rows('bot_menu_updates').length, 1);
});

test('ambiguous send is not retried or treated as confirmed delivery', async () => {
  failDelivery();
  const args = input();
  assert.equal((await message(...args)).status, 'delivery_unknown');
  assert.equal((await message(...args)).status, 'duplicate');
  assert.equal(calls.length, 1);
});

test('missing schema or unavailable storage never falls back to in-memory sends', async () => {
  failStorage();
  assert.equal((await message(...input())).status, 'unavailable');
  assert.equal(calls.length, 0);
  assert.equal((await callback(...button())).status, 'unavailable');
  assert.equal(calls[0].method, 'answerCallbackQuery');
});

test('user limiter allows only 20 operations in a fixed minute', async () => {
  const current = Math.floor(now() / 60) * 60 + 10;
  const results = await Promise.all(Array.from({ length: 30 }, (_, n) => admit(n, 123, current)));
  assert.equal(results.filter((r) => r === 'accepted').length, 20);
  assert.equal(results.filter((r) => r === 'rate_limited').length, 10);
});

test('global limiter bounds aggregate work across subjects', async () => {
  const current = Math.floor(now() / 60) * 60 + 10;
  const results = await Promise.all(Array.from({ length: 140 }, (_, n) => admit(n, 1000 + n, current)));
  assert.equal(results.filter((r) => r === 'accepted').length, 120);
});

test('expiry removes operational IDs and user buckets on the next accepted invocation', async () => {
  await db.run('INSERT INTO bot_menu_updates VALUES(:id, :expiry)', { ':id': 77, ':expiry': now() - 1 });
  await db.run('INSERT INTO bot_menu_limits VALUES(:bucket, 1, :expiry)', { ':bucket': 'user:old:0', ':expiry': now() - 1 });
  await message(...input());
  assert.equal(rows('bot_menu_updates').some((r) => r.update_id === 77), false);
  assert.equal(rows('bot_menu_limits').some((r) => r.bucket === 'user:old:0'), false);
});

test('schema metadata has primary keys and expiry indexes', () => {
  assert.equal(updates.columns.updateId.primary, true);
  assert.equal(limits.columns.bucket.primary, true);
  assert.equal(updates.indexes.expiry.column, 'expires_at');
  assert.equal(limits.indexes.expiry.column, 'expires_at');
});
