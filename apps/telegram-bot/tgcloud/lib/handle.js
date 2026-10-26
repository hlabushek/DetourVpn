import { api } from 'sdk';
import { config } from './config.js';
import { pages, route, validateConfig, view } from './menu.js';
import { admit } from './storage.js';

const positive = (v) => Number.isSafeInteger(v) && v > 0;
const foreign = (m) => ['forward_origin', 'forward_date', 'via_bot', 'business_connection_id', 'sender_chat'].some((key) => m[key] != null);

function principal(payload, ctx, kind, now) {
  const update = ctx?.update;
  if (!update || !Number.isSafeInteger(update.update_id) || update.update_id < 0 ||
      update[kind] == null || JSON.stringify(update[kind]) !== JSON.stringify(payload)) return null;
  const user = payload?.from;
  const message = kind === 'message' ? payload : payload.message;
  if (!positive(user?.id) || user.is_bot !== false || !message ||
      message.chat?.type !== 'private' || message.chat.id !== user.id ||
      !positive(message.message_id) || foreign(message)) return null;
  if (kind === 'message') {
    if (!Number.isSafeInteger(message.date) || now - message.date > 300 || now - message.date < -60) return null;
  } else if (typeof payload.id !== 'string' || payload.id.length < 1 || payload.id.length > 256 ||
             message.from?.is_bot !== true || !positive(message.date) ||
             typeof payload.data !== 'string' || payload.data.length > 64) return null;
  return { updateId: update.update_id, userId: user.id, message };
}

async function ack(id, text) {
  // Never forward SDK exceptions (which may contain request data) to logs or responses.
  try { await api.answerCallbackQuery({ callback_query_id: id, ...(text ? { text } : {}) }); } catch { /* No automatic retry. */ }
}

export async function handle(payload, ctx, kind) {
  const now = Math.floor(Date.now() / 1000);
  const who = principal(payload, ctx, kind, now);
  if (!who) return { status: 'ignored' };
  let page;
  if (kind === 'callback_query') {
    page = payload.data.startsWith('detour:') ? payload.data.slice(7) : null;
  } else {
    page = route(payload.text ?? '', config.botUsername);
    if (page === null) return { status: 'ignored' };
  }
  let allowed;
  try { validateConfig(config); allowed = await admit(who.updateId, who.userId, now); }
  catch { if (kind === 'callback_query') await ack(payload.id, 'Сервис временно недоступен.'); return { status: 'unavailable' }; }
  if (allowed !== 'accepted') {
    if (kind === 'callback_query') await ack(payload.id, allowed === 'rate_limited' ? 'Слишком много запросов. Подождите минуту.' : undefined);
    return { status: allowed };
  }
  if (kind === 'callback_query' && !pages.has(page)) {
    await ack(payload.id, 'Эта кнопка больше не действует. Откройте /start.');
    return { status: 'ignored' };
  }
  if (kind === 'callback_query') await ack(payload.id);
  const startPayload = kind === 'message'
    ? /^\/start(?:@[A-Za-z0-9_]+)? ([0-9a-f]{32})$/.exec(payload.text ?? '')?.[1] ?? null
    : null;
  const content = view(page, config, startPayload);
  try {
    if (kind === 'callback_query') {
      await api.editMessageText({ chat_id: who.userId, message_id: who.message.message_id, ...content });
    } else {
      await api.sendMessage({ chat_id: who.userId, ...content, protect_content: true });
    }
  } catch { return { status: 'delivery_unknown' }; }
  return { status: 'processed' };
}
