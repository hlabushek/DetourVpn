// Pure display helpers. Byte amounts stay exact: they are decimal strings handled with BigInt only.
const UNITS = [
  ['ТиБ', 1024n ** 4n],
  ['ГиБ', 1024n ** 3n],
  ['МиБ', 1024n ** 2n],
  ['КиБ', 1024n],
];

/** "5368709120" -> "5 ГиБ". Exact digits are kept in a tooltip by the caller. */
export function formatBytes(value) {
  if (value == null || !/^-?[0-9]+$/.test(String(value))) return '—';
  const bytes = BigInt(value), sign = bytes < 0n ? '-' : '', abs = bytes < 0n ? -bytes : bytes;
  for (const [name, size] of UNITS) {
    if (abs < size) continue;
    const hundredths = (abs * 100n) / size, whole = hundredths / 100n, part = hundredths % 100n;
    const fraction = part === 0n ? '' : ',' + String(part).padStart(2, '0').replace(/0$/, '');
    return sign + whole + fraction + ' ' + name;
  }
  return sign + abs + ' Б';
}

/** "1,5" + "gib" -> exact byte count as a decimal string; at most 3 decimals, positive only. */
export function parseAmount(text, unit) {
  const size = unit === 'gib' ? 1024n ** 3n : unit === 'mib' ? 1024n ** 2n : null;
  const match = /^([0-9]{1,9})(?:[.,]([0-9]{1,3}))?$/.exec(String(text).trim());
  if (size === null || !match) throw new Error('invalid_amount');
  const decimals = match[2] ?? '';
  const scaled = BigInt(match[1] + decimals.padEnd(3, '0'));
  const bytes = (scaled * size) / 1000n;
  if (bytes <= 0n || bytes > 9223372036854775807n) throw new Error('invalid_amount');
  return String(bytes);
}

export function formatTime(iso) {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
}

/** "5 мин назад" style age; never negative. */
export function ago(iso, now = Date.now()) {
  const time = new Date(iso).getTime();
  if (!iso || Number.isNaN(time)) return 'нет данных';
  const seconds = Math.max(0, Math.round((now - time) / 1000));
  if (seconds < 60) return seconds + ' с назад';
  if (seconds < 3600) return Math.floor(seconds / 60) + ' мин назад';
  if (seconds < 86400) return Math.floor(seconds / 3600) + ' ч назад';
  return Math.floor(seconds / 86400) + ' дн назад';
}

export const OPERATION_LABELS = {
  credit: 'Добавлен трафик',
  debit: 'Уменьшен трафик',
  grant_subscription: 'Выдан тариф',
  revoke_subscription: 'Отменён тариф',
  revoke_device: 'Отозвано устройство',
  revise_plan: 'Новая версия тарифа',
  seed_demo_catalog: 'Демо-каталог тарифов',
  grant_beta_invite: 'Приглашён тестер',
  revoke_beta_invite: 'Отозван доступ тестера',
  resolve_unknown_window: 'Разбор неизвестного окна',
  import_panel_server: 'Добавлен сервер из панели',
};

export const INVITE_STATES = {
  waiting: ['Ждёт входа', 'warn'],
  joined: ['Вошёл', 'ok'],
  revoked: ['Отозван', 'off'],
};

export const ERROR_MESSAGES = {
  beta_invite_exists: 'Этот человек уже приглашён.',
  beta_invite_not_found: 'Активного приглашения с таким Telegram ID нет.',
  beta_invites_disabled: 'Закрытая бета на сервере не включена.',
  beta_invite_revoke_pending: 'Прошлый отзыв ещё не подтверждён на узле. Повторите позже.',
  beta_invite_account_unavailable: 'Аккаунт этого человека сейчас недоступен для приглашения.',
  beta_invite_reason_must_not_contain_subject: 'В пометке не должно быть Telegram ID.',
  reason_must_not_contain_identifier: 'В пометке не должно быть Telegram ID.',
  invalid_telegram_id: 'Telegram ID: только цифры, без пробелов и @.',
  invalid_integer: 'Введите целое положительное число.',
  invalid_amount: 'Введите количество, например 5 или 1,5.',
  admin_unauthorized: 'Нужно войти заново.',
  forbidden: 'Это действие доступно только администратору.',
  rate_limited: 'Слишком много попыток. Подождите минуту.',
  select_user: 'Сначала выберите пользователя.',
  admin_idempotency_conflict: 'Повтор операции отличается от предыдущей. Начните новую операцию.',
  network_error: 'Нет связи с сервером. Повторите: операция не дублируется.',
  // Panel server link checks (ADR-111). The messages never repeat the link.
  panel_link_format: 'Ссылка повреждена: вставьте одну ссылку vless:// целиком, без пробелов.',
  panel_link_scheme: 'Нужна ссылка, которая начинается с vless://.',
  panel_link_uuid: 'В ссылке нет корректного UUID клиента.',
  panel_link_host: 'Адрес сервера в ссылке не подходит (нужно имя или IPv4).',
  panel_link_port: 'Порт в ссылке не указан или неверен.',
  panel_link_security: 'Поддерживается только security=reality.',
  panel_link_public_key: 'Нет или неверен публичный ключ Reality (pbk).',
  panel_link_sni: 'Нет или неверно имя сайта для Reality (sni).',
  panel_link_short_id: 'Short ID (sid) должен быть шестнадцатеричным, до 16 символов.',
  panel_link_fingerprint: 'Нет или неизвестен отпечаток TLS (fp).',
  panel_link_flow: 'Поддерживается только flow=xtls-rprx-vision или пустой flow.',
  panel_link_transport: 'Поддерживается только транспорт type=tcp.',
  panel_link_unknown_field: 'В ссылке есть неизвестный параметр. Скопируйте ссылку из панели заново.',
};

export function errorText(code) {
  return ERROR_MESSAGES[code] || 'Операция не завершена: ' + (code || 'ошибка сети');
}

/** Ready-to-send message for a new tester. The bot link is public; no personal data is included. */
export function inviteMessage(botUsername) {
  const link = /^[A-Za-z][A-Za-z0-9_]{1,28}$/.test(botUsername || '') ? 'https://t.me/' + botUsername : '';
  return [
    'Привет! Приглашаю тебя в закрытый тест DetourVPN.',
    '',
    '1. Открой бота' + (link ? ': ' + link : ' (ссылку пришлю отдельно)') + ' и нажми кнопку приложения (Mini App).',
    '2. Если там написано «Доступ пока не открыт» — нажми «Скопировать ID» и пришли мне число.',
    '3. Я добавлю тебя, и ты сможешь установить приложение и войти.',
    '',
    'Это тест без гарантий: возможны сбои.',
  ].join('\n');
}
