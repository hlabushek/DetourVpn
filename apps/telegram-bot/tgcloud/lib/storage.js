import { db } from 'sdk';

export async function admit(updateId, userId, now) {
  // The platform DB is authoritative. Missing schema/storage never falls back to RAM.
  await db.run('DELETE FROM bot_menu_updates WHERE expires_at <= :now', { ':now': now });
  await db.run('DELETE FROM bot_menu_limits WHERE expires_at <= :now', { ':now': now });
  const claimed = await db.run(
    'INSERT INTO bot_menu_updates(update_id, expires_at) VALUES(:id, :expiry) ON CONFLICT(update_id) DO NOTHING',
    { ':id': updateId, ':expiry': now + 86400 },
  );
  if (claimed.rowsAffected !== 1) return 'duplicate';
  const minute = Math.floor(now / 60);
  for (const [bucket, limit] of [[`global:${minute}`, 120], [`user:${userId}:${minute}`, 20]]) {
    const result = await db.run(
      'INSERT INTO bot_menu_limits(bucket, hits, expires_at) VALUES(:bucket, 1, :expiry) ON CONFLICT(bucket) DO UPDATE SET hits = hits + 1 RETURNING hits',
      { ':bucket': bucket, ':expiry': (minute + 2) * 60 },
    );
    if (!Array.isArray(result.rows) || !Number.isSafeInteger(result.rows[0]?.hits)) throw new Error('bot_storage_invalid');
    if (result.rows[0].hits > limit) return 'rate_limited';
  }
  return 'accepted';
}
