// Bearer session stays in RAM. No localStorage, cookies, HTML injection or third-party scripts.
import { exactJSON } from './exact-json.js';
import { ago, errorText, formatBytes, formatTime, INVITE_STATES, inviteMessage, OPERATION_LABELS, parseAmount } from './format.js';
let access = null, selected = null, role = null, revokeTarget = null;
const pending = new Map();
const $ = id => document.getElementById(id);
const decimalFields = new Set(['amount_bytes','quota_bytes','price_minor','period_days','telegram_subject']);
function status(message, kind = '') { const el = $('status'); el.textContent = message; el.className = 'status ' + kind; }
function encode(body) {
  // Decimal integers are never converted through JS Number, including int64 values.
  return JSON.stringify(body).replace(/"(amount_bytes|quota_bytes|price_minor|period_days|telegram_subject)":"([0-9]+)"/g, '"$1":$2');
}
function integer(value, positive = false) {
  if (!/^(0|[1-9][0-9]*)$/.test(value) || BigInt(value) > 9223372036854775807n || (positive && BigInt(value) === 0n)) throw new Error('invalid_integer');
  return value;
}
function telegramSubject(value) {
  let subject;
  try { subject = integer(String(value).trim(), true); } catch { throw new Error('invalid_telegram_id'); }
  if (BigInt(subject) > 4503599627370495n) throw new Error('invalid_telegram_id');
  return subject;
}
async function request(path, body) {
  const response = await fetch('/api/admin/v1/' + path, { method: body === undefined ? 'GET' : 'POST',
    credentials:'omit', cache:'no-store', redirect:'error', referrerPolicy:'no-referrer', signal:AbortSignal.timeout(12000),
    headers:{...(access ? {Authorization:'Bearer '+access}:{}), ...(body ? {'Content-Type':'application/json'}:{})},
    ...(body ? {body:encode(body)}:{}) });
  const value = exactJSON(await response.text());
  if (!response.ok) {
    if (response.status === 401) showLogin();
    throw new Error(value.error?.code || 'request_failed');
  }
  return value;
}
function showLogin() {
  access = null; selected = null; role = null; revokeTarget = null; pending.clear();
  for (const id of ['workspace','session']) $(id).hidden = true;
  $('login').hidden = false;
}
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const badge = (text, kind) => el('span', text, 'badge ' + kind);
function button(text, onClick, cls = 'btn secondary small') { const b = el('button', text, cls); b.type = 'button'; b.addEventListener('click', onClick); return b; }
const withTitle = (text, title) => { const s = el('span', text); s.title = title; return s; };
const shortId = id => String(id).slice(0, 8);
function table(target, rows, columns, empty = 'Записей нет') {
  const wrap = el('div', null, 'table-wrap');
  if (!rows.length) { wrap.append(el('p', empty, 'empty')); $(target).replaceChildren(wrap); return; }
  const t = el('table'), head = el('tr');
  for (const [label] of columns) head.append(el('th', label));
  t.append(head);
  for (const row of rows) {
    const tr = el('tr');
    for (const [, get] of columns) { const td = el('td'), value = get(row);
      if (value instanceof Node) td.append(value); else td.textContent = value == null ? '—' : String(value); tr.append(td); }
    t.append(tr);
  }
  wrap.append(t); $(target).replaceChildren(wrap);
}
const bytesCell = value => withTitle(formatBytes(value), value == null ? '' : value + ' байт');
function stat(label, value) { const s = el('div', null, 'stat'); s.append(el('b', value), el('span', label)); return s; }

function showTab(name) {
  for (const tab of document.querySelectorAll('#tabs [role=tab]')) tab.setAttribute('aria-selected', String(tab.dataset.tab === name));
  for (const panel of document.querySelectorAll('.panel')) panel.hidden = panel.id !== 'panel-' + name;
}
for (const tab of document.querySelectorAll('#tabs [role=tab]')) tab.addEventListener('click', () => showTab(tab.dataset.tab));

