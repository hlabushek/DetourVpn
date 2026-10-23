import { handle } from '../lib/handle.js';

export default async function callbackQuery(payload, ctx) {
  return handle(payload, ctx, 'callback_query');
}
