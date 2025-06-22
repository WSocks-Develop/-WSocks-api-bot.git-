import uuid
import logging
import sqlite3
import random
import string
import urllib.parse

from aiogram import Router, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from py3xui import Client
from datetime import datetime, timezone, timedelta
from states import SubscriptionStates
from keyboards import (get_main_menu_kb, get_subscriptions_menu_kb,
                      get_settings_menu_kb, get_period_kb, get_manual_kb, get_terms_kb,
                      get_payment_confirm_kb, get_privacy_kb, get_connection_guide_button,
                       get_inline_manual_kb, get_info_keyboard, get_referral_choice_kb, get_referral_link_kb)
from database import (get_user, create_user, update_user_terms, get_trial_status,
                     create_trial_user, activate_trial, add_subscription_to_db,
                     add_payment_to_db, update_subscriptions_on_db, get_referrals,
                              apply_referral_bonus, has_been_referred)
from payments import create_payment_link, check_payment_status
from xui_utils import get_best_panel, get_api_by_name, get_active_subscriptions, extend_subscription

import manual_text as mt
import config as cfg

from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder, KeyboardButton
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton




router = Router()

def generate_sub(length=16):
    chars = string.ascii_lowercase + string.digits  # строчные буквы + цифры
    return ''.join(random.choices(chars, k=length))

DEVICES = [
    {"name": "💻 ПК", "callback": "setup_pc", "reply_name": "💻 ПК", "download_url": "https://github.com/Ckr1tn1y/WSocks-VPN--NekoBox-/archive/refs/heads/main.zip"},
    {"name": "📱 Android", "callback": "setup_android", "reply_name": "📱 Android", "download_url": "https://play.google.com/store/apps/details?id=com.v2raytun.android"},
    {"name": "📺 Android TV", "callback": "setup_android_tv", "reply_name": "📺 Android TV", "download_url": "https://play.google.com/store/apps/details?id=com.google.android.videos"},
    {"name": "🍏 iOS / MacOS", "callback": "setup_ios", "reply_name": "🍏 iOS / MacOS", "download_url": "https://apps.apple.com/ru/app/v2raytun/id6476628951"}
]

@router.message(Command("start"))
async def send_welcome(message: Message, state: FSMContext, bot: Bot):
    tg_id = message.from_user.id
    args = message.text.split()
    referrer_id = int(args[1].split('_')[1]) if len(args) > 1 and args[1].startswith("ref_") else None


    user = await get_user(tg_id)
    if not user:
        if referrer_id and not await has_been_referred(tg_id):
            await create_user(tg_id, referrer_id)
            # Обрабатываем бонус для реферера
            active_subs = get_active_subscriptions(referrer_id)  # Сначала получаем подписки
            referrer_subs = [sub for sub in active_subs if "DE-FRA-TRIAL" not in sub['email']]  # Затем фильтруем
            current_panel = get_best_panel()
            api = get_api_by_name(current_panel['name'])

            if not referrer_subs:  # Нет активных подписок
                expiry_time = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
                email = f"DE-FRA-USER-{referrer_id}-{uuid.uuid4().hex[:6]}"
                subscription_id = generate_sub(16)
                new_client = Client(
                    id=str(uuid.uuid4()),
                    enable=True,
                    tg_id=referrer_id,
                    expiry_time=int(datetime.strptime(expiry_time, "%Y-%m-%d %H:%M:%S").timestamp() * 1000),
                    flow="xtls-rprx-vision",
                    email=email,
                    sub_id=subscription_id,
                    limit_ip=5
                )
                api.client.add(1, [new_client])
                await add_subscription_to_db(referrer_id, email, current_panel['name'], expiry_time)
                await apply_referral_bonus(referrer_id, tg_id)
                subscription_key = current_panel["create_key"](new_client)
                await bot.send_message(
                    referrer_id,
                    f"🎉 Новый пользователь зарегистрировался по вашей ссылке! Вам создана подписка на 7 дней:\n"
                    f"```\n"
                    f"{subscription_key}\n"
                    f"```",
                    parse_mode="MARKDOWN",
                    reply_markup=get_connection_guide_button()
                )
            elif len(referrer_subs) == 1:  # Одна активная подписка
                sub = referrer_subs[0]
                api = get_api_by_name(sub['panel'])
                new_expiry = (datetime.now(timezone.utc) if sub['is_expired'] else sub['expiry_date']) + timedelta(days=7)
                extend_subscription(sub['email'], sub['id'], 7, referrer_id, sub['sub_id'], api)
                await update_subscriptions_on_db(sub['email'], new_expiry.strftime("%Y-%m-%d %H:%M:%S"))
                await apply_referral_bonus(referrer_id, tg_id)
                await bot.send_message(
                    referrer_id,
                    f"🎉 Новый пользователь зарегистрировался по вашей ссылке! Подписка {sub['email']} продлена на 7 дней до {new_expiry.strftime('%Y-%m-%d')}."
                )
            else:  # Несколько активных подписок
                await bot.send_message(
                    referrer_id,
                    "🎉 Новый пользователь зарегистрировался по вашей ссылке! Выберите подписку для продления на 7 дней:",
                    reply_markup=get_referral_choice_kb(referrer_subs, tg_id)
                )
                await state.update_data(referrer_id=referrer_id, referee_id=tg_id)
        else:
            await create_user(tg_id)
        user = await get_user(tg_id)

    if not user['accepted_terms']:
        await message.answer(f"{mt.license_agreement_text}", parse_mode="HTML", reply_markup=get_terms_kb())
        await state.set_state(SubscriptionStates.wait_for_accept)
    else:
        await message.answer("Добро пожаловать! Чем могу помочь?", reply_markup=get_main_menu_kb())

