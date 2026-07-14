from datetime import datetime
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def dates_keyboard(calls: list[dict], back: str | None = None) -> InlineKeyboardMarkup:
    """Кнопки с уникальными датами для статистики."""
    seen = []
    rows = []
    for call in calls:
        date_str = datetime.fromisoformat(call["call_dt"]).strftime("%d.%m.%Y")
        if date_str not in seen:
            seen.append(date_str)
            rows.append([InlineKeyboardButton(
                text=date_str,
                callback_data=f"stats:date:{date_str}",
            )])
    if back:
        rows.append([InlineKeyboardButton(text="Назад", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def times_keyboard(calls: list[dict], back: str | None = None) -> InlineKeyboardMarkup:
    """Кнопки с временами созвонов одной даты."""
    rows = []
    for call in calls:
        time_str = datetime.fromisoformat(call["call_dt"]).strftime("%H:%M МСК")
        rows.append([InlineKeyboardButton(
            text=time_str,
            callback_data=f"stats:call:{call['id']}",
        )])
    if back:
        rows.append([InlineKeyboardButton(text="Назад", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def calls_keyboard(calls: list[dict], prefix: str, back: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    for call in calls:
        dt = datetime.fromisoformat(call["call_dt"]).strftime("%d.%m %H:%M")
        rows.append([InlineKeyboardButton(
            text=f"📅 {dt}",
            callback_data=f"{prefix}:{call['id']}",
        )])
    if back:
        rows.append([InlineKeyboardButton(text="Назад", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard(back: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data=back)],
    ])


def slot_keyboard(calls: list[dict]) -> InlineKeyboardMarkup:
    """Кнопки выбора слота созвона для кандидата."""
    from datetime import timezone
    now = datetime.now(timezone.utc).astimezone()
    rows = []
    for call in calls:
        dt = datetime.fromisoformat(call["call_dt"])
        label = dt.strftime("%d.%m.%Y %H:%M МСК")
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"slot:{call['id']}",
        )])
    rows.append([InlineKeyboardButton(
        text="Ни одно время не подходит",
        callback_data="slot:none",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rsvp_keyboard(call_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, буду на созвоне!", callback_data=f"rsvp:confirmed:{call_id}")],
        [InlineKeyboardButton(text="❌ В этот раз не смогу", callback_data=f"rsvp:declined:{call_id}")],
    ])


def confirmed_keyboard(call_id: int) -> InlineKeyboardMarkup:
    """Клавиатура после подтверждения участия — с кнопкой отмены."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отказаться от созвона", callback_data=f"cancel_slot:{call_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:start")],
    ])


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:start")],
    ])


def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Назначить созвон",       callback_data="admin:new_call")],
        [InlineKeyboardButton(text="📝 Редактировать вакансию", callback_data="admin:vacancy")],
        [InlineKeyboardButton(text="👥 Кураторы",                callback_data="admin:curators")],
        [InlineKeyboardButton(text="🆘 Контакт для подбора времени", callback_data="admin:support")],
        [InlineKeyboardButton(text="📊 Статистика",             callback_data="admin:stats")],
        [InlineKeyboardButton(text="🔗 Реферальные ссылки",     callback_data="admin:links")],
        [InlineKeyboardButton(text="📥 Экспорт в Excel",        callback_data="admin:export")],
        [InlineKeyboardButton(text="📢 Рассылка",               callback_data="admin:broadcast")],
        [InlineKeyboardButton(text="🚫 Чёрный список",          callback_data="admin:blacklist")],
        [InlineKeyboardButton(text="✏️ Редактировать созвон",    callback_data="admin:edit_call")],
        [InlineKeyboardButton(text="❌ Отменить созвон",        callback_data="admin:cancel_call")],
    ])


def broadcast_scope_keyboard() -> InlineKeyboardMarkup:
    """Выбор охвата рассылки: все или конкретный созвон."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Все кандидаты", callback_data="bscope:all")],
        [InlineKeyboardButton(text="📅 По конкретному созвону", callback_data="bscope:call")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin:cancel_fsm")],
    ])


def broadcast_segment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Все кандидаты (ввели ФИО)",  callback_data="admin:broadcast:all")],
        [InlineKeyboardButton(text="📩 Не ответили на инвайт",      callback_data="admin:broadcast:invited")],
        [InlineKeyboardButton(text="✅ Подтвердили участие",         callback_data="admin:broadcast:confirmed")],
        [InlineKeyboardButton(text="❌ Отказались",                  callback_data="admin:broadcast:declined")],
        [InlineKeyboardButton(text="🏆 Получили контакт",           callback_data="admin:broadcast:contact_issued")],
        [InlineKeyboardButton(text="🔙 Отмена",                     callback_data="admin:cancel_fsm")],
    ])


def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить",  callback_data="admin:broadcast:send")],
        [InlineKeyboardButton(text="🔙 Отмена",     callback_data="admin:cancel_fsm")],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin:cancel_fsm")],
    ])
