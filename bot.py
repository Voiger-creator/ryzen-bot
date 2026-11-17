import os
import asyncio
import json
import time
from datetime import datetime
import threading
from flask import Flask

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, LabeledPrice, PreCheckoutQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ===== НАСТРОЙКИ =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

ORDERS_FILE = "orders.json"
SETTINGS_FILE = "settings.json"

# ===== ЦЕНЫ =====
PRICE_PRINT_BW = 20
PRICE_PRINT_COLOR = 35
PRICE_PRINT_FILE = 10
PRICE_DRAW_SKETCH = 50
PRICE_DRAW_ANIME = 100
PRICE_DRAW_JOJO = 200
PRICE_DRAW_TECH = 150
PRICE_SECRET = 75
STARS_PRICE = 50 # Цена в звездах (фиксированная для теста)
# =====================

print(f"DEBUG BOT_TOKEN: length={len(BOT_TOKEN) if BOT_TOKEN else 0}")

bot = Bot(token=BOT_TOKEN.strip() if BOT_TOKEN else "0:invalid")
dp = Dispatcher()

# ===== ВЕБ-СЕРВЕР ДЛЯ RENDER =====
app = Flask(__name__)
@app.route('/')
@app.route('/health')
def health_check(): return "Bot is running", 200
def run_flask(): app.run(host='0.0.0.0', port=10000)

# ===== СОСТОЯНИЯ =====
class Order(StatesGroup):
    waiting_edits = State()
    waiting_admin_photo = State()
    waiting_admin_comment = State()
    waiting_admin_reject = State()
    waiting_file = State()
    waiting_admin_news = State()
    waiting_draw_pause_reason = State()

# ===== РАБОТА С ФАЙЛАМИ =====
def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f: return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def load_orders(): return load_json(ORDERS_FILE) or {"counter": 0, "orders": []}
def save_orders(data): save_json(ORDERS_FILE, data)

def load_settings():
    return load_json(SETTINGS_FILE) or {"is_paused": False, "draw_pause": False, "draw_pause_reason": ""}
def save_settings(data): save_json(SETTINGS_FILE, data)

def toggle_pause():
    s = load_settings()
    s["is_paused"] = not s["is_paused"]
    save_settings(s)
    return s["is_paused"]

def toggle_draw_pause(reason=""):
    s = load_settings()
    s["draw_pause"] = not s["draw_pause"]
    if s["draw_pause"]: s["draw_pause_reason"] = reason
    else: s["draw_pause_reason"] = ""
    save_settings(s)
    return s["draw_pause"]

def get_next_order_number():
    data = load_orders()
    data["counter"] += 1
    save_orders(data)
    return data["counter"]

def save_order(order):
    data = load_orders()
    data["orders"].append(order)
    save_orders(data)

def get_order(order_number):
    for order in load_orders()["orders"]:
        if order["number"] == order_number: return order
    return None

def update_order(order_number, **kwargs):
    data = load_orders()
    for order in data["orders"]:
        if order["number"] == order_number:
            order.update(kwargs)
            save_orders(data)
            break

# ===== КЛАВИАТУРЫ =====
def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖨 Печать", callback_data="menu_print")],
        [InlineKeyboardButton(text="🎨 Рисование", callback_data="menu_draw")],
        [InlineKeyboardButton(text="⚙️ Кодинг / Самоделки", callback_data="menu_code")],
        [InlineKeyboardButton(text="📄 Печать файла", callback_data="menu_file")],
        [InlineKeyboardButton(text="🎁 Секретный заказ", callback_data="menu_secret")],
        [InlineKeyboardButton(text="ℹ️ О боте", callback_data="menu_info")],
    ])

def back_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅ Назад в меню", callback_data="back_main")]])

def after_order_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Сделать еще один заказ", callback_data="new_order")],
        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_main")]
    ])

def admin_panel_kb():
    s = load_settings()
    pause_text = "▶️ Снять с паузы (всё)" if s["is_paused"] else "⏸ Поставить на паузу (всё)"
    draw_pause_text = "▶️ Разрешить рисование" if s["draw_pause"] else "⏸ Пауза для рисования"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=pause_text, callback_data="toggle_pause")],
        [InlineKeyboardButton(text=draw_pause_text, callback_data="toggle_draw_pause")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📰 Сделать рассылку (Новости)", callback_data="admin_news")]
    ])