@router.callback_query(lambda c: c.data.startswith("extend_ref_"))
async def handle_referral_choice(callback: CallbackQuery, bot: Bot):
    try:
        # Разбираем callback_data: extend_ref_<email>_<referee_id>
        parts = callback.data.split('_')
        email = callback.data.split("_")[2] # Собираем email обратно
        referee_id = int(parts[3])
        referrer_id = callback.from_user.id

        referrer_subs = get_active_subscriptions(referrer_id)
        sub = next((s for s in referrer_subs if s['email'] == email), None)
        if sub:
            api = get_api_by_name(sub['panel'])  # Используем панель подписки
            new_expiry = (datetime.now(timezone.utc) if sub['is_expired'] else sub['expiry_date']) + timedelta(days=7)
            extend_subscription(sub['email'], sub['id'], 7, referrer_id, sub['sub_id'], api)
            await update_subscriptions_on_db(sub['email'], new_expiry.strftime("%Y-%m-%d %H:%M:%S"))
            await apply_referral_bonus(referrer_id, referee_id)
            await bot.send_message(referrer_id,
                                      f"🎉 Подписка {sub['email']} продлена на 7 дней до {new_expiry.strftime('%Y-%m-%d')}.")
            await bot.answer_callback_query(callback.id)
            await bot.delete_message(chat_id=callback.message.chat.id, message_id=callback.message.message_id)


        else:
            await bot.answer_callback_query(callback.id, "Подписка не найдена.", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка в handle_referral_choice: {e}")
        await bot.answer_callback_query(callback.id, "Произошла ошибка. Попробуйте снова.", show_alert=True)


@router.message(lambda message: message.text == "👥 Реферальная система")
async def show_referrals(message: Message):
    tg_id = message.from_user.id
    ref_link = f"t.me/{cfg.BOT_USERNAME}?start=ref_{tg_id}"
    rfr_lnk = f"https://t.me/share/url?url=https://t.me/{cfg.BOT_USERNAME}?start=ref_{tg_id}"

    await message.answer(
            f"\nПриглашайте друзей по ссылке и получайте <b>7 дней</b> подписки бесплатно:\n"
            f"<code>{ref_link}</code>\n"
            f"<i>Нажмите на ссылку чтобы скопировать</i>",
            reply_markup=get_referral_link_kb(rfr_lnk),
            parse_mode="HTML")

@router.callback_query(lambda c: c.data == "accept_terms", StateFilter(SubscriptionStates.wait_for_accept))
async def accept_terms(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    tg_id = callback_query.from_user.id
    await update_user_terms(tg_id, True)
    if not await get_user(tg_id):
        await create_user(tg_id)
    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(tg_id, "✅ Спасибо! Вы приняли соглашение. Теперь можете пользоваться ботом.", reply_markup=get_main_menu_kb())
    await state.clear()


@router.message(lambda message: message.text == "🎁 Пробная подписка")
async def trial_subscription_handler(message: Message, bot: Bot):
    tg_id = message.from_user.id
    trial_status = await get_trial_status(tg_id)
    if trial_status is None:
        await create_trial_user(tg_id)
        trial_status = 0
    if trial_status == 1:
        await message.answer("Вы уже активировали пробную подписку. Она доступна только один раз.")
        return
    expiry_time = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    email = f"DE-FRA-TRIAL-{tg_id}"
    subscription_id = generate_sub(16)
    trial_client = Client(
        id=str(uuid.uuid4()),
        enable=True,
        tg_id=tg_id,
        expiry_time=int(datetime.strptime(expiry_time, "%Y-%m-%d %H:%M:%S").timestamp() * 1000),
        flow="xtls-rprx-vision",
        email=email,
        sub_id=subscription_id,
        limit_ip=1
    )
    current_panel = get_best_panel()
    api = get_api_by_name(current_panel['name'])
    try:
        await add_subscription_to_db(tg_id, email, current_panel['name'], expiry_time)
        api.client.add(1, [trial_client])
        trial_key = current_panel["create_key"](trial_client)
        await message.answer(
            f"✅ Пробная подписка активирована!\n",
            parse_mode="HTML",
            reply_markup=get_main_menu_kb()
        )

        await  bot.send_message(
            message.from_user.id,
            f"🔑 ***Ключ:***"
            f"```\n"
            f"{trial_key}\n"
            f"```",
            parse_mode="MARKDOWN",
            reply_markup=get_connection_guide_button()
        )
        await activate_trial(tg_id)
    except Exception as e:
        await message.answer("Ошибка при создании пробной подписки.")
        logging.error(f"Ошибка пробной подписки: {e}")

@router.message(lambda message: message.text in ["📌 Информация", "🛒 Подписки", "❤️ Чат с поддержкой"])
async def open_submenu(message: Message, bot: Bot):
    if message.text == "📌 Информация":
        await message.answer(
            "📖 Выберите интересующий вас раздел:",
            reply_markup=get_info_keyboard()
        )
    elif message.text == "🛒 Подписки":
        tg_id = message.from_user.id
        subscriptions = get_active_subscriptions(tg_id)
        if subscriptions:
            response = ""
            for i, sub in enumerate(subscriptions, start=1):
                status = "⚠️ Срок подписки истёк!" if sub['is_expired'] else "✅ Подписка активна."
                response += (
                    f"\n*Подключение {i}:*\n"
                    f"👤 ***Пользователь:*** {sub['email']}\n"
                    f"🔑 ***Ключ:***"
                    f"```\n"
                    f"{sub['key']}\n"
                    f"```"
                    f"⏳ ***Дата окончания:*** {sub['expiry_date'].strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"{status}\n\n"
                )

            await bot.send_message(
                message.from_user.id,
                "Выберите действие:",
                reply_markup=get_subscriptions_menu_kb()
            )
            await message.reply(response, parse_mode="MARKDOWN", reply_markup=get_connection_guide_button())

        else:
            await message.reply("У вас нет активных подписок.", reply_markup=get_subscriptions_menu_kb())
    elif message.text == "❤️ Чат с поддержкой":
        support_text = ("Для связи с нашей поддержкой отправьте сообщение сюда, @WSocks_Support и наша команда ответит вам в ближайшее время!\n\n"
                        f"Ваш id в телеграмм:👉🏿<code>{message.from_user.id}</code>👈🏿\n"
                        "<i>(нажмите на id чтобы скопировать)</i>")
        await message.reply(support_text, reply_markup=get_main_menu_kb(), parse_mode="HTML")



@router.message(lambda message: message.text == "⬅️ Назад в главное меню")
async def back_to_main(message: Message):
    await message.answer("Вы вернулись в главное меню:", reply_markup=get_main_menu_kb())

@router.message(lambda message: message.text == "💳 Купить подписку")
async def buy_subscription(message: Message, state: FSMContext):
    await message.reply("Выберите срок подписки:", reply_markup=get_period_kb())
    await state.set_state(SubscriptionStates.choosing_buy_period)

@router.message(lambda message: message.text in ["30 дней", "90 дней", "180 дней", "360 дней"], StateFilter(SubscriptionStates.choosing_buy_period))
async def confirm_subscription_purchase(message: Message, state: FSMContext):
    try:
        days = int(message.text.split()[0])
        prices = {30: 89, 90: 249, 180: 449, 360: 849}
        amount = prices[days]
        tg_id = message.from_user.id
        label = f"{tg_id}-{uuid.uuid4().hex[:6]}"
        payment_link = create_payment_link(amount, label)
        await state.update_data(days=days, amount=amount, tg_id=tg_id, label=label)
        payment_message = await message.reply(
            f"Стоимость подписки на {days} дней: {amount} рублей.\n\n"
            f"Для оплаты нажмите кнопку <b>Оплатить</b>, после успешной оплаты нажмите <b>Подтвердить оплату</b>\n"
            f"Если хотите отменить платёж нажмите <b>Отмена</b>",
            reply_markup=get_payment_confirm_kb(payment_link),
            parse_mode="HTML"
        )
        await state.update_data(payment_message_id=payment_message.message_id)
        await state.set_state(SubscriptionStates.awaiting_payment)
    except Exception as e:
        await message.reply(f"Произошла ошибка: {e}")
        await state.clear()

@router.message(lambda message: message.text == "Назад", StateFilter(SubscriptionStates.choosing_buy_period))
async def cancel_subscription(message: Message, state: FSMContext):
    await message.answer("Вы вернулись в главное меню.", reply_markup=get_main_menu_kb())
    await state.clear()

@router.callback_query(lambda c: c.data in ["confirm_payment", "cancel_payment"], StateFilter(SubscriptionStates.awaiting_payment))
async def handle_payment_action(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    payment_message_id = data.get('payment_message_id')
    custom_label = data.get('label')
    days = data.get('days')
    tg_id = data.get('tg_id')
    amount = data.get('amount')
    current_panel = get_best_panel()
    api = get_api_by_name(current_panel['name'])
    if callback_query.data == "cancel_payment":
        if payment_message_id:
            await bot.delete_message(chat_id=callback_query.message.chat.id, message_id=payment_message_id)
        await bot.send_message(callback_query.message.chat.id, "Оплата отменена.", reply_markup=get_main_menu_kb())
        await state.clear()
    elif callback_query.data == "confirm_payment":
        if check_payment_status(custom_label):
            if payment_message_id:
                await bot.delete_message(chat_id=callback_query.message.chat.id, message_id=payment_message_id)
            expiry_time = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            email = f"DE-FRA-USER-{tg_id}-{uuid.uuid4().hex[:6]}"
            subscription_id = generate_sub(16)
            new_client = Client(
                id=str(uuid.uuid4()),
                enable=True,
                tg_id=tg_id,
                expiry_time=int(datetime.strptime(expiry_time, "%Y-%m-%d %H:%M:%S").timestamp() * 1000),
                flow="xtls-rprx-vision",
                email=email,
                sub_id=subscription_id,
                limit_ip=5
            )
            api.client.add(1, [new_client])
            subscription_key = current_panel["create_key"](new_client)
            await add_subscription_to_db(tg_id, email, current_panel['name'], expiry_time)
            await add_payment_to_db(tg_id, custom_label, "покупка", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), amount, email)
            await bot.send_message(
                callback_query.message.chat.id,
                f"✅ Оплата подтверждена! Подписка оформлена.",
                parse_mode="HTML",
                reply_markup=get_main_menu_kb()
            )

            await  bot.send_message(
                callback_query.message.chat.id,
                f"👤 ***Пользователь:*** {email}\n"
                f"🔑 ***Ключ:***"
                f"```\n"
                f"{subscription_key}\n"
                f"```",
                parse_mode="MARKDOWN",
                reply_markup=get_connection_guide_button()
            )
            await state.clear()
        else:
            await bot.answer_callback_query(callback_query.id, "Оплата не найдена. Попробуйте ещё раз.", show_alert=True)


@router.message(lambda message: message.text == "🕑 Продлить подписку")
async def extend_subscription_menu(message: Message, state: FSMContext):
    tg_id = message.from_user.id
    subscriptions = get_active_subscriptions(tg_id)

    # Фильтруем подписки, исключая пробные
    non_trial_subs = [sub for sub in subscriptions if sub['email'] != f"DE-FRA-TRIAL-{tg_id}"]

    if non_trial_subs:  # Если есть непробные подписки
        kb = ReplyKeyboardBuilder()
        for sub in non_trial_subs:
            kb.button(text=sub['email'])
        kb.button(text="Назад")
        kb.adjust(1)
        await message.answer(
            "Выберите пользователя для продления:",
            reply_markup=kb.as_markup(resize_keyboard=True)
        )
        await state.set_state(SubscriptionStates.extending_subscription)
    else:  # Если нет непробных подписок (или подписок вообще)
        await message.answer("У вас нет активных подписок.")

@router.message(StateFilter(SubscriptionStates.extending_subscription))
async def extend_selected_subscription(message: Message, state: FSMContext):
    email = message.text
    if email == "Назад":
        await state.clear()
        await message.reply("Вы вернулись в главное меню.", reply_markup=get_main_menu_kb())
        return
    tg_id = message.from_user.id
    subscriptions = get_active_subscriptions(tg_id)
    selected_sub = next((sub for sub in subscriptions if sub['email'] == email), None)
    if selected_sub:
        await state.update_data(selected_sub=selected_sub, panel_name=selected_sub['panel'])
        await message.reply("Выберите срок продления подписки:", reply_markup=get_period_kb())
        await state.set_state(SubscriptionStates.choosing_extend_period)
    else:
        await message.reply("Выбранное подключение не найдено.", reply_markup=get_main_menu_kb())
        await state.clear()

@router.message(lambda message: message.text in ["30 дней", "90 дней", "180 дней", "360 дней"], StateFilter(SubscriptionStates.choosing_extend_period))
async def confirm_extension_purchase(message: Message, state: FSMContext, bot: Bot):
    days = int(message.text.split()[0])
    prices = {30: 89, 90: 249, 180: 449, 360: 849}
    amount = prices[days]
    tg_id = message.from_user.id
    label = f"EXTEND-{tg_id}-{uuid.uuid4().hex[:6]}"
    payment_link = create_payment_link(amount, label)
    await state.update_data(days=days, amount=amount, tg_id=tg_id, label=label)
    payment_message = await message.reply(
        f"Стоимость подписки на {days} дней: {amount} рублей.\n\n"
            f"Для оплаты нажмите кнопку <b>Оплатить</b>, после успешной оплаты нажмите <b>Подтвердить оплату</b>\n"
            f"Если хотите отменить платёж нажмите <b>Отмена</b>",
            reply_markup=get_payment_confirm_kb(payment_link),
            parse_mode="HTML"
    )
    await state.update_data(payment_message_id=payment_message.message_id)
    await state.set_state(SubscriptionStates.awaiting_extend_payment)

@router.message(lambda message: message.text == "Назад", StateFilter(SubscriptionStates.choosing_extend_period))
async def cancel_subscription(message: Message, state: FSMContext):
    await message.reply("Вы вернулись в главное меню.", reply_markup=get_main_menu_kb())
    await state.clear()

@router.callback_query(lambda c: c.data in ["confirm_payment", "cancel_payment"], StateFilter(SubscriptionStates.awaiting_extend_payment))
async def handle_extension_payment_action(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    payment_message_id = data.get('payment_message_id')
    custom_label = "1615487633"  #data.get('label')
    days = data.get('days')
    tg_id = data.get('tg_id')
    selected_sub = data.get('selected_sub')
    amount = data.get('amount')
    current_panel = data.get("panel_name")
    api = get_api_by_name(current_panel)
    if callback_query.data == "cancel_payment":
        if payment_message_id:
            await bot.delete_message(chat_id=callback_query.message.chat.id, message_id=payment_message_id)
        await bot.send_message(callback_query.message.chat.id, "Продление отменено.", reply_markup=get_main_menu_kb())
        await state.clear()
    elif callback_query.data == "confirm_payment":
        if check_payment_status(custom_label):
            if payment_message_id:
                await bot.delete_message(chat_id=callback_query.message.chat.id, message_id=payment_message_id)
            inbounds = api.inbound.get_list()
            for inbound in inbounds:
                for client in inbound.settings.clients:
                    if client.email == selected_sub['email'] and client.tg_id == tg_id:
                        extend_subscription(selected_sub['email'], client.id, days, tg_id, client.sub_id, api)
                        current_expiry = selected_sub["expiry_date"]
                        new_expiry = current_expiry + timedelta(days=days)
                        await update_subscriptions_on_db(selected_sub['email'], new_expiry.strftime("%Y-%m-%d %H:%M:%S"))
                        await add_payment_to_db(tg_id, custom_label, "продление", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), amount, selected_sub['email'])
                        await bot.send_message(
                            callback_query.message.chat.id,
                            f"✅ Оплата подтверждена! Подписка продлена на {days} дней.\n"
                            f"👤 <b>Пользователь: </b> {client.email}\n"
                            f"⏳ <em><b>Новая дата окончания: </b></em> {new_expiry.strftime('%Y-%m-%d %H:%M:%S')}",
                            parse_mode="HTML",
                            reply_markup=get_main_menu_kb()
                        )
                        await state.clear()
                        return
        else:
            await bot.answer_callback_query(callback_query.id, "Оплата не найдена.", show_alert=True)

@router.callback_query(lambda c: c.data == "show_setup_menu")
async def show_setup_menu(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    tg_id = callback_query.from_user.id
    subscriptions = get_active_subscriptions(tg_id)

    if not subscriptions:
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text="У вас нет активных подключений.",
            reply_markup=get_main_menu_kb()
        )
        await callback_query.answer()
        return

    if len(subscriptions) == 1:
        # Одна подписка — сразу выбор устройства
        sub_key = subscriptions[0]["key"]
        await state.update_data(selected_sub=sub_key)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=device["name"], callback_data=device["callback"])]
            for device in DEVICES
        ])
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text="Выберите ваше устройство для настройки:",
            reply_markup=keyboard
        )
    else:
        # Больше одной — выбор подписки
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=sub.get("email", f"Подписка {i+1}"), callback_data=f"sub_{i}")]
            for i, sub in enumerate(subscriptions)
        ])
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text="Выберите подписку:",
            reply_markup=keyboard
        )
    await state.set_state(SubscriptionStates.selecting_manual)
    await callback_query.answer()