// Sorting runs on the loaded page only (server returns at most 50 rows, newest first).
// Users without a Telegram ID sort last for the Telegram sort.
const telegramSortKey = r => r.telegram_id ? '0' + String(r.telegram_id).padStart(20, '0') : '1';
// Users without a Telegram username sort last; case-insensitive like Telegram itself (ADR-110).
const usernameSortKey = r => r.telegram_username ? '0' + String(r.telegram_username).toLowerCase() : '1';
const USER_SORTS = {
  created_desc: (a, b) => String(b.created_at).localeCompare(String(a.created_at)),
  created_asc: (a, b) => String(a.created_at).localeCompare(String(b.created_at)),
  telegram: (a, b) => telegramSortKey(a).localeCompare(telegramSortKey(b)),
  username: (a, b) => usernameSortKey(a).localeCompare(usernameSortKey(b)),
  status: (a, b) => String(a.status).localeCompare(String(b.status)) || String(b.created_at).localeCompare(String(a.created_at)),
};
let usersPage = [];
function renderUsers() {
  const sort = USER_SORTS[$('users-sort').value] || USER_SORTS.created_desc;
  const rows = [...usersPage].sort(sort);
  const columns = [
    ['Пользователь', r => button(shortId(r.id) + '…', () => run(() => openUser(r.id)), 'btn secondary small')],
    ['Статус', r => r.status === 'active' ? badge('Активен', 'ok') : badge(r.status === 'disabled' ? 'Отключён' : r.status, 'off')],
    ['Создан', r => formatTime(r.created_at)],
  ];
  // The Telegram column appears only when at least one listed user has a linked Telegram ID.
  if (rows.some(r => r.telegram_id)) columns.splice(1, 0, ['Telegram ID', r => r.telegram_id || null]);
  // Same rule for the username from the last Mini App login (admin-only, ADR-110).
  if (rows.some(r => r.telegram_username)) columns.splice(1, 0, ['Telegram username', r => r.telegram_username ? '@' + r.telegram_username : null]);
  table('users', rows, columns, 'Пользователей не найдено');
}
async function loadUsers(q = '') {
  usersPage = await request('users?q=' + encodeURIComponent(q));
  renderUsers();
  return usersPage;
}
$('users-sort').addEventListener('change', renderUsers);
async function openUser(id) { showTab('users'); await loadUser(id); }
function chooseResource(operation, id) {
  const form = $('action-form'); form.elements.operation.value = operation; form.elements.resource_id.value = id; syncActionFields();
  form.scrollIntoView({ behavior: 'smooth', block: 'center' }); status('Заполните причину и нажмите «Применить».');
}
async function loadUser(id) {
  const value = await request('users/' + id); selected = id;
  $('selected').textContent = 'Пользователь ' + id + ' · ' + (value.status === 'active' ? 'активен' : value.status);
  table('quota', [value.quota], [['Начислено', r => bytesCell(r.granted_bytes)], ['Использовано', r => bytesCell(r.used_bytes)], ['Доступно', r => bytesCell(r.available_bytes)], ['В резерве', r => bytesCell(r.reserved_bytes)], ['Недоступно (срок/отзыв)', r => bytesCell(r.unavailable_bytes)]]);
  table('devices', value.devices, [['Устройство', r => r.id], ['Платформа', r => r.platform], ['Состояние', r => r.revoked ? badge('Отозвано', 'off') : badge('Активно', 'ok')],
    ['', r => r.revoked || role !== 'admin' ? '' : button('Отозвать…', () => chooseResource('revoke_device', r.id))]], 'Устройств нет');
  table('subscriptions', value.subscriptions, [['Подписка', r => r.id], ['Тариф', r => r.plan.name + ' v' + r.plan.version], ['Основание', r => r.basis], ['Состояние', r => r.state], ['Начало', r => formatTime(r.starts_at)], ['Окончание', r => formatTime(r.ends_at)], ['Остаток', r => bytesCell(r.remaining_bytes)],
    ['', r => role !== 'admin' || r.state !== 'active' ? '' : button('Отменить…', () => chooseResource('revoke_subscription', r.id))]], 'Подписок нет');
  table('history', value.history, [['№', r => r.sequence], ['Источник', r => r.source_type], ['Изменение', r => bytesCell(r.delta_bytes)], ['Время', r => formatTime(r.created_at)]], 'Движений нет');
}
async function loadInvites() {
  const data = await request('beta-invites');
  $('invite-message').value = inviteMessage(data.bot_username);
  if (!data.enabled) { $('invites').replaceChildren(el('p', 'Закрытая бета выключена на сервере: приглашения не работают.', 'empty')); return { waiting: 0, joined: 0, total: 0 }; }
  table('invites', data.items, [
    ['Пометка', r => r.label],
    ['Статус', r => { const [text, kind] = INVITE_STATES[r.state] || [r.state, 'off']; return badge(text, kind); }],
    ['Приглашён', r => formatTime(r.created_at)],
    ['', r => { const cell = el('span');
      if (r.user_id) cell.append(button('Открыть пользователя', () => run(() => openUser(r.user_id))));
      if (r.state !== 'revoked') cell.append(button('Отозвать…', () => openRevoke(r)));
      return cell; }],
  ], 'Пока никого не приглашали');
  return { waiting: data.items.filter(i => i.state === 'waiting').length, joined: data.items.filter(i => i.state === 'joined').length, total: data.items.length };
}
function openRevoke(invite) {
  revokeTarget = invite; $('revoke-label').textContent = invite.label; $('revoke-panel').hidden = false;
  $('beta-revoke-form').reset(); $('revoke-panel').scrollIntoView({ behavior: 'smooth', block: 'center' });
}
function closeRevoke() { revokeTarget = null; $('revoke-panel').hidden = true; $('beta-revoke-form').reset(); }
const nodeOnline = r => r.status === 'active' && r.last_seen_at && Date.now() - new Date(r.last_seen_at).getTime() < 120000;
async function refresh() {
  const users = await loadUsers();
  const nodes = await request('nodes');
  table('nodes', nodes, [
    ['Сервер', r => r.name],
    ['Источник', r => r.source === 'vps' ? 'VPS' : r.source],
    ['Состояние', r => r.status !== 'active' ? badge('Выключен', 'off') : nodeOnline(r) ? badge('На связи', 'ok') : badge('Нет связи', 'warn')],
    ['Последний ответ', r => withTitle(ago(r.last_seen_at), r.last_seen_at || '')],
    ['Занято / ёмкость', r => r.occupied + ' / ' + r.capacity_limit],
    ['Команды без ответа', r => r.unacknowledged_commands],
    ['Зависли дольше 90 с', r => r.stalled_commands],
  ], 'Серверов нет');
  // Panel servers (ADR-111): the API never returns the link, host or keys.
  table('panel-servers', await request('panel-servers'), [
    ['Сервер', r => r.label],
    ['Источник', r => 'Панель (' + (r.panel_kind === 'remnawave' ? 'Remnawave' : '3x-ui') + ')'],
    ['Протокол', r => r.protocol === 'vless_reality' ? 'VLESS + Reality' : r.protocol],
    ['Устройства', r => (r.delivered_to_devices ? 'Выдаётся' : 'Пока не выдаётся') + '; Android: ' + (r.android_supported ? 'да' : 'не поддерживается')],
    ['Добавлен', r => formatTime(r.created_at)],
  ], 'Серверов из панели нет');
  const online = nodes.filter(nodeOnline).length, active = nodes.filter(r => r.status === 'active').length;
  const summary = [stat('Серверы на связи', online + ' из ' + active)];
  if (role === 'admin') {
    const plans = await request('plans');
    table('plans', plans, [['Название', r => r.name], ['Версия', r => 'v' + r.version], ['Цена (демо)', r => String(BigInt(r.price_minor) / 100n) + ',' + String(BigInt(r.price_minor) % 100n).padStart(2, '0') + ' ₽'], ['Квота', r => bytesCell(r.quota_bytes)], ['Период', r => r.period_days + ' дн.'], ['Номер версии', r => r.id]]);
    const previousPlan = $('plan-picker').value;
    $('plan-picker').replaceChildren(); const latest = new Set();
    for (const p of plans) { if (latest.has(p.code)) continue; latest.add(p.code); const o = document.createElement('option'); o.value = p.id; o.textContent = p.name + ' v' + p.version; $('plan-picker').append(o); }
    const keep = [...$('plan-picker').options].find(o => o.value === previousPlan), initial = plans.find(p => p.code === 'start');
    if (keep) $('plan-picker').value = keep.value; else if (initial) $('plan-picker').value = initial.id;
    const invites = await loadInvites();
    summary.push(stat('Ждут входа', String(invites.waiting)), stat('Вошли', String(invites.joined)));
  }
  summary.push(stat('Пользователей в списке', String(users.length)));
  $('summary').replaceChildren(...summary);
  table('audit', await request('audit'), [['Время', r => formatTime(r.time)], ['Автор', r => r.actor], ['Действие', r => OPERATION_LABELS[r.operation] || r.operation], ['Пользователь', r => r.user_id ? withTitle(shortId(r.user_id) + '…', r.user_id) : '—'], ['Пометка / причина', r => r.reason]]);
  if (selected) await loadUser(selected);
}
async function run(fn, success = 'Готово') {
  try { status('Выполняется…'); const result = await fn(); status(typeof result === 'string' ? result : success, 'ok'); }
  catch (e) { status(errorText(e.message), 'err'); }
}
async function action(body) {
  const signature = JSON.stringify(body); let key = pending.get(signature);
  if (!key) { key = crypto.randomUUID(); pending.set(signature, key); }
  const result = await request('actions', { ...body, idempotency_key: key });
  // Keep a completed form's key too: accidental duplicate submit must not grant twice.
  await refresh();
  return result;
}
function syncActionFields() {
  const operation = $('action-form').elements.operation.value;
  for (const node of $('action-form').querySelectorAll('[data-for]')) node.hidden = !node.dataset.for.split(' ').includes(operation);
}
$('action-form').elements.operation.addEventListener('change', syncActionFields);
$('login-form').addEventListener('submit', e => { e.preventDefault(); run(async () => {
  const form = e.target, body = Object.fromEntries(new FormData(form));
  const result = await request('login', body); access = result.access_token; role = result.role;
  form.reset(); $('workspace').hidden = false; $('session').hidden = false; $('login').hidden = true;
  const admin = role === 'admin';
  for (const id of ['changes', 'tab-plans', 'tab-invites', 'panel-import']) $(id).hidden = !admin;
  syncActionFields();
  await refresh(); showTab(admin ? 'invites' : 'users');
  return 'Вы вошли как ' + (admin ? 'администратор' : 'оператор');
}); });
$('panel-form').addEventListener('submit', e => { e.preventDefault(); run(async () => {
  const form = e.target, f = Object.fromEntries(new FormData(form));
  const body = { label: String(f.label).trim(), panel_kind: f.panel_kind, link: String(f.link).trim(), reason: f.reason };
  // Same retry rule as actions: a resubmitted identical form reuses its idempotency key.
  const signature = 'panel:' + JSON.stringify(body); let key = pending.get(signature);
  if (!key) { key = crypto.randomUUID(); pending.set(signature, key); }
  await request('panel-servers', { ...body, idempotency_key: key });
  form.reset(); // drop the link from the page as soon as it is stored
  await refresh();
  return 'Сервер из панели сохранён. Устройствам он пока не выдаётся.';
}); });
$('search-form').addEventListener('submit', e => { e.preventDefault(); run(async () => { const rows = await loadUsers(new FormData(e.target).get('q')); return rows.length ? 'Найдено: ' + rows.length : 'Никого не найдено'; }); });
$('refresh').addEventListener('click', () => run(refresh, 'Обновлено'));
$('new-operation').addEventListener('click', () => { pending.clear(); status('Следующее применение создаст новую операцию. Проверьте историю предыдущей.'); });
$('logout').addEventListener('click', () => run(async () => { await request('logout', {}); showLogin(); return 'Вы вышли'; }));
$('demo-catalog').addEventListener('click', () => run(() => action({ operation: 'seed_demo_catalog', reason: 'Initial illustrative catalog; payments disabled' }), 'Каталог создан'));
$('copy-message').addEventListener('click', () => run(async () => { await navigator.clipboard.writeText($('invite-message').value); return 'Текст скопирован'; }));
$('revoke-cancel').addEventListener('click', closeRevoke);
$('action-form').addEventListener('submit', e => { e.preventDefault(); run(async () => {
  if (!selected) throw new Error('select_user');
  const f = Object.fromEntries(new FormData(e.target)); const body = { operation: f.operation, user_id: selected, reason: f.reason };
  if (f.operation === 'credit' || f.operation === 'debit') body.amount_bytes = parseAmount(f.amount, f.unit);
  else body.resource_id = f.operation === 'grant_subscription' ? f.plan_id : f.resource_id;
  await action(body);
  return 'Применено';
}); });
$('plan-form').addEventListener('submit', e => { e.preventDefault(); run(async () => {
  const f = Object.fromEntries(new FormData(e.target)), reason = f.reason; delete f.reason;
  for (const key of decimalFields) if (key in f) f[key] = integer(f[key], key !== 'price_minor');
  await action({ operation: 'revise_plan', reason, plan: f });
  return 'Версия тарифа создана';
}); });
for (const [formId, operation] of [['beta-grant-form', 'grant_beta_invite'], ['beta-revoke-form', 'revoke_beta_invite']]) {
  $(formId).addEventListener('submit', e => { e.preventDefault(); run(async () => {
    const f = Object.fromEntries(new FormData(e.target));
    const subject = telegramSubject(f.telegram_subject);
    if (f.reason.includes(subject)) throw new Error('reason_must_not_contain_identifier');
    if (operation === 'revoke_beta_invite' && !window.confirm('Отзыв отключит аккаунт тестера «' + (revokeTarget ? revokeTarget.label : '') + '» и отзовёт его сессии и VPN-подключения. Продолжить?')) return 'Отзыв отменён';
    await action({ operation, telegram_subject: subject, reason: f.reason });
    e.target.reset();
    if (operation === 'revoke_beta_invite') { closeRevoke(); return 'Доступ отозван. Завершение на узле проверяйте отдельно, до подтверждения не приглашайте этого человека заново.'; }
    return 'Человек добавлен. Пусть откроет Mini App ещё раз.';
  }); });
}