def admin_order_kb(order_num):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнен", callback_data=f"admin_done_{order_num}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject_{order_num}")],
        [InlineKeyboardButton(text="💬 Комментарий", callback_data=f"admin_comment_{order_num}")]
    ])

def client_cancel_kb(order_num):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"client_cancel_{order_num}")]])

def rating_kb(order_num):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1", callback_data=f"rate_1_{order_num}"),
         InlineKeyboardButton(text="⭐ 3", callback_data=f"rate_3_{order_num}"),
         InlineKeyboardButton(text="⭐ 5", callback_data=f"rate_5_{order_num}")]
    ])

def payment_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐ Оплатить {STARS_PRICE} Stars", callback_data="pay_stars")],
        [InlineKeyboardButton(text="💵 Оплата при встрече", callback_data="pay_cash")]
    ])

# ===== АДМИН ПАНЕЛЬ =====
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID: return
    s = load_settings()
    status = "На паузе ⏸" if s["is_paused"] else "Работает ✅"
    draw_status = f"Рисование на паузе (Причина: {s['draw_pause_reason']})" if s["draw_pause"] else "Рисование работает"
    
    await message.answer(
        f"👑 <b>Панель админа</b>\n\n"
        f"Общий статус: <b>{status}</b>\n"
        f"Рисование: <b>{draw_status}</b>", 
        reply_markup=admin_panel_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "toggle_pause")