# Обработка выбора подписки в inline
@router.callback_query(lambda c: c.data.startswith("sub_"), StateFilter(SubscriptionStates.selecting_manual))
async def process_inline_subscription(callback: CallbackQuery, bot: Bot, state: FSMContext):
    tg_id = callback.from_user.id
    sub_index = int(callback.data.split("_")[1])
    subscriptions = get_active_subscriptions(tg_id)
    sub_key = subscriptions[sub_index]["key"]

    await state.update_data(selected_sub=sub_key)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=device["name"], callback_data=device["callback"])]
        for device in DEVICES
    ])
    await bot.edit_message_text(
        text="Выберите ваше устройство для настройки:",
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        reply_markup=keyboard
    )
    await callback.answer()

# Обработка выбора устройства в inline
@router.callback_query(lambda c: c.data.startswith("setup_"), StateFilter(SubscriptionStates.selecting_manual))
async def send_manual(callback: CallbackQuery, bot: Bot, state: FSMContext):
    data = await state.get_data()
    sub_key = data.get("selected_sub")
    if not sub_key:
        await callback.message.answer("Ошибка: подписка не выбрана.", reply_markup=get_main_menu_kb())
        await state.clear()
        return

    encoded_key = urllib.parse.quote(sub_key, safe="")
    redirect_link = f"{cfg.BASE_REDIRECT_URL}/?key={encoded_key}"
    device = next(d for d in DEVICES if d["callback"] == callback.data)

    print(redirect_link)

    manuals = {
        "setup_pc": f"{mt.manual_pc(x=sub_key)}",
        "setup_android": f"{mt.manual_android}",
        "setup_ios": f"{mt.manual_ios}",
        "setup_android_tv": f"{mt.manual_android_tv(x=sub_key)}"
    }
    manual_text = manuals.get(callback.data, "Инструкция для данного устройства недоступна.")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать", url=device["download_url"])]
    ])
    if callback.data in ["setup_android", "setup_ios"]:
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔗 Импортировать подписку", url=redirect_link)])

    await bot.edit_message_text(
        text=manual_text,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        parse_mode="MARKDOWN",
        disable_web_page_preview=True,
        reply_markup=keyboard
    )
    await state.clear()
    await callback.answer()

