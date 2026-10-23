const callback = (text, page) => ({ text, callback_data: `detour:${page}` });

export const commands = Object.freeze([
  { command: 'start', description: 'Главное меню DetourVpn' },
  { command: 'cabinet', description: 'Открыть кабинет' },
  { command: 'android', description: 'Приложение Android' },
  { command: 'connect', description: 'Как подключиться' },
  { command: 'help', description: 'Помощь' },
  { command: 'privacy', description: 'Данные и приватность' },
]);

export const pages = new Set(['home', 'cabinet', 'android', 'connect', 'help', 'privacy', 'login']);

export function validateConfig(input) {
  if (!input || typeof input !== 'object' ||
      Object.keys(input).sort().join() !== 'androidUrl,botUsername,miniAppUrl,privacyUrl,supportUrl') {
    throw new Error('bot_configuration_invalid');
  }
  if (typeof input.botUsername !== 'string' ||
      (input.botUsername !== '' && !/^[A-Za-z][A-Za-z0-9_]{1,28}bot$/i.test(input.botUsername))) {
    throw new Error('bot_configuration_invalid');
  }
  // Portable across V8 runtimes; no dependency on Node or browser globals.
  // Only ASCII public DNS hosts and a conservative URL character set are accepted.
  for (const key of ['miniAppUrl', 'androidUrl', 'supportUrl', 'privacyUrl']) {
    const value = input[key];
    if (typeof value !== 'string' || value.length > 1024) throw new Error('bot_configuration_invalid');
    if (value === '') continue;
    const match = /^https:\/\/([a-z0-9-]+(?:\.[a-z0-9-]+)+)(\/[^\s<>"'\\]*)?$/i.exec(value);
    if (!match || match[1].length > 253 || match[1].split('.').some((label) => label.length > 63 || label.startsWith('-') || label.endsWith('-')) ||
        !/\.[a-z]{2,63}$/i.test(match[1]) ||
        /\.(?:localhost|local|internal|test|invalid)$/i.test(match[1])) {
      throw new Error('bot_configuration_invalid');
    }
  }
  return input;
}

export function view(page, settings) {
  if (!pages.has(page)) page = 'home';
  const home = [
    [settings.miniAppUrl ? { text: '◈ Открыть кабинет', web_app: { url: settings.miniAppUrl } } : callback('◈ Кабинет', 'cabinet')],
    [callback('↓ Android', 'android'), callback('Как подключиться', 'connect')],
    [callback('Помощь', 'help'), callback('Данные и приватность', 'privacy')],
  ];
  const back = [callback('‹ Главное меню', 'home')];
  let text;
  const rows = [];
  switch (page) {
    case 'cabinet':
      text = settings.miniAppUrl
        ? 'Ваш кабинет\n\nБаланс, история трафика, доступные бонусы и приглашения — в Mini App. VPN подключается в Android-приложении.'
        : 'Ваш кабинет\n\nMini App уже разработан, но публичный адрес пока не настроен. Кнопка открытия появится здесь после настройки.';
      if (settings.miniAppUrl) rows.push([{ text: 'Открыть кабинет', web_app: { url: settings.miniAppUrl } }]);
      break;
    case 'android':
      text = settings.androidUrl
        ? 'DetourVpn для Android\n\nОткройте страницу загрузки и следуйте инструкции установки. Сейчас это тестовая версия: подключение до 60 секунд с резервом 1 048 576 байт.'
        : 'DetourVpn для Android\n\nПриложение находится на этапе тестирования. Публичная ссылка на скачивание пока не опубликована.\n\nТекущие ограничения: подключение до 60 секунд с резервом 1 048 576 байт.';
      if (settings.androidUrl) rows.push([{ text: 'Открыть загрузку Android', url: settings.androidUrl }]);
      rows.push([callback('Как подключиться', 'connect')]);
      break;
    case 'connect':
      text = 'Как подключиться\n\n1. Откройте DetourVpn на Android.\n2. Начните вход в приложении и перейдите к боту по выданной ссылке.\n3. Код из бота вводится только в приложении, где вы начали вход.\n4. Выберите сервер, нажмите «Подключить» и подтвердите системное разрешение Android.\n\nВыдача кодов в serverless-версии пока не подключена. Она требует защищённой связи с API; этот бот не выдаёт тестовые коды и не создаёт VPN-подключение сам.';
      rows.push([callback('Приложение Android', 'android')]);
      break;
    case 'help':
      text = 'Помощь\n\nНет подключения? Проверьте интернет и доступность сервера в приложении. Истёк тестовый сеанс? Дождитесь завершения отключения.\n\nНе присылайте код входа, токены, ключи, конфигурацию VPN или историю посещённых сайтов. Для обращения достаточно версии приложения и текста ошибки без личных данных.';
      if (settings.supportUrl) rows.push([{ text: 'Связаться с поддержкой', url: settings.supportUrl }]);
      else text += '\n\nКанал поддержки пока не опубликован. Сообщения в этом боте не создают обращение оператору.';
      break;
    case 'privacy':
      text = 'Данные и приватность\n\nЭтот serverless-бот обрабатывает личные сообщения и нажатия кнопок. Код меню не сохраняет их текст, файлы, код входа или историю сайтов.\n\nДля защиты от повторов и частых запросов хранятся ID обновления до 24 часов и счётчик с Telegram ID до 2 минут. Истёкшие записи удаляются при следующем допустимом обращении. История самого чата остаётся в Telegram по его правилам.\n\nЭто описание текущего тестового бота, а не политика готового коммерческого сервиса.';
      if (settings.privacyUrl) rows.push([{ text: 'Политика конфиденциальности', url: settings.privacyUrl }]);
      break;
    case 'login':
      text = 'Вход пока недоступен\n\nСвязь serverless-бота с API авторизации ещё не настроена. Код не выпущен. Начните новую попытку в Android-приложении после подключения этой функции.\n\nНе отправляйте код входа или ключ устройства в этот чат.';
      break;
    default:
      text = 'DetourVpn\nВаш маршрут под контролем.\n\nПриложение Android управляет подключением. В кабинете — трафик, история и доступные бонусы.\n\nСервис находится на этапе тестирования. Выберите нужный раздел.';
  }
  rows.push(...(page === 'home' ? home : [back]));
  return { text, reply_markup: { inline_keyboard: rows } };
}

export function route(text, username) {
  if (typeof text !== 'string' || text.length > 4096) return 'home';
  const match = /^\/([a-z]+)(?:@([A-Za-z0-9_]+))?(?: ([^\s]+))?$/.exec(text);
  if (!match) return 'home';
  if (match[2] && match[2].toLowerCase() !== username.toLowerCase()) return null;
  if (match[1] === 'start') return match[3] ? 'login' : 'home';
  if (match[3]) return 'home';
  return { menu: 'home', cabinet: 'cabinet', android: 'android', connect: 'connect', help: 'help', privacy: 'privacy', login: 'login' }[match[1]] ?? 'home';
}