async def process_toggle_pause(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return await call.answer("Нет прав", show_alert=True)
    toggle_pause()
    s = load_settings()
    status = "На паузе ⏸" if s["is_paused"] else "Работает ✅"
    draw_status = f"Рисование на паузе" if s["draw_pause"] else "Рисование работает"
    await call.message.edit_text(f"👑 <b>Панель админа</b>\n\nОбщий статус: <b>{status}</b>\nРисование: <b>{draw_status}</b>", reply_markup=admin_panel_kb(), parse_mode="HTML")
    await call.answer(f"Общий статус: {status}")

@dp.callback_query(F.data == "toggle_draw_pause")
async def process_toggle_draw_pause(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return await call.answer("Нет прав", show_alert=True)
    s = load_settings()
    if s["draw_pause"]:
        toggle_draw_pause()
        await call.answer("Рисование снова разрешено!")
    else:
        await state.set_state(Order.waiting_draw_pause_reason)
        await call.message.answer("Введите причину паузы для рисования (клиенты её увидят):")
    await call.answer()

@dp.message(Order.waiting_draw_pause_reason)
async def set_draw_pause_reason(message: Message, state: FSMContext):
    toggle_draw_pause(reason=message.text)
    await state.clear()
    await message.answer(f"✅ Рисование на паузе. Причина: {message.text}")
    await cmd_admin(message) 

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return await call.answer("Нет прав", show_alert=True)
    data = load_orders()
    total = len(data["orders"])
    done = sum(1 for o in data["orders"] if o["status"] == "Выполнен")
    await call.message.answer(f"📊 <b>Статистика</b>\n\nВсего заказов: {total}\nВыполнено: {done}", parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "admin_news")
async def admin_news(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return await call.answer("Нет прав", show_alert=True)
    await state.set_state(Order.waiting_admin_news)
    await call.message.answer("Напиши текст новости для рассылки всем клиентам (или 'Отмена'):")
    await call.answer()

@dp.message(Order.waiting_admin_news)
async def send_news(message: Message, state: FSMContext):
    if message.text.lower() == 'отмена':
        await state.clear()
        return await message.answer("Рассылка отменена.")
    
    data = load_orders()
    user_ids = set(order["user_id"] for order in data["orders"])
    count = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, f"📰 <b>НОВОСТИ МАСТЕРСКОЙ:</b>\n\n{message.text}", parse_mode="HTML")
            count += 1
            await asyncio.sleep(0.1)
        except: pass
    await state.clear()
    await message.answer(f"✅ Новость отправлена {count} клиентам.")

# ===== КЛИЕНТСКАЯ ЧАСТЬ =====
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(f"Привет, {message.from_user.first_name}! 👋\nВыбери услугу:", reply_markup=main_menu_kb())

@dp.callback_query(F.data.in_(["back_main", "new_order"]))
async def back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Выбери услугу:", reply_markup=main_menu_kb())
    await call.answer()

@dp.callback_query(F.data == "menu_info")
async def show_info(call: CallbackQuery):
    text = (
        "🤖 <b>О нашем боте и мастерской</b>\n\n"
        "👨‍💻 <b>Создатель:</b> @Ryzen\n"
        "Увлекаюсь программированием, пайкой, 3D-печатью и рисованием.\n\n"
        "🧠 <b>Наша команда:</b>\n"
        "• <b>Человек (Я):</b> Идеи, заказы, финальная сборка.\n"
        "• <b>DeepSeek (ИИ):</b> Помощник в коде и отладке.\n"
        "• <b>Ryzen AI (В разработке):</b> Локальный ИИ-помощник.\n\n"
        "🛠 <b>Услуги:</b> Печать, Арты, Кодинг, Секретные заказы.\n"
        "🕒 <b>Сроки:</b> Печать 1 день, Арт 3-5 дней, Кодинг от недели."
    )
    await call.message.edit_text(text, reply_markup=back_main_kb(), parse_mode="HTML")
    await call.answer()

# --- ПЕЧАТЬ ---
@dp.callback_query(F.data == "menu_print")
async def print_menu(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Ч/Б текст/картинка", callback_data="print_bw")],
        [InlineKeyboardButton(text="Цветная картинка", callback_data="print_color")],
        [InlineKeyboardButton(text="⬅ Назад", callback_data="back_main")],
    ])
    await call.message.edit_text("🖨 Выбери тип печати:", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.in_(["print_bw", "print_color"]))
async def print_choose(call: CallbackQuery, state: FSMContext):
    price = PRICE_PRINT_BW if call.data == "print_bw" else PRICE_PRINT_COLOR
    name = "Ч/Б" if call.data == "print_bw" else "Цветная"
    await state.update_data(service=f"Печать ({name})", price=price)
    await state.set_state(Order.waiting_edits)
    await call.message.edit_text(f"Выбрано: {name} печать. Цена: {price} грн.\n\nНапиши детали заказа:")
    await call.answer()

# --- РИСОВАНИЕ ---
@dp.callback_query(F.data == "menu_draw")
async def draw_menu(call: CallbackQuery):
    s = load_settings()
    if s["draw_pause"]:
        return await call.answer(f"⏸ Рисование на паузе. Причина: {s['draw_pause_reason']}", show_alert=True)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Скетч ({PRICE_DRAW_SKETCH} грн)", callback_data="draw_sketch")],
        [InlineKeyboardButton(text=f"Аниме ({PRICE_DRAW_ANIME} грн)", callback_data="draw_anime")],
        [InlineKeyboardButton(text=f"Стиль Джо Джо ({PRICE_DRAW_JOJO} грн)", callback_data="draw_jojo")],
        [InlineKeyboardButton(text=f"Техника ({PRICE_DRAW_TECH} грн)", callback_data="draw_tech")],
        [InlineKeyboardButton(text="⬅ Назад", callback_data="back_main")],
    ])
    await call.message.edit_text("🎨 Выбери стиль рисования:", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("draw_"))
async def draw_choose(call: CallbackQuery, state: FSMContext):
    styles = {"draw_sketch": ("Скетч", PRICE_DRAW_SKETCH), "draw_anime": ("Аниме", PRICE_DRAW_ANIME), "draw_jojo": ("Джо Джо", PRICE_DRAW_JOJO), "draw_tech": ("Техника", PRICE_DRAW_TECH)}
    style_name, price = styles[call.data]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Физический (отдам вживую)", callback_data="format_phys")],
        [InlineKeyboardButton(text="Графический (файл)", callback_data="format_digi")],
        [InlineKeyboardButton(text="⬅ Назад", callback_data="menu_draw")],
    ])
    await state.update_data(service=f"Рисование ({style_name})", price=price)
    await call.message.edit_text(f"Стиль: {style_name}. Цена: {price} грн.\n\nВыбери формат:", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.in_(["format_phys", "format_digi"]))
async def draw_format(call: CallbackQuery, state: FSMContext):
    fmt = "Физический" if call.data == "format_phys" else "Графический"
    data = await state.get_data()
    if fmt == "Физический": data["price"] += 20
    await state.update_data(service=f"{data['service']} ({fmt})", price=data["price"])
    
    await state.set_state(Order.waiting_edits)
    await call.message.edit_text(f"Формат: {fmt}. Итоговая цена: {data['price']} грн.\n\nОпиши заказ:")
    await call.answer()

