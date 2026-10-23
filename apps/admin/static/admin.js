// Bearer session stays in RAM. No localStorage, cookies, HTML injection or third-party scripts.
import { exactJSON } from './exact-json.js';
let access = null, selected = null, role = null;
const pending = new Map();
const $ = id => document.getElementById(id);
const status = message => { $('status').textContent = message; };
const decimalFields = new Set(['amount_bytes','quota_bytes','price_minor','period_days']);
function encode(body) {
  // Decimal integers are never converted through JS Number, including int64 values.
  return JSON.stringify(body).replace(/"(amount_bytes|quota_bytes|price_minor|period_days)":"([0-9]+)"/g, '"$1":$2');
}
function integer(value, positive = false) {
  if (!/^(0|[1-9][0-9]*)$/.test(value) || BigInt(value) > 9223372036854775807n || (positive && BigInt(value) === 0n)) throw new Error('invalid_integer');
  return value;
}
async function request(path, body) {
  const response = await fetch('/api/admin/v1/' + path, { method: body === undefined ? 'GET' : 'POST',
    credentials:'omit', cache:'no-store', redirect:'error', referrerPolicy:'no-referrer', signal:AbortSignal.timeout(12000),
    headers:{...(access ? {Authorization:'Bearer '+access}:{}), ...(body ? {'Content-Type':'application/json'}:{})},
    ...(body ? {body:encode(body)}:{}) });
  const value = exactJSON(await response.text());
  if (!response.ok) {
    if (response.status === 401) { access = null; $('workspace').hidden = true; $('login').hidden = false; }
    throw new Error(value.error?.code || 'request_failed');
  }
  return value;
}
function table(target, rows, columns) {
  const wrap = document.createElement('div'); wrap.className='table-wrap';
  if (!rows.length) { wrap.textContent='Записей нет'; $(target).replaceChildren(wrap); return; }
  const t=document.createElement('table'), head=document.createElement('tr');
  for (const [label] of columns) { const th=document.createElement('th'); th.textContent=label; head.append(th); }
  t.append(head);
  for (const row of rows) {
    const tr=document.createElement('tr');
    for (const [, get] of columns) { const td=document.createElement('td'), value=get(row);
      if (value instanceof Node) td.append(value); else td.textContent=value == null ? '—' : String(value); tr.append(td); }
    t.append(tr);
  }
  wrap.append(t); $(target).replaceChildren(wrap);
}
function userButton(row) { const b=document.createElement('button'); b.textContent=row.id; b.addEventListener('click',()=>run(()=>loadUser(row.id))); return b; }
async function loadUsers(q='') {
  const rows=await request('users?q='+encodeURIComponent(q));
  table('users',rows,[['Пользователь',userButton],['Статус',r=>r.status],['Создан',r=>r.created_at]]);
}
async function loadUser(id) {
  const value=await request('users/'+id); selected=id; $('selected').textContent=id+' · '+value.status;
  table('quota',[value.quota],[['Начислено, bytes',r=>r.granted_bytes],['Списано, bytes',r=>r.used_bytes],['Доступно, bytes',r=>r.available_bytes],['Резерв, bytes',r=>r.reserved_bytes],['Недоступно по сроку/отзыву',r=>r.unavailable_bytes]]);
  table('devices',value.devices,[['Устройство',r=>r.id],['Платформа',r=>r.platform],['Отозвано',r=>r.revoked?'Да':'Нет']]);
  table('subscriptions',value.subscriptions,[['Подписка',r=>r.id],['Тариф',r=>r.plan.name+' v'+r.plan.version],['Основание',r=>r.basis],['Состояние',r=>r.state],['Начало',r=>r.starts_at],['Окончание',r=>r.ends_at],['Остаток, bytes',r=>r.remaining_bytes]]);
  table('history',value.history,[['№',r=>r.sequence],['Источник',r=>r.source_type],['Изменение, bytes',r=>r.delta_bytes],['Время',r=>r.created_at]]);
}
async function refresh() {
  await loadUsers();
  const plans=await request('plans');
  table('plans',plans,[['UUID версии',r=>r.id],['Тариф',r=>r.name],['Версия',r=>r.version],['Копейки (пример)',r=>r.price_minor],['Bytes',r=>r.quota_bytes],['Дни',r=>r.period_days]]);
  const previousPlan=$('plan-picker').value;
  $('plan-picker').replaceChildren(); const latest=new Set();
  for (const p of plans) { if(latest.has(p.code)) continue; latest.add(p.code); const o=document.createElement('option'); o.value=p.id; o.textContent=p.name+' v'+p.version; $('plan-picker').append(o); }
  const planOptions=[...$('plan-picker').options];
  const keep=planOptions.find(o=>o.value===previousPlan);
  const initial=plans.find(p=>p.code==='start');
  if(keep) $('plan-picker').value=keep.value; else if(initial) $('plan-picker').value=initial.id;
  table('nodes',await request('nodes'),[['Узел',r=>r.name],['Статус',r=>r.status],['Последний heartbeat',r=>r.last_seen_at],['Протокол',r=>r.peer_protocol_version],['Занято',r=>r.occupied],['Ёмкость',r=>r.capacity_limit],['Без ACK',r=>r.unacknowledged_commands],['Зависло >90s',r=>r.stalled_commands]]);
  table('audit',await request('audit'),[['Время',r=>r.time],['Автор',r=>r.actor],['Действие',r=>r.operation],['Пользователь',r=>r.user_id],['Причина',r=>r.reason],['Ключ',r=>r.idempotency_key]]);
  if(selected) await loadUser(selected);
}
async function run(fn) { try { status('Выполняется…'); await fn(); status('Готово'); } catch(e) { status('Операция не завершена: '+(e.message || 'network_error')); } }
async function action(body) {
  const signature=JSON.stringify(body); let key=pending.get(signature);
  if(!key) { key=crypto.randomUUID(); pending.set(signature,key); }
  await request('actions',{...body,idempotency_key:key});
  // Keep a completed form's key too: accidental duplicate submit must not grant twice.
  await refresh();
}
$('login-form').addEventListener('submit',e=>{e.preventDefault(); run(async()=>{
  const form=e.target, body=Object.fromEntries(new FormData(form));
  const result=await request('login',body); access=result.access_token; role=result.role;
  form.reset(); $('workspace').hidden=false; $('login').hidden=true;
  for(const id of ['changes','plan-form','demo-catalog']) $(id).hidden=role!=='admin';
  await refresh();
});});
$('search-form').addEventListener('submit',e=>{e.preventDefault(); run(()=>loadUsers(new FormData(e.target).get('q')));});
$('refresh').addEventListener('click',()=>run(refresh));
$('new-operation').addEventListener('click',()=>{pending.clear();status('Следующее применение создаст новую операцию. Проверьте историю предыдущей выдачи.');});
$('logout').addEventListener('click',()=>run(async()=>{ await request('logout',{}); access=null;selected=null;pending.clear();$('workspace').hidden=true;$('login').hidden=false; }));
$('demo-catalog').addEventListener('click',()=>run(()=>action({operation:'seed_demo_catalog',reason:'Initial illustrative catalog; payments disabled'})));
$('action-form').addEventListener('submit',e=>{e.preventDefault(); run(async()=>{
  if(!selected) throw new Error('select_user');
  const f=Object.fromEntries(new FormData(e.target)); const body={operation:f.operation,user_id:selected,reason:f.reason};
  if(f.operation==='credit'||f.operation==='debit') body.amount_bytes=integer(f.amount_bytes,true);
  else body.resource_id=f.operation==='grant_subscription'?f.plan_id:f.resource_id;
  await action(body);
});});
$('plan-form').addEventListener('submit',e=>{e.preventDefault(); run(async()=>{
  const f=Object.fromEntries(new FormData(e.target)), reason=f.reason; delete f.reason;
  for(const key of decimalFields) if(key in f) f[key]=integer(f[key],key!=='price_minor');
  await action({operation:'revise_plan',reason,plan:f});
});});
