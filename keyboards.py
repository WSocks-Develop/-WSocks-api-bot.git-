from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder, KeyboardButton
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def get_main_menu_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="👥 Реферальная система"))
    builder.row(KeyboardButton(text="📌 Информация"))
    builder.row(KeyboardButton(text="🛒 Подписки"))
    builder.row(KeyboardButton(text="❤️ Чат с поддержкой"))
    return builder.as_markup(resize_keyboard=True)

def get_subscriptions_menu_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="💳 Купить подписку"))
    builder.row(KeyboardButton(text="🕑 Продлить подписку"))
    builder.row(KeyboardButton(text="🎁 Пробная подписка"))
    builder.row(KeyboardButton(text="⬅️ Назад в главное меню"))
    return builder.as_markup(resize_keyboard=True)

def get_settings_menu_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🛠️ Настройка подключения"))
    builder.row(KeyboardButton(text="❤️ Чат с поддержкой"))
    builder.row(KeyboardButton(text="⬅️ Назад в главное меню"))
    return builder.as_markup(resize_keyboard=True)

def get_period_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="30 дней"), KeyboardButton(text="90 дней"))
    builder.row(KeyboardButton(text="180 дней"), KeyboardButton(text="360 дней"))
    builder.row(KeyboardButton(text="Назад"))
    return builder.as_markup(resize_keyboard=True)

def get_manual_kb():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📺 Android TV"))
    builder.row(KeyboardButton(text="💻 ПК"))
    builder.row(KeyboardButton(text="📱 Android"))
    builder.row(KeyboardButton(text="🍏 iOS / MacOS"))
    builder.row(KeyboardButton(text="Назад"))
    return builder.as_markup(resize_keyboard=True)

def get_inline_manual_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="💻 ПК", callback_data="setup_pc")
    builder.button(text="📱 Android", callback_data="setup_android")
    builder.button(text="🍏 iOS / MacOS", callback_data="setup_ios")
    builder.button(text="📺 Android TV", callback_data="setup_android_tv")
    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()

def get_terms_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Согласиться", callback_data="accept_terms")
    return builder.as_markup()

def get_trial_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎁 Пробный период", callback_data="use_trial")
    return builder.as_markup()

def get_referral_choice_kb(subscriptions, tg_id):
    builder = InlineKeyboardBuilder()
    for sub in subscriptions:
        builder.row(
            InlineKeyboardButton(
                text=f"{sub['email']} (до {sub['expiry_date'].strftime('%Y-%m-%d')})",
                callback_data=f"extend_ref_{sub['email']}_{tg_id}"
            )
        )
    return builder.as_markup()

def get_referral_link_kb(ref_link):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Переслать ➦",
            url=ref_link
        )
    )
    return builder.as_markup()

def get_payment_confirm_kb(payment_link: str):
    builder = InlineKeyboardBuilder()

    # Кнопка с ссылкой на оплату
    builder.button(text="💳 Оплатить", url=payment_link)

    # Кнопки подтверждения и отмены платежа
    builder.button(text="✅ Подтвердить оплату", callback_data="confirm_payment")
    builder.button(text="❌ Отмена", callback_data="cancel_payment")

    builder.adjust(1, 2)

    return builder.as_markup()

def get_info_keyboard():
    """Создаёт inline-клавиатуру для раздела 'Информация'."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📃 Пользовательское соглашение", callback_data="info_terms")
    builder.button(text="🔐 Политика конфиденциальности", callback_data="info_privacy")
    builder.button(text="ℹ️ Информация о подписке", callback_data="info_subscription")
    builder.button(text="🧦 Информация о сервисе", callback_data="info_service")
    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()


def get_connection_guide_button():
    builder = InlineKeyboardBuilder()
    builder.button(text="📌 Как подключить?", callback_data="show_setup_menu")
    return builder.as_markup()

def get_privacy_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Открыть", url="https://telegra.ph/Politika-konfidencialnosti-08-04-7")
    return builder.as_markup()
