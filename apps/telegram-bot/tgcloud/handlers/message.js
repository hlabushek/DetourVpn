import { handle } from '../lib/handle.js';

export default async function message(payload, ctx) {
  return handle(payload, ctx, 'message');
}