# --- СЕКРЕТНЫЙ ЗАКАЗ ---
@dp.callback_query(F.data == "menu_secret")
async def secret_order(call: CallbackQuery, state: FSMContext):
    await state.update_data(service="🎁 Секретный заказ", price=PRICE_SECRET)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Да, хочу сюрприз!", callback_data="secret_yes")]])
    await call.message.edit_text(f"Секретный заказ! Цена: {PRICE_SECRET} грн.\nЯ нарисую то, что захочу. Согласен?", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == "secret_yes")
async def secret_yes(call: CallbackQuery, state: FSMContext):
    await state.set_state(Order.waiting_edits)
    await call.message.edit_text("Отлично! Напиши пожелания (или 'Нет'):")
    await call.answer()

# --- КОДИНГ ---
@dp.callback_query(F.data == "menu_code")
async def code_menu(call: CallbackQuery):
    text = "⚙️ <b>Кодинг и Самоделки</b>\n\nИспользуется <b>Вайб-кодинг</b>. Согласны?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Согласен", callback_data="code_agree")],
        [InlineKeyboardButton(text="⬅ Назад", callback_data="back_main")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "code_agree")
async def code_agree(call: CallbackQuery, state: FSMContext):
    await state.update_data(service="Кодинг/Самоделки", price="Индивидуально")
    await state.set_state(Order.waiting_edits)
    await call.message.edit_text("✅ Условия приняты!\nОпиши, что нужно:")
    await call.answer()

# --- ПЕЧАТЬ ФАЙЛА ---
@dp.callback_query(F.data == "menu_file")
async def file_menu(call: CallbackQuery, state: FSMContext):
    await state.update_data(service="Печать файла", price=PRICE_PRINT_FILE)
    await state.set_state(Order.waiting_file)
    await call.message.edit_text("📄 Прикрепи файл (PDF, DOCX) и напиши количество страниц:")
    await call.answer()

@dp.message(Order.waiting_file, F.document | F.photo)
async def receive_file(message: Message, state: FSMContext):
    file_id = message.document.file_id if message.document else message.photo[-1].file_id
    await state.update_data(file_id=file_id, details="Прикреплен файл")
    await state.set_state(Order.waiting_edits)
    await message.answer("Файл получен! Напиши комментарии (цветная/чб):")

# --- ФИНАЛИЗАЦИЯ И ОПЛАТА ---
@dp.message(Order.waiting_edits)
async def finish_order(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    
    order_num = get_next_order_number()
    user = message.from_user
    price = data.get('price', 0)
    price_str = f"{price} грн" if isinstance(price, (int, float)) else price

    order = {
        "number": order_num, "user_id": user.id, "username": user.username,
        "full_name": user.full_name, "service": data.get("service"),
        "price": price, "details": data.get("details", message.text),
        "edits": message.text, "status": "В работе", "rating": None,
        "created_at": time.time(), "file_id": data.get("file_id")
    }
    save_order(order)

    await message.answer(
        f"✅ Заказ <b>№{order_num}</b> создан!\n"
        f"К оплате: <b>{price_str}</b>\n\n"
        "Выбери способ оплаты:",
        parse_mode="HTML",
        reply_markup=payment_kb()
    )

    admin_text = (
        f"🆕 <b>Новый заказ №{order_num}</b>\n\n"
        f"👤 {user.full_name} (@{user.username})\n"
        f"🛠 Услуга: {order['service']}\n"
        f"💰 К оплате: <b>{price_str}</b>\n"
        f"📝 Детали: {order['details']}\n"
    )
    try:
        await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML", reply_markup=admin_order_kb(order_num))
        if order["file_id"]: await bot.send_document(ADMIN_ID, order["file_id"], caption=f"Файл к заказу №{order_num}")
    except Exception as e: print(f"Ошибка: {e}")

# ===== ОПЛАТА ЗВЕЗДАМИ (TELEGRAM STARS) =====
@dp.callback_query(F.data == "pay_stars")
async def process_pay_stars(call: CallbackQuery):
    prices = [LabeledPrice(label="Заказ", amount=STARS_PRICE)]
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="Оплата заказа",
        description="Оплата услуги в мастерской Ryzen",
        payload=f"order_payment_{call.from_user.id}",
        provider_token="", 
        currency="XTR",
        prices=prices,
    )
    await call.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    await message.answer("✅ Оплата звездами прошла успешно! Спасибо!")
    try:
        await bot.send_message(ADMIN_ID, f"💰 Клиент @{message.from_user.username} оплатил заказ звездами!")
    except: pass