@router.message(lambda message: message.text == "О сервисе")
async def service_info(message: Message):
    info_text = (
        "<b>Информация о сервисе:</b>\n\n"
        "- WSocks VPN - это сервис предоставляющий возможность безопасного сёрфинга в интернете посредством "
        "подключения к удалённому серверу в Германии (в будущем и в других странах).\n\n"
        "- Мы гарантируем сохранность ваших данных и отвечаем за стабильность и скорость предоставления наших "
        "услуг.\n\n"
        "- WSocks VPN не подвержен блокировкам, т.к использует самые современные протоколы TLS+Vless+TCP+Reality  и "
        "не привязан к одному приложению. Для использования VPN услуг вы можете сами выбрать удобную вам программу."
    )
    await message.reply(info_text, parse_mode="HTML", reply_markup=get_main_menu_kb())

@router.message(lambda message: message.text == "О подписке")
async def subscription_info(message: Message):
    support_text = (
        "<b>Информация о подписке:</b>\n\n"
        "<b>🞄 Стоимость:</b> <i>89 рублей/мес</i>\n\n"
        "<b>🞄 Ограничение скорости:</b> <i>нет</i>\n\n"
        "<b>🞄 Лимит трафика:</b> <i>Безлимит</i>\n\n"
        "<b>🞄 Протоколы:</b> <i>TLS+Vless+TCP+Reality</i>\n\n"
        "<b>🞄 Программы для использования:</b> <i>Amnezia, NekoBox/NekoRay, Hiddify, V2RayNG и т.п</i>\n\n"
        "<b>🞄 Макс. Кол-во устройств:</b> <i>3 устройства (Android TV, Android, IOS, MAC, Windows, Linux)</i>\n\n"
        "<b>!ВАЖНО!</b>\n\n"
        "- Если вы планируете не отключать VPN во время загрузки игр (со стима или других магазинов) или скачивания "
        "больших или скачивания Торрент файлов , обязательно добавьте их в исключения, иначе скорость вашего подключения будет временно снижена!\n"
        "     - Мануал по этому действию есть в нашем боте!\n\n"
        "- Если вы используете крякнутый софт при включённом VPN, то добавьте его в исключения. Есть риск, "
        "что вам ограничат доступ к его использованию ввиду законодательств других стран.\n"
        "     - Мануал по этому действию есть в нашем боте!"
    )
    await message.reply(support_text, parse_mode="HTML", reply_markup=get_main_menu_kb())

