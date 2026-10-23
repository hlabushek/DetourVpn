// TEST ONLY SDK substitute. SQL runs on genuine local SQLite; Telegram is never called.
import { DatabaseSync } from 'node:sqlite';

let sqlite;
export const calls = [];
export let brokenStorage = false;
export let brokenDelivery = false;
export function reset() {
  sqlite?.close();
  sqlite = new DatabaseSync(':memory:');
  sqlite.exec('CREATE TABLE bot_menu_updates(update_id INTEGER PRIMARY KEY, expires_at INTEGER NOT NULL); CREATE INDEX bot_menu_updates_expiry ON bot_menu_updates(expires_at); CREATE TABLE bot_menu_limits(bucket TEXT PRIMARY KEY, hits INTEGER NOT NULL, expires_at INTEGER NOT NULL); CREATE INDEX bot_menu_limits_expiry ON bot_menu_limits(expires_at);');
  calls.length = 0;
  brokenStorage = brokenDelivery = false;
}
export function failStorage() { brokenStorage = true; }
export function failDelivery() { brokenDelivery = true; }
export const db = {
  async run(query, params) {
    if (brokenStorage) throw new Error('TEST ONLY unavailable DB');
    const statement = sqlite.prepare(query);
    if (/RETURNING/i.test(query)) {
      return { rowsAffected: 1, rows: statement.all(params) };
    }
    return { rowsAffected: Number(statement.run(params).changes), rows: [] };
  },
};
export function rows(tableName) {
  if (!['bot_menu_updates', 'bot_menu_limits'].includes(tableName)) throw new Error('test_table_invalid');
  return sqlite.prepare(`SELECT * FROM ${tableName}`).all();
}
export const api = Object.fromEntries(['sendMessage', 'editMessageText', 'answerCallbackQuery'].map((method) => [method, async (params) => {
  calls.push({ method, params });
  if (brokenDelivery) throw new Error('TEST ONLY ambiguous delivery');
  return true;
}]));

// Metadata-only schema DSL double, not a platform migration test.
export function integer(name) { return column(name); }
export function text(name) { return column(name); }
function column(name) {
  return { name, primaryKey() { this.primary = true; return this; }, notNull() { this.required = true; return this; } };
}
export function index(name) { return { name, on: (col) => ({ name, column: col.name }) }; }
export function table(name, columns, extras) { return { name, columns, indexes: extras(columns) }; }