@dp.callback_query(F.data == "pay_cash")
async def process_pay_cash(call: CallbackQuery):
    await call.message.edit_text("✅ Вы выбрали оплату при встрече. Я свяжусь с вами для уточнения деталей.", reply_markup=after_order_kb())
    await call.answer()

# ===== ОСТАЛЬНОЕ =====
@dp.callback_query(F.data.startswith("client_cancel_"))
async def client_cancel(call: CallbackQuery):
    order_num = int(call.data.split("_")[2])
    order = get_order(order_num)
    if time.time() - order["created_at"] > 86400: return await call.answer("Время отмены истекло.", show_alert=True)
    update_order(order_num, status="Отменен клиентом")
    await call.message.edit_text(f"❌ Вы отменили заказ №{order_num}.")
    await bot.send_message(ADMIN_ID, f"⚠️ Клиент отменил заказ №{order_num}.")

@dp.callback_query(F.data.startswith("admin_done_"))
async def admin_done(call: CallbackQuery, state: FSMContext):
    order_num = int(call.data.split("_")[2])
    await state.update_data(admin_order_num=order_num)
    await state.set_state(Order.waiting_admin_photo)
    await call.message.answer(f"Отправьте фото работы для заказа №{order_num}:")

@dp.message(Order.waiting_admin_photo)
async def send_done_photo(message: Message, state: FSMContext):
    if not message.photo: return await message.answer("Отправьте фото.")
    data = await state.get_data()
    order_num = data.get("admin_order_num")
    await state.clear()
    update_order(order_num, status="Выполнен")
    order = get_order(order_num)
    await bot.send_photo(order["user_id"], message.photo[-1].file_id, caption=f"🎉 Заказ <b>№{order_num}</b> готов!\nОцените:", reply_markup=rating_kb(order_num), parse_mode="HTML")
    await message.answer(f"✅ Отправлено.")

@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject(call: CallbackQuery, state: FSMContext):
    order_num = int(call.data.split("_")[2])
    await state.update_data(admin_order_num=order_num)
    await state.set_state(Order.waiting_admin_reject)
    await call.message.answer(f"Причина отклонения №{order_num}:")

@dp.message(Order.waiting_admin_reject)
async def send_reject_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    order_num = data.get("admin_order_num")
    await state.clear()
    update_order(order_num, status="Отклонен")
    order = get_order(order_num)
    await bot.send_message(order["user_id"], f"❌ Заказ <b>№{order_num}</b> отклонен.\nПричина: {message.text}", parse_mode="HTML")
    await message.answer(f"✅ Отклонено.")

@dp.callback_query(F.data.startswith("admin_comment_"))
async def admin_comment(call: CallbackQuery, state: FSMContext):
    order_num = int(call.data.split("_")[2])
    await state.update_data(admin_order_num=order_num)
    await state.set_state(Order.waiting_admin_comment)
    await call.message.answer(f"Комментарий по №{order_num}:")

@dp.message(Order.waiting_admin_comment)
async def send_admin_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    order_num = data.get("admin_order_num")
    await state.clear()
    order = get_order(order_num)
    await bot.send_message(order["user_id"], f"💬 <b>По заказу №{order_num}:</b>\n{message.text}", parse_mode="HTML")
    await message.answer(f"✅ Отправлено.")

@dp.callback_query(F.data.startswith("rate_"))
async def client_rate(call: CallbackQuery):
    _, rating, order_num_str = call.data.split("_")
    order_num = int(order_num_str)
    update_order(order_num, rating=rating)
    await call.message.edit_caption(caption=f"Спасибо за оценку: {rating} ⭐", parse_mode="HTML")
    await bot.send_message(ADMIN_ID, f"⭐ Клиент оценил заказ №{order_num} на {rating}.")

# ===== ЗАПУСК =====
async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