@router.message(lambda message: message.text == "Пользовательское соглашение")
async def user_agreement(message: Message):
    await message.reply(f"{mt.license_agreement_text}", parse_mode="HTML", reply_markup=get_main_menu_kb())

@router.message(lambda message: message.text == "Политика конфиденциальности")
async def privacy_policy(message: Message):
    privacy_policy_text = (
        f"WSocks VPN использует данные, которые предоставляет Telegram.\n"
        f"Мы уважаем вашу приватность.\n\n"
        f"Больше деталей в документе:"
    )
    await message.reply(privacy_policy_text, parse_mode="Markdown", reply_markup=get_privacy_kb())


@router.callback_query(lambda c: c.data == "info_subscription")
async def show_subscription_info(callback_query: CallbackQuery):
    support_text = (
        "<b>Информация о подписке:</b>\n\n"
        "<b>- Стоимость:</b> <i>89 рублей/мес</i>\n\n"
        "<b>- Ограничение скорости:</b> <i>нет</i>\n\n"
        "<b>- Лимит трафика:</b> <i>Безлимит</i>\n\n"
        "<b>- Протоколы:</b> <i>TLS+Vless+TCP+Reality</i>\n\n"
        "<b>- Программы для использования:</b> <i>Amnezia, NekoBox/NekoRay, Hiddify, V2RayNG и т.п</i>\n\n"
        "<b>- Макс. Кол-во устройств:</b> <i>3 устройства (Android TV, Android, IOS, MAC, Windows, Linux)</i>\n\n"
        "<b>!ВАЖНО!</b>\n\n"
        "- Если вы планируете не отключать VPN во время загрузки игр (со стима или других магазинов) или скачивания "
        "больших или скачивания Торрент файлов , обязательно добавьте их в исключения, иначе скорость вашего подключения будет временно снижена!\n"
        "     - Мануал по этому действию есть в нашем боте!\n\n"
        "- Если вы используете крякнутый софт при включённом VPN, то добавьте его в исключения. Есть риск, "
        "что вам ограничат доступ к его использованию ввиду законодательств других стран.\n"
        "     - Мануал по этому действию есть в нашем боте!"
    )
    await callback_query.message.answer(support_text, parse_mode="HTML", reply_markup=get_main_menu_kb())

