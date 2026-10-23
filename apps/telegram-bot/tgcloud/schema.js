import { table, integer, text, index } from 'sdk/db';

// Operational metadata only. Never store messages, codes, sessions or device keys.
export const updates = table('bot_menu_updates', {
  updateId: integer('update_id').primaryKey(),
  expiresAt: integer('expires_at').notNull(),
}, (t) => ({ expiry: index('bot_menu_updates_expiry').on(t.expiresAt) }));

export const limits = table('bot_menu_limits', {
  bucket: text('bucket').primaryKey(),
  hits: integer('hits').notNull(),
  expiresAt: integer('expires_at').notNull(),
}, (t) => ({ expiry: index('bot_menu_limits_expiry').on(t.expiresAt) }));
