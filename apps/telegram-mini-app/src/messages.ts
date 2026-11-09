import { ApiError } from "./api.mjs";

/** Server codes that mean the cabinet session is gone; the UI offers to reopen. */
export const SESSION_GONE = new Set([
    "unauthorized",
    "invalid_refresh",
    "refresh_reused",
    "invalid_init_data",
    "init_data_replayed",
]);

export function message(error: unknown) {
    if (error instanceof ApiError) {
        const messages: Record<string, string> = {
            referral_self_invitation: "Свой код использовать нельзя.",
            referral_binding_conflict: "Пригласивший уже закреплён. Сменить его нельзя.",
            referral_binding_ineligible:
                "Код доступен новым аккаунтам до первого подключения, в течение 7 суток. Пригласивший должен зарегистрироваться раньше.",
            referral_code_unavailable: "Код недоступен. Проверьте его у пригласившего.",
            referral_campaign_unavailable: "Кампания завершена или приостановлена.",
            referral_not_qualified:
                "Приглашение ещё не подходит для бонуса. Нужен завершённый VPN-сеанс приглашённого с подтверждённым трафиком.",
            referral_inviter_limit: "Лимит бонусов этой кампании исчерпан.",
            referral_budget_exhausted: "Бюджет кампании исчерпан. Обратитесь в поддержку при несогласии.",
            referral_idempotency_conflict: "Повтор запроса конфликтует с предыдущим. Обновите историю.",
            invalid_init_data: "Не удалось подтвердить вход. Закройте и откройте Mini App заново.",
            init_data_replayed: "Этот вход уже использован. Откройте Mini App заново.",
            invalid_login_attempt: "Попытка входа недействительна. Начните вход заново в Android-приложении.",
            mini_app_disabled: "Кабинет ещё не включён на сервере.",
            unauthorized: "Сессия истекла. Откройте Mini App заново.",
            invalid_refresh: "Сессия истекла. Откройте Mini App заново.",
            refresh_reused: "Сессия отозвана. Откройте Mini App заново.",
            reward_cooldown: "Награда пока недоступна. Обновите кабинет чуть позже.",
            reward_limit_reached: "Лимит награды достигнут. Следующее обновление — по UTC.",
            rate_limited: "Слишком много запросов. Попробуйте через минуту.",
            device_limit_reached:
                "Достигнут лимит устройств. Отключите ненужное устройство или отмените неиспользованный код.",
            link_code_exists: "Этот запрос уже обработан. Отмените неиспользованный код и создайте новый.",
            forbidden: "Это действие доступно только владельцу аккаунта.",
            not_found: "Устройство или код уже не существует. Обновите список.",
        };
        return messages[error.message] ?? "Сервер не смог завершить запрос. Попробуйте ещё раз.";
    }
    return "Ответ не получен. Повтор награды использует тот же ключ, пока кабинет открыт.";
}