@router.callback_query(lambda c: c.data == "info_service")
async def show_service_info(callback_query: CallbackQuery):
    info_text = (
        "<b>Информация о сервисе:</b>\n\n"
        "- WSocks VPN - это сервис предоставляющий возможность безопасного сёрфинга в интернете посредством "
        "подключения к удалённому серверу в Германии (в будущем и в других странах).\n\n"
        "- Мы гарантируем сохранность ваших данных и отвечаем за стабильность и скорость предоставления наших "
        "услуг.\n\n"
        "- WSocks VPN не подвержен блокировкам, т.к использует самые современные протоколы TLS+Vless+TCP+Reality  и "
        "не привязан к одному приложению. Для использования VPN услуг вы можете сами выбрать удобную вам программу."
    )
    await callback_query.message.reply(info_text, parse_mode="HTML", reply_markup=get_main_menu_kb())


@router.callback_query(lambda c: c.data == "info_terms")
async def show_terms(callback_query: CallbackQuery):
    await callback_query.message.reply(f"{mt.license_agreement_text}", parse_mode="HTML", reply_markup=get_main_menu_kb())


@router.callback_query(lambda c: c.data == "info_privacy")
async def show_privacy(callback_query: CallbackQuery):
    privacy_policy_text = (
        f"WSocks VPN использует данные, которые предоставляет Telegram.\n"
        f"Мы уважаем вашу приватность.\n\n"
        f"Больше деталей в документе:"
    )
    await callback_query.message.reply(privacy_policy_text, parse_mode="Markdown", reply_markup=get_privacy_kb())

# Обработка "Назад" в обычной клавиатуре
@router.message(lambda message: message.text == "Назад", StateFilter(SubscriptionStates.selecting_manual))
async def cancel_manual(message: Message, state: FSMContext):
    await message.reply("Вы вернулись в главное меню.", reply_markup=get_main_menu_kb())
    await state.clear()

@router.message()
async def handle_random_text(message: Message):
    await message.answer("Я не понял вашего запроса. Пожалуйста, используйте кнопки меню.", reply_markup=get_main_menu_kb())
