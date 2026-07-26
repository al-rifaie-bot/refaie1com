import logging
import sqlite3
import re
import time
import os
from datetime import datetime, timedelta
import pytz
import telebot
from telebot import types
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify
from collections import deque
import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import arabic_reshaper
from bidi.algorithm import get_display

# ==========================================
# إعدادات النظام الأساسية
# ==========================================
# قراءة مفتاح البوت من ملف خارجي لحمايته
token_file = "bot_token.txt"
if os.path.exists(token_file):
    with open(token_file, "r") as f:
        API_TOKEN = f.read().strip()
else:
    # في حال نسيان إنشاء الملف، ضع المفتاح الجديد بين القوسين هنا مؤقتاً
    API_TOKEN = '8675732184:AAEskLv4mG9UQf_gbdyB5EwFrKKHh4dFynA'
import secrets

# تأمين المفتاح السري للجلسات (إنشاء مفتاح عشوائي مشفر وحفظه)
key_file = ".secret_key"
if os.path.exists(key_file):
    with open(key_file, "r") as f:
        SECRET_KEY = f.read().strip()
else:
    SECRET_KEY = secrets.token_hex(32)
    with open(key_file, "w") as f:
        f.write(SECRET_KEY)
DB_NAME = "refaie_system.db"
APP_NAME = "Refaie"
ADMIN_TELEGRAM_IDS = [924578267] # 👈 استبدل الرقم 123456789 برقم حسابك الحقيقي
ADMIN_TG_ID = 924578267

local_tz = pytz.timezone('Asia/Damascus')
import threading
bot = telebot.TeleBot(API_TOKEN, threaded=False)
app = Flask(__name__)
app.secret_key = SECRET_KEY
# 🛡️ إعدادات الجلسة الإجبارية (تمنع الخروج عند إغلاق التطبيق)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True
app.config['SESSION_COOKIE_MAX_AGE'] = 31536000
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

user_steps, reg_steps, manual_order_steps, user_last_action = {}, {}, {}, {}

# ==========================================
# 1. قاعدة البيانات
# ==========================================
def get_db_connection():
    # زيادة وقت الانتظار وتفعيل أوامر المزامنة لتحمل ضغط الزبائن بدون أخطاء
    conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=20)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA synchronous=NORMAL;')
    conn.execute('PRAGMA busy_timeout=20000;')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    try:
        conn.execute("ALTER TABLE users ADD COLUMN username TEXT UNIQUE")
        conn.execute("ALTER TABLE users ADD COLUMN password TEXT")
        conn.execute("ALTER TABLE users ADD COLUMN access_method TEXT DEFAULT 'telegram'")
    except: pass
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, first_name TEXT, username TEXT, balance REAL DEFAULT 0, bills_balance REAL DEFAULT 0, can_pay_bills INTEGER DEFAULT 0, joined_date TIMESTAMP, is_banned INTEGER DEFAULT 0, real_name TEXT, shop_name TEXT, phone_contact TEXT, is_approved INTEGER DEFAULT 0, role TEXT DEFAULT 'user', agent_id INTEGER DEFAULT 0, password TEXT DEFAULT NULL)''')

    for col in ["bills_balance REAL DEFAULT 0", "can_pay_bills INTEGER DEFAULT 0", "is_vip INTEGER DEFAULT 0", "debt_balance REAL DEFAULT 0", "custom_sell_price REAL DEFAULT 1.05", "debt_limit REAL DEFAULT 50000", "emp_cash REAL DEFAULT 0", "emp_profit REAL DEFAULT 0", "loyalty_points INTEGER DEFAULT 0"]:
        try: c.execute(f"ALTER TABLE users ADD COLUMN {col}")
        except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS deposit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, by_admin_id INTEGER, amount REAL, wallet_type TEXT DEFAULT 'units', date TIMESTAMP)''')
    for col in ["wallet_type TEXT DEFAULT 'units'", "actual_paid REAL DEFAULT 0", "profit REAL DEFAULT 0", "is_debt INTEGER DEFAULT 0", "emp_balance_before REAL DEFAULT 0", "emp_balance_after REAL DEFAULT 0", "user_balance_before REAL DEFAULT 0", "user_balance_after REAL DEFAULT 0"]:
        try: c.execute(f"ALTER TABLE deposit_logs ADD COLUMN {col}")
        except: pass

    for col in ["wallet_type TEXT DEFAULT 'units'", "actual_paid REAL DEFAULT 0", "profit REAL DEFAULT 0", "is_debt INTEGER DEFAULT 0", "emp_balance_before REAL DEFAULT 0", "emp_balance_after REAL DEFAULT 0", "user_balance_before REAL DEFAULT 0", "user_balance_after REAL DEFAULT 0", "platform TEXT DEFAULT 'web'"]:
        try: c.execute(f"ALTER TABLE deposit_logs ADD COLUMN {col}")
        except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, network TEXT, service_type TEXT, amount REAL, display_name TEXT)''')
    try: c.execute("ALTER TABLE categories ADD COLUMN ussd_amount TEXT")
    except: pass
    try: c.execute("ALTER TABLE categories ADD COLUMN is_active INTEGER DEFAULT 1")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS companies (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, is_active INTEGER DEFAULT 1)''')
    try: c.execute("ALTER TABLE companies ADD COLUMN category TEXT DEFAULT 'bill'")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS manual_services (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER DEFAULT 0, name TEXT, price REAL, is_active INTEGER DEFAULT 1)''')
    for col in ["company_id INTEGER DEFAULT 0", "cost REAL DEFAULT 0", "execution_time TEXT DEFAULT '15 دقيقة'", "working_hours TEXT DEFAULT '09:00 ص - 10:00 م'"]:
        try: c.execute(f"ALTER TABLE manual_services ADD COLUMN {col}")
        except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS manual_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, service_name TEXT, target_info TEXT, price REAL, status TEXT, date TIMESTAMP)''')
    try: c.execute("ALTER TABLE manual_orders ADD COLUMN profit REAL DEFAULT 0")
    except: pass
    try: c.execute("ALTER TABLE manual_orders ADD COLUMN reject_reason TEXT")
    except: pass
    # 💡 التعديل الجديد: إضافة عواميد تصوير الرصيد لجدول الفواتير
    try: c.execute("ALTER TABLE manual_orders ADD COLUMN balance_before REAL DEFAULT 0")
    except: pass
    try: c.execute("ALTER TABLE manual_orders ADD COLUMN balance_after REAL DEFAULT 0")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS user_favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, phone TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, phone TEXT, network TEXT, target_amount REAL, actual_deduction REAL, combo TEXT, combo_ussd TEXT, date TIMESTAMP)''')

    c.execute('''CREATE TABLE IF NOT EXISTS merchant_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, amount REAL, cost_price REAL, total_debt_increase REAL, note TEXT, date TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS ussd_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, service_name TEXT UNIQUE, ussd_format TEXT, secret_pin TEXT DEFAULT '', success_keyword TEXT DEFAULT 'بنجاح', failure_keyword TEXT DEFAULT 'فشل', app_timeout INTEGER DEFAULT 20, request_interval INTEGER DEFAULT 5
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS sms_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT, message TEXT, date TIMESTAMP
    )''')

    default_codes = [
        ('cash_syriatel', '*150*1*{phone}*{amount}*0000#'),
        ('cash_mtn', '*150*{phone}*{amount}*0000#'),
        ('bill_syriatel', '*144*{phone}*{amount}*0000#'),
        ('bill_mtn', '*144*{phone}*{amount}*0000#')
    ]
    for name, code in default_codes:
        try: c.execute("INSERT OR IGNORE INTO ussd_codes (service_name, ussd_format) VALUES (?, ?)", (name, code))
        except: pass

    settings_defaults = [
        ('whatsapp', '963900000000'), ('copyright', f'جميع الحقوق محفوظة © 2026 - Refaie'),
        ('maintenance', '0'), ('max_trans_syriatel', '500'), ('max_trans_mtn', '50000'),
        ('status_Syriatel', '1'), ('status_MTN', '1'), ('sim_balance_Syriatel', '0'), ('sim_balance_MTN', '0'),
        ('fail_count_Syriatel', '0'), ('fail_count_MTN', '0'), ('ussd_bal_Syriatel', ''), ('ussd_bal_MTN', ''),
        ('pending_cmd_Syriatel', '0'), ('pending_cmd_MTN', '0'), ('status_secret_code', '1'),
        ('open_bill_percent_fee', '2'), ('open_bill_fixed_fee', '0'), ('open_bill_active', '1'),
        ('current_unit_cost', '1.05'), ('cash_drawer', '0'),
        ('merchant_debt', '0'), ('total_purchases', '0'), ('unit_buy_price', '1.03')
    ]
    for k, v in settings_defaults:
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    if c.execute("SELECT count(*) FROM companies").fetchone()[0] == 0:
        c.execute("INSERT INTO companies (name) VALUES ('شركة رن نت')")

    if not c.execute("SELECT * FROM users WHERE role='admin'").fetchone():
        c.execute("INSERT INTO users (user_id, real_name, username, password, role, balance, bills_balance, can_pay_bills, joined_date, is_approved, is_vip, debt_balance, custom_sell_price) VALUES (1, 'المدير العام', 'admin', 'admin', 'admin', 0, 0, 1, ?, 1, 1, 0, 1.05)", (datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit(); conn.close()

try: init_db()
except Exception as e: print(f"DB Init Error: {e}")

def add_category(network, service_type, amount, display_name, ussd_amount):
    conn = get_db_connection()
    conn.execute("INSERT INTO categories (network, service_type, amount, display_name, ussd_amount) VALUES (?, ?, ?, ?, ?)", (network, service_type, amount, display_name, ussd_amount))
    conn.commit(); conn.close()

def get_setting(key):
    conn = get_db_connection()
    res = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return res[0] if res else ""

def set_setting(key, value):
    conn = get_db_connection()
    conn.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit(); conn.close()

def get_user(user_id):
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    u = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return u

def topup_treasury(admin_id, amount, wallet_type, actual_paid=0):
    conn = get_db_connection()
    balance_col = 'balance' if wallet_type == 'units' else 'bills_balance'
    conn.execute(f"UPDATE users SET {balance_col} = {balance_col} + ? WHERE user_id = ?", (amount, admin_id))
    conn.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, date) VALUES (?, 0, ?, ?, ?, ?)", (admin_id, amount, actual_paid, wallet_type, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit(); conn.close()

def add_user_if_not_exists(user_id, first_name, username, agent_id=0):
    if not get_user(user_id):
        import html
        safe_first = html.escape(str(first_name)) if first_name else "بدون اسم"
        safe_user = html.escape(str(username)) if username else ""
        conn = get_db_connection()
        conn.execute("INSERT INTO users (user_id, first_name, username, balance, bills_balance, can_pay_bills, joined_date, is_banned, is_approved, agent_id, is_vip, debt_balance, custom_sell_price) VALUES (?, ?, ?, 0, 0, 0, ?, 0, 0, ?, 0, 0, 1.05)",
                  (user_id, safe_first, safe_user, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S"), agent_id))
        conn.commit(); conn.close()

def update_user_profile(user_id, real_name, shop_name, phone_contact):
    import html
    safe_name = html.escape(str(real_name)) if real_name else "بدون اسم"
    safe_shop = html.escape(str(shop_name)) if shop_name else ""
    safe_phone = html.escape(str(phone_contact)) if phone_contact else ""
    conn = get_db_connection()
    conn.execute("UPDATE users SET real_name=?, shop_name=?, phone_contact=? WHERE user_id=?", (safe_name, safe_shop, safe_phone, user_id))
    conn.commit()
    conn.close()

# ==========================================
# 2. خوارزمية الميزان الذكي
# ==========================================
def find_best_denominations(target, denoms):
    if not denoms: return [target]
    target_int = int(round(target * 100))
    denoms_int = sorted(list(set([int(round(d * 100)) for d in denoms])), reverse=True)
    if not denoms_int: return [target]
    best_score, best_path, queue, visited = float('inf'), [], deque([(0, [])]), {0: 0}
    iterations, max_iterations, STEP_PENALTY = 0, 20000, 10

    while queue and iterations < max_iterations:
        iterations += 1
        curr_sum, path = queue.popleft()
        if len(path) >= 5: continue
        for d in denoms_int:
            nxt_sum, nxt_path = curr_sum + d, path + [d]
            if nxt_sum >= target_int:
                score = (nxt_sum - target_int) + (len(nxt_path) * STEP_PENALTY)
                if score < best_score: best_score, best_path = score, nxt_path
                elif score == best_score and len(nxt_path) < len(best_path): best_path = nxt_path
            else:
                if nxt_sum not in visited or len(nxt_path) < visited[nxt_sum]:
                    visited[nxt_sum] = len(nxt_path)
                    queue.append((nxt_sum, nxt_path))

    if best_score == float('inf'):
        remaining, combo = target_int, []
        for d in denoms_int:
            if remaining >= d:
                c = remaining // d
                combo.extend([d] * c)
                remaining %= d
        if remaining > 0:
            val_d = [d for d in denoms_int if d >= remaining]
            combo.append(min(val_d) if val_d else denoms_int[0])
        best_path = combo
    return [round(c / 100.0, 3) for c in best_path]

# ==========================================
# 3. قسم البوت (التلغرام) الأساسي
# ==========================================
# قاموس جديد خاص بتنظيف الذاكرة فقط (تأكد من نسخه مع الكود)
# === نظام الكاش السريع جداً (Smart Cache) لمنع التقطيع ===
fast_cache = {}
last_cleanup_time = time.time()

def get_cached_setting(key, ttl=30):
    now = time.time()
    cache_key = f"setting_{key}"
    if cache_key in fast_cache and now - fast_cache[cache_key]['time'] < ttl:
        return fast_cache[cache_key]['data']
    val = get_setting(key)
    fast_cache[cache_key] = {'data': val, 'time': now}
    return val

def get_cached_user(user_id, ttl=30):
    now = time.time()
    cache_key = f"user_{user_id}"
    if cache_key in fast_cache and now - fast_cache[cache_key]['time'] < ttl:
        return fast_cache[cache_key]['data']
    val = get_user(user_id)
    fast_cache[cache_key] = {'data': val, 'time': now}
    return val

user_memory_tracker = {}

def is_bot_active_and_user_allowed(message):
    global last_cleanup_time
    now = time.time()

    # 1. تنظيف الذاكرة الذكي (كل 5 دقائق فقط بدل كل رسالة لتخفيف الضغط)
    if now - last_cleanup_time > 300:
        keys_to_del = [k for k, v in user_memory_tracker.items() if now - v > 3600]
        for k in keys_to_del:
            user_steps.pop(k, None)
            manual_order_steps.pop(k, None)
            reg_steps.pop(k, None)
            user_memory_tracker.pop(k, None)
        last_cleanup_time = now

    user_memory_tracker[message.from_user.id] = now

    # 2. فحص الصيانة من الذاكرة السريعة (بدون فتح الداتا بيز كل مرة)
    if get_cached_setting('maintenance') == '1' and message.from_user.id != ADMIN_TG_ID:
        bot.send_message(message.chat.id, "🛠️ *نعتذر منك*\nالنظام حالياً في وضع الصيانة والتحديث. سنعود للعمل قريباً جداً.", parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
        return False
        
    # 3. فحص حالة الزبون من الذاكرة السريعة (استجابة لحظية)
    user = get_cached_user(message.from_user.id)
    if user:
        if user['is_banned'] == 1:
            bot.send_message(message.chat.id, "⛔ *عذراً*\nلقد تم حظر حسابك من قبل إدارة النظام.", parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
            return False
        if user['is_approved'] == 0:
            bot.send_message(message.chat.id, "❄️ *عذراً*\nحسابك مجمد مؤقتاً. يرجى الانتظار لحين التفعيل.", parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
            return False
    return True

def is_callback_allowed(call):
    if get_cached_setting('maintenance') == '1' and call.from_user.id != ADMIN_TG_ID:
        bot.answer_callback_query(call.id, "🛠️ النظام في وضع الصيانة حالياً. يرجى المحاولة لاحقاً.", show_alert=True)
        return False
        
    user = get_cached_user(call.from_user.id)
    if user and (user['is_banned'] == 1 or user['is_approved'] == 0):
        bot.answer_callback_query(call.id, "⛔ حساب موقوف أو مجمد من قبل الإدارة.", show_alert=True)
        return False

    # إنهاء إشارة التحميل فوراً ليظهر البوت سريعاً
    try:
        bot.answer_callback_query(call.id)
    except:
        pass

    return True

import threading

def delete_last_bot_message(chat_id, user_id, context_dict):
    last_msg_id = context_dict.get(user_id, {}).get('last_bot_msg_id')
    if last_msg_id:
        # نقوم بإزالة الأزرار من الرسالة القديمة عبر مسار خلفي (Thread) 
        # لكي يبقى البوت طلقة ولا ينتظر رد سيرفرات تلغرام
        def remove_buttons():
            try: 
                bot.edit_message_reply_markup(chat_id, last_msg_id, reply_markup=None)
            except: 
                pass
        threading.Thread(target=remove_buttons).start()

def check_abort(message):
    if not message.text: return False
    commands = ["/start", "/cancel", "『 🚀 تـحـويـل رصـيـد 』", "💸 كـاش وفـواتيـر إتـصـالات", "📦 الـتحويـل الـجماعـي", "【 🧾 دفـع الـفـواتـيـر 】", "🎮 شحـن ألـعـاب وبـرامـج", "⟪ 💳 حـسـابـي والـديـون ⟫", "📊 سـجـل الـعـمـلـيـات", "⭐ الأرقــام الـمـفـضـلـة", "📱 تـطـبـيـق الـمـوبـايـل", "📞 الـدعـم الـفـنـي", "⚠️ تـدقـيـق رقـم أو مـشـكـلـة", "👑 لـوحـة الـمـديـر"]
    if message.text in commands:
        bot.clear_step_handler_by_chat_id(message.chat.id)
        user_steps.pop(message.from_user.id, None)
        manual_order_steps.pop(message.from_user.id, None)
        reg_steps.pop(message.from_user.id, None)
        if message.text == "/start": send_welcome(message)
        elif message.text == "/cancel": cancel_process(message)
        elif message.text == "『 🚀 تـحـويـل رصـيـد 』": menu_transfer(message)
        elif message.text == "💸 كـاش وفـواتيـر إتـصـالات": menu_cash_bills(message)
        elif message.text == "📦 الـتحويـل الـجماعـي": start_bulk_transfer(message)
        elif message.text == "【 🧾 دفـع الـفـواتـيـر 】": menu_services_handler(message)
        elif message.text == "⟪ 💳 حـسـابـي والـديـون ⟫": menu_balance(message)
        elif message.text == "📊 سـجـل الـعـمـلـيـات": menu_history(message)
        elif message.text == "⭐ الأرقــام الـمـفـضـلـة": menu_favorites(message)
        elif message.text == "📱 تـطـبـيـق الـمـوبـايـل": menu_download_app(message)
        elif message.text == "📞 الـدعـم الـفـنـي": menu_support(message)
        elif message.text == "⚠️ تـدقـيـق رقـم أو مـشـكـلـة": menu_support_ticket(message)
        elif message.text == "👑 لـوحـة الـمـديـر": menu_admin_panel(message)
        return True
    return False

def main_menu(user_id=None):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)

    # 💡 فحص حالة زر الكاش والفواتير من الإعدادات
    cash_btn_status = get_setting('status_cash_bills') or '0'

    if cash_btn_status == '1':
        markup.add(types.KeyboardButton("『 🚀 تـحـويـل رصـيـد 』"), types.KeyboardButton("💸 كـاش وفـواتيـر إتـصـالات"))
    else:
        # إذا كان مخفي، نظهر زر تحويل الرصيد لوحده بالسطر الأول
        markup.add(types.KeyboardButton("『 🚀 تـحـويـل رصـيـد 』"))

    markup.add(types.KeyboardButton("📦 الـتحويـل الـجماعـي"))
    markup.add(types.KeyboardButton("【 🧾 دفـع الـفـواتـيـر 】"), types.KeyboardButton("🎮 شحـن ألـعـاب وبـرامـج"))
    markup.add(types.KeyboardButton("⟪ 💳 حـسـابـي والـديـون ⟫"), types.KeyboardButton("📊 سـجـل الـعـمـلـيـات"))

    bottom_row = [types.KeyboardButton("⭐ الأرقــام الـمـفـضـلـة")]
    if get_setting('status_app_button') != '0':
        bottom_row.append(types.KeyboardButton("📱 تـطـبـيـق الـمـوبـايـل"))
    markup.add(*bottom_row)

    markup.add(types.KeyboardButton("⚠️ تـدقـيـق رقـم أو مـشـكـلـة"), types.KeyboardButton("📞 الـدعـم الـفـنـي"))

    if user_id and user_id in ADMIN_TELEGRAM_IDS:
        markup.add(types.KeyboardButton("👑 لـوحـة الـمـديـر"))


    return markup


# --- دوال الإدارة والتدقيق الأساسية ---
@bot.message_handler(func=lambda message: message.text == "👑 لـوحـة الـمـديـر")
def menu_admin_panel(message):
    if message.from_user.id not in ADMIN_TELEGRAM_IDS: return
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🟢 إضافة رصيد لزبون", callback_data="admin_add_balance"),
        types.InlineKeyboardButton("🔴 تسديد دفعة (تنزيل دين)", callback_data="admin_pay_debt"),
        types.InlineKeyboardButton("🧹 تصفير رصيد زبون", callback_data="admin_zero_balance"), # <-- الزر بمكانه الصح
        types.InlineKeyboardButton("❌ إغلاق", callback_data="close_admin")
    )
    # أمر الإرسال بيكون دائماً بآخر الدالة
    bot.reply_to(message, "👑 *مرحباً بك يا مدير في لوحة التحكم الخفية*\n\nيرجى اختيار العملية المطلوبة:", reply_markup=markup, parse_mode="Markdown")
    
@bot.message_handler(func=lambda message: message.text == "⚠️ تـدقـيـق رقـم أو مـشـكـلـة")
def menu_support_ticket(message):
    if not is_bot_active_and_user_allowed(message): return
    msg = bot.reply_to(message, "⚠️ *قسم التدقيق والمساعدة*\n\nيرجى كتابة الرقم الذي تواجه مشكلة فيه مع شرح بسيط لمشكلتك في رسالة واحدة، وسيتم إرسالها للإدارة فوراً للتدقيق:", parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, process_support_ticket)

def process_support_ticket(message):
    if check_abort(message): return

    # جلب اسم الزبون
    user_row = get_user(message.from_user.id)
    user_name = user_row['real_name'] if user_row and user_row['real_name'] else message.from_user.first_name

    # رسالة الإشعار التي ستصلك كمدير
    admin_msg = f"⚠️ *طلب تدقيق جديد من زبون*\n\n👤 الزبون: *{user_name}*\n🆔 رقم الحساب: `{message.from_user.id}`\n\n📝 المشكلة أو الرقم:\n{message.text}"

    # زر الرد المباشر الذي سيظهر لك
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("↩️ الرد على الزبون", callback_data=f"reply_ticket_{message.from_user.id}"))

    # إرسال الإشعار لجميع المدراء (لرقمك)
    for admin_id in ADMIN_TELEGRAM_IDS:
        try:
            bot.send_message(admin_id, admin_msg, parse_mode="Markdown", reply_markup=markup)
        except: pass

    bot.reply_to(message, "✅ تم إرسال طلب التدقيق للإدارة بنجاح. سيصلك الرد قريباً.", reply_markup=main_menu(message.from_user.id))


@bot.message_handler(commands=['start'])
def send_welcome(message):
    if not is_bot_active_and_user_allowed(message): return
    user_id = message.from_user.id
    bot.clear_step_handler_by_chat_id(message.chat.id)
    add_user_if_not_exists(user_id, message.from_user.first_name, message.from_user.username, 0)
    user = get_user(user_id)
    if not user['real_name']:
        msg = bot.send_message(message.chat.id, f"أهلاً بك في *{APP_NAME}* 💎\nللتسجيل، يرجى كتابة *الاسم الثلاثي*:", parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
        bot.register_next_step_handler(msg, process_reg_name)
        return
    bot.send_message(message.chat.id, f"أهلاً بك أستاذ *{user['real_name']}* 💎\nاختر الخدمة المطلوبة:", parse_mode="Markdown", reply_markup=main_menu(message.from_user.id))

def process_reg_name(message):
    if check_abort(message): return
    if not message.text:
        msg = bot.reply_to(message, "يرجى كتابة نص فقط:"); bot.register_next_step_handler(msg, process_reg_name); return
    reg_steps[message.from_user.id] = {'real_name': message.text}
    msg = bot.reply_to(message, "ممتاز! 🏢 الآن يرجى كتابة *اسم محلك التجاري*:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_reg_shop)

def process_reg_shop(message):
    if check_abort(message): return
    if not message.text:
        msg = bot.reply_to(message, "يرجى كتابة نص فقط:"); bot.register_next_step_handler(msg, process_reg_shop); return
    reg_steps[message.from_user.id]['shop_name'] = message.text
    msg = bot.reply_to(message, "أخيراً 📞، أدخل *رقم هاتفك* للتواصل:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_reg_phone)

def process_reg_phone(message):
    if check_abort(message): return
    if not message.text:
        msg = bot.reply_to(message, "يرجى إدخال رقم:"); bot.register_next_step_handler(msg, process_reg_phone); return
    user_id = message.from_user.id
    data = reg_steps.get(user_id)
    if data:
        update_user_profile(user_id, data['real_name'], data['shop_name'], message.text)
        bot.send_message(message.chat.id, "✅ *تم تسجيل طلب بنجاح!*\nسيتم إشعارك فور تفعيل حسابك.", parse_mode="Markdown")

@bot.message_handler(commands=['cancel'])
def cancel_process(message):
    if not is_bot_active_and_user_allowed(message): return
    user_steps.pop(message.from_user.id, None); manual_order_steps.pop(message.from_user.id, None)
    bot.clear_step_handler_by_chat_id(message.chat.id)
    bot.send_message(message.chat.id, "❌ تم إلغاء العملية.", reply_markup=main_menu(message.from_user.id))
@bot.callback_query_handler(func=lambda call: call.data.startswith('hist_page_'))
def hist_page_callback(call):
    if not is_callback_allowed(call): return
    page = int(call.data.split('_')[2])
    show_bot_history(call.message.chat.id, call.from_user.id, page, call.message.message_id)

def show_bot_history(chat_id, user_id, page, message_id=None):
    per_page = 10
    offset = (page - 1) * per_page
    conn = get_db_connection()
    deposits = conn.execute("SELECT amount, actual_paid, date, wallet_type, is_debt FROM deposit_logs WHERE user_id=? AND by_admin_id!=0", (user_id,)).fetchall()
    transfers = conn.execute("SELECT amount, date, status, phone FROM transactions WHERE user_id=?", (user_id,)).fetchall()
    bills = conn.execute("SELECT price as amount, date, status, service_name FROM manual_orders WHERE user_id=?", (user_id,)).fetchall()
    conn.close()

    history = []
    for d in deposits:
        if d['wallet_type'] == 'debt_payment': history.append({'type': 'DEBT_PAY', 'amount': d['actual_paid'], 'date': d['date'], 'status': 'SUCCESS', 'detail': 'تسديد دفعة دين'})
        elif d['wallet_type'] == 'free_debt': history.append({'type': 'FREE_DEBT', 'amount': d['actual_paid'], 'date': d['date'], 'status': 'SUCCESS', 'detail': 'تسجيل دين حر'})
        else: history.append({'type': 'DEPOSIT', 'amount': d['amount'], 'date': d['date'], 'status': 'SUCCESS', 'detail': f"محفظة {'الوحدات' if d['wallet_type']=='units' else 'الفواتير'}", 'is_debt': d['is_debt']})
    for t in transfers: history.append({'type': 'TRANSFER', 'amount': t['amount'], 'date': t['date'], 'status': t['status'], 'detail': f"{t['phone']}"})
    for b in bills: history.append({'type': 'BILL', 'amount': b['amount'], 'date': b['date'], 'status': b['status'], 'detail': b['service_name']})

    history.sort(key=lambda x: x['date'], reverse=True)
    total_records = len(history)
    total_pages = max(1, (total_records + per_page - 1) // per_page)
    page_data = history[offset:offset+per_page]

    if not page_data and page == 1:
        text = "لا يوجد سجل عمليات في حسابك حتى الآن."
        if message_id: bot.edit_message_text(text, chat_id, message_id)
        else: bot.send_message(chat_id, text)
        return

    text = f"📜 *كشف حساب (صفحة {page} من {total_pages}):*\n━━━━━━━━━━━━━━━\n"
    for i, h in enumerate(page_data, start=offset + 1):
        date_short = h['date'][:16]
        if h['type'] == 'DEPOSIT':
            sign, debt_txt = ("➕ إيداع", " (آجل)" if h.get('is_debt') else "") if h['amount'] > 0 else ("➖ خصم", "")
            text += f"*{i}- {sign}* | `{abs(h['amount']):,.0f}` وحدة/ليرة | {h['detail']}{debt_txt} | ⏳ {date_short}\n\n"
        elif h['type'] == 'DEBT_PAY': text += f"*{i}-* 💰 *تسديد* | `{abs(h['amount']):,.0f}` ل.س | {h['detail']} | ⏳ {date_short}\n\n"
        elif h['type'] == 'FREE_DEBT': text += f"*{i}-* 📝 *دين خارجي* | `{abs(h['amount']):,.0f}` ل.س | {h['detail']} | ⏳ {date_short}\n\n"
        elif h['type'] == 'TRANSFER':
            stat = '✅' if h['status']=='SUCCESS' else '⏳' if h['status']=='QUEUED' else '⚙️' if h['status']=='PROCESSING' else '🔎' if h['status']=='MANUAL_CHECK' else '↩️' if h['status']=='REFUNDED' else '❌'
            text += f"*{i}-* 📱 *تحويل* | `{h['amount']:,.0f}` وحدة | `{h['detail']}` | {stat} | ⏳ {date_short}\n\n"
        elif h['type'] == 'BILL':
            stat = '✅' if h['status']=='COMPLETED' else '⏳' if h['status']=='PENDING' else '❌'
            text += f"*{i}-* 🧾 *فاتورة* | `{h['amount']:,.0f}` ل.س | {h['detail']} | {stat} | ⏳ {date_short}\n\n"

    markup = types.InlineKeyboardMarkup(row_width=2)
    btns = []
    if page < total_pages: btns.append(types.InlineKeyboardButton("التالي ⬅️", callback_data=f"hist_page_{page+1}"))
    if page > 1: btns.append(types.InlineKeyboardButton("➡️ السابق", callback_data=f"hist_page_{page-1}"))
    if btns: markup.add(*btns)
    markup.add(types.InlineKeyboardButton("❌ إغلاق", callback_data="dash_main"))

    if message_id: bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown", reply_markup=markup)
    else: bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "dash_main")
def back_to_main_callback(call):
    if not is_callback_allowed(call): return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    user_steps.pop(call.from_user.id, None)
    manual_order_steps.pop(call.from_user.id, None)
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    # تم إصلاح الخطأ البرمجي هنا (استخدام call بدلاً من message)
    bot.send_message(call.message.chat.id, "🔙 تم الإلغاء.", reply_markup=main_menu(call.from_user.id))

@bot.message_handler(commands=['report'])
def send_report_to_admin(message):
    if message.from_user.id != ADMIN_TG_ID: return
    conn = get_db_connection()
    today = datetime.now(local_tz).strftime("%Y-%m-%d")
    s_sales = conn.execute("SELECT sum(amount) FROM transactions WHERE status='SUCCESS' AND network='Syriatel' AND date LIKE ?", (f"{today}%",)).fetchone()[0] or 0
    m_sales = conn.execute("SELECT sum(amount) FROM transactions WHERE status='SUCCESS' AND network='MTN' AND date LIKE ?", (f"{today}%",)).fetchone()[0] or 0
    total_debt = conn.execute("SELECT sum(debt_balance) FROM users WHERE role='user'").fetchone()[0] or 0
    conn.close()
    cash = float(get_setting('cash_drawer') or 0)
    sim_s = float(get_setting('sim_balance_Syriatel') or 0)
    sim_m = float(get_setting('sim_balance_MTN') or 0)

    text = f"📊 *الجرد السريع (لليوم)*\n━━━━━━━━━━━━━━\n🔴 مبيعات سيريتل: `{s_sales:g}`\n🟡 مبيعات إم تي إن: `{m_sales:g}`\n\n💳 *السيولة الحالية:*\n💵 الكاش (بالدرج): `{cash:,.0f}` ل.س\n📝 ديون الزبائن: `{total_debt:,.0f}` ل.س\n\n📱 *رصيد الشرائح الفعلي:*\n🔴 سيريتل: `{sim_s:g}`\n🟡 إم تي إن: `{sim_m:g}`"
    bot.reply_to(message, text, parse_mode="Markdown")

def handle_favorites_logic(chat_id, user_id, mid=None):
    conn = get_db_connection()
    favs = conn.execute("SELECT * FROM user_favorites WHERE user_id=?", (user_id,)).fetchall()
    conn.close()

    markup = types.InlineKeyboardMarkup(row_width=2)
    if not favs:
        text = "⭐ *دفتر العناوين (الأرقام المحفوظة):*\n\n📭 الدفتر فارغ حالياً. أضف أرقام زبائنك لسرعة التحويل."
    else:
        text = "⭐ *دفتر العناوين (الأرقام المحفوظة):*\n\n👇 اضغط على اسم الزبون للتحويل فوراً:"
        btns = []
        for f in favs:
            if f['phone'].startswith('09'):
                btns.append(types.InlineKeyboardButton(f"📱 {f['name']} | {f['phone'][2:]}", callback_data=f"favuse_trans_{f['phone']}"))
            elif f['phone'].startswith('011'):
                btns.append(types.InlineKeyboardButton(f"📞 {f['name']} | {f['phone'][3:]}", callback_data=f"favuse_bill_start_{f['phone']}"))
            elif len(f['phone']) == 8:
                btns.append(types.InlineKeyboardButton(f"🔐 {f['name']}", callback_data=f"favuse_trans_{f['phone']}"))
        markup.add(*btns)

    markup.row(types.InlineKeyboardButton("➕ إضافة رقم جديد", callback_data="fav_add"))
    if favs:
        markup.row(types.InlineKeyboardButton("🗑️ إدارة وحذف الأرقام", callback_data="fav_manage_delete"))
    markup.row(types.InlineKeyboardButton("❌ إغلاق", callback_data="dash_main"))

    if mid: bot.edit_message_text(text, chat_id, mid, reply_markup=markup, parse_mode="Markdown")
    else: bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'dash_favorites')
def dash_fav_callback(call):
    if is_callback_allowed(call): handle_favorites_logic(call.message.chat.id, call.from_user.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == 'fav_manage_delete')
def fav_manage_delete(call):
    if not is_callback_allowed(call): return
    conn = get_db_connection()
    favs = conn.execute("SELECT * FROM user_favorites WHERE user_id=?", (call.from_user.id,)).fetchall()
    conn.close()
    markup = types.InlineKeyboardMarkup(row_width=1)
    for f in favs: markup.add(types.InlineKeyboardButton(f"🗑️ حذف: {f['name']} ({f['phone']})", callback_data=f"fav_del_{f['id']}"))
    markup.add(types.InlineKeyboardButton("🔙 عودة", callback_data="dash_favorites"))
    bot.edit_message_text("اختر الرقم الذي تود مسحه:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('fav_del_'))
def fav_del(call):
    if not is_callback_allowed(call): return
    conn = get_db_connection()
    conn.execute("DELETE FROM user_favorites WHERE id=? AND user_id=?", (call.data.split('_')[2], call.from_user.id))
    conn.commit(); conn.close()
    bot.answer_callback_query(call.id, "✅ تم الحذف.")
    handle_favorites_logic(call.message.chat.id, call.from_user.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == 'fav_add')
def fav_add_start(call):
    if not is_callback_allowed(call): return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    msg = bot.edit_message_text("➕ *إضافة رقم جديد:*\nيرجى كتابة *اسم العميل*:", call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_favorites")), parse_mode="Markdown")
    bot.register_next_step_handler(msg, fav_add_name)

def fav_add_name(message):
    if check_abort(message): return
    if not message.text:
        msg = bot.reply_to(message, "يرجى كتابة نص فقط:"); bot.register_next_step_handler(msg, fav_add_name); return
    user_steps[message.from_user.id] = {'fav_name': message.text.strip()}

    code_enabled = get_setting('status_secret_code') == '1'
    prompt_txt = "الآن يرجى إرسال *الرقم* (موبايل 09، أرضي 011، أو كود سري 8 أرقام):" if code_enabled else "الآن يرجى إرسال *الرقم* (موبايل 10 أرقام 09، أو أرضي 011):"

    msg = bot.reply_to(message, prompt_txt, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_favorites")), parse_mode="Markdown")
    bot.register_next_step_handler(msg, fav_add_phone)

def fav_add_phone(message):
    if check_abort(message): return
    if not message.text:
        msg = bot.reply_to(message, "يرجى إدخال أرقام فقط:"); bot.register_next_step_handler(msg, fav_add_phone); return
    uid, phone = message.from_user.id, message.text.strip()

    code_enabled = get_setting('status_secret_code') == '1'
    pattern = r'^(09\d{8}|011\d{7}|\d{8})$' if code_enabled else r'^(09\d{8}|011\d{7})$'

    if not re.match(pattern, phone):
        err_txt = "⚠️ صيغة الرقم غير صحيحة:" if code_enabled else "⚠️ صيغة الرقم غير صحيحة (ميزة الكود السري متوقفة حالياً):"
        msg = bot.reply_to(message, err_txt, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_favorites")))
        bot.register_next_step_handler(msg, fav_add_phone); return

    name = user_steps.get(uid, {}).get('fav_name', 'بدون اسم')
    conn = get_db_connection()
    if conn.execute("SELECT count(*) FROM user_favorites WHERE user_id=?", (uid,)).fetchone()[0] >= 50:
        conn.close(); return bot.reply_to(message, "⚠️ محفظة الأرقام ممتلئة (الحد الأقصى 50 رقم).", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 عودة", callback_data="dash_favorites")))
    conn.execute("INSERT INTO user_favorites (user_id, name, phone) VALUES (?, ?, ?)", (uid, name, phone)); conn.commit(); conn.close()
    user_steps.pop(uid, None)
    bot.reply_to(message, f"╭━━━ ✅ تم الحفظ ━━━╮\n👤 الاسم: *{name}*\n🎯 الرقم/الكود: `{phone}`\n╰━━━━━━━━━━━━━╯", parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 عودة للمفضلة", callback_data="dash_favorites")))

def start_transfer_flow(message):
    user_id = message.from_user.id
    now = time.time()
    
    # فحص الضغط المتكرر
    if now - user_last_action.get(user_id, 0) < 5: 
        return bot.reply_to(message, f"⏳ يرجى الانتظار `{int(5 - (now - user_last_action.get(user_id, 0)))}` ثانية.", parse_mode="Markdown")
        
    bot.clear_step_handler_by_chat_id(message.chat.id)
    user_steps[user_id] = {'service_type': 'Jahez'}

    code_enabled = get_setting('status_secret_code') == '1'

    # تجهيز زر الإلغاء فقط
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("❌ إلغاء العملية", callback_data="dash_main"))

    # النص الجديد بدون ذكر القائمة السريعة
    if code_enabled:
        prompt_txt = "⚡ *تحويل رصيد سريع*\nأدخل رقم المستلم (10 أرقام)، أو كود التحويل لسيريتل (8 أرقام):"
    else:
        prompt_txt = "⚡ *تحويل رصيد سريع*\nأدخل رقم المستلم (10 أرقام فقط):"

    msg = bot.reply_to(message, prompt_txt, parse_mode="Markdown", reply_markup=markup)
    user_steps[user_id]['last_bot_msg_id'] = msg.message_id
    bot.register_next_step_handler(msg, process_phone_and_network)

@bot.callback_query_handler(func=lambda call: call.data.startswith('favuse_trans_'))
def favuse_trans(call):
    if not is_callback_allowed(call): return
    user_id = call.from_user.id
    now = time.time()
    if now - user_last_action.get(user_id, 0) < 5: return bot.answer_callback_query(call.id, f"⏳ يرجى الانتظار {int(5 - (now - user_last_action.get(user_id, 0)))} ثانية.", show_alert=True)
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    user_steps[user_id] = {'service_type': 'Jahez'}
    execute_transfer_phone(user_id, call.message.chat.id, call.data.split('_')[2], None)

def process_phone_and_network(message):
    delete_last_bot_message(message.chat.id, message.from_user.id, user_steps)
    if check_abort(message): return
    if not message.text:
        msg = bot.reply_to(message, "⚠️ يرجى إدخال أرقام فقط:"); bot.register_next_step_handler(msg, process_phone_and_network); return
    execute_transfer_phone(message.from_user.id, message.chat.id, message.text.strip(), None)

def execute_transfer_phone(user_id, chat_id, phone, message_id=None):
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_main"))
    if user_id not in user_steps: user_steps[user_id] = {'service_type': 'Jahez'}

    code_enabled = get_setting('status_secret_code') == '1'
    match = re.search(r'^((09\d{8})|(\d{8}))$', phone)

    if not match:
        text = "⚠️ الرقم المدخل غير صحيح:"
        msg = bot.edit_message_text(text, chat_id, message_id, reply_markup=markup) if message_id else bot.send_message(chat_id, text, reply_markup=markup)
        user_steps[user_id]['last_bot_msg_id'] = msg.message_id
        bot.register_next_step_handler(msg, process_phone_and_network); return

    clean_phone = phone
    if len(clean_phone) == 8:
        if not code_enabled:
            text = "⚠️ خدمة التحويل عبر الكود السري متوقفة."
            msg = bot.edit_message_text(text, chat_id, message_id, reply_markup=markup) if message_id else bot.send_message(chat_id, text, reply_markup=markup)
            user_steps[user_id]['last_bot_msg_id'] = msg.message_id
            bot.register_next_step_handler(msg, process_phone_and_network); return
        network = "Syriatel"
    else:
        prefix = clean_phone[0:3]
        network = "Syriatel" if prefix in ['093','098','099'] else "MTN" if prefix in ['094','095','096'] else None

    if not network:
        text = "⚠️ الرقم لا يتبع لشبكتي سيريتل أو إم تي إن."
        msg = bot.edit_message_text(text, chat_id, message_id, reply_markup=markup) if message_id else bot.send_message(chat_id, text, reply_markup=markup)
        user_steps[user_id]['last_bot_msg_id'] = msg.message_id
        bot.register_next_step_handler(msg, process_phone_and_network); return

    if get_setting(f'status_{network}') == '0':
        text = f"⚠️ نعتذر، خدمة تحويل (*{'سيريتل' if network=='Syriatel' else 'MTN'}*) متوقفة حالياً."
        bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown") if message_id else bot.send_message(chat_id, text, parse_mode="Markdown")
        user_steps.pop(user_id, None); return

    user_steps[user_id].update({'phone': clean_phone, 'network': network})
    net_icon = "🔴" if network == 'Syriatel' else "🟡"
    target_str = f"`{clean_phone}` (كود سري)" if len(clean_phone) == 8 else f"`{clean_phone}`"

    text = f"الشبكة: {net_icon} *{network}*\nالهدف: {target_str}\n\n💸 *أدخل الكمية المطلوبة (أرقام فقط):*"
    msg = bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="Markdown") if message_id else bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
    user_steps[user_id]['last_bot_msg_id'] = msg.message_id
    bot.register_next_step_handler(msg, process_transfer_amount)

def process_transfer_amount(message):
    delete_last_bot_message(message.chat.id, message.from_user.id, user_steps)
    if check_abort(message): return
    user_id = message.from_user.id
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_main"))
    if user_id not in user_steps or 'phone' not in user_steps[user_id]: return bot.reply_to(message, "⏳ انتهت الجلسة.", reply_markup=main_menu(message.from_user.id))

    try:
        data = user_steps[user_id]
        network, target_amount = data['network'], float(message.text.strip())
        if target_amount <= 0: raise ValueError

        max_limit = float(get_setting('max_trans_syriatel' if network == 'Syriatel' else 'max_trans_mtn') or (500 if network == 'Syriatel' else 50000))
        if target_amount > max_limit:
            bot.reply_to(message, f"⚠️ الحد الأقصى لشبكة {network} هو `{max_limit:g}` وحدة.", reply_markup=markup, parse_mode="Markdown")
            user_steps.pop(user_id, None); return

        conn = get_db_connection()
        cats = conn.execute("SELECT amount, ussd_amount FROM categories WHERE network=? AND is_active=1", (network,)).fetchall()

        if not cats:
            conn.close(); bot.reply_to(message, "⚠️ لا توجد فئات متوفرة.", reply_markup=main_menu(message.from_user.id)); user_steps.pop(user_id, None); return

        denoms = [float(c['amount']) for c in cats]
        amt_to_ussd = {float(c['amount']): c['ussd_amount'] for c in cats}
        combo = find_best_denominations(target_amount, denoms)

        if len(combo) > 5:
            bot.reply_to(message, f"⚠️ *عذراً، الفئات غير متوفرة.*\nيحتاج طلبك لـ ({len(combo)}) عمليات مما يسبب فشلها بالشبكة.", parse_mode="Markdown", reply_markup=markup)
            user_steps.pop(user_id, None); conn.close(); return

        total_sum = sum(combo)
        user = get_user(user_id)

        if user['balance'] < total_sum:
            conn.close(); bot.reply_to(message, f"⚠️ رصيد محفظتك غير كافٍ.\nالمطلوب: `{total_sum:g}` وحدة", reply_markup=markup, parse_mode="Markdown"); user_steps.pop(user_id, None); return

        sim_bal = float(get_setting(f'sim_balance_{network}') or 0)
        if total_sum > sim_bal:
            conn.close()
            bot.reply_to(message, f"⚠️ *نعتذر منك!*\nرصيد شبكة {network} غير كافٍ حالياً لتلبية طلبك. يرجى المحاولة لاحقاً.", parse_mode="Markdown", reply_markup=markup)
            try: bot.send_message(ADMIN_TG_ID, f"🚨 *تنبيه نفاد رصيد الشريحة!*\nحاول زبون تحويل `{total_sum:g}` ولكن شريحة {network} فيها `{sim_bal:g}` وحدة فقط! يرجى الشحن.", parse_mode="Markdown")
            except: pass
            user_steps.pop(user_id, None)
            return

        cur = conn.cursor()
        cur.execute("INSERT INTO drafts (user_id, phone, network, target_amount, actual_deduction, combo, combo_ussd, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (user_id, data['phone'], network, target_amount, total_sum, ",".join(map(str, combo)), ",".join([str(amt_to_ussd[c]) for c in combo]), datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
        draft_id = cur.lastrowid
        conn.commit(); conn.close()

        diff = round(total_sum - target_amount, 3)
        diff_text = f"\n*(تجاوز بمقدار {diff:g} وحدة)*" if diff > 0 else ""
        combo_formatted = ' + '.join([str(c) for c in combo])
        target_str = f"`{data['phone']}` (كود سري)" if len(data['phone']) == 8 else f"`{data['phone']}`"

        conf_msg = f"╭━━━ 🧾 *مراجعة الطلب* ━━━╮\n📱 الشبكة: *{network}*\n🎯 الهدف: {target_str}\n💸 طلبك: `{target_amount:g}` وحدة\n📦 الإرسال: {combo_formatted} {diff_text}\n┣━━━━━━━━━━━━━━━━━━┫\n💎 الخصم من الرصيد: `{total_sum:g}` وحدة\n╰━━━━━━━━━━━━━━━━━━╯\nهل توافق؟"
        conf_markup = types.InlineKeyboardMarkup()
        conf_markup.add(types.InlineKeyboardButton("✅ تأكيد", callback_data=f"conf_yes_{draft_id}"), types.InlineKeyboardButton("❌ إلغاء", callback_data=f"conf_no_{draft_id}"))
        msg = bot.send_message(message.chat.id, conf_msg, reply_markup=conf_markup, parse_mode="Markdown")
        user_steps[user_id]['last_bot_msg_id'] = msg.message_id
    except ValueError:
        msg = bot.reply_to(message, "⚠️ يرجى إدخال أرقام صحيحة:", reply_markup=markup)
        user_steps[user_id]['last_bot_msg_id'] = msg.message_id
        bot.register_next_step_handler(msg, process_transfer_amount)

@bot.callback_query_handler(func=lambda call: call.data.startswith('conf_'))
def callback_confirm(call):
    if not is_callback_allowed(call): return
    if call.data.startswith('conf_openbill_'): return

    user_id = call.from_user.id
    chat_id, mid = call.message.chat.id, call.message.message_id

    now = time.time()
    if now - user_last_action.get(user_id, 0) < 2:
        bot.answer_callback_query(call.id, "⏳ يرجى عدم الضغط المتكرر!", show_alert=False)
        return
    user_last_action[user_id] = now

    try: bot.edit_message_reply_markup(chat_id, mid, reply_markup=None)
    except: pass

    bot.answer_callback_query(call.id)
    action, draft_id = call.data.split('_')[1], call.data.split('_')[2]

    conn = get_db_connection()
    draft = conn.execute("SELECT * FROM drafts WHERE id=? AND user_id=?", (draft_id, user_id)).fetchone()
    if not draft:
        conn.close()
        return bot.edit_message_text("⏳ الجلسة منتهية أو تم تنفيذ الطلب مسبقاً.", chat_id, mid)

    cur = conn.cursor()
    cur.execute("DELETE FROM drafts WHERE id=? AND user_id=?", (draft_id, user_id))
    if cur.rowcount == 0:
        conn.commit(); conn.close()
        return

    if action == "no":
        conn.commit(); conn.close()
        return bot.edit_message_text("❌ تم الإلغاء.", chat_id, mid)

    actual_deduction = draft['actual_deduction']

    # 🛡️ الخصم الآمن (Atomic Update) لمنع الاحتيال عبر التلغرام
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?", (actual_deduction, user_id, actual_deduction))
    if cur.rowcount == 0:
        conn.rollback(); conn.close()
        return bot.edit_message_text("⚠️ الرصيد غير كافٍ أو توجد عملية جارية.", chat_id, mid)

    bot.edit_message_text("⏳ *🚀 ثواني والطلب بيجهز...*", chat_id, mid, parse_mode="Markdown")

    user_row = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
    new_bal = user_row['balance']
    current_bal = new_bal + actual_deduction

    combo, combo_ussd = [float(x) for x in draft['combo'].split(',')], draft['combo_ussd'].split(',')
    for c, u in zip(combo, combo_ussd):
        cur.execute("INSERT INTO transactions (user_id, type, network, service_type, phone, amount, ussd_amount, status, ussd_response, profit, date, balance_before, balance_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (user_id, "TRANSFER", draft['network'], "Jahez", draft['phone'], c, u, "QUEUED", "Waiting", 0, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S"), current_bal, new_bal))

    conn.commit(); conn.close()
    user_steps.pop(user_id, None)

def process_bulk_list(message):
    if check_abort(message): return
    lines = message.text.strip().split('\n')
    valid_requests = []
    total_deduction = 0
    user = get_user(message.from_user.id)

    for line in lines:
        parts = line.split()
        if len(parts) >= 2:
            phone, amt_str = parts[0], parts[1]
            try:
                amount = float(amt_str)
                prefix = phone[0:3]
                network = "Syriatel" if prefix in ['093','098','099'] or len(phone) == 8 else "MTN" if prefix in ['094','095','096'] else None
                if network and amount > 0:
                    valid_requests.append({'phone': phone, 'amount': amount, 'network': network})
                    total_deduction += amount
            except: continue

    if not valid_requests:
        msg = bot.reply_to(message, "⚠️ لم يتم التعرف على أي أرقام صحيحة. حاول مرة أخرى:")
        bot.register_next_step_handler(msg, process_bulk_list); return

    if user['balance'] < total_deduction:
        bot.reply_to(message, f"⚠️ رصيدك غير كافٍ للعملية الجماعية.\nالمطلوب تقريباً: `{total_deduction:g}` وحدة.")
        return

    confirm_txt = f"📦 *مراجعة التحويل الجماعي:*\n\n✅ عدد الأرقام: `{len(valid_requests)}`\n💰 إجمالي المبالغ: `{total_deduction:g}` وحدة\n\n⚠️ سيتم فحص كل رقم وجدولة عملياته آلياً. هل تود التأكيد؟"
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("✅ تأكيد الإرسال للكل", callback_data="bulk_confirm_yes"), types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_main"))
    user_steps[message.from_user.id] = {'bulk_list': valid_requests}
    bot.send_message(message.chat.id, confirm_txt, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "bulk_confirm_yes")
def bulk_confirm_callback(call):
    if not is_callback_allowed(call): return
    user_id = call.from_user.id
    data = user_steps.get(user_id, {}).get('bulk_list')
    if not data: return bot.answer_callback_query(call.id, "انتهت الجلسة.")

    bot.edit_message_text("⏳ جاري جدولة العمليات... يرجى الانتظار", call.message.chat.id, call.message.message_id)

    conn = get_db_connection()
    cur = conn.cursor()
    success_count = 0
    fail_count = 0

    for item in data:
        cats = cur.execute("SELECT amount, ussd_amount FROM categories WHERE network=? AND is_active=1", (item['network'],)).fetchall()
        if cats:
            denoms = [float(c['amount']) for c in cats]
            amt_map = {float(c['amount']): c['ussd_amount'] for c in cats}
            combo = find_best_denominations(item['amount'], denoms)

            if len(combo) <= 5:
                actual_sum = sum(combo)

                # 🛡️ الخصم الآمن (Atomic) لكل رقم على حدة للتأكد من عدم نزول الرصيد للسالب
                cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?", (actual_sum, user_id, actual_sum))

                if cur.rowcount > 0: # إذا كان الرصيد كافياً وتم الخصم بنجاح
                    for c in combo:
                        cur.execute("INSERT INTO transactions (user_id, type, network, service_type, phone, amount, ussd_amount, status, ussd_response, date) VALUES (?, 'TRANSFER', ?, 'Bulk', ?, ?, ?, 'QUEUED', 'Waiting', ?)",
                                     (user_id, item['network'], item['phone'], c, amt_map[c], datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
                    success_count += 1
                else:
                    fail_count += 1 # فشل لعدم كفاية الرصيد لهذا الرقم

    conn.commit(); conn.close()

    msg_text = f"✅ تم الانتهاء من القائمة!\nنجاح جدولة: `{success_count}` أرقام."
    if fail_count > 0:
        msg_text += f"\n❌ تم تخطي `{fail_count}` أرقام لعدم كفاية رصيدك."

    bot.send_message(call.message.chat.id, msg_text, parse_mode="Markdown")
    user_steps.pop(user_id, None)
    # ==========================================
# 5. نظام الفواتير وشركات الإنترنت
# ==========================================
@bot.callback_query_handler(func=lambda call: call.data.startswith('favuse_bill_start_'))
def favuse_bill_start(call):
    if not is_callback_allowed(call): return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    user_steps[call.from_user.id] = {'pre_filled_phone': call.data.split('_')[3]}
    handle_services_logic(call.message.chat.id, call.from_user.id, call.message.message_id)

def show_companies(message, cat='bill'): handle_services_logic(message.chat.id, message.from_user.id, None, cat)

def handle_services_logic(chat_id, user_id, mid=None, cat='bill'):
    user = get_user(user_id)
    title_text = "📡 *شركات الانترنت والخدمات المتاحة*" if cat == 'bill' else "🎮 *أقسام الألعاب والبرامج المتاحة*"
    base_text = f"{title_text}\n━━━━━━━━━━━━━━"
    markup_close = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إغلاق", callback_data="dash_main"))
    if not user or user['can_pay_bills'] == 0:
        text = f"{base_text}\n⚠️ *عذراً*\nهذه الخدمة غير مفعلة لحسابك حالياً."
        if mid: return bot.edit_message_text(text, chat_id, mid, reply_markup=markup_close, parse_mode="Markdown")
        else: return bot.send_message(chat_id, text, reply_markup=markup_close, parse_mode="Markdown")

    conn = get_db_connection()
    # جلب الشركات حسب النوع
    companies = conn.execute("SELECT * FROM companies WHERE is_active=1 AND category=?", (cat,)).fetchall()
    conn.close()
    if not companies: return bot.send_message(chat_id, f"{base_text}\n⚠️ *نعتذر*\nلا توجد أقسام حالياً بهذا التصنيف.", reply_markup=markup_close, parse_mode="Markdown")

    cmp_markup = types.InlineKeyboardMarkup(row_width=2)
    icon_c = "🏢" if cat == 'bill' else "🕹️"
    btn_list = [types.InlineKeyboardButton(f"{icon_c} {c['name']}", callback_data=f"cmp_{c['id']}") for c in companies]
    cmp_markup.add(*btn_list); cmp_markup.row(types.InlineKeyboardButton("🔙 العودة للرئيسية", callback_data="dash_main"))

    text = f"{base_text}\nيرجى اختيار القسم المطلوب:"
    if user_steps.get(user_id, {}).get('pre_filled_phone'): text = f"🎯 الهدف: `{user_steps[user_id]['pre_filled_phone']}`\n\n" + text
    if mid: bot.edit_message_text(text, chat_id, mid, reply_markup=cmp_markup, parse_mode="Markdown")
    else: bot.send_message(chat_id, text, reply_markup=cmp_markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('cmp_'))
def show_company_services(call):
    if not is_callback_allowed(call): return
    cmp_id = call.data.split('_')[1]
    conn = get_db_connection()
    company = conn.execute("SELECT * FROM companies WHERE id=? AND is_active=1", (cmp_id,)).fetchone()
    services = conn.execute("SELECT * FROM manual_services WHERE company_id=? AND is_active=1", (cmp_id,)).fetchall()
    conn.close()
    if not company: return bot.answer_callback_query(call.id, "الشركة متوقفة حالياً.")

    markup = types.InlineKeyboardMarkup(row_width=1)
    for s in services: markup.add(types.InlineKeyboardButton(f"📦 {s['name']} | 💰 {s['price']:,.0f} ل.س", callback_data=f"buy_srv_{s['id']}"))
    if get_setting('open_bill_active') == '1': markup.add(types.InlineKeyboardButton("🔄 دفع مبلغ حر (شحن رصيد)", callback_data=f"buy_open_{cmp_id}"))
    markup.add(types.InlineKeyboardButton("🔙 العودة للشركات", callback_data="dash_services"))
    bot.edit_message_text(f"🏢 *قسم: {company['name']}*\n\nاختر الباقة أو الخدمة المناسبة:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'dash_services')
def back_to_companies(call):
    if is_callback_allowed(call): handle_services_logic(call.message.chat.id, call.from_user.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_open_'))
def handle_buy_open(call):
    if not is_callback_allowed(call): return
    cmp_id = call.data.split('_')[2]
    conn = get_db_connection(); company = conn.execute("SELECT * FROM companies WHERE id=?", (cmp_id,)).fetchone(); conn.close()
    if not company: return bot.answer_callback_query(call.id, "الشركة غير موجودة.")
    user_id = call.from_user.id
    manual_order_steps[user_id] = {'type': 'open', 'cmp_id': cmp_id, 'company_name': company['name']}
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_services"))
    bot.edit_message_text(f"🔄 *دفع مبلغ حر / شحن رصيد*\n🏢 الشركة: {company['name']}\n\n📞 يرجى إدخال *الرقم أو رقم الحساب* للتسديد:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    manual_order_steps[user_id]['last_bot_msg_id'] = call.message.message_id
    bot.register_next_step_handler(call.message, process_open_bill_target)

def process_open_bill_target(message):
    user_id = message.from_user.id
    delete_last_bot_message(message.chat.id, user_id, manual_order_steps)
    if check_abort(message): return
    if user_id not in manual_order_steps or manual_order_steps[user_id].get('type') != 'open': return bot.reply_to(message, "⏳ انتهت الجلسة.", reply_markup=main_menu(message.from_user.id))
    if not message.text:
        msg = bot.reply_to(message, "⚠️ أدخل رقم صحيح:"); manual_order_steps[user_id]['last_bot_msg_id'] = msg.message_id; bot.register_next_step_handler(msg, process_open_bill_target); return
    manual_order_steps[user_id]['target'] = message.text.strip()
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_services"))
    msg = bot.reply_to(message, "💰 يرجى إدخال *المبلغ المراد تسديده* للشركة (أرقام فقط بالليرة السورية):", reply_markup=markup, parse_mode="Markdown")
    manual_order_steps[user_id]['last_bot_msg_id'] = msg.message_id
    bot.register_next_step_handler(msg, process_open_bill_amount)

def process_open_bill_amount(message):
    user_id = message.from_user.id
    delete_last_bot_message(message.chat.id, user_id, manual_order_steps)
    if check_abort(message): return
    if user_id not in manual_order_steps or manual_order_steps[user_id].get('type') != 'open': return bot.reply_to(message, "⏳ انتهت الجلسة.", reply_markup=main_menu(message.from_user.id))
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_services"))
    try:
        cost = float(message.text.strip())
        if cost <= 0: raise ValueError
    except:
        msg = bot.reply_to(message, "⚠️ يرجى إدخال مبلغ صحيح (أرقام فقط):", reply_markup=markup); manual_order_steps[user_id]['last_bot_msg_id'] = msg.message_id; bot.register_next_step_handler(msg, process_open_bill_amount); return

    percent = float(get_setting('open_bill_percent_fee') or 0)
    fixed = float(get_setting('open_bill_fixed_fee') or 0)
    fee = (cost * percent / 100.0) + fixed
    total = cost + fee

    user = get_user(user_id)
    if user['bills_balance'] < total:
        bot.reply_to(message, f"⚠️ رصيد الفواتير غير كافٍ.\nالمطلوب إجمالاً: `{total:,.0f}` ل.س", reply_markup=markup, parse_mode="Markdown"); manual_order_steps.pop(user_id, None); return

    manual_order_steps[user_id].update({'cost': cost, 'fee': fee, 'total': total})
    conf_msg = f"╭━━━ 🧾 *مراجعة الفاتورة (دفع حر)* ━━━╮\n🏢 الشركة: {manual_order_steps[user_id]['company_name']}\n🎯 الحساب: `{manual_order_steps[user_id]['target']}`\n💵 الدفعة للشركة: `{cost:,.0f}` ل.س\n⚙️ أجور الخدمة: `{fee:,.0f}` ل.س\n┣━━━━━━━━━━━━━━━━━━┫\n💎 الإجمالي المخصوم: `{total:,.0f}` ل.س\n╰━━━━━━━━━━━━━━━━━━╯\nهل توافق؟"
    conf_markup = types.InlineKeyboardMarkup()
    conf_markup.add(types.InlineKeyboardButton("✅ تأكيد الدفع", callback_data="obconf_yes"), types.InlineKeyboardButton("❌ إلغاء", callback_data="obconf_no"))
    msg = bot.send_message(message.chat.id, conf_msg, reply_markup=conf_markup, parse_mode="Markdown")
    manual_order_steps[user_id]['last_bot_msg_id'] = msg.message_id

@bot.callback_query_handler(func=lambda call: call.data.startswith('obconf_'))
def confirm_open_bill(call):
    if not is_callback_allowed(call): return
    user_id = call.from_user.id
    chat_id, mid = call.message.chat.id, call.message.message_id
    try: bot.edit_message_reply_markup(chat_id, mid, reply_markup=None)
    except: pass
    bot.answer_callback_query(call.id)
    action = call.data.split('_')[1]
    data = manual_order_steps.get(user_id)
    if not data or data.get('type') != 'open': return bot.edit_message_text("⏳ الجلسة منتهية.", chat_id, mid)
    if action == "no": manual_order_steps.pop(user_id, None); return bot.edit_message_text("❌ تم الإلغاء.", chat_id, mid)

    total = data['total']
    conn = get_db_connection()
    cur = conn.cursor()

    # 🛡️ خصم آمن لرصيد الفواتير (دفع حر)
    cur.execute("UPDATE users SET bills_balance = bills_balance - ? WHERE user_id = ? AND bills_balance >= ?", (total, user_id, total))
    if cur.rowcount == 0:
        conn.rollback(); conn.close()
        manual_order_steps.pop(user_id, None)
        return bot.edit_message_text("⚠️ الرصيد غير كافٍ أو توجد عملية جارية.", chat_id, mid)

    service_full_name = f"{data['company_name']} - شحن حر ({data['cost']:g} ل.س)"
    cur.execute("INSERT INTO manual_orders (user_id, service_name, target_info, price, status, profit, date) VALUES (?, ?, ?, ?, 'PENDING', ?, ?)", (user_id, service_full_name, data['target'], total, data['fee'], datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
    order_id = cur.lastrowid
    conn.commit(); conn.close()

    user = get_user(user_id)
    bot.edit_message_text(f"╭━━━ ✅ تم الاستلام ━━━╮\n🎯 الرقم: `{data['target']}`\n💎 الخصم الإجمالي: `{total:,.0f}` ل.س\n╰━━━━━━━━━━━━━╯\nقيد المعالجة ⏳", chat_id, mid, parse_mode="Markdown")
    manual_order_steps.pop(user_id, None)
    try: bot.send_message(ADMIN_TG_ID, f"🔔 *طلب دفع حر جديد! (رقم #{order_id})*\n👤 العميل: {user['real_name']}\n🏢 الشركة: {data['company_name']}\n🎯 الحساب: `{data['target']}`\n💵 الدفعة الصافية للشركة: {data['cost']:g} ل.س\n💰 المخصوم من العميل: {total:g} ل.س\nيرجى الدخول للوحة التحكم للتنفيذ.", parse_mode="Markdown")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_srv_'))
def handle_buy_service(call):
    if not is_callback_allowed(call): return
    user_id = call.from_user.id
    conn = get_db_connection()
    # جلب نوع الشركة لمعرفة هل هي فاتورة أم لعبة
    service_row = conn.execute("SELECT s.*, c.name as company_name, c.category as comp_category FROM manual_services s LEFT JOIN companies c ON s.company_id = c.id WHERE s.id=?", (call.data.split('_')[2],)).fetchone()
    conn.close()

    if not service_row: return bot.answer_callback_query(call.id, "الخدمة غير متاحة.")
    service = dict(service_row)
    manual_order_steps[user_id] = {'type': 'fixed', 'service_id': service['id'], 'name': f"{service['company_name']} - {service['name']}", 'price': service['price'], 'cost': service['cost']}

    if user_steps.get(user_id, {}).get('pre_filled_phone'):
        phone = user_steps[user_id].pop('pre_filled_phone')
        return execute_bill_phone(user_id, call.message.chat.id, phone, call.message.message_id)

    conn = get_db_connection(); favs = conn.execute("SELECT * FROM user_favorites WHERE user_id=? AND phone LIKE '011%'", (user_id,)).fetchall(); conn.close()
    markup = types.InlineKeyboardMarkup(row_width=1)

    # لا داعي لإظهار الأرقام الأرضية المفضلة إذا كانت الخدمة (لعبة)
    if service.get('comp_category') != 'game':
        for f in favs: markup.add(types.InlineKeyboardButton(f"⭐ {f['name']} - {f['phone']}", callback_data=f"favuse_bill_direct_{f['phone']}"))

    markup.add(types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_main")) # تعديل للعودة للرئيسية

    # تغيير النص بذكاء بناءً على نوع الخدمة!
    if service.get('comp_category') == 'game':
        prompt_text = "🎮 يرجى إدخال *الآيدي (ID)* الخاص باللاعب للشحن:"
    else:
        prompt_text = "📞 يرجى إدخال *الرقم الأرضي أو رقم الحساب* للتسديد:"

    text = f"📦 الباقة: *{manual_order_steps[user_id]['name']}*\n💰 السعر: `{service['price']:,.0f}` ل.س\n\n⏱️ مدة التنفيذ المتوقعة: {service.get('execution_time', 'غير محدد')}\n🕒 أوقات الدوام: {service.get('working_hours', 'غير محدد')}\n\n{prompt_text}"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    manual_order_steps[user_id]['last_bot_msg_id'] = call.message.message_id
    bot.register_next_step_handler(call.message, process_manual_order_target)

@bot.callback_query_handler(func=lambda call: call.data.startswith('favuse_bill_direct_'))
def favuse_bill_direct(call):
    if not is_callback_allowed(call): return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    if call.from_user.id not in manual_order_steps: return bot.send_message(call.message.chat.id, "⏳ انتهت الجلسة.")
    execute_bill_phone(call.from_user.id, call.message.chat.id, call.data.split('_')[3], None)

def process_manual_order_target(message):
    delete_last_bot_message(message.chat.id, message.from_user.id, manual_order_steps)
    if check_abort(message): return
    if message.from_user.id not in manual_order_steps: return bot.reply_to(message, "⏳ انتهت الجلسة.", reply_markup=main_menu(message.from_user.id))
    if not message.text:
        msg = bot.reply_to(message, "⚠️ أدخل رقم صحيح:"); manual_order_steps[message.from_user.id]['last_bot_msg_id'] = msg.message_id; bot.register_next_step_handler(msg, process_manual_order_target); return
    execute_bill_phone(message.from_user.id, message.chat.id, message.text.strip(), None)

def execute_bill_phone(user_id, chat_id, target_info, message_id=None):
    data = manual_order_steps.get(user_id)
    if not data: return
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء الطلب", callback_data="dash_services"))

    conn = get_db_connection()
    cur = conn.cursor()

    # 🛡️ خصم آمن لرصيد الفواتير (باقة ثابتة)
    cur.execute("UPDATE users SET bills_balance = bills_balance - ? WHERE user_id = ? AND bills_balance >= ?", (data['price'], user_id, data['price']))
    if cur.rowcount == 0:
        conn.rollback(); conn.close()
        text = f"⚠️ رصيد الفواتير غير كافٍ أو توجد عملية جارية.\nالمطلوب: `{data['price']:,.0f}` ل.س"
        bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown") if message_id else bot.send_message(chat_id, text, parse_mode="Markdown")
        manual_order_steps.pop(user_id, None); return

    profit = data['price'] - data.get('cost', 0)
    cur.execute("INSERT INTO manual_orders (user_id, service_name, target_info, price, status, profit, date) VALUES (?, ?, ?, ?, 'PENDING', ?, ?)",
                 (user_id, data['name'], target_info, data['price'], profit, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
    order_id = cur.lastrowid
    conn.commit(); conn.close()

    user = get_user(user_id)
    text = f"╭━━━ ✅ تم الاستلام ━━━╮\n🎯 الرقم: `{target_info}`\n💎 الخصم: `{data['price']:,.0f}` ل.س\n╰━━━━━━━━━━━━━╯\nقيد المعالجة ⏳"
    bot.edit_message_text(text, chat_id, message_id, reply_markup=None, parse_mode="Markdown") if message_id else bot.send_message(chat_id, text, parse_mode="Markdown")
    manual_order_steps.pop(user_id, None)
    try: bot.send_message(ADMIN_TG_ID, f"🔔 *طلب فاتورة (باقة ثابتة) رقم #{order_id}*\n👤 العميل: {user['real_name']}\n📦 الخدمة: {data['name']}\n🎯 الرقم: `{target_info}`\n💰 المبلغ المخصوم: {data['price']:g} ل.س\nيرجى الدخول للوحة التحكم للتنفيذ.", parse_mode="Markdown")
    except: pass

@bot.message_handler(func=lambda message: message.text == "『 🚀 تـحـويـل رصـيـد 』")
def menu_transfer(message):
    if is_bot_active_and_user_allowed(message): start_transfer_flow(message)


@bot.message_handler(func=lambda message: message.text == "💸 كـاش وفـواتيـر إتـصـالات")
def menu_cash_bills(message):
    if not is_bot_active_and_user_allowed(message): return

    # 💡 حماية إضافية: منع الوصول إذا كانت الخدمة متوقفة
    if get_setting('status_cash_bills') == '0':
        bot.reply_to(message, "⚠️ هذه الخدمة قيد التجهيز حالياً، يرجى المحاولة لاحقاً.")
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("💸 تحويل كاش سيريتل", callback_data="cb_cash_syriatel"),
        types.InlineKeyboardButton("💸 تحويل كاش MTN", callback_data="cb_cash_mtn"),
        types.InlineKeyboardButton("🧾 دفع فواتير سيريتل", callback_data="cb_bill_syriatel"),
        types.InlineKeyboardButton("🧾 دفع فواتير MTN", callback_data="cb_bill_mtn"),
        types.InlineKeyboardButton("❌ إغلاق", callback_data="dash_main")
    )
    bot.send_message(message.chat.id, "⚡ *خدمات الكاش والفواتير*\n(سيتم الخصم حصراً من رصيد الوحدات 📱):\n\n👇 يرجى اختيار الخدمة:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('cb_'))
def cb_handler(call):
    if not is_callback_allowed(call): return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    service_type = call.data.replace('cb_', '')
    user_steps[call.from_user.id] = {'cb_service': service_type}

    names = {
        'cash_syriatel': 'كاش سيريتل 🔴', 'cash_mtn': 'كاش MTN 🟡',
        'bill_syriatel': 'فواتير سيريتل 🔴', 'bill_mtn': 'فواتير MTN 🟡'
    }

    msg = bot.edit_message_text(f"الخدمة المختارة: *{names[service_type]}*\n\n📞 يرجى إدخال الرقم المطلوب:", call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_main")), parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_cb_phone)

def process_cb_phone(message):
    if check_abort(message): return
    if not message.text or not message.text.isdigit():
        msg = bot.reply_to(message, "⚠️ يرجى إدخال أرقام فقط:")
        bot.register_next_step_handler(msg, process_cb_phone)
        return
    user_steps[message.from_user.id]['phone'] = message.text
    msg = bot.reply_to(message, "💰 ممتاز، الآن أدخل المبلغ المطلوب (أرقام فقط):", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_main")))
    bot.register_next_step_handler(msg, process_cb_amount)


def process_cb_amount(message):
    if check_abort(message): return
    uid = message.from_user.id
    try:
        amt = float(message.text)
        if amt <= 0: raise ValueError
    except:
        msg = bot.reply_to(message, "⚠️ يرجى إدخال مبلغ صحيح (أرقام فقط):")
        bot.register_next_step_handler(msg, process_cb_amount)
        return

    data = user_steps.get(uid)
    if not data: return bot.reply_to(message, "⏳ انتهت الجلسة.", reply_markup=main_menu(message.from_user.id))

    try:
        conn = get_db_connection()
        network = 'Syriatel' if 'syriatel' in data['cb_service'] else 'MTN'

        # 💡 حماية الاستعلام: في حال لم يتم تحديث قاعدة البيانات، يتم اعتبار العمولة 0
        try:
            code_info = conn.execute("SELECT custom_percent_fee, custom_fixed_fee FROM ussd_codes WHERE service_name=?", (data['cb_service'],)).fetchone()
            perc = float(code_info['custom_percent_fee'] or 0) if code_info else 0
            fixed = float(code_info['custom_fixed_fee'] or 0) if code_info else 0
        except Exception:
            perc = 0
            fixed = 0

        # 🧮 حساب العمولات
        fee = (amt * perc / 100.0) + fixed
        total_deduction = amt + fee

        user = dict(get_user(uid))
        if float(user.get('balance', 0) or 0) < total_deduction:
            bot.reply_to(message, f"⚠️ رصيد الوحدات الخاص بك غير كافٍ.\nالمطلوب إجمالاً: `{total_deduction:g}` وحدة.", parse_mode="Markdown")
            user_steps.pop(uid, None)
            conn.close()
            return

        # --- خوارزمية تقسيم الكاش والفواتير (على 1000) ---
        chunks = []
        remaining = int(amt)
        while remaining > 1000:
            chunks.append(1000)
            remaining -= 1000
        if remaining > 0:
            chunks.append(remaining)
        combo_str = " + ".join(map(str, chunks))
        # ------------------------------------------------

        cur = conn.cursor()
        cur.execute("INSERT INTO drafts (user_id, phone, network, target_amount, actual_deduction, combo, combo_ussd, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (uid, data['phone'], network, amt, total_deduction, str(amt), data['cb_service'], datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
        draft_id = cur.lastrowid
        conn.commit(); conn.close()

        names = {'cash_syriatel': 'كاش سيريتل 🔴', 'cash_mtn': 'كاش MTN 🟡', 'bill_syriatel': 'فواتير سيريتل 🔴', 'bill_mtn': 'فواتير MTN 🟡'}
        conf_msg = f"╭━━━ 🧾 *مراجعة الطلب* ━━━╮\nالخدمة: *{names[data['cb_service']]}*\nالرقم: `{data['phone']}`\nالمبلغ الإجمالي: `{amt:g}`\n📦 دفعات الإرسال: {combo_str}\n⚙️ أجور الخدمة: `{fee:g}`\n┣━━━━━━━━━━━━━━━━━━┫\n💎 الإجمالي المخصوم (من الوحدات): `{total_deduction:g}`\n╰━━━━━━━━━━━━━━━━━━╯\nهل توافق؟"

        markup = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("✅ تأكيد", callback_data=f"cbconf_yes_{draft_id}"),
            types.InlineKeyboardButton("❌ إلغاء", callback_data=f"cbconf_no_{draft_id}")
        )
        bot.send_message(message.chat.id, conf_msg, reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ حدث خطأ داخلي يمنع إكمال العملية:\n`{str(e)}`", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('cbconf_'))
def cb_confirm(call):
    if not is_callback_allowed(call): return
    action, draft_id = call.data.split('_')[1], call.data.split('_')[2]
    uid = call.from_user.id

    try:
        conn = get_db_connection()
        draft = conn.execute("SELECT * FROM drafts WHERE id=? AND user_id=?", (draft_id, uid)).fetchone()
        if not draft:
            conn.close()
            return bot.edit_message_text("⏳ الجلسة منتهية أو تم التنفيذ مسبقاً.", call.message.chat.id, call.message.message_id)

        cur = conn.cursor()
        cur.execute("DELETE FROM drafts WHERE id=? AND user_id=?", (draft_id, uid))
        if action == "no":
            conn.commit(); conn.close()
            return bot.edit_message_text("❌ تم الإلغاء.", call.message.chat.id, call.message.message_id)

        target_amt = float(draft['target_amount'])
        actual_deduction = float(draft['actual_deduction'])
        profit = actual_deduction - target_amt

        # --- جدار حماية الخزينة المركزية للكاش والفواتير ---
        service_name = draft['combo_ussd']
        network_name = draft['network']
        if 'cash' in service_name or 'bill' in service_name or 'كاش' in service_name or 'فاتورة' in service_name:
            if not check_central_cash(network_name, service_name, target_amt):
                conn.rollback(); conn.close()
                bot.answer_callback_query(call.id, "❌ الخدمة غير متاحة حالياً (نقص بالخزينة المركزية).", show_alert=True)
                return bot.edit_message_text("❌ عذراً، الخدمة غير متاحة حالياً (لا توجد سيولة كافية في الخزينة المركزية الخاصة بالإدارة).", call.message.chat.id, call.message.message_id)
        # ---------------------------------------------------

        # 🛡️ الخصم الذكي من رصيد الوحدات باستخدام الإجمالي (المبلغ + الأجور) 🛡️
        cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?", (actual_deduction, uid, actual_deduction))
        if cur.rowcount == 0:
            conn.rollback(); conn.close()
            return bot.edit_message_text("⚠️ الرصيد (الوحدات) غير كافٍ أو توجد عملية جارية.", call.message.chat.id, call.message.message_id)

        bot.edit_message_text("⏳ *🚀 ثواني والطلب بيجهز...*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

        try:
            code_row = conn.execute("SELECT ussd_format, secret_pin FROM ussd_codes WHERE service_name=?", (service_name,)).fetchone()
            ussd_code = code_row['ussd_format'] if code_row else ""
            pin = code_row['secret_pin'] if code_row else ""
        except Exception:
            ussd_code = ""
            pin = ""

        user_row = conn.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
        new_bal = float(user_row['balance'] or 0)
        current_bal = new_bal + actual_deduction

        # --- خوارزمية التقطيع المخصصة للكاش والفواتير (1000) ---
        chunks = []
        remaining = int(target_amt)
        while remaining > 1000:
            chunks.append(1000)
            remaining -= 1000
        if remaining > 0:
            chunks.append(remaining)

        for i, chunk in enumerate(chunks):
            if ussd_code:
                ussd_val = ussd_code.replace("{phone}", str(draft['phone'])).replace("{amount}", str(chunk)).replace("{pin}", str(pin))
            else:
                ussd_val = "CODE_ERROR"

            # نضع الربح في أول دفعة فقط حتى لا يتكرر بالسجلات المحاسبية
            chunk_profit = profit if i == 0 else 0

            cur.execute("INSERT INTO transactions (user_id, type, network, service_type, phone, amount, ussd_amount, status, ussd_response, profit, date, balance_before, balance_after) VALUES (?, ?, ?, ?, ?, ?, ?, 'QUEUED', 'Waiting', ?, ?, ?, ?)",
                        (uid, "TRANSFER", draft['network'], service_name, draft['phone'], chunk, ussd_val, chunk_profit, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S"), current_bal, new_bal))
        # --------------------------------------------------------

        conn.commit(); conn.close()
        user_steps.pop(uid, None)
    except Exception as e:
        bot.edit_message_text(f"❌ حدث خطأ داخلي:\n`{str(e)}`", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "📦 الـتحويـل الـجماعـي")
def start_bulk_transfer(message):
    if not is_bot_active_and_user_allowed(message): return
    msg = bot.send_message(message.chat.id, "📦 *نظام التحويل الجماعي الذكي* 🚀\nيمكنك إرسال عدة أرقام ومبالغ ليتم تحويلها آلياً دفعة واحدة.\n\n👇 يرجى إرسال القائمة بالتنسيق التالي:\nرقم الهاتف [فراغ] المبلغ\nرقم الهاتف [فراغ] المبلغ\n\n*مثال:*\n`0933111222 500`\n`0944333444 1000`\n`0999555666 1500`", parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ إلغاء", callback_data="dash_main")))
    bot.register_next_step_handler(msg, process_bulk_list)

@bot.message_handler(func=lambda message: message.text == "【 🧾 دفـع الـفـواتـيـر 】")
def menu_services_handler(message):
    if is_bot_active_and_user_allowed(message): show_companies(message)
@bot.message_handler(func=lambda message: message.text == "🎮 شحـن ألـعـاب وبـرامـج")
def menu_games_handler(message):
    if is_bot_active_and_user_allowed(message): show_companies(message, cat='game')
@bot.message_handler(func=lambda message: "حـسـابـي والـديـون" in message.text)
def menu_balance(message):
    if not is_bot_active_and_user_allowed(message): return

    # جلب البيانات
    u_row = get_user(message.from_user.id)
    if not u_row:
        bot.reply_to(message, "⚠️ لم يتم العثور على بياناتك، يرجى الضغط على /start")
        return

    # تحويل الصف إلى قاموس (هذا يحل مشكلة الـ get)
    user = dict(u_row)

    try:
        debt = float(user.get('debt_balance', 0) or 0)
        points = user.get('loyalty_points', 0)

        debt_text = f"🔴 *الديون المستحقة:* `{debt:,.0f}` ل.س" if debt > 0 else "🟢 *الديون المستحقة:* `0` ل.س"

        text = (
            f"╭━━━ 💳 *كشف حسابك* ━━━╮\n"
            f"👤 *الاسم:* {user.get('real_name', 'غير معروف')}\n"
            f"🆔 *رقم الحساب:* `{user.get('user_id')}`\n"
            f"┣━━━━━━━━━━━━━━━━━━┫\n\n"
            f"📱 *رصيد الوحدات:* `{float(user.get('balance', 0) or 0):,.0f}` وحدة\n"
            f"🧾 *رصيد الفواتير:* `{float(user.get('bills_balance', 0) or 0):,.0f}` ل.س\n\n"
            f"{debt_text}\n\n"
            f"🎁 *نقاط الولاء:* `{points}` نقطة\n"
            f"╰━━━━━━━━━━━━━━━━━━╯"
        )

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🌐 إعداد كلمة مرور للويب", callback_data="set_web_pass"))
        bot.reply_to(message, text, parse_mode="Markdown", reply_markup=markup)

    except Exception as e:
        bot.reply_to(message, f"حدث خطأ في عرض الحساب: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == 'set_web_pass')
def set_web_pass_callback(call):
    if not is_callback_allowed(call): return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    msg = bot.edit_message_text("🔐 *إعداد كلمة مرور الويب*\n\nأرسل الآن كلمة المرور التي تريدها (أرقام أو حروف إنجليزية فقط، بدون مسافات):", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_web_password)

def process_web_password(message):
    if check_abort(message): return
    password = message.text.strip()
    if " " in password:
        msg = bot.reply_to(message, "⚠️ كلمة المرور يجب ألا تحتوي على مسافات. حاول مرة أخرى:")
        bot.register_next_step_handler(msg, process_web_password)
        return

    user_id = message.from_user.id
    conn = get_db_connection()
    user = conn.execute("SELECT username FROM users WHERE user_id=?", (user_id,)).fetchone()

    # إذا الزبون ما عنده يوزرنيم بالتلغرام، منخلي الآيدي تبعه هو اليوزرنيم
    login_username = user['username'] if user['username'] else str(user_id)

    conn.execute("UPDATE users SET username=?, password=? WHERE user_id=?", (login_username, password, user_id))
    conn.commit()
    conn.close()

    bot.reply_to(message, f"✅ *تم تعيين كلمة المرور بنجاح!*\n\n🌐 *رابط الموقع:* `https://refaie.pythonanywhere.com`\n👤 *اسم المستخدم:* `{login_username}`\n🔑 *كلمة المرور:* `{password}`\n\nاحتفظ بهذه البيانات لتدخل إلى حسابك عبر متصفح الويب.", parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "📊 سـجـل الـعـمـلـيـات")
def menu_history(message):
    if not is_bot_active_and_user_allowed(message): return
    logs, total_pages, user = get_statement_page(message.chat.id, 1)

    if not logs:
        bot.send_message(message.chat.id, "❌ لا توجد حركات مسجلة في حسابك حتى الآن.")
        return

    msg_text = f"📊 *كشف حسابك - الصفحة (1/{total_pages})*\n━━━━━━━━━━━━━━\n"
    for l in logs:
        date_short = l['date'][:16] if l['date'] else ""
        if l['src_type'] == 'DEPOSIT':
            w_name = "وحدات 📱" if l['detail'] == 'units' else "فواتير 🧾"
            if l['detail'] == 'debt_payment':
                msg_text += f"🔹 *النوع:* تسديد ديون 💰\n💵 *المبلغ:* `{l['extra']:g}` ل.س\n"
            elif l['detail'] == 'free_debt':
                msg_text += f"🔹 *النوع:* دين جديد 📝\n💵 *المبلغ:* `{l['extra']:g}` ل.س\n"
            else:
                msg_text += f"🔹 *النوع:* إيداع {w_name} 📥\n💵 *المبلغ:* `{l['amount']:g}`\n"
        elif l['src_type'] == 'TRANSFER':
            stat = '✅' if l['status']=='SUCCESS' else '⏳' if l['status']=='QUEUED' else '⚙️' if l['status']=='PROCESSING' else '🔎' if l['status']=='MANUAL_CHECK' else '❌'
            bb = l['balance_before'] or 0
            ba = l['balance_after'] or 0
            msg_text += f"🔹 *النوع:* تحويل {l['extra']} 🚀\n📱 *للرقم:* `{l['detail']}`\n💵 *المبلغ:* `{l['amount']:g}` وحدة {stat}\n📉 *كان رصيدك:* `{bb:g}` ➔ *صار:* `{ba:g}`\n"
        elif l['src_type'] == 'BILL':
            stat = '✅' if l['status']=='COMPLETED' else '⏳' if l['status']=='PENDING' else '❌'
            msg_text += f"🔹 *النوع:* فاتورة ({l['detail']}) 🧾\n🎯 *الرقم:* `{l['extra']}`\n💵 *المبلغ:* `{l['amount']:g}` ل.س {stat}\n"
        msg_text += f"📅 *الوقت:* {date_short}\n┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"

    bal = user['balance'] or 0
    b_bal = user['bills_balance'] or 0
    msg_text += f"💰 *رصيدك الحالي:* `{bal:g}` وحدة | `{b_bal:g}` فواتير"

    markup = types.InlineKeyboardMarkup(row_width=2)
    if total_pages > 1: markup.add(types.InlineKeyboardButton("التالي ➡️", callback_data="stmt_page_2"))
    markup.add(types.InlineKeyboardButton("📄 تحميل كشف الحساب (PDF)", callback_data="stmt_pdf"))

    bot.send_message(message.chat.id, msg_text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "⭐ الأرقــام الـمـفـضـلـة")
def menu_favorites(message):
    if is_bot_active_and_user_allowed(message): handle_favorites_logic(message.chat.id, message.from_user.id, None)

@bot.message_handler(func=lambda message: message.text == "📱 تـطـبـيـق الـمـوبـايـل")
def menu_download_app(message):
    if not is_bot_active_and_user_allowed(message): return
    if get_setting('status_app_button') == '0': return

    text = "🌟 *تطبيق (ون تاتش) صار جاهز للتحميل!* 🌟\n\nحمل التطبيق الآن (بصيغة APK) لتستمتع بسرعة خيالية في تحويل الرصيد وتسديد الفواتير، مع ميزة حفظ الأرقام المفضلة.\n\n👇 *اضغط على الزر أدناه لبدء التحميل المباشر:*"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📥 تحميل التطبيق الآن (APK)", url="https://refaie.pythonanywhere.com/static/OneTouch.apk"))
    bot.reply_to(message, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "📞 الـدعـم الـفـنـي")
def menu_support(message):
    if is_bot_active_and_user_allowed(message): bot.reply_to(message, "🛎️ فريق الدعم الفني جاهز لخدمتك دائماً.\nاضغط أدناه للتواصل:", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("💬 تواصل عبر واتساب", url=f"https://wa.me/{get_setting('whatsapp')}")))

@bot.message_handler(func=lambda message: True)
def auto_reply_faq(message):
    if not is_bot_active_and_user_allowed(message): return
    text = message.text.lower()

    # 1. الاستجابة للكلمات المفتاحية
    if any(word in text for word in ["تأخر", "طول", "لسه", "ما وصل"]):
        bot.reply_to(message, "⏳ رصيدك بأمان تام والعملية قيد المتابعة.")
    elif any(word in text for word in ["مشكلة", "خطأ", "غلط", "دعم", "مساعدة"]):
        bot.reply_to(message, f"🛎️ راسل الدعم الفني: {get_setting('whatsapp')}")
    elif any(word in text for word in ["كيف", "طريقة", "شلون"]):
        bot.reply_to(message, "⚡ اضغط على الخدمة المطلوبة من القائمة أدناه واتبع التعليمات 👇")
    else:
        # 2. التحسين: الرد على أي نص غير مفهوم أو بعد انتهاء الجلسة بدل تجاهل الزبون
        bot.reply_to(message, "عذراً، لم أفهم طلبك أو أن الجلسة السابقة انتهت ⏱️\nيرجى اختيار الخدمة المطلوبة من القائمة أدناه لفتح جلسة جديدة 👇", reply_markup=main_menu(message.from_user.id))

# ==========================================
# 6. قسم الموقع (CRM) - تصميم الـ HTML
# ==========================================
HTML_BASE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{{ app_name }} | Command Center</title>
    <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <style>
        :root { --primary: #0f172a; --secondary: #334155; --accent: #3b82f6; --bg: #f1f5f9; --card-bg: #ffffff; }
        body { font-family: 'Tajawal', Tahoma, sans-serif; background: var(--bg); display: flex; margin: 0; color: #334155; overflow-x: hidden; }
        .fade-in { animation: fadeIn 0.6s cubic-bezier(0.16, 1, 0.3, 1); }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(15px); } to { opacity: 1; transform: translateY(0); } }
        .sidebar { width: 270px; background: var(--primary); color: white; top: 0; bottom: 0; padding: 25px 20px 80px 20px; position: fixed; right: 0; overflow-y: auto; z-index: 1000; box-shadow: -4px 0 20px rgba(0,0,0,0.1); transition: transform 0.3s ease; }
        .sidebar::-webkit-scrollbar { width: 4px; }
        .sidebar::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 10px; }
        .sidebar a { color: #94a3b8; display: flex; align-items: center; padding: 12px 15px; margin-bottom: 6px; text-decoration: none; border-radius: 10px; transition: 0.3s; font-weight: 500; font-size: 0.95rem; }
        .sidebar a i { width: 28px; text-align: center; font-size: 1.1rem; color: #64748b;}
        .sidebar a:hover { background: rgba(255,255,255,0.05); color: white; transform: translateX(-5px); }
        .sidebar a:hover i { color: var(--accent); }
        .sidebar a.active { background: var(--accent); color: white; box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3); font-weight: 700; }
        .sidebar a.active i { color: white; }
        .section-title { color: #475569; font-size: 0.75rem; font-weight: 800; text-transform: uppercase; letter-spacing: 1px; margin: 25px 0 10px 10px; }
        .main-content { margin-right: 270px; padding: 30px 40px; flex: 1; min-height: 100vh; transition: margin 0.3s ease; width: 100%; overflow-x: hidden; }
        .card-bank { background: var(--card-bg); border-radius: 16px; padding: 20px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05), 0 2px 4px -1px rgba(0,0,0,0.03); margin-bottom: 24px; border: 1px solid #e2e8f0; width: 100%; overflow-x: auto;}
        .kpi-card { display: flex; align-items: center; padding: 20px; border-radius: 16px; background: white; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); transition: 0.2s; }
        .kpi-card:hover { transform: translateY(-3px); box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); }
        .kpi-icon { width: 50px; height: 50px; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 1.5rem; margin-left: 15px; }
        .kpi-title { font-size: 0.85rem; color: #64748b; font-weight: 700; margin-bottom: 5px; }
        .kpi-value { font-size: 1.6rem; font-weight: 800; color: #0f172a; margin: 0; line-height: 1; }
        .live-feed-row { border-bottom: 1px solid #f1f5f9; transition: 0.2s; }
        .live-feed-row:hover { background-color: #f8fafc; }
        .live-feed-icon { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.1rem; }
        .radar-box { background: #1e293b; color: white; border-radius: 16px; padding: 20px; position: relative; overflow: hidden; }
        .radar-box::before { content: ''; position: absolute; top: -50%; left: -50%; width: 200%; height: 200%; background: radial-gradient(circle, rgba(59,130,246,0.1) 0%, transparent 60%); z-index: 0; pointer-events: none; }
        .pulse-dot-red { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background-color: #ef4444; margin-left: 8px; animation: pulseRed 1.5s infinite; }
        @keyframes pulseRed { 0% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); } 70% { box-shadow: 0 0 0 8px rgba(239, 68, 68, 0); } 100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); } }
        .table { margin-bottom: 0; white-space: nowrap; }
        .table th { border-bottom: 2px solid #e2e8f0; color: #64748b; font-weight: 700; font-size: 0.85rem; text-transform: uppercase; }
        .table td { vertical-align: middle; border-bottom: 1px solid #f1f5f9; padding: 15px 10px; font-weight: 500; }
        .login-page { background: var(--primary); height: 100vh; width: 100%; display: flex; align-items: center; justify-content: center; position: fixed; z-index: 999; }
        .login-card { background: white; padding: 40px; border-radius: 20px; box-shadow: 0 20px 50px rgba(0,0,0,0.5); width: 400px; max-width: 90%; }
        .user-link { text-decoration: none; color: var(--accent); transition: 0.2s; }
        .user-link:hover { text-decoration: underline; color: #1e40af; }
        .sidebar-toggle { display: none; position: fixed; top: 15px; right: 15px; z-index: 1001; background: var(--primary); color: white; border: none; padding: 8px 15px; border-radius: 8px; cursor: pointer; box-shadow: 0 4px 6px rgba(0,0,0,0.1); font-family: 'Tajawal'; font-weight: bold;}
        .sidebar-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 999; }
        .sidebar-overlay.active { display: block; }
        @media (max-width: 768px) {
            .sidebar { transform: translateX(100%); }
            .sidebar.active { transform: translateX(0); }
            .main-content { margin-right: 0; padding: 70px 10px 20px 10px; width: 100vw; }
            .sidebar-toggle { display: block; }
            .card-bank { padding: 15px; }
            .kpi-card { padding: 15px; }
            .kpi-value { font-size: 1.3rem; }
            .table-responsive { overflow-x: auto; -webkit-overflow-scrolling: touch; }
        }
        /* ستايل الكروت الفخمة للمستخدمين */
        .user-card-premium { background: var(--card-bg); border: 1px solid #e2e8f0; border-radius: 20px; padding: 20px; transition: all 0.3s ease; position: relative; overflow: hidden; box-shadow: 0 10px 30px rgba(0,0,0,0.04); background-color: white; }
        .user-card-premium:hover { transform: translateY(-5px); border-color: var(--accent); box-shadow: 0 15px 35px rgba(59, 130, 246, 0.15); }
        .status-badge { font-size: 10px; padding: 5px 12px; border-radius: 50px; font-weight: 800; text-transform: uppercase; }
        .recharge-modal-content { border-radius: 30px !important; border: none !important; }
        .quick-action-btn { width: 40px; height: 40px; border-radius: 12px; display: flex; align-items: center; justify-content: center; transition: 0.2s; font-size: 1.1rem; text-decoration: none; }
        .quick-action-btn:hover { transform: scale(1.1); filter: brightness(0.9); }
    </style>
</head>
<body>
{% if page == 'login' %}
<style>
    .glass-body { background: #0f172a; overflow: hidden; position: relative; height: 100vh; display: flex; align-items: center; justify-content: center; font-family: 'Tajawal', sans-serif; margin: 0; padding: 15px; }
    .glass-body::before { content: ''; position: absolute; width: 400px; height: 400px; background: #3b82f6; border-radius: 50%; top: -15%; left: -10%; filter: blur(100px); opacity: 0.4; animation: float 7s infinite alternate; }
    .glass-body::after { content: ''; position: absolute; width: 400px; height: 400px; background: #8b5cf6; border-radius: 50%; bottom: -15%; right: -10%; filter: blur(100px); opacity: 0.4; animation: float 9s infinite alternate-reverse; }
    @keyframes float { 0% { transform: translate(0, 0); } 100% { transform: translate(40px, 40px); } }
    .glass-card-login { background: rgba(30, 41, 59, 0.4); backdrop-filter: blur(25px); -webkit-backdrop-filter: blur(25px); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 30px; padding: 45px 30px; width: 100%; max-width: 420px; box-shadow: 0 30px 60px rgba(0, 0, 0, 0.4); position: relative; z-index: 10; text-align: center; }
    .glass-input { background: rgba(15, 23, 42, 0.5) !important; color: white !important; border: 1px solid rgba(255,255,255,0.05) !important; border-radius: 16px !important; padding: 18px !important; transition: 0.3s; font-size: 1.1rem; }
    .glass-input:focus { border-color: #3b82f6 !important; box-shadow: 0 0 20px rgba(59,130,246,0.3) !important; outline: none; }
    .glass-input::placeholder { color: #64748b !important; }
    .glass-btn { background: linear-gradient(135deg, #3b82f6, #2563eb); color: white; border: none; border-radius: 16px; padding: 18px; font-weight: 900; font-size: 1.2rem; transition: 0.3s; width: 100%; box-shadow: 0 10px 25px rgba(59, 130, 246, 0.4); }
    .glass-btn:hover { transform: translateY(-3px); box-shadow: 0 15px 35px rgba(59, 130, 246, 0.6); }
</style>
<div class="glass-body">
    <div class="glass-card-login fade-in">
        <div style="width: 85px; height: 85px; background: linear-gradient(135deg, #3b82f6, #8b5cf6); border-radius: 24px; display: flex; align-items: center; justify-content: center; font-size: 36px; color: #fff; margin: 0 auto 25px auto; box-shadow: 0 15px 30px rgba(59, 130, 246, 0.4); border: 1px solid rgba(255,255,255,0.2);">
            <i class="fas fa-fingerprint"></i>
        </div>
        <h3 class="fw-black mb-2 text-white">{{ app_name }}</h3>
        <p class="mb-4 fw-bold" style="color: #94a3b8;">بوابة الإدارة المركزية والموظفين</p>
        <form method="POST">
            <input type="text" name="username" class="form-control glass-input mb-3 text-center fw-bold" placeholder="اسم المستخدم" required autofocus>
            <input type="password" name="password" class="form-control glass-input mb-4 text-center fw-bold" placeholder="كلمة المرور" required>
            <button type="submit" class="glass-btn">دخول آمن <i class="fas fa-lock ms-2"></i></button>
        </form>
    </div>
</div>
{% else %}
<button class="sidebar-toggle" onclick="toggleSidebar()"><i class="fas fa-bars me-1"></i> القائمة</button>
<div class="sidebar-overlay" id="sidebarOverlay" onclick="toggleSidebar()"></div>

<div class="sidebar fade-in" id="sidebar">
    <div class="text-center mb-5 mt-2"><div class="d-inline-block bg-primary bg-opacity-25 p-3 rounded-circle mb-2"><i class="fas fa-bolt text-info fs-3"></i></div><h3 class="text-white fw-black mb-0" style="letter-spacing: 1px;">{{ app_name }}</h3><span class="badge bg-secondary rounded-pill mt-2 px-3 py-1 fw-normal" style="font-size: 0.75rem;">المستخدم: {{ current_user }}</span></div>

    {% if role != 'employee' %}
    <div class="section-title">غرفة التحكم</div>
    <a href="/admin_center" class="{{ 'active' if page == 'dashboard' else '' }}"><i class="fas fa-satellite-dish ms-2"></i> الرادار والبث الحي</a>
    <div class="section-title">إدارة الحسابات</div>
    <a href="/users" class="{{ 'active' if page == 'users' else '' }}"><i class="fas fa-users ms-2"></i> العملاء والأرصدة</a>
    <a href="/agents" class="{{ 'active' if page == 'agents' else '' }}"><i class="fas fa-user-tie ms-2"></i> الوكلاء المعتمدين</a>

    <a href="/fund_employee" class="{{ 'active' if page == 'fund_employee' else '' }} text-success fw-bold bg-success bg-opacity-10"><i class="fas fa-wallet ms-2"></i> شحن عهدة الموظف</a>

    {% if role == 'admin' %}<a href="/broadcast" class="{{ 'active' if page == 'broadcast' else '' }}"><i class="fas fa-bullhorn ms-2"></i> الإشعارات الجماعية</a>{% endif %}
    <div class="section-title">نظام الفواتير</div>
    <a href="/manual_orders" class="{{ 'active' if page == 'manual_orders' else '' }} d-flex align-items-center"><i class="fas fa-shopping-bag ms-2"></i> الطلبات الواردة {% if pending_count > 0 %}<span class="badge bg-danger ms-auto rounded-pill">{{ pending_count }}</span>{% endif %}</a>
    <a href="/companies" class="{{ 'active' if page == 'companies' else '' }}"><i class="fas fa-building ms-2"></i> الشركات والأقسام</a>
    <a href="/manual_services" class="{{ 'active' if page == 'manual_services' else '' }}"><i class="fas fa-box-open ms-2"></i> الباقات والخدمات</a>
    <div class="section-title">نظام الوحدات</div>
    <a href="/pending" class="{{ 'active' if page == 'pending' else '' }}"><i class="fas fa-spinner ms-2"></i> التحويلات الجارية</a>
    <a href="/transactions" class="{{ 'active' if page == 'transactions' else '' }}"><i class="fas fa-list-ul ms-2"></i> سجل المبيعات</a>
    <div class="section-title">السجلات والإعدادات</div>
    <a href="/reports" class="{{ 'active' if page == 'reports' else '' }}"><i class="fas fa-chart-pie ms-2"></i> الجرد والتقارير</a>
    <a href="/deposits" class="{{ 'active' if page == 'deposits' else '' }}"><i class="fas fa-university ms-2"></i> الخزينة ودفتر الأستاذ</a>
    <a href="/admin_cash" class="{{ 'active' if page == 'admin_cash' else '' }}"><i class="fas fa-money-bill-wave ms-2"></i> خزينة الكاش والفواتير</a>
    <a href="/merchants" class="{{ 'active' if page == 'merchants' else '' }}"><i class="fas fa-truck-loading ms-2"></i> الموردين والمشتريات</a>
    <a href="/admin_ussd_settings" class="{{ 'active' if page == 'ussd_logs' else '' }}"><i class="fas fa-cogs ms-2"></i> إدارة أكواد USSD</a>
    <a href="/sms_inbox" class="{{ 'active' if page == 'sms_inbox' else '' }}"><i class="fas fa-envelope-open-text ms-2"></i> صندوق رسائل الموبايل</a>
    <a href="/categories" class="{{ 'active' if page == 'categories' else '' }}"><i class="fas fa-tags ms-2"></i> فئات العرض (والطوارئ)</a>
    <a href="/settings" class="{{ 'active' if page == 'settings' else '' }}"><i class="fas fa-cog ms-2"></i> الإعدادات</a>

    <a href="/admin_app" class="text-warning fw-bold border border-warning mt-3 shadow-sm" style="background: rgba(255, 193, 7, 0.1);"><i class="fas fa-mobile-alt ms-2"></i> تطبيق الإدارة 📱</a>

    {% else %}
    <div class="section-title">واجهة الموظفين</div>
    <a href="/employee_dashboard" class="{{ 'active' if page == 'employee_dashboard' else '' }}"><i class="fas fa-user-clock ms-2"></i> شاشة الموظف</a>
    {% endif %}

    <a href="/logout" class="text-danger mt-4 bg-danger bg-opacity-10"><i class="fas fa-power-off ms-2"></i> تسجيل خروج</a>
</div>

<div class="main-content fade-in">
    {% if request.args.get('reset') == 'success' %}
    <div class="alert alert-success fw-bold shadow-sm mb-4"><i class="fas fa-check-circle me-2"></i> تم تصفير كافة السجلات والأرصدة وبدء صفحة جديدة بنجاح!</div>
    {% endif %}
    {% if request.args.get('backup') == 'sent' %}
    <div class="alert alert-primary fw-bold shadow-sm mb-4"><i class="fas fa-cloud-upload-alt me-2"></i> تم إرسال نسخة احتياطية من قاعدة البيانات إلى التلجرام الخاص بك بنجاح!</div>
    {% endif %}
    {% if request.args.get('err') == 'nofunds' %}
    <div class="alert alert-danger fw-bold shadow-sm mb-4"><i class="fas fa-exclamation-circle me-2"></i> عذراً! رصيد الخزينة المركزية الخاص بك غير كافٍ.</div>
    {% endif %}
    {% if request.args.get('err') == 'insufficient_user_bal' %}
    <div class="alert alert-danger fw-bold shadow-sm mb-4"><i class="fas fa-exclamation-circle me-2"></i> ⚠️ عذراً! رصيد العميل الحالي أقل من المبلغ المراد سحبه.</div>
    {% endif %}

    {% block content %}{% endblock %}

    <div class="modal fade" id="pmModal" tabindex="-1" aria-hidden="true">
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content border-0 shadow-lg rounded-4">
          <div class="modal-header bg-primary text-white border-0 rounded-top-4">
            <h5 class="modal-title fw-bold"><i class="fas fa-envelope me-2"></i> إرسال رسالة للعميل</h5>
            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
          </div>
          <form action="/send_pm" method="POST">
              <div class="modal-body p-4">
                <input type="hidden" name="uid" id="pm_uid">
                <p class="text-muted fw-bold mb-3">العميل: <span id="pm_name" class="text-primary fs-5"></span></p>
                <textarea name="message" class="form-control bg-light border-0 shadow-sm p-3" rows="4" placeholder="اكتب رسالتك..." required></textarea>
              </div>
              <div class="modal-footer border-0 pb-4 pe-4">
                <button type="button" class="btn btn-light shadow-sm fw-bold" data-bs-dismiss="modal">إلغاء</button>
                <button type="submit" class="btn btn-primary px-4 fw-bold shadow-sm"><i class="fas fa-paper-plane me-1"></i> إرسال</button>
              </div>
          </form>
        </div>
      </div>
    </div>

    <div class="modal fade" id="editLogModal" tabindex="-1" aria-hidden="true">
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content border-0 shadow-lg rounded-4">
          <div class="modal-header bg-primary text-white border-0 rounded-top-4">
            <h5 class="modal-title fw-bold"><i class="fas fa-pen me-2"></i> تعديل القيد المحاسبي</h5>
            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
          </div>
          <form action="/edit_log" method="POST">
              <div class="modal-body p-4">
                <input type="hidden" name="log_id" id="edit_log_id">
                <div class="mb-3">
                    <label class="form-label fw-bold text-muted mb-2">الكمية المشحونة (وحدات/فواتير)</label>
                    <input type="number" step="0.01" name="amount" id="edit_log_amt" class="form-control bg-light border-0 shadow-sm p-3 fw-bold" required>
                </div>
                <div class="mb-3">
                    <label class="form-label fw-bold text-success mb-2">المبلغ المالي (ل.س)</label>
                    <input type="number" step="0.01" name="actual_paid" id="edit_log_paid" class="form-control bg-light border-0 shadow-sm p-3 fw-bold fs-5" required>
                </div>
                <p class="small text-danger fw-bold mt-2"><i class="fas fa-info-circle"></i> تنبيه: النظام سيقوم بتسوية وتعديل أرصدة وديون العميل تلقائياً عند الحفظ.</p>
              </div>
              <div class="modal-footer border-0 pb-4 pe-4">
                <button type="button" class="btn btn-light shadow-sm fw-bold" data-bs-dismiss="modal">إلغاء</button>
                <button type="submit" class="btn btn-primary px-4 fw-bold shadow-sm"><i class="fas fa-save me-1"></i> حفظ التعديل</button>
              </div>
          </form>
        </div>
      </div>
    </div>

    <script>
        function openPmModal(uid, name) {
            document.getElementById('pm_uid').value = uid;
            document.getElementById('pm_name').innerText = name;
            new bootstrap.Modal(document.getElementById('pmModal')).show();
        }
        function openEditLogModal(id, amt, paid) {
            document.getElementById('edit_log_id').value = id;
            document.getElementById('edit_log_amt').value = amt;
            document.getElementById('edit_log_paid').value = paid;
            new bootstrap.Modal(document.getElementById('editLogModal')).show();
        }
        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('active');
            document.getElementById('sidebarOverlay').classList.toggle('active');
        }
    </script>
    <div class="text-center mt-5 text-muted fw-bold" style="font-size: 0.85rem; opacity: 0.6;"><i class="fas fa-code me-1 text-primary"></i> {{ copyright }}</div>
</div>
{% endif %}
</body>
</html>
"""

@app.context_processor
def inject_globals():
    conn = get_db_connection()
    pending_count = conn.execute("SELECT count(*) FROM manual_orders WHERE status='PENDING'").fetchone()[0] or 0
    conn.close()
    return dict(app_name=APP_NAME, copyright=get_setting('copyright'), role=session.get('role'), current_user=session.get('username'), pending_count=pending_count)

# ==========================================
# 🛡️ نظام الحماية من هجمات التخمين (Anti Brute-Force) المطور
# ==========================================
login_attempts = {}

@app.before_request
def limit_login_attempts():
    # فحص الروابط الحساسة فقط
    if request.method == 'POST' and request.path in ['/secure_rifaie_admin', '/portal', '/api/login']:
        raw_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        client_ip = raw_ip.split(',')[0].strip() if raw_ip else request.remote_addr
        now = time.time()

        # جلب سجل الـ IP أو إنشاء سجل جديد
        record = login_attempts.get(client_ip, {'count': 0, 'last_time': now})

        # فك الحظر تلقائياً إذا مرت 15 دقيقة (900 ثانية)
        if now - record['last_time'] > 900:
            record = {'count': 0, 'last_time': now}

        # حظر فعلي إذا تجاوز 5 محاولات خاطئة
        if record['count'] >= 5:
            remaining = int(900 - (now - record['last_time']))
            return f"<h2 dir='rtl' style='text-align:center; color:red; margin-top:50px;'>⛔ تم حظر عنوانك مؤقتاً بسبب تكرار محاولات الدخول الخاطئة.<br>الرجاء الانتظار {remaining} ثانية لحماية النظام.</h2>", 429

        # تحديث الوقت
        record['last_time'] = now
        login_attempts[client_ip] = record

# 1. الرابط السري لدخول المدير
# 💡 غيرنا الرابط من /manager المكشوف إلى رابط سري مخصص
@app.route('/secure_rifaie_admin', methods=['GET', 'POST'])
def admin_login():
    error_msg = ""
    if request.args.get('err') == '1':
        error_msg = "❌ خطأ في اسم المستخدم أو كلمة المرور."
    elif request.args.get('err') == '2':
        error_msg = "⚠️ هذا الحساب تابع لزبون، يرجى الدخول من 'بوابة الزبائن'."

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        raw_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        client_ip = raw_ip.split(',')[0].strip() if raw_ip else request.remote_addr

        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE (username=? OR user_id=?) AND password=?", (username, username, password)).fetchone()
        conn.close()

        if user:
            # ✅ الدخول ناجح: تصفير عداد الأخطاء لهذا الـ IP فوراً
            if client_ip in login_attempts:
                login_attempts.pop(client_ip, None)

            session['user_id'] = user['user_id']
            session['username'] = user['real_name']
            session['role'] = user['role']
            session['logged_in'] = True
            session.permanent = True

            if user['role'] in ['admin', 'agent', 'employee']:
                return redirect('/')
            else:
                session.clear()
                return redirect('/secure_rifaie_admin?err=2')
        else:
            # ❌ الدخول فاشل: زيادة العداد
            if client_ip in login_attempts:
                login_attempts[client_ip]['count'] += 1
            return redirect('/secure_rifaie_admin?err=1')

    login_html = HTML_BASE.replace(
        '<p class="text-muted mb-4 fw-bold">تسجيل الدخول للإدارة المركزية</p>',
        f'<p class="text-muted mb-4 fw-bold">تسجيل الدخول للإدارة المركزية</p><div class="alert alert-danger p-2 small fw-bold">{error_msg}</div>' if error_msg else '<p class="text-muted mb-4 fw-bold">تسجيل الدخول للإدارة المركزية</p>'
    )
    return render_template_string(login_html, page='login')

# 2. بوابة التوجيه الذكية (الرابط الرئيسي للجميع)
@app.route('/')
def root_gate():
    # إذا الشخص مو مسجل دخول، ابعته فوراً لبوابة الزبائن
    if not session.get('logged_in'):
        return redirect('/portal')

    # إذا مسجل دخول، وجهه حسب رتبته
    if session.get('role') == 'admin':
        return redirect('/admin_app') # 👈 هنا التعديل السحري للتطبيق
    elif session.get('role') == 'employee':
        return redirect('/employee_dashboard')
    else:
        return redirect('/customer_dashboard')


# 3. لوحة تحكم المدير الفعلية (الجرد والإحصائيات)
@app.route('/admin_center')
def admin_dashboard():
    if not session.get('logged_in') or session.get('role') not in ['admin', 'agent']:
        return redirect('/secure_rifaie_admin')

    today = datetime.now(local_tz).strftime("%Y-%m-%d")
    conn = get_db_connection()

    # الإحصائيات العلوية
    s_units = conn.execute("SELECT sum(amount) FROM transactions WHERE status='SUCCESS' AND network='Syriatel' AND date LIKE ?", (f"{today}%",)).fetchone()[0] or 0
    m_units = conn.execute("SELECT sum(amount) FROM transactions WHERE status='SUCCESS' AND network='MTN' AND date LIKE ?", (f"{today}%",)).fetchone()[0] or 0
    total_units = s_units + m_units
    s_bills = conn.execute("SELECT sum(price) FROM manual_orders WHERE status='COMPLETED' AND date LIKE ?", (f"{today}%",)).fetchone()[0] or 0
    new_users = conn.execute("SELECT count(*) FROM users WHERE joined_date LIKE ?", (f"{today}%",)).fetchone()[0] or 0
    total_debt = conn.execute("SELECT sum(debt_balance) FROM users WHERE role='user'").fetchone()[0] or 0

    # البث الحي
    feed = []
    for x in conn.execute("SELECT u.real_name, u.user_id, t.amount, t.phone as detail, t.status, t.date, 'وحدات' as type FROM transactions t LEFT JOIN users u ON t.user_id = u.user_id ORDER BY t.id DESC LIMIT 20").fetchall(): feed.append(dict(x))
    for x in conn.execute("SELECT u.real_name, u.user_id, m.price as amount, m.service_name as detail, m.status, m.date, 'فواتير' as type FROM manual_orders m LEFT JOIN users u ON m.user_id = u.user_id ORDER BY m.id DESC LIMIT 20").fetchall(): feed.append(dict(x))
    for x in conn.execute("SELECT u.real_name, u.user_id, d.amount, d.wallet_type as detail, 'COMPLETED' as status, d.date, 'شحن' as type, d.is_debt as is_debt FROM deposit_logs d LEFT JOIN users u ON d.user_id = u.user_id ORDER BY d.id DESC LIMIT 20").fetchall(): feed.append(dict(x))

    feed.sort(key=lambda x: x['date'] if x['date'] else "", reverse=True)
    feed = feed[:20]

    # الرادار
    active_tasks = conn.execute("SELECT id, phone, amount, status, date FROM transactions WHERE status IN ('QUEUED', 'PROCESSING', 'MANUAL_CHECK') ORDER BY id ASC LIMIT 6").fetchall()

    # الأرصدة
    sim_s = float(get_setting('sim_balance_Syriatel') or 0)
    sim_m = float(get_setting('sim_balance_MTN') or 0)
    cash_s = float(get_setting('sim_cash_Syriatel') or 0)
    cash_m = float(get_setting('sim_cash_MTN') or 0)
    bill_s = float(get_setting('sim_bill_Syriatel') or 0)
    bill_m = float(get_setting('sim_bill_MTN') or 0)

    last_hb = get_setting('last_mobile_heartbeat')
    conn.close()

    # 💡 التعديل السحري هنا: تقليل مهلة الانتظار لـ 30 ثانية بدل 300 ثانية
    is_online = False
    if last_hb:
        try:
            if time.time() - float(last_hb) <= 30:
                is_online = True
        except: pass

    if is_online:
        mobile_status = """<div class="d-inline-flex align-items-center bg-success bg-opacity-10 text-success px-3 py-1 rounded-2" style="font-size: 13px; font-weight: 800;"><i class="fas fa-check-circle me-2"></i> الموبايل متصل</div>"""
    else:
        mobile_status = """<div class="d-inline-flex align-items-center bg-danger bg-opacity-10 text-danger px-3 py-1 rounded-2" style="font-size: 13px; font-weight: 800;"><i class="fas fa-exclamation-triangle me-2"></i> الموبايل مفصول</div>"""

    # توليد HTML للبث الحي
    feed_html = ""
    if not feed: feed_html = "<tr><td colspan='5' class='text-center text-muted py-4'>لا توجد حركات اليوم.</td></tr>"
    else:
        for item in feed:
            time_only = item['date'][11:16] if item['date'] else ""
            user_link = f"<a href='/user/{item['user_id']}' class='user-link fw-black'>{item['real_name'] or 'مجهول'}</a>"
            if item['type'] == 'وحدات':
                icon = "<div class='live-feed-icon bg-primary bg-opacity-10 text-primary'><i class='fas fa-mobile-alt'></i></div>"
                stat = "<span class='badge bg-success bg-opacity-10 text-success border border-success'>ناجحة</span>" if item['status'] == 'SUCCESS' else "<span class='badge bg-warning bg-opacity-10 text-warning border border-warning'>جاري...</span>" if item['status'] in ('QUEUED', 'PROCESSING') else "<span class='badge bg-danger bg-opacity-10 text-danger border border-danger'>فشل</span>"
            elif item['type'] == 'فواتير':
                icon = "<div class='live-feed-icon bg-warning bg-opacity-10 text-warning'><i class='fas fa-receipt'></i></div>"
                stat = "<span class='badge bg-success bg-opacity-10 text-success border border-success'>مكتملة</span>" if item['status'] == 'COMPLETED' else "<span class='badge bg-danger bg-opacity-10 text-danger border border-danger'>مرفوضة</span>" if item['status'] == 'REJECTED' else "<span class='badge bg-secondary bg-opacity-10 text-secondary border border-secondary'>بالانتظار</span>"
            else:
                icon = "<div class='live-feed-icon bg-success bg-opacity-10 text-success'><i class='fas fa-arrow-down'></i></div>"
                stat = "<span class='badge bg-success rounded-pill'>تم الإيداع</span>"
            feed_html += f"""<tr class="live-feed-row"><td>{icon}</td><td>{user_link}<br><small class="text-muted">{item['type']}</small></td><td dir="ltr" class="text-end fw-bold">{item['detail']}</td><td><strong class="text-dark fs-6">{item['amount']:,.2f}</strong></td><td>{stat}</td><td class="text-muted small" dir="ltr">{time_only}</td></tr>"""

    # توليد HTML للرادار
    radar_html = ""
    for task in active_tasks:
        bg_color, text_color, icon, label = ("bg-warning bg-opacity-10 border-warning", "text-warning", "fa-spinner fa-spin", "جاري الإرسال...") if task['status'] in ('QUEUED', 'PROCESSING') else ("bg-danger bg-opacity-10 border-danger", "text-danger", "fa-exclamation-triangle", "🚨 مراجعة!")
        radar_html += f"<div class='p-3 {bg_color} border rounded-3 mt-3'><small class='{text_color} fw-bold d-block mb-1'><i class='fas {icon} me-1'></i> {label}</small><div class='small text-dark fw-bold'>الرقم: <span dir='ltr'>{task['phone']}</span> | المبلغ: {task['amount']:,.2f}</div></div>"

    content = f"""
    <div class="d-flex justify-content-between align-items-center mb-4">
        <div>
            <h3 class="fw-black m-0 text-primary"><i class="fas fa-tachometer-alt me-2"></i> لوحة تحكم الرفاعي تليكوم</h3>
            <p class="text-muted m-0 mt-2 fw-bold">مراقبة حية لنشاط النظام</p>
        </div>
        <div class="text-end d-flex flex-column align-items-end">
            {mobile_status}
            <h5 class="m-0 fw-bold text-dark mt-2" dir="ltr">{today}</h5>
        </div>
    </div>

    <div class="row mb-4 g-3">
        <div class="col-md-3"><div class="kpi-card"><div class="flex-grow-1"><div class="kpi-title">مبيعات الوحدات</div><div class="kpi-value">{total_units:,.2f}</div></div><div class="kpi-icon bg-primary bg-opacity-10 text-primary"><i class="fas fa-mobile-alt"></i></div></div></div>
        <div class="col-md-3"><div class="kpi-card"><div class="flex-grow-1"><div class="kpi-title">مبيعات الفواتير</div><div class="kpi-value">{s_bills:,.2f}</div></div><div class="kpi-icon bg-info bg-opacity-10 text-info"><i class="fas fa-file-invoice-dollar"></i></div></div></div>
        <div class="col-md-3"><div class="kpi-card"><div class="flex-grow-1"><div class="kpi-title">الديون في السوق</div><div class="kpi-value text-danger">{total_debt:,.2f}</div></div><div class="kpi-icon bg-danger bg-opacity-10 text-danger"><i class="fas fa-hand-holding-usd"></i></div></div></div>
        <div class="col-md-3"><div class="kpi-card"><div class="flex-grow-1"><div class="kpi-title">عملاء جدد</div><div class="kpi-value text-success">+{new_users}</div></div><div class="kpi-icon bg-success bg-opacity-10 text-success"><i class="fas fa-user-plus"></i></div></div></div>
    </div>

    <h5 class="fw-bold text-dark mb-3 mt-4"><i class="fas fa-wallet text-primary me-2"></i> مركز السيولة (رصيد الشرائح)</h5>
    <div class="row g-3 mb-5">
        <div class="col-md-6">
            <div class="card border-danger border-start border-4 shadow-sm p-4 h-100 bg-white">
                <h6 class="text-danger fw-bold border-bottom pb-2 mb-4">🔴 سيريتل (Syriatel)</h6>
                <div class="d-flex justify-content-between align-items-center mb-3"><span class="fw-bold text-muted">📱 الوحدات:</span><span class="fw-black fs-5">{sim_s:,.0f}</span></div>
                <div class="d-flex justify-content-between align-items-center mb-3"><span class="fw-bold text-muted">💵 الكاش:</span><span class="fw-black fs-5">{cash_s:,.0f}</span></div>
                <div class="d-flex justify-content-between align-items-center mb-4"><span class="fw-bold text-muted">🧾 الفواتير:</span><span class="fw-black fs-5">{bill_s:,.0f}</span></div>
                <div class="d-flex gap-2 mt-auto">
                    <form action="/trigger_bal_check/Syriatel" method="POST" class="w-50"><button class="btn btn-outline-danger w-100 fw-bold">تحديث آلي 📡</button></form>
                    <a href="/admin_cash" class="btn btn-danger w-50 fw-bold">تعديل الخزائن ✏️</a>
                </div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="card border-warning border-start border-4 shadow-sm p-4 h-100 bg-white">
                <h6 class="text-warning fw-bold border-bottom pb-2 mb-4" style="color:#d97706!important;">🟡 MTN</h6>
                <div class="d-flex justify-content-between align-items-center mb-3"><span class="fw-bold text-muted">📱 الوحدات:</span><span class="fw-black fs-5">{sim_m:,.0f}</span></div>
                <div class="d-flex justify-content-between align-items-center mb-3"><span class="fw-bold text-muted">💵 الكاش:</span><span class="fw-black fs-5">{cash_m:,.0f}</span></div>
                <div class="d-flex justify-content-between align-items-center mb-4"><span class="fw-bold text-muted">🧾 الفواتير:</span><span class="fw-black fs-5">{bill_m:,.0f}</span></div>
                <div class="d-flex gap-2 mt-auto">
                    <form action="/trigger_bal_check/MTN" method="POST" class="w-50"><button class="btn btn-outline-warning text-dark w-100 fw-bold">تحديث آلي 📡</button></form>
                    <a href="/admin_cash" class="btn btn-warning text-dark w-50 fw-bold">تعديل الخزائن ✏️</a>
                </div>
            </div>
        </div>
    </div>

    <div class="row g-4">
        <div class="col-md-8"><div class="card-bank h-100 m-0"><h5 class="fw-bold text-dark mb-4"><i class="fas fa-stream text-primary me-2"></i> البث الحي</h5><div class="table-responsive"><table class="table table-borderless align-middle"><thead><tr><th>النوع</th><th>العميل</th><th class="text-end">التفاصيل</th><th>المبلغ</th><th>الحالة</th><th>الوقت</th></tr></thead><tbody>{feed_html}</tbody></table></div></div></div>
        <div class="col-md-4"><div class="radar-box h-100"><h5 class="fw-bold mb-4 text-white"><i class="fas fa-radar text-accent me-2"></i> رادار النظام</h5>{radar_html}</div></div>
    </div>
    """
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='dashboard')



@app.route('/trigger_bal_check/<network>', methods=['POST'])
def trigger_bal_check(network):
    if session.get('logged_in') and session.get('role') == 'admin':
        conn = get_db_connection()
        code_name = 'check_bal_syriatel' if network == 'Syriatel' else 'check_bal_mtn'
        # جلب الكود مع الباسورد الخاص به
        code_row = conn.execute("SELECT ussd_format, secret_pin FROM ussd_codes WHERE service_name=?", (code_name,)).fetchone()
        conn.close()

        ussd_code = code_row['ussd_format'] if code_row else ""
        pin = code_row['secret_pin'] if code_row else ""

        if ussd_code:
            final_ussd = ussd_code.replace("{pin}", str(pin))
            set_setting(f'pending_cmd_{network}', final_ussd)
        else:
            set_setting(f'pending_cmd_{network}', '1')

    return redirect(request.referrer or '/')


@app.route('/sys_cmd_result', methods=['POST'])
def sys_cmd_result():
    data = request.get_json() or request.form
    network = data.get('network')
    message = data.get('message', '')
    
    if network:
        set_setting(f'pending_cmd_{network}', '0')
        
        # 🚀 التحديث الذكي: سحب الكلمات المفتاحية من لوحة التحكم (الداتا بيز) 🚀
        conn = get_db_connection()
        code_name = 'check_bal_syriatel' if network == 'Syriatel' else 'check_bal_mtn'
        try:
            code_row = conn.execute("SELECT success_keyword FROM ussd_codes WHERE service_name=?", (code_name,)).fetchone()
            success_str = code_row['success_keyword'] if code_row and code_row['success_keyword'] else "رصيد,balance"
        except:
            success_str = "رصيد,balance"
        conn.close()
        
        # تنظيف الكلمات وفصلها
        success_keywords = [k.strip().lower() for k in success_str.split(',') if k.strip()]
        lower_msg = message.lower()
        
        # فحص إذا كانت الرسالة تحتوي على أي كلمة من كلمات النجاح المسجلة باللوحة
        is_valid_msg = any(kw in lower_msg for kw in success_keywords)
        
        if is_valid_msg:
            # الخوارزمية القشاشة لسحب الأرقام
            nums_str = re.findall(r'\d+(?:[.,]\d+)*', message)
            if nums_str:
                nums_float = [float(n.replace(',', '')) for n in nums_str]
                valid_nums = [n for n in nums_float if n < 900000000] 
                if valid_nums:
                    actual_balance = max(valid_nums)
                    set_setting(f'sim_balance_{network}', str(actual_balance))
                    
    return jsonify({"status": "success"})


@app.route('/reports')
def reports_page():
    if not session.get('logged_in'): return redirect('/')

    # نظام الفلترة الزمني الذكي
    period = request.args.get('period', 'today')
    now = datetime.now(local_tz)

    if period == 'today':
        start_date = now.strftime("%Y-%m-%d 00:00:00")
        end_date = now.strftime("%Y-%m-%d 23:59:59")
        p_name = "اليوم"
    elif period == 'yesterday':
        yesterday = now - timedelta(days=1)
        start_date = yesterday.strftime("%Y-%m-%d 00:00:00")
        end_date = yesterday.strftime("%Y-%m-%d 23:59:59")
        p_name = "الأمس"
    elif period == 'week':
        start_date = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d 00:00:00")
        end_date = now.strftime("%Y-%m-%d 23:59:59")
        p_name = "هذا الأسبوع"
    elif period == 'month':
        start_date = now.strftime("%Y-%m-01 00:00:00")
        end_date = now.strftime("%Y-%m-%d 23:59:59")
        p_name = "هذا الشهر"
    else:
        start_date = now.strftime("%Y-%m-%d 00:00:00")
        end_date = now.strftime("%Y-%m-%d 23:59:59")
        p_name = "اليوم"
        period = 'today'

    conn = get_db_connection()

    # 1. إحصائيات الفترة المحددة (تتغير مع الفلتر الزمني)
    s_sales_p = conn.execute("SELECT sum(amount) FROM transactions WHERE status='SUCCESS' AND network='Syriatel' AND date BETWEEN ? AND ?", (start_date, end_date)).fetchone()[0] or 0
    m_sales_p = conn.execute("SELECT sum(amount) FROM transactions WHERE status='SUCCESS' AND network='MTN' AND date BETWEEN ? AND ?", (start_date, end_date)).fetchone()[0] or 0
    bills_sales_p = conn.execute("SELECT sum(price) FROM manual_orders WHERE status='COMPLETED' AND date BETWEEN ? AND ?", (start_date, end_date)).fetchone()[0] or 0

    prof_u_p = conn.execute("SELECT sum(profit) FROM deposit_logs WHERE by_admin_id!=0 AND date BETWEEN ? AND ?", (start_date, end_date)).fetchone()[0] or 0
    prof_b_p = conn.execute("SELECT sum(profit) FROM manual_orders WHERE status='COMPLETED' AND date BETWEEN ? AND ?", (start_date, end_date)).fetchone()[0] or 0
    # 💡 حساب أرباح الكاش والفواتير الآلية للفترة المحددة
    prof_t_p = conn.execute("SELECT sum(profit) FROM transactions WHERE status='SUCCESS' AND date BETWEEN ? AND ?", (start_date, end_date)).fetchone()[0] or 0

    try: audit_loss_p = conn.execute("SELECT sum(amount) FROM deposit_logs WHERE wallet_type='system_audit' AND date BETWEEN ? AND ?", (start_date, end_date)).fetchone()[0] or 0
    except: audit_loss_p = 0

    net_prof_p = prof_u_p + prof_b_p + prof_t_p + audit_loss_p

    # 2. إحصائيات السيولة والمستودع اللحظية
    tot_debt = conn.execute("SELECT sum(debt_balance) FROM users WHERE role='user'").fetchone()[0] or 0
    rem_units = conn.execute("SELECT sum(balance) FROM users WHERE role='user'").fetchone()[0] or 0

    admin_data = conn.execute("SELECT balance FROM users WHERE user_id=?", (session['user_id'],)).fetchone()
    admin_units = admin_data['balance'] if admin_data else 0
    theo_stock = rem_units + admin_units

    # 3. الأرباح التراكمية الإجمالية (طوال الوقت)
    prof_u_tot = conn.execute("SELECT sum(profit) FROM deposit_logs WHERE by_admin_id!=0").fetchone()[0] or 0
    prof_b_tot = conn.execute("SELECT sum(profit) FROM manual_orders WHERE status='COMPLETED'").fetchone()[0] or 0
    prof_t_tot = conn.execute("SELECT sum(profit) FROM transactions WHERE status='SUCCESS'").fetchone()[0] or 0

    try: audit_loss_tot = conn.execute("SELECT sum(amount) FROM deposit_logs WHERE wallet_type='system_audit'").fetchone()[0] or 0
    except: audit_loss_tot = 0

    net_prof_tot = prof_u_tot + prof_b_tot + prof_t_tot + audit_loss_tot

    conn.close()

    cash_drawer = float(get_setting('cash_drawer') or 0)
    merchant_debt = float(get_setting('merchant_debt') or 0)
    sim_s = float(get_setting('sim_balance_Syriatel') or 0)
    sim_m = float(get_setting('sim_balance_MTN') or 0)
    actual_stock = sim_s + sim_m

    net_liq = (cash_drawer + tot_debt) - merchant_debt
    stock_diff = actual_stock - theo_stock

    # 🤖 نظام المفتش الآلي وتوليد زر التسوية آلياً بناءً على الفروقات
    if abs(stock_diff) < 0.01:
        stock_status = "<div class='alert alert-success mt-3 mb-0 fw-bold'><i class='fas fa-check-double me-1'></i> تطابق 100% (لا يوجد فروقات)</div>"
        reconcile_btn = ""
    elif stock_diff > 0:
        stock_status = f"<div class='alert alert-primary mt-3 mb-0 fw-bold'><i class='fas fa-arrow-up me-1'></i> يوجد فائض بالمخزون: {stock_diff:,.2f}</div>"
        reconcile_btn = "<form action='/auto_reconcile' method='POST' class='mt-2'><button type='submit' class='btn btn-primary w-100 fw-bold shadow-sm' onclick='return confirm(\"تأكيد تقييد الفائض في حساب الخزينة؟\");'><i class='fas fa-magic me-1'></i> تسوية الجرد آلياً</button></form>"
    else:
        stock_status = f"<div class='alert alert-danger mt-3 mb-0 fw-bold'><i class='fas fa-exclamation-triangle me-1'></i> يوجد عجز (رسوم مخفية): {abs(stock_diff):,.2f}</div>"
        reconcile_btn = "<form action='/auto_reconcile' method='POST' class='mt-2'><button type='submit' class='btn btn-danger w-100 fw-bold shadow-sm' onclick='return confirm(\"تأكيد خصم العجز من الخزينة المركزية ليتطابق الجرد؟\");'><i class='fas fa-magic me-1'></i> تسوية الجرد آلياً</button></form>"

    alert_msg = ""
    if request.args.get('success') == 'reconciled':
        alert_msg = "<div class='alert alert-success fw-bold shadow-sm mb-4'><i class='fas fa-check-circle me-2'></i> تمت تسوية الفروقات بنجاح وتوثيقها بدفتر الأستاذ!</div>"
    elif request.args.get('success') == 'reset_profits':
        alert_msg = "<div class='alert alert-success fw-bold shadow-sm mb-4'><i class='fas fa-check-circle me-2'></i> تم تصفير الأرباح التراكمية بنجاح.</div>"

    content = f"""
    <style>
        .glass-panel {{ background: rgba(255,255,255,0.85); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.9); border-radius: 24px; box-shadow: 0 10px 30px rgba(0,0,0,0.03); padding: 25px; transition: 0.3s; }}
        .glass-panel:hover {{ transform: translateY(-3px); box-shadow: 0 15px 35px rgba(0,0,0,0.06); }}
        .card-3d {{ border-radius: 24px; padding: 25px; position: relative; overflow: hidden; z-index: 1; border: none; box-shadow: 0 15px 30px rgba(0,0,0,0.08); transition: 0.3s; }}
        .card-3d:hover {{ transform: scale(1.02); }}
        .card-3d::before {{ content: ''; position: absolute; top: -50%; right: -50%; width: 200%; height: 200%; background: radial-gradient(circle, rgba(255,255,255,0.15) 0%, transparent 60%); transform: rotate(45deg); z-index: -1; pointer-events: none; }}
        .grad-green {{ background: linear-gradient(135deg, #059669 0%, #10b981 100%); color: white; }}
        .grad-blue {{ background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%); color: white; }}
        .grad-red {{ background: linear-gradient(135deg, #991b1b 0%, #ef4444 100%); color: white; }}
        .grad-gold {{ background: linear-gradient(135deg, #b45309 0%, #f59e0b 100%); color: white; }}
        .grad-dark {{ background: linear-gradient(135deg, #0f172a 0%, #334155 100%); color: white; }}
        .grad-purple {{ background: linear-gradient(135deg, #6d28d9 0%, #8b5cf6 100%); color: white; }}
        .filter-btn {{ border-radius: 50px; font-weight: 800; padding: 8px 20px; transition: 0.3s; text-decoration: none; display: inline-block; margin-left: 5px; margin-bottom: 10px; }}
        .filter-btn.active {{ background: #0f172a; color: white; box-shadow: 0 5px 15px rgba(15,23,42,0.3); }}
        .filter-btn:not(.active) {{ background: white; color: #64748b; border: 1px solid #e2e8f0; }}
        .filter-btn:hover:not(.active) {{ background: #f1f5f9; color: #0f172a; }}
        .icon-circle {{ width: 50px; height: 50px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.5rem; background: rgba(255,255,255,0.2); margin-bottom: 15px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }}
    </style>

    <div class="d-flex justify-content-between align-items-center mb-4 flex-wrap">
        <h3 class="fw-black text-primary m-0"><i class="fas fa-chart-line me-2"></i> لوحة القيادة الذكية (الجرد)</h3>
        <div class="mt-3 mt-md-0">
            <a href="?period=today" class="filter-btn {'active' if period=='today' else ''}">اليوم</a>
            <a href="?period=yesterday" class="filter-btn {'active' if period=='yesterday' else ''}">الأمس</a>
            <a href="?period=week" class="filter-btn {'active' if period=='week' else ''}">هذا الأسبوع</a>
            <a href="?period=month" class="filter-btn {'active' if period=='month' else ''}">هذا الشهر</a>
        </div>
    </div>

    {alert_msg}

    <h5 class="fw-bold text-secondary mt-2 mb-3"><i class="fas fa-calendar-alt me-2"></i> ملخص مبيعات وأرباح ( {p_name} )</h5>
    <div class="row g-4 mb-5">
        <div class="col-md-3">
            <div class="card-3d grad-gold">
                <div class="icon-circle"><i class="fas fa-hand-holding-usd"></i></div>
                <p class="mb-1 fw-bold opacity-75">صافي الأرباح (بعد الفروقات)</p>
                <h2 class="fw-black m-0">{net_prof_p:,.2f} <small class="fs-6">ل.س</small></h2>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card-3d grad-red">
                <div class="icon-circle"><i class="fas fa-sim-card"></i></div>
                <p class="mb-1 fw-bold opacity-75">مبيعات سيريتل</p>
                <h2 class="fw-black m-0">{s_sales_p:,.2f} <small class="fs-6">وحدة</small></h2>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card-3d" style="background: linear-gradient(135deg, #d97706 0%, #fbbf24 100%); color: white;">
                <div class="icon-circle"><i class="fas fa-sim-card"></i></div>
                <p class="mb-1 fw-bold opacity-75">مبيعات MTN</p>
                <h2 class="fw-black m-0">{m_sales_p:,.2f} <small class="fs-6">وحدة</small></h2>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card-3d grad-blue">
                <div class="icon-circle"><i class="fas fa-file-invoice-dollar"></i></div>
                <p class="mb-1 fw-bold opacity-75">مبيعات الفواتير</p>
                <h2 class="fw-black m-0">{bills_sales_p:,.2f} <small class="fs-6">ل.س</small></h2>
            </div>
        </div>
    </div>

    <h5 class="fw-bold text-secondary mb-3"><i class="fas fa-boxes me-2"></i> مطابقة المستودع (الوحدات)</h5>
    <div class="row g-4 mb-5">
        <div class="col-md-4">
            <div class="card-3d grad-purple h-100 text-center">
                <i class="fas fa-users fs-1 opacity-50 mb-3 mt-2"></i>
                <p class="fw-bold opacity-75 mb-1">أرصدة الزبائن بالسيستم</p>
                <h2 class="fw-black">{rem_units:,.2f}</h2>
            </div>
        </div>
        <div class="col-md-4">
            <div class="card-3d grad-dark h-100 text-center">
                <i class="fas fa-box-open fs-1 opacity-50 mb-3 mt-2"></i>
                <p class="fw-bold opacity-75 mb-1">خزينتك غير المباعة</p>
                <h2 class="fw-black">{admin_units:,.2f}</h2>
            </div>
        </div>
        <div class="col-md-4">
            <div class="card-3d grad-green h-100 text-center border border-white border-2 shadow-lg">
                <i class="fas fa-sim-card fs-1 opacity-50 mb-3 mt-2"></i>
                <p class="fw-bold opacity-75 mb-1">المخزون الفعلي بالشرائح</p>
                <h2 class="fw-black mb-3">{actual_stock:,.2f}</h2>
                {stock_status}
                {reconcile_btn}
            </div>
        </div>
    </div>

    <h5 class="fw-bold text-secondary mb-3"><i class="fas fa-wallet me-2"></i> الخزينة والسيولة (الوضع الحالي)</h5>
    <div class="glass-panel mb-5">
        <div class="row g-4">
            <div class="col-md-3 border-end border-light">
                <div class="text-center">
                    <div class="d-inline-block bg-success bg-opacity-10 text-success p-3 rounded-circle mb-3"><i class="fas fa-cash-register fs-3"></i></div>
                    <h6 class="fw-bold text-muted">الكاش (بالدرج)</h6>
                    <h3 class="fw-black text-success m-0">{cash_drawer:,.2f}</h3>
                </div>
            </div>
            <div class="col-md-3 border-end border-light">
                <div class="text-center">
                    <div class="d-inline-block bg-primary bg-opacity-10 text-primary p-3 rounded-circle mb-3"><i class="fas fa-users fs-3"></i></div>
                    <h6 class="fw-bold text-muted">ديون الزبائن (لنا)</h6>
                    <h3 class="fw-black text-primary m-0">{tot_debt:,.2f}</h3>
                </div>
            </div>
            <div class="col-md-3 border-end border-light">
                <div class="text-center">
                    <div class="d-inline-block bg-danger bg-opacity-10 text-danger p-3 rounded-circle mb-3"><i class="fas fa-truck-loading fs-3"></i></div>
                    <h6 class="fw-bold text-muted">ديون الموردين (علينا)</h6>
                    <h3 class="fw-black text-danger m-0">{merchant_debt:,.2f}</h3>
                </div>
            </div>
            <div class="col-md-3">
                <div class="text-center">
                    <div class="d-inline-block bg-dark bg-opacity-10 text-dark p-3 rounded-circle mb-3"><i class="fas fa-balance-scale fs-3"></i></div>
                    <h6 class="fw-bold text-muted">صافي السيولة الفعلي</h6>
                    <h3 class="fw-black text-dark m-0">{net_liq:,.2f}</h3>
                    <small class="text-muted" style="font-size:11px;">(الكاش + ديوننا) - ديون الموردين</small>
                </div>
            </div>
        </div>
    </div>

    <div class="glass-panel text-center">
        <div class="mb-3">
            <h5 class="fw-bold text-dark m-0">🏆 الأرباح التراكمية (طوال الوقت)</h5>
        </div>

        <div class="mb-4">
            <form action="/reset_profits" method="POST">
                <button type="submit" class="btn btn-sm btn-outline-danger fw-bold shadow-sm" onclick="return confirm('تأكيد تصفير الأرباح التراكمية للصفر؟');">
                    <i class="fas fa-undo me-1"></i> تصفير الأرباح
                </button>
            </form>
        </div>

        <div class="row">
            <div class="col-4 border-end">
                <p class="text-muted fw-bold mb-1">من شحن الأرصدة</p>
                <h4 class="fw-black text-success">{prof_u_tot:,.2f}</h4>
            </div>
            <div class="col-4 border-end">
                <p class="text-muted fw-bold mb-1">من الفواتير والكاش</p>
                <h4 class="fw-black text-info">{(prof_b_tot + prof_t_tot):,.2f}</h4>
            </div>
            <div class="col-4">
                <p class="text-muted fw-bold mb-1">الربح الصافي الكلي</p>
                <h3 class="fw-black text-warning m-0" style="color: #d97706!important;">{net_prof_tot:,.2f} <small class="fs-6 text-muted">ل.س</small></h3>
            </div>
        </div>
    </div>
    """
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='reports')


@app.route('/user/<int:uid>', methods=['GET', 'POST'])
def user_profile_page(uid):
    if not session.get('logged_in'): return redirect('/')

    if request.method == 'POST':
        new_price = request.form.get('custom_sell_price')
        new_limit = request.form.get('debt_limit')

        if new_price and new_limit:
            conn = get_db_connection()
            conn.execute("UPDATE users SET custom_sell_price=?, debt_limit=? WHERE user_id=?", (float(new_price), float(new_limit), uid))
            conn.commit(); conn.close()
            return redirect(f'/user/{uid}')

    # ⏳ سحب تواريخ (من - إلى) لآلة الزمن
    today_date = datetime.now(local_tz).strftime("%Y-%m-%d")
    start_date = request.args.get('start_date', today_date)
    end_date = request.args.get('end_date', today_date)

    # تجهيز التواريخ للبحث الدقيق في قاعدة البيانات (من أول ثانية بـ start لآخر ثانية بـ end)
    start_dt = f"{start_date} 00:00:00"
    end_dt = f"{end_date} 23:59:59"

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    if not user: conn.close(); return redirect('/users')

    # 🧠 الحسابات الستة الدقيقة للمدى الزمني المحدد (BETWEEN)
    # 1. رصيد سيريتل (وحدات عادية)
    s_bal_range = conn.execute("SELECT sum(amount) FROM transactions WHERE user_id=? AND network='Syriatel' AND service_type IN ('Jahez', 'Bulk') AND status='SUCCESS' AND date BETWEEN ? AND ?", (uid, start_dt, end_dt)).fetchone()[0] or 0
    # 2. رصيد MTN (وحدات عادية)
    m_bal_range = conn.execute("SELECT sum(amount) FROM transactions WHERE user_id=? AND network='MTN' AND service_type IN ('Jahez', 'Bulk') AND status='SUCCESS' AND date BETWEEN ? AND ?", (uid, start_dt, end_dt)).fetchone()[0] or 0
    # 3. كاش سيريتل
    s_cash_range = conn.execute("SELECT sum(amount) FROM transactions WHERE user_id=? AND service_type='cash_syriatel' AND status='SUCCESS' AND date BETWEEN ? AND ?", (uid, start_dt, end_dt)).fetchone()[0] or 0
    # 4. كاش MTN
    m_cash_range = conn.execute("SELECT sum(amount) FROM transactions WHERE user_id=? AND service_type='cash_mtn' AND status='SUCCESS' AND date BETWEEN ? AND ?", (uid, start_dt, end_dt)).fetchone()[0] or 0
    # 5. فواتير سيريتل (آلية)
    s_bill_range = conn.execute("SELECT sum(amount) FROM transactions WHERE user_id=? AND service_type='bill_syriatel' AND status='SUCCESS' AND date BETWEEN ? AND ?", (uid, start_dt, end_dt)).fetchone()[0] or 0
    # 6. فواتير MTN (آلية)
    m_bill_range = conn.execute("SELECT sum(amount) FROM transactions WHERE user_id=? AND service_type='bill_mtn' AND status='SUCCESS' AND date BETWEEN ? AND ?", (uid, start_dt, end_dt)).fetchone()[0] or 0

    # جلب السجل الشامل (دفتر الأستاذ) للزبون
    u_trans = conn.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    u_bills = conn.execute("SELECT * FROM manual_orders WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    u_deps = conn.execute("SELECT * FROM deposit_logs WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    conn.close()

    history = []

    for t in u_trans:
        stat_html = "<span class='text-success'>ناجح</span>" if t['status'] == 'SUCCESS' else "<span class='text-danger'>فشل</span>" if t['status'] in ('REFUNDED','CANCELLED') else "<span class='text-warning'>معلق</span>"
        amt = float(t['amount'] or 0) + float(dict(t).get('profit') or 0) # المجموع اللي انخصم من الزبون فعلياً
        b_before = float(dict(t).get('balance_before') or 0)
        b_after = float(dict(t).get('balance_after') or 0)
        phone_num = t['phone']
        ussd_resp = dict(t).get('ussd_response')
        if ussd_resp:
            clean_resp = str(ussd_resp).replace('رد الشركة:', '').replace('📝', '').strip()
            detail_html = f"<div class='d-flex flex-column'><span>{phone_num}</span><span class='text-muted mt-1 fw-normal' style='font-size: 11px; max-width: 250px; white-space: normal; line-height: 1.4;'><i class='fas fa-reply text-success opacity-75' style='transform: scaleX(-1);'></i> {clean_resp}</span></div>"
        else: detail_html = f"<span>{phone_num}</span>"

        trans_desc = "تحويل " + t['network']
        if t['service_type'] == 'cash_syriatel': trans_desc = "كاش سيريتل"
        elif t['service_type'] == 'cash_mtn': trans_desc = "كاش MTN"
        elif t['service_type'] == 'bill_syriatel': trans_desc = "فاتورة سيريتل"
        elif t['service_type'] == 'bill_mtn': trans_desc = "فاتورة MTN"

        history.append({'date': t['date'] or "", 'type': trans_desc, 'detail': detail_html, 'amount': f"- {amt:,.2f} وحدة", 'status': stat_html, 'color': 'danger', 'b_before': b_before, 'b_after': b_after})

    for b in u_bills:
        stat_html = "<span class='text-success'>مكتمل</span>" if b['status'] == 'COMPLETED' else "<span class='text-danger'>مرفوض</span>" if b['status'] == 'REJECTED' else "<span class='text-warning'>بالانتظار</span>"
        price = float(b['price'] or 0)
        b_before = float(dict(b).get('balance_before') or 0)
        b_after = float(dict(dict(b)).get('balance_after') or 0)
        reject_reason = dict(b).get('reject_reason')
        if b['status'] == 'REJECTED' and reject_reason:
            detail_html = f"<div class='d-flex flex-column'><span>{b['service_name']}</span><span class='text-danger mt-1 fw-normal' style='font-size: 11px;'><i class='fas fa-exclamation-circle opacity-75'></i> {reject_reason}</span></div>"
        else: detail_html = f"<span>{b['service_name']}</span>"
        history.append({'date': b['date'] or "", 'type': "فاتورة (يدوية)", 'detail': detail_html, 'amount': f"- {price:,.2f} ل.س", 'status': stat_html, 'color': 'warning', 'b_before': b_before, 'b_after': b_after})

    for d in u_deps:
        actual_paid = float(d['actual_paid'] or 0)
        d_amount = float(d['amount'] or 0)
        b_before = float(dict(d).get('user_balance_before') or 0)
        b_after = float(dict(d).get('user_balance_after') or 0)

        # 💡 تمييز مصدر الشحن (بصمة المنصة)
        platform = dict(d).get('platform', 'web')
        plat_icon = "<br><small class='text-primary fw-bold mt-1 d-block' style='font-size:10px;'><i class='fab fa-telegram-plane'></i> عبر التلغرام</small>" if platform == 'telegram' else "<br><small class='text-secondary fw-bold mt-1 d-block' style='font-size:10px;'><i class='fas fa-laptop'></i> عبر الموقع</small>"

        if d['wallet_type'] == 'debt_payment':
            history.append({'id': d['id'], 'source': 'deposit', 'date': d['date'] or "", 'type': 'تسديد ذمة', 'detail': f"دفعة نقدية{plat_icon}", 'amount': f"+ {actual_paid:,.2f} ل.س", 'status': "<span class='text-success'>مقبوض</span>", 'color': 'success', 'amount_raw': d_amount, 'paid_raw': actual_paid, 'b_before': b_before, 'b_after': b_after})
        elif d['wallet_type'] == 'free_debt':
            history.append({'id': d['id'], 'source': 'deposit', 'date': d['date'] or "", 'type': 'دين حر', 'detail': f"تسجيل دين{plat_icon}", 'amount': f"- {actual_paid:,.2f} ل.س", 'status': "<span class='text-danger'>دين مقيد</span>", 'color': 'danger', 'amount_raw': d_amount, 'paid_raw': actual_paid, 'b_before': b_before, 'b_after': b_after})
        else:
            w_name, sign, color = ("وحدات" if d['wallet_type'] == 'units' else "فواتير", "+" if d_amount > 0 else "-", "success" if d_amount > 0 else "danger")
            pay_type = " (آجل)" if d['is_debt'] else " (كاش)"
            if d_amount < 0: pay_type = ""
            amt_str = f"{sign} {abs(d_amount):,.2f} وحدة" if d['wallet_type'] == 'units' else f"{sign} {abs(d_amount):,.2f} ل.س"
            history.append({'id': d['id'], 'source': 'deposit', 'date': d['date'] or "", 'type': "إدارة رصيد", 'detail': f"محفظة {w_name}{pay_type}{plat_icon}", 'amount': amt_str, 'status': f"<span class='text-{color}'>تم</span>", 'color': color, 'amount_raw': d_amount, 'paid_raw': actual_paid, 'b_before': b_before, 'b_after': b_after})

    history.sort(key=lambda x: x['date'], reverse=True)
    search_term = request.args.get('search', '').strip().lower()
    if search_term: history = [h for h in history if search_term in str(h['detail']).lower() or search_term in str(h['type']).lower()]

    try: page = int(request.args.get('page', 1))
    except ValueError: page = 1
    per_page = 10
    total_pages = max(1, (len(history) + per_page - 1) // per_page)
    if page > total_pages: page = total_pages
    page_data = history[(page-1)*per_page : page*per_page]

    rows = ""
    for h in page_data:
        actions = "-"
        if h.get('source') == 'deposit':
            edit_btn = f"<button type='button' class='btn btn-sm btn-outline-primary rounded-pill px-2' onclick='openEditLogModal({h['id']}, {h['amount_raw']}, {h['paid_raw']})'><i class='fas fa-pen'></i></button>"
            del_btn = f"<form action='/delete_log/{h['id']}' method='POST' class='d-inline' onsubmit='return confirm(\"حذف العملية وعكس الأرصدة تلقائياً؟\");'><button type='submit' class='btn btn-sm btn-outline-danger rounded-pill px-2 ms-1'><i class='fas fa-trash'></i></button></form>"
            actions = f"<div class='d-flex gap-1 justify-content-center'>{edit_btn}{del_btn}</div>"

        bb_str = f"{h['b_before']:,.2f}" if h.get('b_before') != 0 else "<span class='text-muted opacity-50'>---</span>"
        ba_str = f"{h['b_after']:,.2f}" if h.get('b_after') != 0 else "<span class='text-muted opacity-50'>---</span>"

        rows += f"<tr><td dir='ltr' class='text-muted small align-middle'>{h['date']}</td><td class='align-middle'><span class='badge bg-{h['color']} bg-opacity-10 text-{h['color']} border border-{h['color']}'>{h['type']}</span></td><td class='fw-bold text-start align-middle'>{h['detail']}</td><td dir='ltr' class='fw-black text-{h['color']} align-middle'>{h['amount']}</td><td dir='ltr' class='fw-bold text-secondary align-middle'>{bb_str}</td><td dir='ltr' class='fw-black text-dark align-middle'>{ba_str}</td><td class='align-middle'>{h['status']}</td><td class='align-middle'>{actions}</td></tr>"

    if not rows: rows = "<tr><td colspan='8' class='text-muted py-4'>لا يوجد حركات.</td></tr>"

    pagination_html = f"<div class='d-flex justify-content-center gap-2 mt-3'>"
    if page > 1: pagination_html += f"<a href='?search={search_term}&page={page-1}&start_date={start_date}&end_date={end_date}' class='btn btn-sm btn-outline-primary fw-bold'>السابق</a>"
    pagination_html += f"<span class='badge bg-primary py-2 px-3'>صفحة {page} من {total_pages}</span>"
    if page < total_pages: pagination_html += f"<a href='?search={search_term}&page={page+1}&start_date={start_date}&end_date={end_date}' class='btn btn-sm btn-outline-primary fw-bold'>التالي</a>"
    pagination_html += "</div>"

    bal_val = float(user['balance'] or 0)
    bills_val = float(user['bills_balance'] or 0)
    debt_val = float(user['debt_balance'] or 0)
    sell_val = float(user['custom_sell_price'] or 1.05)
    real_name_val = user['real_name'] or "بدون اسم"
    user_dict = dict(user)
    limit_val = float(user_dict.get('debt_limit', 50000) or 50000)

    content = f'''
    <div class="mb-4 d-flex justify-content-between align-items-center">
        <a href="/users" class="btn btn-sm btn-light fw-bold shadow-sm"><i class="fas fa-arrow-right me-1"></i> عودة للعملاء</a>
    </div>

    <div class="row g-4 mb-4">
        <div class="col-md-4">
            <div class="card-bank h-100 bg-white border-primary border-top border-4">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h4 class="fw-black text-dark m-0"><i class="fas fa-user-circle text-primary me-2"></i> {real_name_val}</h4>
                </div>
                <hr>
                <form method="POST" class="mt-3">
                    <label class="text-muted fw-bold small mb-1">تسعيرة المبيع المخصصة له (ل.س):</label>
                    <input type="number" step="0.01" name="custom_sell_price" value="{sell_val}" class="form-control border-primary fw-bold mb-3" required>
                    <label class="text-danger fw-bold small mb-1">سقف الدين المسموح (ل.س):</label>
                    <input type="number" step="0.01" name="debt_limit" value="{limit_val}" class="form-control border-danger fw-bold mb-3" required>
                    <button class="btn btn-primary fw-bold w-100 shadow-sm" type="submit"><i class="fas fa-save me-1"></i> حفظ التعديلات</button>
                </form>
            </div>
        </div>
        <div class="col-md-4">
            <div class="row g-3 h-100">
                <div class="col-12"><div class="card-bank h-100 m-0 text-center" style="background: linear-gradient(135deg, #1e3a8a, #3b82f6);"><p class="text-white-50 fw-bold mb-2">رصيد الوحدات الحالي 📱</p><h2 class="text-white fw-black m-0">{bal_val:,.2f}</h2></div></div>
                <div class="col-12"><div class="card-bank h-100 m-0 text-center" style="background: linear-gradient(135deg, #065f46, #10b981);"><p class="text-white-50 fw-bold mb-2">رصيد الفواتير الحالي 🧾</p><h2 class="text-white fw-black m-0">{bills_val:,.2f}</h2></div></div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="card-bank h-100 text-center border-danger border-start border-4 bg-light">
                <p class="text-danger fw-bold mb-2"><i class="fas fa-book me-1"></i> رصيد الديون المستحقة 📝</p><h2 class="fw-black text-danger m-0 mb-3">{debt_val:,.2f} <small class="fs-6">ل.س</small></h2><hr>
                <form action="/pay_debt" method="POST" class="d-flex gap-2"><input type="hidden" name="uid" value="{user['user_id']}"><input name="amount" type="number" step="0.01" class="form-control border-danger shadow-sm" placeholder="تسديد دفعة..." required><button type="submit" class="btn btn-danger fw-bold shadow-sm">قبض</button></form>
            </div>
        </div>
    </div>

    <div class="card-bank bg-white mb-4 border-warning border-start border-5 shadow-sm p-4" style="border-radius: 20px;">
        <div class="d-flex justify-content-between align-items-center mb-4 flex-wrap">
            <h5 class="fw-black text-dark m-0"><i class="fas fa-tachometer-alt text-warning me-2"></i> ملخص سحبات الزبون (آلة الزمن)</h5>
            <form method="GET" class="d-flex align-items-center gap-2 mt-2 mt-md-0 bg-light p-2 rounded-pill border shadow-sm">
                <input type="hidden" name="search" value="{search_term}">
                <span class="text-muted small fw-bold ms-2">من:</span>
                <input type="date" name="start_date" value="{start_date}" class="form-control form-control-sm border-0 bg-transparent fw-bold text-dark" style="width: 120px;" required>
                <span class="text-muted small fw-bold border-start ps-2">إلى:</span>
                <input type="date" name="end_date" value="{end_date}" class="form-control form-control-sm border-0 bg-transparent fw-bold text-dark" style="width: 120px;" required>
                <button type="submit" class="btn btn-sm btn-warning fw-bold shadow-sm text-dark rounded-pill px-3"><i class="fas fa-filter"></i> فلترة</button>
                <button type="button" class="btn btn-sm btn-outline-secondary fw-bold rounded-pill px-3" onclick="document.querySelector('input[name=start_date]').value='{today_date}'; document.querySelector('input[name=end_date]').value='{today_date}'; this.form.submit();">اليوم</button>
            </form>
        </div>

        <div class="row g-3 text-center mb-2">
            <div class="col-6 col-md-4">
                <div class="p-3 rounded-4 shadow-sm h-100" style="background: linear-gradient(135deg, #ef4444 0%, #b91c1c 100%); border: 1px solid rgba(255,255,255,0.2);">
                    <div class="text-white-50 fw-bold mb-1" style="font-size: 11px;"><i class="fas fa-mobile-alt"></i> رصيد سيريتل</div>
                    <h4 class="fw-black text-white m-0" dir="ltr">{s_bal_range:,.0f}</h4>
                </div>
            </div>
            <div class="col-6 col-md-4">
                <div class="p-3 rounded-4 shadow-sm h-100" style="background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); border: 1px solid rgba(255,255,255,0.2);">
                    <div class="text-white-50 fw-bold mb-1" style="font-size: 11px; color: rgba(0,0,0,0.6)!important;"><i class="fas fa-mobile-alt"></i> رصيد MTN</div>
                    <h4 class="fw-black text-dark m-0" dir="ltr">{m_bal_range:,.0f}</h4>
                </div>
            </div>

            <div class="col-6 col-md-4">
                <div class="p-3 rounded-4 shadow-sm h-100" style="background: linear-gradient(135deg, #be123c 0%, #7f1d1d 100%); border: 1px solid rgba(255,255,255,0.2);">
                    <div class="text-white-50 fw-bold mb-1" style="font-size: 11px;"><i class="fas fa-money-bill-wave"></i> كاش سيريتل</div>
                    <h4 class="fw-black text-white m-0" dir="ltr">{s_cash_range:,.0f}</h4>
                </div>
            </div>
            <div class="col-6 col-md-4">
                <div class="p-3 rounded-4 shadow-sm h-100" style="background: linear-gradient(135deg, #d97706 0%, #92400e 100%); border: 1px solid rgba(255,255,255,0.2);">
                    <div class="text-white-50 fw-bold mb-1" style="font-size: 11px;"><i class="fas fa-money-bill-wave"></i> كاش MTN</div>
                    <h4 class="fw-black text-white m-0" dir="ltr">{m_cash_range:,.0f}</h4>
                </div>
            </div>

            <div class="col-6 col-md-4">
                <div class="p-3 rounded-4 shadow-sm h-100" style="background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%); border: 1px solid rgba(255,255,255,0.2);">
                    <div class="text-white-50 fw-bold mb-1" style="font-size: 11px;"><i class="fas fa-receipt"></i> فواتير سيريتل</div>
                    <h4 class="fw-black text-white m-0" dir="ltr">{s_bill_range:,.0f}</h4>
                </div>
            </div>
            <div class="col-6 col-md-4">
                <div class="p-3 rounded-4 shadow-sm h-100" style="background: linear-gradient(135deg, #10b981 0%, #047857 100%); border: 1px solid rgba(255,255,255,0.2);">
                    <div class="text-white-50 fw-bold mb-1" style="font-size: 11px;"><i class="fas fa-receipt"></i> فواتير MTN</div>
                    <h4 class="fw-black text-white m-0" dir="ltr">{m_bill_range:,.0f}</h4>
                </div>
            </div>
        </div>
    </div>

    <div class="card-bank">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h5 class="fw-bold text-dark m-0"><i class="fas fa-book-open text-primary me-2"></i> السجل الشامل (دفتر الأستاذ)</h5>
            <form method="GET" class="d-flex gap-2">
                <input type="hidden" name="start_date" value="{start_date}">
                <input type="hidden" name="end_date" value="{end_date}">
                <input type="text" name="search" value="{search_term}" class="form-control form-control-sm border-0 bg-light" placeholder="بحث...">
                <button type="submit" class="btn btn-sm btn-primary"><i class="fas fa-search"></i></button>
            </form>
        </div>
        <div class="table-responsive"><table class="table text-center align-middle"><thead><tr><th>التاريخ والوقت</th><th>النوع</th><th class="text-start">التفاصيل</th><th>المبلغ</th><th class="text-secondary bg-light border-end">الرصيد قبل</th><th class="text-dark bg-light">الرصيد بعد</th><th>الحالة</th><th>إدارة</th></tr></thead><tbody>{rows}</tbody></table></div>
        {pagination_html}
    </div>
    '''
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='user_profile')

@app.route('/delete_log/<int:log_id>', methods=['POST'])
def delete_log(log_id):
    if session.get('logged_in') and session.get('role') == 'admin':
        conn = get_db_connection()
        log = conn.execute("SELECT * FROM deposit_logs WHERE id=?", (log_id,)).fetchone()
        if log and log['by_admin_id'] != 0:
            uid, amt, paid, w_type, is_debt = log['user_id'], round(log['amount'], 3), round(log['actual_paid'], 3), log['wallet_type'], log['is_debt']
            
            if w_type == 'debt_payment': 
                conn.execute("UPDATE users SET debt_balance = ROUND(debt_balance + ?, 3) WHERE user_id=?", (paid, uid))
            elif w_type == 'free_debt': 
                conn.execute("UPDATE users SET debt_balance = ROUND(debt_balance - ?, 3) WHERE user_id=?", (paid, uid))
            elif w_type in ['units', 'bills']:
                bal_col = 'balance' if w_type == 'units' else 'bills_balance'
                conn.execute(f"UPDATE users SET {bal_col} = ROUND({bal_col} - ?, 3) WHERE user_id=?", (amt, uid))
                if is_debt == 1:
                    if amt > 0: conn.execute("UPDATE users SET debt_balance = ROUND(debt_balance - ?, 3) WHERE user_id=?", (paid, uid))
                    else: conn.execute("UPDATE users SET debt_balance = ROUND(debt_balance + ?, 3) WHERE user_id=?", (paid, uid))
            conn.execute("DELETE FROM deposit_logs WHERE id=?", (log_id,)); conn.commit()
        conn.close()
    return redirect(request.referrer or '/deposits')

@app.route('/edit_log', methods=['POST'])
def edit_log():
    if session.get('logged_in') and session.get('role') == 'admin':
        try:
            log_id = int(request.form.get('log_id'))
            new_amt = round(float(request.form.get('amount', 0)), 3)
            new_paid = round(float(request.form.get('actual_paid', 0)), 3)

            conn = get_db_connection()
            log = conn.execute("SELECT * FROM deposit_logs WHERE id=?", (log_id,)).fetchone()
            if log and log['by_admin_id'] != 0:
                uid, old_amt, old_paid, w_type, is_debt = log['user_id'], round(log['amount'], 3), round(log['actual_paid'], 3), log['wallet_type'], log['is_debt']

                if w_type == 'debt_payment':
                    diff_paid = round(new_paid - old_paid, 3)
                    conn.execute("UPDATE users SET debt_balance = ROUND(debt_balance - ?, 3) WHERE user_id=?", (diff_paid, uid))
                    conn.execute("UPDATE deposit_logs SET actual_paid=? WHERE id=?", (new_paid, log_id))
                elif w_type == 'free_debt':
                    diff_paid = round(new_paid - old_paid, 3)
                    conn.execute("UPDATE users SET debt_balance = ROUND(debt_balance + ?, 3) WHERE user_id=?", (diff_paid, uid))
                    conn.execute("UPDATE deposit_logs SET actual_paid=? WHERE id=?", (new_paid, log_id))
                elif w_type in ['units', 'bills']:
                    amt_diff = round(new_amt - old_amt, 3)
                    paid_diff = round(new_paid - old_paid, 3)
                    bal_col = 'balance' if w_type == 'units' else 'bills_balance'

                    conn.execute(f"UPDATE users SET {bal_col} = ROUND({bal_col} + ?, 3) WHERE user_id=?", (amt_diff, uid))
                    if is_debt == 1:
                        if old_amt > 0: conn.execute("UPDATE users SET debt_balance = ROUND(debt_balance + ?, 3) WHERE user_id=?", (paid_diff, uid))
                        else: conn.execute("UPDATE users SET debt_balance = ROUND(debt_balance - ?, 3) WHERE user_id=?", (paid_diff, uid))
                    conn.execute("UPDATE deposit_logs SET amount=?, actual_paid=? WHERE id=?", (new_amt, new_paid, log_id))
                conn.commit()
            conn.close()
        except (ValueError, TypeError): pass
    return redirect(request.referrer or '/deposits')

@app.route('/pay_debt', methods=['POST'])
def pay_debt():
    if not session.get('logged_in'): return redirect('/')
    uid_str, amt_str = request.form.get('uid'), request.form.get('amount')
    if uid_str and amt_str:
        try:
            uid, amt = int(uid_str), float(amt_str)
            if amt > 0:
                conn = get_db_connection()
                conn.execute("UPDATE users SET debt_balance = debt_balance - ? WHERE user_id = ?", (amt, uid))
                conn.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (uid, session['user_id'], 0, amt, 'debt_payment', 0, 0, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit(); conn.close()
                try: bot.send_message(uid, f"✅ *إشعار تسديد دفعة:*\nتم استلام مبلغ `{amt:g}` ل.س وخصمه من حساب الذمم الخاص بك.", parse_mode="Markdown")
                except: pass
        except ValueError: pass
    return redirect(request.referrer or '/users')

@app.route('/transactions')
def transactions_page():
    if not session.get('logged_in'): return redirect('/')
    tab = request.args.get('tab', 'syriatel')
    search = request.args.get('search', '').strip()

    try: page = int(request.args.get('page', 1))
    except ValueError: page = 1
    per_page = 20
    offset = (page - 1) * per_page
    conn = get_db_connection()

    if tab in ['syriatel', 'mtn']:
        network = 'Syriatel' if tab == 'syriatel' else 'MTN'
        query_base = "FROM transactions t LEFT JOIN users u ON t.user_id = u.user_id WHERE t.network=?"
        params = [network]
        if search:
            query_base += " AND (t.phone LIKE ? OR u.real_name LIKE ? OR t.id=?)"
            params.extend([f"%{search}%", f"%{search}%", search.replace('#', '')])
        total_count = conn.execute(f"SELECT count(*) {query_base}", params).fetchone()[0]
        total_pages = max(1, (total_count + per_page - 1) // per_page)
        items = conn.execute(f"SELECT t.*, u.real_name {query_base} ORDER BY t.id DESC LIMIT ? OFFSET ?", params + [per_page, offset]).fetchall()
    else:
        query_base = "FROM manual_orders o LEFT JOIN users u ON o.user_id = u.user_id WHERE 1=1"
        params = []
        if search:
            query_base += " AND (o.target_info LIKE ? OR u.real_name LIKE ? OR o.id=?)"
            params.extend([f"%{search}%", f"%{search}%", search.replace('#', '')])
        total_count = conn.execute(f"SELECT count(*) {query_base}", params).fetchone()[0]
        total_pages = max(1, (total_count + per_page - 1) // per_page)
        items = conn.execute(f"SELECT o.*, u.real_name as real_name {query_base} ORDER BY o.id DESC LIMIT ? OFFSET ?", params + [per_page, offset]).fetchall()

    s_global = conn.execute("SELECT sum(amount) FROM transactions WHERE status='SUCCESS' AND network='Syriatel'").fetchone()[0] or 0
    m_global = conn.execute("SELECT sum(amount) FROM transactions WHERE status='SUCCESS' AND network='MTN'").fetchone()[0] or 0
    conn.close()

    rows = ""
    if tab in ['syriatel', 'mtn']:
        for i, t in enumerate(items, start=offset + 1):
            if t['status'] == 'SUCCESS': badge, action = "<span class='badge bg-success rounded-pill'>ناجحة</span>", "-"
            elif t['status'] == 'QUEUED': badge, action = "<span class='badge bg-warning text-dark rounded-pill'>قيد التنفيذ</span>", "-"
            elif t['status'] == 'PROCESSING': badge, action = "<span class='badge bg-info text-dark rounded-pill'><i class='fas fa-cog fa-spin me-1'></i> جاري العمل</span>", "-"
            elif t['status'] == 'REFUNDED': badge, action = "<span class='badge bg-secondary text-white rounded-pill'>مستردة</span>", "-"
            elif t['status'] == 'MANUAL_CHECK':
                badge = "<span class='badge bg-warning text-dark rounded-pill shadow-sm'><i class='fas fa-search me-1'></i> مراجعة يدوية</span>"
                action = f"<div class='d-flex gap-1 justify-content-center'><a href='/force_success/{t['id']}' class='btn btn-sm btn-success shadow-sm fw-bold' onclick=\"return confirm('تأكيد النجاح وخصم الرصيد؟');\">تأكيد</a><button type='button' class='btn btn-sm btn-danger shadow-sm fw-bold' onclick='openRefundModal({t['id']}, \"{t['phone']}\")'>استرداد</button><a href='/retry_trans/{t['id']}' class='btn btn-sm btn-primary shadow-sm fw-bold' onclick=\"return confirm('إعادة إرسال للموبايل؟');\"><i class='fas fa-sync-alt'></i></a></div>"
            else: badge, action = "<span class='badge bg-danger rounded-pill'>فشل</span>", "-"
            rows += f"<tr><td class='fw-black text-muted fs-5'>{i}</td><td class='text-muted small'>#{t['id']}</td><td><a href='/user/{t['user_id']}' class='user-link fw-bold'>{t['real_name']}</a></td><td class='fw-bold text-dark' dir='ltr'>{t['phone']}</td><td class='fw-black text-primary'>{t['amount']:,.2f}</td><td>{badge}</td><td dir='ltr' class='text-muted small'>{t['date']}</td><td>{action}</td></tr>"
        table_headers = "<th>#</th><th>المرجع</th><th>العميل</th><th>الرقم</th><th>الكمية (وحدة)</th><th>الحالة</th><th>التاريخ والوقت</th><th>إدارة</th>"
    else:
        for i, o in enumerate(items, start=offset + 1):
            o_dict = dict(o)
            if o_dict['status'] == 'PENDING': badge = "<span class='badge bg-warning text-dark rounded-pill'>بانتظار التنفيذ</span>"
            elif o_dict['status'] == 'COMPLETED': badge = "<span class='badge bg-success rounded-pill'>مكتملة</span>"
            else: badge = f"<span class='badge bg-danger rounded-pill'>مرفوضة</span><br><small class='text-muted mt-1'>{o_dict.get('reject_reason','')}</small>"
            rows += f"<tr><td class='fw-black text-muted fs-5'>{i}</td><td class='text-muted small'>#{o_dict['id']}</td><td><a href='/user/{o_dict['user_id']}' class='user-link fw-bold'>{o_dict['real_name']}</a></td><td class='text-primary fw-bold'>{o_dict['service_name']}</td><td dir='ltr' class='fw-bold text-dark'>{o_dict['target_info']}</td><td class='fw-bold text-success'>{o_dict['price']:,.2f} ل.س</td><td>{badge}</td><td dir='ltr' class='text-muted small'>{o_dict['date']}</td></tr>"
        table_headers = "<th>#</th><th>المرجع</th><th>العميل</th><th>الخدمة المطلوبة</th><th>الرقم المطلوب</th><th>المبلغ</th><th>الحالة والسبب</th><th>التاريخ والوقت</th>"

    if not rows: rows = f"<tr><td colspan='8' class='text-muted py-4'>لا توجد نتائج مطابقة للبحث.</td></tr>"

    pagination_html = f"<div class='d-flex justify-content-center gap-2 mt-4'>"
    if page > 1: pagination_html += f"<a href='?tab={tab}&search={search}&page={page-1}' class='btn btn-outline-primary fw-bold'>السابق</a>"
    pagination_html += f"<span class='badge bg-primary py-2 px-3'>صفحة {page} من {total_pages}</span>"
    if page < total_pages: pagination_html += f"<a href='?tab={tab}&search={search}&page={page+1}' class='btn btn-outline-primary fw-bold'>التالي</a>"
    pagination_html += "</div>"

    modal_refund = """
    <div class="modal fade" id="refundTransModal" tabindex="-1" aria-hidden="true">
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content border-0 shadow-lg rounded-4">
          <div class="modal-header bg-danger text-white border-0 rounded-top-4">
            <h5 class="modal-title fw-bold"><i class="fas fa-undo me-2"></i> إلغاء الطلب واسترداد الرصيد</h5>
            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
          </div>
          <form action="/force_refund" method="POST">
              <div class="modal-body p-4">
                <input type="hidden" name="tid" id="refund_tid">
                <p class="text-muted fw-bold mb-3">إلغاء التحويل للرقم: <span id="refund_phone" class="text-danger fs-5" dir="ltr"></span></p>
                <label class="form-label text-danger fw-bold mb-2">سبب الاسترداد (سيظهر للزبون):</label>
                <input type="text" name="reason" class="form-control bg-light border-0 shadow-sm p-3 fw-bold" placeholder="مثال: الرقم مفصول، يرجى التأكد..." required>
              </div>
              <div class="modal-footer border-0 pb-4 pe-4">
                <button type="button" class="btn btn-light shadow-sm fw-bold" data-bs-dismiss="modal">تراجع</button>
                <button type="submit" class="btn btn-danger px-4 fw-bold shadow-sm">تأكيد الإلغاء 🗑️</button>
              </div>
          </form>
        </div>
      </div>
    </div>
    <script>
    function openRefundModal(tid, phone) {
        document.getElementById('refund_tid').value = tid;
        document.getElementById('refund_phone').innerText = phone;
        new bootstrap.Modal(document.getElementById('refundTransModal')).show();
    }
    </script>
    """

    content = f'''
    <div class="row mb-4"><div class="col-md-6"><div class="card-bank border-danger border-start border-4 m-0"><p class="text-danger fw-bold mb-1"><i class="fas fa-sim-card"></i> إجمالي مبيعات سيريتل الناجحة</p><h3 class="fw-black m-0">{s_global:,.2f} <small class="fs-6">وحدة</small></h3></div></div><div class="col-md-6"><div class="card-bank border-warning border-start border-4 m-0"><p class="text-warning text-dark fw-bold mb-1" style="color:#d97706!important;"><i class="fas fa-sim-card"></i> إجمالي مبيعات MTN الناجحة</p><h3 class="fw-black m-0">{m_global:,.2f} <small class="fs-6">وحدة</small></h3></div></div></div>
    <div class="card-bank border-primary border-start border-4 mb-4 bg-white shadow-sm"><form method="GET" action="/transactions" class="row g-2 align-items-center"><input type="hidden" name="tab" value="{tab}"><div class="col-md-9"><input type="text" name="search" value="{search}" class="form-control form-control-lg bg-light border-0" placeholder="بحث برقم المرجع، رقم الموبايل/الأرضي/الكود، أو اسم العميل..."></div><div class="col-md-3"><button type="submit" class="btn btn-primary btn-lg w-100 fw-bold"><i class="fas fa-search me-2"></i> بحث ذكي</button></div></form></div>
    <ul class="nav nav-pills nav-fill bg-white p-2 rounded-4 shadow-sm mb-4 border"><li class="nav-item"><a class="nav-link fw-bold fs-5 {'active shadow' if tab == 'syriatel' else 'text-muted'}" href="?tab=syriatel&search={search}">🔴 مبيعات سيريتل</a></li><li class="nav-item"><a class="nav-link fw-bold fs-5 {'active shadow text-dark bg-warning' if tab == 'mtn' else 'text-muted'}" href="?tab=mtn&search={search}">🟡 مبيعات MTN</a></li><li class="nav-item"><a class="nav-link fw-bold fs-5 {'active shadow bg-success' if tab == 'bills' else 'text-muted'}" href="?tab=bills&search={search}">🧾 الفواتير والخدمات</a></li></ul>
    <div class="card-bank"><h4 class="fw-bold mb-4 text-primary"><i class="fas fa-list-ul me-2"></i> السجل الشامل للمبيعات</h4><div class="table-responsive"><table class="table text-center align-middle"><thead><tr>{table_headers}</tr></thead><tbody>{rows}</tbody></table></div>{pagination_html}</div>
    {modal_refund}
    '''
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='transactions')

@app.route('/force_success/<int:tid>')
def force_success(tid):
    if session.get('logged_in') and session.get('role') == 'admin':
        conn = get_db_connection(); t = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
        if t and t['status'] == 'MANUAL_CHECK':
            network = t['network']
            deduct_val = float(t['amount'])
            sim_bal = float(get_setting(f'sim_balance_{network}') or 0)
            set_setting(f'sim_balance_{network}', str(sim_bal - deduct_val))

            conn.execute("UPDATE transactions SET status='SUCCESS', ussd_response='تم التأكيد يدوياً من الإدارة ✅' WHERE id=?", (tid,)); conn.commit()
            try: bot.send_message(t['user_id'], f"╭━━━ ✅ تم التحويل ━━━╮\n🎯 الرقم: `{t['phone']}`\n💰 المبلغ: *{t['amount']:g}* وحدة\n┣━━━━━━━━━━━━━┫\n📝 ملاحظة: تم التأكيد بنجاح من الإدارة.\n╰━━━━━━━━━━━━━╯", parse_mode="Markdown")
            except: pass
        conn.close()
    return redirect(request.referrer or '/transactions')

@app.route('/force_refund', methods=['POST'])
def force_refund():
    if session.get('logged_in') and session.get('role') == 'admin':
        tid = request.form.get('tid')
        reason = request.form.get('reason', 'تم الإلغاء يدوياً من قبل الإدارة ❌')

        conn = get_db_connection()
        cur = conn.cursor()

        # 🛡️ التحديث الآمن: نغير الحالة للمسترد فقط إذا كانت مراجعة يدوية، ليتم التنفيذ مرة واحدة حصراً
        cur.execute("UPDATE transactions SET status='REFUNDED', ussd_response=? WHERE id=? AND status='MANUAL_CHECK'", (reason, tid))

        if cur.rowcount > 0: # إذا تمت العملية ولم يتم استردادها مسبقاً
            t = cur.execute("SELECT user_id, amount, phone FROM transactions WHERE id=?", (tid,)).fetchone()
            cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (t['amount'], t['user_id']))
            conn.commit()

            try:
                msg = f"⚠️ *إشعار استرداد رصيد*\nتم إعادة مبلغ `{t['amount']:g}` وحدة لحسابك.\n📱 *العملية:* تحويل لرقم `{t['phone']}`\n📝 *السبب:* {reason}"
                bot.send_message(t['user_id'], msg, parse_mode="Markdown")
            except: pass

        conn.close()
    return redirect(request.referrer or '/transactions')

@app.route('/retry_trans/<int:tid>')
def retry_trans(tid):
    if session.get('logged_in') and session.get('role') == 'admin':
        conn = get_db_connection(); t = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
        if t and t['status'] == 'MANUAL_CHECK': 
            new_date = datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE transactions SET status='QUEUED', ussd_response='Waiting', date=? WHERE id=?", (new_date, tid))
            conn.commit()
        conn.close()
    return redirect(request.referrer or '/transactions')


def check_stuck_transactions():
    """مراقب زمني لاصطياد التحويلات التي علقت في الشبكة لأكثر من 3 دقائق"""
    try:
        conn = get_db_connection()
        time_limit = (datetime.now(local_tz) - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")
        
        # الفحص يشمل حصراً حالة PROCESSING (تم سحبها من الموبايل)
        stuck = conn.execute("SELECT * FROM transactions WHERE status = 'PROCESSING' AND date < ?", (time_limit,)).fetchall()
        
        for st in stuck:
            # تحويل الحالة لمراجعة يدوية حتى لا يتكرر الإنذار
            conn.execute("UPDATE transactions SET status='MANUAL_CHECK', ussd_response='تأخير في استجابة شبكة الاتصالات (تم التعليق)' WHERE id=?", (st['id'],))
            conn.commit()
            
            # تجهيز أزرار الإنقاذ الثلاثية للمدير
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("✅ تأكيد النجاح يدوياً", callback_data=f"admact_success_{st['id']}"),
                types.InlineKeyboardButton("🔄 إعادة إرسال للموبايل", callback_data=f"admact_retry_{st['id']}"),
                types.InlineKeyboardButton("❌ إلغاء واسترداد للزبون", callback_data=f"admact_refund_{st['id']}")
            )
            
            # --- 💡 المترجم الذكي (تم الإصلاح هنا) 💡 ---
            st_dict = dict(st)
            srv_type = st_dict.get('service_type', '')
            net = st_dict.get('network', '')
            
            if srv_type in ['Jahez', 'Bulk'] or not srv_type:
                type_name = "وحدات سيريتل 🔴" if net == 'Syriatel' else "وحدات MTN 🟡"
            elif srv_type == 'cash_syriatel': type_name = "كاش سيريتل 🔴"
            elif srv_type == 'cash_mtn': type_name = "كاش MTN 🟡"
            elif srv_type == 'bill_syriatel': type_name = "فواتير سيريتل 🔴"
            elif srv_type == 'bill_mtn': type_name = "فواتير MTN 🟡"
            else: type_name = f"{net} - {srv_type}"
            # --------------------------------------------
            
            # إرسال الإنذار للمدير بنص يعكس المشكلة الحقيقية والنوع
            try:
                bot.send_message(ADMIN_TG_ID, f"🚨 *إنذار طوارئ: تحويلة عالقة!*\nالموبايل سحب الطلب `#{st['id']}` ومرت 3 دقائق ولم ترد شبكة الاتصالات.\n\nنوع العملية: *{type_name}*\nالرقم: `{st['phone']}`\n💰 المبلغ: `{st['amount']:g}`\n\n👇 يرجى المراجعة واتخاذ القرار:", parse_mode="Markdown", reply_markup=markup)
            except: pass
            
        conn.close()
    except: pass

# =============================================================
# 🚀🚀🚀 معمارية القائمة البيضاء لاستقبال الردود من الموبايل 🚀🚀🚀
# =============================================================
@app.route('/get_pending_transactions', methods=['GET'])
def get_pending():
    check_stuck_transactions() # 👈 تفعيل المراقب الزمني
    
    # 💡 1. تسجيل نبض الموبايل
    set_setting('last_mobile_heartbeat', str(time.time()))

    # 💡 2. فحص طلبات "تحديث الرصيد" (باستخدام أرقام ID وهمية ومدروسة)
    cmd_s = get_setting('pending_cmd_Syriatel')
    if cmd_s and cmd_s not in ['0', '']:
        set_setting('pending_cmd_Syriatel', '0')
        return jsonify({"status": "success", "id": 999999901, "phone": "0000", "amount": cmd_s, "network": "Syriatel"})

    cmd_m = get_setting('pending_cmd_MTN')
    if cmd_m and cmd_m not in ['0', '']:
        set_setting('pending_cmd_MTN', '0')
        return jsonify({"status": "success", "id": 999999902, "phone": "0000", "amount": cmd_m, "network": "MTN"})

    # 💡 3. سحب الطلبات العادية للزبائن
    conn = get_db_connection()
    trans = conn.execute("SELECT t.*, u.is_vip FROM transactions t JOIN users u ON t.user_id = u.user_id WHERE t.status='QUEUED' ORDER BY u.is_vip DESC, t.id ASC LIMIT 1").fetchone()

    if trans:
        conn.execute("UPDATE transactions SET status='PROCESSING' WHERE id=?", (trans['id'],))
        conn.commit()

        network = trans['network']
        amt_float = float(trans['amount'])
        phone_str = str(trans['phone']).strip()

        # إذا كان كاش أو فواتير (له كود جاهز)
        stored_ussd = str(trans['ussd_amount']).strip()
        if stored_ussd and '*' in stored_ussd and '#' in stored_ussd:
            ussd_val = stored_ussd
        else:
            # معالجة الوحدات العادية (مع حماية الفولاذ ضد نقص عواميد قاعدة البيانات)
            srv_name = 'transfer_syriatel' if network == 'Syriatel' else 'transfer_mtn'
            try:
                code_row = conn.execute("SELECT ussd_format, secret_pin FROM ussd_codes WHERE service_name=?", (srv_name,)).fetchone()
                ussd_format = code_row['ussd_format'] if code_row and code_row['ussd_format'] else ""
                pin_val = code_row['secret_pin'] if code_row and code_row['secret_pin'] else ""
            except Exception:
                try:
                    code_row = conn.execute("SELECT ussd_format FROM ussd_codes WHERE service_name=?", (srv_name,)).fetchone()
                    ussd_format = code_row['ussd_format'] if code_row and code_row['ussd_format'] else ""
                except Exception:
                    ussd_format = ""
                pin_val = ""

            final_amt_str = str(int(round(amt_float * 100))) if network == 'Syriatel' else str(int(round(amt_float)))

            if ussd_format:
                ussd_val = ussd_format.replace("{pin}", str(pin_val)).replace("{phone}", phone_str).replace("{amount}", final_amt_str)
                ussd_val = ussd_val.replace(" ", "")
            else:
                ussd_val = final_amt_str

        conn.close()
        return jsonify({"status": "success", "id": trans['id'], "phone": phone_str, "amount": ussd_val, "network": network})

    conn.close()
    return jsonify({"status": "empty"})

@app.route('/complete_transaction/<int:tid>', methods=['GET', 'POST'])
def complete_trans(tid):
    ussd_message = ""
    if request.method == 'POST':
        try:
            if request.is_json: ussd_message = request.get_json().get('message', "")
            elif request.form.get('message'): ussd_message = request.form.get('message', "")
            else: ussd_message = request.get_data(as_text=True)
        except: pass
    if not ussd_message: ussd_message = request.args.get('message', "")

    # 💡 💡 💡 فحص استعلام الرصيد الآلي 💡 💡 💡
    if tid in [999999901, 999999902]:
        network = 'Syriatel' if tid == 999999901 else 'MTN'
        
        # 🚀 التحديث الذكي: سحب الكلمات المفتاحية من اللوحة للرادار الآلي 🚀
        conn = get_db_connection()
        code_name = 'check_bal_syriatel' if network == 'Syriatel' else 'check_bal_mtn'
        try:
            code_row = conn.execute("SELECT success_keyword FROM ussd_codes WHERE service_name=?", (code_name,)).fetchone()
            success_str = code_row['success_keyword'] if code_row and code_row['success_keyword'] else "رصيد,balance"
        except:
            success_str = "رصيد,balance"
        conn.close()
        
        success_keywords = [k.strip().lower() for k in success_str.split(',') if k.strip()]
        lower_msg = ussd_message.lower()
        
        is_valid_msg = any(kw in lower_msg for kw in success_keywords)
        
        if is_valid_msg:
            nums_str = re.findall(r'\d+(?:[.,]\d+)*', ussd_message)
            if nums_str:
                nums_float = [float(n.replace(',', '')) for n in nums_str]
                valid_nums = [n for n in nums_float if n < 900000000] 
                if valid_nums:
                    actual_balance = max(valid_nums)
                    set_setting(f'sim_balance_{network}', str(actual_balance))
        return jsonify({"status": "success"})
    # -----------------------------------------------------------

    conn = get_db_connection()
    trans = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()

    if trans and trans['status'] in ('QUEUED', 'PROCESSING'):
        network = trans['network']
        lower_msg = (ussd_message.lower() if ussd_message else "")
        try: service_type = trans['service_type']
        except: service_type = ""

        if service_type in ['cash_syriatel', 'cash_mtn', 'bill_syriatel', 'bill_mtn']:
            srv_name = service_type
        else:
            srv_name = 'transfer_syriatel' if network == 'Syriatel' else 'transfer_mtn'

        # 🛡️ الحماية من خطأ عدم وجود عمود الكلمات المفتاحية
        try:
            code_row = conn.execute("SELECT success_keyword, failure_keyword FROM ussd_codes WHERE service_name=?", (srv_name,)).fetchone()
            success_str = code_row['success_keyword'] if code_row and code_row['success_keyword'] else "بنجاح"
            failure_str = code_row['failure_keyword'] if code_row and code_row['failure_keyword'] else "فشل"
        except Exception:
            success_str = "بنجاح"
            failure_str = "فشل"

        success_keywords = [k.strip().lower() for k in success_str.split(',') if k.strip()]
        failure_keywords = [k.strip().lower() for k in failure_str.split(',') if k.strip()]

        is_success = False
        is_failed = False
        friendly_fail_reason = "مرفوضة من سيرفرات الشركة."

        if lower_msg:
            for keyword in failure_keywords:
                if keyword in lower_msg:
                    is_failed = True
                    if "لا يملك حساب" in lower_msg: friendly_fail_reason = "الرقم المطلوب لا يملك حساب كاش مفعل."
                    elif "غير موجود" in lower_msg or "خاطئ" in lower_msg: friendly_fail_reason = "الرقم الذي أدخلته خاطئ أو غير موجود بالشبكة."
                    elif "تجاوز الحد" in lower_msg or "المسموح" in lower_msg: friendly_fail_reason = "تم تجاوز الحد اليومي المسموح للتحويل."
                    elif "التفعيل" in lower_msg: friendly_fail_reason = "الرقم المطلوب لم يقم بتفعيل خطه."
                    elif "لا يستطيع الاستمرار" in lower_msg or "النظام" in lower_msg: friendly_fail_reason = "الرقم خاطئ، أو غير مفعل، أو قد يكون خط لاحق الدفع (فاتورة)."
                    elif "غير كاف" in lower_msg: friendly_fail_reason = "رصيد مركز التحويل غير كافٍ، يرجى إبلاغ الإدارة."
                    else: friendly_fail_reason = f"مرفوضة بسبب: {keyword}"
                    break

            if not is_failed:
                for keyword in success_keywords:
                    if keyword in lower_msg:
                        is_success = True
                        break

        # نظام التحقق المزدوج للرادار
        if not is_success and not is_failed:
            time_threshold = (datetime.now(local_tz) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
            full_phone = str(trans['phone'])
            short_phone = full_phone[1:] if full_phone.startswith('0') else full_phone
            amt_int = int(float(trans['amount']))
            amt_str1 = str(amt_int)
            amt_str2 = f"{amt_int:,}"

            recent_sms = conn.execute("SELECT message FROM sms_logs WHERE date >= ? ORDER BY id DESC", (time_threshold,)).fetchall()
            for sms in recent_sms:
                msg_txt = sms['message']
                if short_phone in msg_txt and (amt_str1 in msg_txt or amt_str2 in msg_txt):
                    is_success = True
                    ussd_message = f"تم التأكيد بفضل الرادار 📡: {msg_txt}"
                    break

        if is_failed:
            fail_msg_to_user = f"❌ *نعتذر منك، فشلت العملية*\nالرقم: `{trans['phone']}`\nالمبلغ: `{trans['amount']:g}`\n\n⚠️ *السبب:* {friendly_fail_reason}\n\n(تمت إعادة الرصيد إلى محفظتك تلقائياً ✅)"
            conn.execute("UPDATE transactions SET status='FAILED', ussd_response=? WHERE id=?", (ussd_message, tid))
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (trans['amount'], trans['user_id']))
            conn.commit()
            try: bot.send_message(trans['user_id'], fail_msg_to_user, parse_mode="Markdown")
            except: pass

        elif is_success:
            clean_ussd_msg = re.split(r'باقي في حسابك|الرصيد المتبقي|رصيدك الحالي|رصيدك|prepaid NSYP was transferred|transaction successful', ussd_message, flags=re.IGNORECASE)[0].strip()
            if "transferred to the number" in ussd_message:
                clean_ussd_msg = f"تم تحويل {trans['amount']:g} بنجاح إلى الرقم {trans['phone']}"

            success_msg_to_user = f"╭━━━ ✅ تم التحويل ━━━╮\n🎯 الرقم: `{trans['phone']}`\n💰 المبلغ: *{trans['amount']:g}*\n📱 الشبكة: {network}\n┣━━━━━━━━━━━━━┫\n📝 {clean_ussd_msg}\n╰━━━━━━━━━━━━━╯"

            deduct_val = float(trans['amount'])
            sim_bal = float(get_setting(f'sim_balance_{network}') or 0)
            new_sim_bal = sim_bal - deduct_val
            set_setting(f'sim_balance_{network}', str(new_sim_bal))

            if 'cash' in service_type or 'bill' in service_type or 'كاش' in service_type or 'فاتورة' in service_type:
                try: deduct_central_cash(network, service_type, deduct_val)
                except: pass

            points_to_add = int(deduct_val / 1000)
            conn.execute("UPDATE users SET loyalty_points = loyalty_points + ? WHERE user_id = ?", (points_to_add, trans['user_id']))

            if new_sim_bal < 5000:
                try: bot.send_message(ADMIN_TG_ID, f"⚠️ *تنبيه مبكر (رصيد منخفض)!*\nرصيد شريحة *{network}* انخفض وأصبح `{new_sim_bal:g}`.", parse_mode="Markdown")
                except: pass

            conn.execute("UPDATE transactions SET status='SUCCESS', ussd_response=? WHERE id=?", (ussd_message, tid))
            conn.commit()

            try: bot.send_message(trans['user_id'], success_msg_to_user, parse_mode="Markdown")
            except: pass
    conn.close()
    return jsonify({"status": "success"})



# =============================================================
# 🚀 أزرار التحكم للمدير عبر التلغرام (للقائمة البيضاء والأسباب)
# =============================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith('admact_') or call.data.startswith('ref_reason_'))
def admin_action_callback(call):
    if call.from_user.id != ADMIN_TG_ID: return

    conn = get_db_connection()

    if call.data.startswith('admact_'):
        action = call.data.split('_')[1]
        tid = call.data.split('_')[2]
        t = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()

        if not t or t['status'] not in ('QUEUED', 'PROCESSING', 'MANUAL_CHECK'):
            conn.close()
            bot.edit_message_text(f"الطلب #{tid}\nتمت معالجته مسبقاً أو تم إغلاقه.", call.message.chat.id, call.message.message_id)
            return

        if action == 'refund':
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("❌ الرقم خطأ أو مفصول", callback_data=f"ref_reason_{tid}_wrong"),
                types.InlineKeyboardButton("❌ فشل من الشبكة المصدر", callback_data=f"ref_reason_{tid}_network"),
                types.InlineKeyboardButton("🔙 تراجع (إلغاء الأمر)", callback_data=f"ref_reason_{tid}_cancel")
            )
            bot.edit_message_text(f"الطلب #{tid}\nالرقم: {t['phone']}\n\n👇 اختر سبب الإلغاء لإرساله للزبون:", call.message.chat.id, call.message.message_id, reply_markup=markup)
            conn.close()
            return

        elif action == 'success':
            deduct_val = float(t['amount'])
            sim_bal = float(get_setting(f'sim_balance_{t["network"]}') or 0)
            set_setting(f'sim_balance_{t["network"]}', str(sim_bal - deduct_val))

            conn.execute("UPDATE transactions SET status='SUCCESS', ussd_response='تم التأكيد يدوياً عبر التلغرام' WHERE id=?", (tid,))
            conn.commit()
            bot.edit_message_text(f"الطلب #{tid}\n✅ تم تأكيد النجاح وخصم الرصيد من مخزون الشريحة.", call.message.chat.id, call.message.message_id)
            try:
                success_msg = f"╭━━━ ✅ تم التحويل ━━━╮\n🎯 الرقم: `{t['phone']}`\n💰 المبلغ: *{t['amount']:g}* ل.س\n📱 الشبكة: {t['network']}\n┣━━━━━━━━━━━━━┫\n📝 تم تحويل {t['amount']:g} ليرة بنجاح إلى الرقم {t['phone']}\n╰━━━━━━━━━━━━━╯"
                bot.send_message(t['user_id'], success_msg, parse_mode="Markdown")
            except: pass

        elif action == 'retry':
            new_date = datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE transactions SET status='QUEUED', ussd_response='Waiting', date=? WHERE id=?", (new_date, tid))
            conn.commit()
            bot.edit_message_text(f"الطلب #{tid}\n🔄 تمت إعادة الطلب إلى الطابور وتصفير العداد الزمني.", call.message.chat.id, call.message.message_id)

    elif call.data.startswith('ref_reason_'):
        tid = call.data.split('_')[2]
        reason_type = call.data.split('_')[3]

        t = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
        if not t or t['status'] not in ('QUEUED', 'PROCESSING', 'MANUAL_CHECK'):
            conn.close()
            bot.edit_message_text(f"الطلب #{tid}\nتمت معالجته مسبقاً أو تم إغلاقه.", call.message.chat.id, call.message.message_id)
            return

        if reason_type == 'cancel':
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("❌ إلغاء واسترداد الرصيد", callback_data=f"admact_refund_{tid}"),
                types.InlineKeyboardButton("✅ تأكيد النجاح يدوياً", callback_data=f"admact_success_{tid}"),
                types.InlineKeyboardButton("🔄 إعادة محاولة", callback_data=f"admact_retry_{tid}")
            )
            bot.edit_message_text(f"🚨 *تنبيه فشل أو تعليق آلي!*\nالطلب: `#{tid}`\n📱 الشبكة: {t['network']}\nالرقم: `{t['phone']}`\n💰 المبلغ: {t['amount']:g}\n📝 رد الشبكة: {t['ussd_response']}\n\n👇 يرجى المراجعة واتخاذ القرار:", call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
            conn.close()
            return

        reason_text = "الرقم غير صحيح أو خارج الخدمة." if reason_type == 'wrong' else "نعتذر، فشل التحويل بسبب مشكلة في الشبكة المصدر."

        conn.execute("UPDATE transactions SET status='REFUNDED', ussd_response=? WHERE id=?", (reason_text, tid))
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (t['amount'], t['user_id']))
        conn.commit()

        bot.edit_message_text(f"الطلب #{tid}\n❌ تم الإلغاء واسترداد الرصيد.\nالسبب: {reason_text}", call.message.chat.id, call.message.message_id)

        try:
            msg = f"⚠️ *إشعار استرداد رصيد*\nتم إعادة مبلغ `{t['amount']:g}` وحدة لحسابك.\n📱 *العملية:* تحويل لرقم `{t['phone']}`\n📝 *السبب:* {reason_text}"
            bot.send_message(t['user_id'], msg, parse_mode="Markdown")
        except: pass

    conn.close()

# =============================================================
# 🚀 شاشة التحويلات الجارية مع زر (✅ تم)
# =============================================================
@app.route('/pending')
def pending_page():
    if not session.get('logged_in') or session['role'] != 'admin': return redirect('/')

    conn = get_db_connection()
    trans = conn.execute("SELECT * FROM transactions WHERE status IN ('QUEUED', 'PROCESSING') ORDER BY id DESC").fetchall()
    conn.close()
    rows = ""
    for t in trans:
        status_badge = "<span class='badge bg-warning text-dark px-3 py-2 rounded-pill shadow-sm'><i class='fas fa-spinner fa-spin me-1'></i> بانتظار الموبايل</span>" if t['status'] == 'QUEUED' else "<span class='badge bg-info text-dark px-3 py-2 rounded-pill shadow-sm'><i class='fas fa-cog fa-spin me-1'></i> قيد المعالجة الفعلية</span>"
        
        # --- 💡 المترجم الذكي للوحة الويب (تم الإصلاح هنا) 💡 ---
        t_dict = dict(t)
        srv_type = t_dict.get('service_type', '')
        net = t_dict.get('network', '')
        
        if srv_type in ['Jahez', 'Bulk'] or not srv_type:
            type_badge = "<span class='badge bg-danger bg-opacity-10 text-danger border border-danger'>وحدات سيريتل 🔴</span>" if net == 'Syriatel' else "<span class='badge bg-warning bg-opacity-10 text-dark border border-warning'>وحدات MTN 🟡</span>"
        elif srv_type == 'cash_syriatel': type_badge = "<span class='badge bg-danger text-white shadow-sm'>كاش سيريتل 🔴</span>"
        elif srv_type == 'cash_mtn': type_badge = "<span class='badge bg-warning text-dark shadow-sm'>كاش MTN 🟡</span>"
        elif srv_type == 'bill_syriatel': type_badge = "<span class='badge bg-primary text-white shadow-sm'>فواتير سيريتل 🔴</span>"
        elif srv_type == 'bill_mtn': type_badge = "<span class='badge bg-success text-white shadow-sm'>فواتير MTN 🟡</span>"
        else: type_badge = f"<span class='badge bg-secondary'>{net}</span>"
        # --------------------------------------------------------
        
        actions = f"""
        <div class='d-flex gap-1 justify-content-center'>
            <form action='/manual_complete/{t['id']}' method='POST'>
                <button type='submit' class='btn btn-sm btn-success fw-bold rounded-pill px-3' onclick=\"return confirm('تأكيد يدوي للنجاح؟');\">✅ تم</button>
            </form>
            <form action='/retry_transaction/{t['id']}' method='POST'>
                <button type='submit' class='btn btn-sm btn-primary fw-bold rounded-pill px-3' onclick=\"return confirm('إعادة إرسال للموبايل مرة ثانية؟');\">🔄 إعادة</button>
            </form>
            <form action='/cancel_trans/{t['id']}' method='POST'>
                <button type='submit' class='btn btn-sm btn-outline-danger fw-bold rounded-pill px-3' onclick=\"return confirm('إلغاء واسترداد الرصيد؟');\">❌ إلغاء</button>
            </form>
        </div>
        """
        rows += f"<tr><td class='fw-bold text-muted'>#{t['id']}</td><td>{type_badge}</td><td dir='ltr' class='text-muted small'>{t['date']}</td><td class='fw-black text-primary fs-5'>{t['phone']}</td><td class='fw-bold text-success'>{t['amount']:g}</td><td>{status_badge}</td><td>{actions}</td></tr>"
    
    content = f"""<div class="card-bank"><h4 class="fw-bold mb-4 text-primary"><i class="fas fa-spinner me-2"></i> التحويلات الجارية (الآلية)</h4><div class="alert alert-info py-2 shadow-sm border-0 fw-bold small"><i class="fas fa-sync fa-spin me-2"></i> تتحدث تلقائياً...</div><div class="table-responsive"><table class="table text-center align-middle"><thead><tr><th>رقم الطلب</th><th>النوع</th><th>الوقت</th><th>الرقم</th><th>الكمية</th><th>الحالة</th><th>إدارة يدوية</th></tr></thead><tbody>{rows}</tbody></table></div></div><script>setTimeout(function(){{ window.location.reload(1); }}, 30000);</script>"""
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='pending')


@app.route('/accept_order/<int:oid>')
def accept_order(oid):
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    conn = get_db_connection(); o = conn.execute("SELECT * FROM manual_orders WHERE id=? AND status='PENDING'", (oid,)).fetchone()
    if o:
        conn.execute("UPDATE manual_orders SET status='COMPLETED' WHERE id=?", (oid,)); conn.commit()
        try:
            receipt_msg = (
                f"╭━━━ 🧾 *إيصال دفع إلكتروني* ━━━╮\n"
                f"🏢 الخدمة: *{o['service_name']}*\n"
                f"🎯 الحساب/الرقم: `{o['target_info']}`\n"
                f"💰 المبلغ: *{o['price']:g}* ل.س\n"
                f"📅 التاريخ: {datetime.now(local_tz).strftime('%Y-%m-%d %H:%M')}\n"
                f"┣━━━━━━━━━━━━━━━━━━━━┫\n"
                f"✅ *تم التنفيذ بنجاح - شكراً لثقتكم*\n"
                f"╰━━━━━━━━━━━━━━━━━━━━╯"
            )
            bot.send_message(o['user_id'], receipt_msg, parse_mode="Markdown")
        except: pass
    conn.close()
    return redirect('/manual_orders')

@app.route('/toggle_bills_perm/<int:uid>')
def toggle_bills_perm(uid):
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    conn=get_db_connection(); conn.execute("UPDATE users SET can_pay_bills = CASE WHEN can_pay_bills=1 THEN 0 ELSE 1 END WHERE user_id=?", (uid,)); conn.commit(); conn.close()
    return redirect('/users')

@app.route('/del_cmp/<int:cid>')
def del_cmp(cid):
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    try: conn=get_db_connection(); conn.execute("DELETE FROM companies WHERE id=?", (cid,)); conn.execute("DELETE FROM manual_services WHERE company_id=?", (cid,)); conn.commit(); conn.close()
    except: pass
    return redirect('/companies')

@app.route('/del_service/<int:sid>')
def del_service(sid):
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    try: conn = get_db_connection(); conn.execute("DELETE FROM manual_services WHERE id=?", (sid,)); conn.commit(); conn.close()
    except: pass
    return redirect('/manual_services')

@app.route('/del_cat/<int:cid>')
def del_cat(cid):
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    try: conn = get_db_connection(); conn.execute("DELETE FROM categories WHERE id=?", (cid,)); conn.commit(); conn.close()
    except: pass
    return redirect('/categories')

@app.route('/cancel_trans/<int:tid>', methods=['POST'])
def cancel_trans(tid):
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    
    conn = get_db_connection()
    t = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    
    if t and t['status'] in ('QUEUED', 'PROCESSING', 'MANUAL_CHECK'):
        # 1. تحديث الحالة
        conn.execute("UPDATE transactions SET status='CANCELLED', ussd_response='تم الإلغاء يدوياً من اللوحة ❌' WHERE id=?", (tid,))
        
        # 2. إرجاع الرصيد مع حماية التقريب (ROUND)
        conn.execute("UPDATE users SET balance = ROUND(balance + ?, 3) WHERE user_id=?", (t['amount'], t['user_id']))
        conn.commit()
        
        # 3. إطلاق صافرة التلغرام للزبون
        try:
            msg_to_user = f"⚠️ *إشعار استرداد رصيد*\nتعذر تنفيذ طلبك بسبب ضغط الشبكة، وتم إلغاء العملية وإعادة مبلغ `{t['amount']:g}` وحدة لحسابك.\n📱 *العملية:* تحويل لرقم `{t['phone']}`\n✅ *رصيدك الآن بأمان.*"
            bot.send_message(t['user_id'], msg_to_user, parse_mode="Markdown")
        except: pass
        
    conn.close()
    return redirect('/pending')

@app.route('/retry_transaction/<int:tid>', methods=['POST'])
def retry_transaction(tid):
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    conn = get_db_connection()
    new_date = datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE transactions SET status='QUEUED', ussd_response='Waiting', date=? WHERE id=?", (new_date, tid))
    conn.commit()
    conn.close()
    return redirect('/pending')

@app.route('/manual_complete/<int:tid>', methods=['POST'])
def manual_complete(tid):
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    conn = get_db_connection()
    t = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    if t and t['status'] in ('QUEUED', 'PROCESSING'):
        network = t['network']
        deduct_val = float(t['amount'])
        sim_bal = float(get_setting(f'sim_balance_{network}') or 0)
        set_setting(f'sim_balance_{network}', str(sim_bal - deduct_val))

        conn.execute("UPDATE transactions SET status='SUCCESS', ussd_response='تم تأكيد التحويل يدوياً من الإدارة ✅' WHERE id=?", (tid,))
        conn.commit()

        try: bot.send_message(t['user_id'], f"╭━━━ ✅ تم التحويل ━━━╮\n🎯 الرقم: `{t['phone']}`\n💰 المبلغ: *{t['amount']:g}* وحدة\n┣━━━━━━━━━━━━━┫\n📝 ملاحظة: تم التأكيد بنجاح من الإدارة.\n╰━━━━━━━━━━━━━╯", parse_mode="Markdown")
        except: pass
    conn.close()
    return redirect('/pending')

# =============================================================
# إدارة الطلبات اليدوية والفواتير
# =============================================================
@app.route('/manual_orders')
def manual_orders_page():
    if not session.get('logged_in'): return redirect('/')
    conn = get_db_connection()
    orders = conn.execute("SELECT o.*, u.real_name FROM manual_orders o JOIN users u ON o.user_id = u.user_id ORDER BY o.id DESC LIMIT 100").fetchall()
    conn.close()
    rows = ""
    for o in orders:
        o_dict = dict(o)
        if o_dict['status'] == 'PENDING': badge, actions = "<span class='badge bg-warning text-dark px-3 py-2 rounded-pill shadow-sm'><i class='fas fa-hourglass-half me-1'></i> بالانتظار</span>", f"<div class='d-flex justify-content-center gap-2'><a href='/accept_order/{o_dict['id']}' class='btn btn-sm btn-success shadow-sm fw-bold px-3'><i class='fas fa-check me-1'></i> تنفيذ</a> <button type='button' class='btn btn-sm btn-danger shadow-sm fw-bold px-3' onclick=\"openRejectModal({o_dict['id']})\"><i class='fas fa-times me-1'></i> إرجاع</button></div>"
        elif o_dict['status'] == 'COMPLETED': badge, actions = "<span class='badge bg-success px-3 py-2 rounded-pill'><i class='fas fa-check-double me-1'></i> مكتملة</span>", "-"
        else: badge, actions = f"<span class='badge bg-danger px-3 py-2 rounded-pill'><i class='fas fa-ban me-1'></i> مرفوضة</span>", "-"
        rows += f"<tr><td class='text-muted fw-bold'>#{o_dict['id']}</td><td><a href='/user/{o_dict['user_id']}' class='user-link fw-bold'>{o_dict['real_name']}</a></td><td class='text-primary fw-bold'>{o_dict['service_name']}</td><td dir='ltr'><span class='bg-light px-3 py-1 rounded border text-dark fw-black fs-6'>{o_dict['target_info']}</span></td><td class='fw-bold text-success'>{o_dict['price']:,.2f}</td><td>{badge}</td><td dir='ltr' class='text-muted small'>{o_dict['date']}</td><td>{actions}</td></tr>"

    modal_html = """<div class="modal fade" id="rejectModal" tabindex="-1" aria-hidden="true"><div class="modal-dialog modal-dialog-centered"><div class="modal-content border-0 shadow-lg rounded-4"><div class="modal-header bg-danger text-white border-0 rounded-top-4"><h5 class="modal-title fw-bold"><i class="fas fa-times-circle me-2"></i> إلغاء الطلب واسترداد الرصيد</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><form action="/reject_order_reason" method="POST"><div class="modal-body p-4"><input type="hidden" name="oid" id="reject_oid"><label class="form-label text-danger fw-bold mb-2">سبب الإلغاء:</label><input type="text" name="reason" class="form-control bg-light border-0 shadow-sm p-3 fw-bold" placeholder="مثال: الرقم مفصول..." required></div><div class="modal-footer border-0 pb-4 pe-4"><button type="button" class="btn btn-light shadow-sm fw-bold" data-bs-dismiss="modal">تراجع</button><button type="submit" class="btn btn-danger px-4 fw-bold shadow-sm">تأكيد الإلغاء 🗑️</button></div></form></div></div></div><script>function openRejectModal(oid) { document.getElementById('reject_oid').value = oid; new bootstrap.Modal(document.getElementById('rejectModal')).show(); }</script>"""
    content = f"""<div class="card-bank"><h4 class="fw-bold mb-4 text-primary"><i class="fas fa-shopping-bag me-2"></i> الطلبات الواردة (الفواتير)</h4><div class="table-responsive"><table class="table text-center align-middle"><thead><tr><th>الطلب</th><th>العميل</th><th>الخدمة المطلوبة</th><th>الرقم المطلوب</th><th>المبلغ</th><th>الحالة</th><th>الوقت</th><th>إدارة</th></tr></thead><tbody>{rows}</tbody></table></div></div><script>setTimeout(function(){{ window.location.reload(1); }}, 30000);</script>{modal_html}"""
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='manual_orders')



@app.route('/reject_order_reason', methods=['POST'])
def reject_order_reason():
    if session.get('logged_in') and session.get('role') == 'admin':
        oid = request.form.get('oid')
        reason = request.form.get('reason', 'مرفوض من الإدارة')
        conn = get_db_connection()
        o = conn.execute("SELECT * FROM manual_orders WHERE id=? AND status='PENDING'", (oid,)).fetchone()
        if o:
            conn.execute("UPDATE manual_orders SET status='REJECTED', reject_reason=? WHERE id=?", (reason, oid))
            conn.execute("UPDATE users SET bills_balance = bills_balance + ? WHERE user_id=?", (o['price'], o['user_id']))
            conn.commit()
            try: bot.send_message(o['user_id'], f"عذراً، تم إلغاء طلبك وإرجاع مبلغ `{o['price']:g}` ل.س إلى محفظة الفواتير.\n\n📝 *السبب:* {reason}", parse_mode="Markdown")
            except: pass
        conn.close()
    return redirect('/manual_orders')

# =============================================================
# إدارة الخزينة المركزية، الإيداعات، والموردين
# =============================================================
@app.route('/topup_admin_wallet', methods=['POST'])
def topup_admin_wallet():
    if session.get('logged_in') and session.get('role') == 'admin':
        w_type, amt_str = request.form.get('wallet_type'), request.form.get('amount')
        if w_type and amt_str:
            try:
                amt = float(amt_str)
                if amt > 0:
                    admin_id = session['user_id']
                    conn = get_db_connection()
                    bal_col = 'balance' if w_type == 'units' else 'bills_balance'
                    conn.execute(f"UPDATE users SET {bal_col} = {bal_col} + ? WHERE user_id = ?", (amt, admin_id))
                    conn.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, date) VALUES (?, 0, ?, ?, ?, ?)",
                                 (admin_id, amt, 0, w_type, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
                    conn.commit()
                    conn.close()
            except Exception as e:
                logging.error(f"خطأ في شحن خزينة الإدارة: {str(e)}")
                return f"<script>alert('❌ حدث خطأ فني، تم تسجيله للإدارة.'); window.location.href='/deposits';</script>"
    return redirect('/deposits')

@app.route('/topup_sim', methods=['POST'])
def topup_sim():
    if session.get('logged_in') and session.get('role') == 'admin':
        network, amount_str, action = request.form.get('network'), request.form.get('amount'), request.form.get('action')
        if network in ['Syriatel', 'MTN']:
            if action == 'reset':
                set_setting(f'sim_balance_{network}', '0')
            elif amount_str:
                try:
                    amount = float(amount_str)
                    current = float(get_setting(f'sim_balance_{network}') or 0)
                    new_bal = current + amount if action == 'add' else current - amount
                    set_setting(f'sim_balance_{network}', str(new_bal))
                except ValueError: pass
    return redirect('/deposits')

@app.route('/update_cash', methods=['POST'])
def update_cash():
    if session.get('logged_in') and session.get('role') == 'admin':
        action, amount_str = request.form.get('action'), request.form.get('amount')
        if action == 'reset':
            set_setting('cash_drawer', '0')
        else:
            try:
                amt = float(amount_str)
                current_cash = float(get_setting('cash_drawer') or 0)
                new_cash = current_cash + amt if action == 'add' else current_cash - amt
                set_setting('cash_drawer', str(new_cash))
            except (ValueError, TypeError): pass
    return redirect('/deposits')

@app.route('/add_free_debt', methods=['POST'])
def add_free_debt():
    if not session.get('logged_in'): return redirect('/')
    uid_str, amt_str, note = request.form.get('uid'), request.form.get('amount'), request.form.get('note', 'دين خارجي')
    if uid_str and amt_str:
        try:
            uid, amt = int(uid_str), float(amt_str)
            if amt > 0:
                conn = get_db_connection()
                # نستخدم عملية مؤمنة (Atomic)
                conn.execute("UPDATE users SET debt_balance = debt_balance + ? WHERE user_id = ?", (amt, uid))
                conn.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (uid, session['user_id'], 0, amt, 'free_debt', 0, 1, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit()
                conn.close()
                return "<script>alert('✅ تم تسجيل الدين بنجاح!'); window.location.href='/deposits';</script>"
        except Exception as e:
            if 'conn' in locals(): conn.close()
            logging.error(f"خطأ في تسجيل الدين اليدوي: {str(e)}")
            return "<script>alert('❌ حدث خطأ أثناء تسجيل الدين!'); window.location.href='/deposits';</script>"
    return redirect('/deposits')

@app.route('/admin_direct_edit_wallet', methods=['POST'])
def admin_direct_edit_wallet():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    target = request.form.get('target')
    new_val_str = request.form.get('new_val')
    if target and new_val_str:
        try:
            new_val = float(new_val_str)
            if new_val >= 0:
                conn = get_db_connection()
                col = 'balance' if target == 'units' else 'bills_balance'
                conn.execute(f"UPDATE users SET {col} = ? WHERE user_id = ?", (new_val, session['user_id']))
                conn.commit()
                conn.close()
                return redirect('/deposits?success=updated')
        except ValueError: pass
    return redirect('/deposits')

@app.route('/deposits', methods=['GET', 'POST'])
def deposits_page():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')

    try: page = int(request.args.get('page', 1))
    except ValueError: page = 1
    per_page = 20
    offset = (page - 1) * per_page

    conn = get_db_connection()
    total_count = conn.execute("SELECT count(*) FROM deposit_logs").fetchone()[0]
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    logs = conn.execute("SELECT d.*, u1.real_name as receiver_name, u1.user_id as receiver_id FROM deposit_logs d LEFT JOIN users u1 ON d.user_id = u1.user_id ORDER BY d.id DESC LIMIT ? OFFSET ?", (per_page, offset)).fetchall()

    sim_s = float(get_setting('sim_balance_Syriatel') or 0)
    sim_m = float(get_setting('sim_balance_MTN') or 0)
    cash_drawer = float(get_setting('cash_drawer') or 0)
    users_list = conn.execute("SELECT user_id, real_name FROM users WHERE role='user' AND is_approved=1").fetchall()

    admin_data = conn.execute("SELECT balance, bills_balance FROM users WHERE user_id=?", (session['user_id'],)).fetchone()
    admin_units = admin_data['balance'] if admin_data else 0
    admin_bills = admin_data['bills_balance'] if admin_data else 0

    conn.close()

    user_options = "".join([f"<option value='{u['user_id']}'>{u['real_name']}</option>" for u in users_list])

    rows = ""
    for i, l in enumerate(logs, start=offset + 1):
        l_dict = dict(l)
        edit_btn = f"<button type='button' class='btn btn-sm btn-outline-primary rounded-pill px-2' onclick='openEditLogModal({l_dict['id']}, {l_dict['amount']}, {l_dict['actual_paid']})'><i class='fas fa-pen'></i></button>"
        del_btn = f"<form action='/delete_log/{l_dict['id']}' method='POST' class='d-inline' onsubmit='return confirm(\"حذف وتعديل الأرصدة تلقائياً؟\");'><button type='submit' class='btn btn-sm btn-outline-danger rounded-pill px-2 ms-1'><i class='fas fa-trash'></i></button></form>"
        actions = f"<div class='d-flex gap-1 justify-content-center'>{edit_btn}{del_btn}</div>"

        if l_dict['wallet_type'] == 'debt_payment':
            mov_type, receiver, amount_html = '<span class="badge bg-success bg-opacity-10 text-success">تسديد دين</span>', f"<a href='/user/{l_dict['receiver_id']}' class='user-link fw-bold'>{l_dict['receiver_name']}</a>", f"<span class='text-success fw-bold'>+ {l_dict['actual_paid']:,.2f} كاش</span>"
        elif l_dict['wallet_type'] == 'free_debt':
            mov_type, receiver, amount_html = '<span class="badge bg-danger bg-opacity-10 text-danger">تسجيل دين</span>', f"<a href='/user/{l_dict['receiver_id']}' class='user-link fw-bold'>{l_dict['receiver_name']}</a>", f"<span class='text-danger fw-bold'>- {l_dict['actual_paid']:,.2f} مقيد</span>"
        elif l_dict['by_admin_id'] == 0:
            mov_type, receiver, amount_html, actions = '<span class="badge bg-success bg-opacity-10 text-success">شراء بضاعة</span>', f"<strong>الخزينة المركزية</strong>", f"<span class='text-success fw-bold'>+ {l_dict['amount']:,.2f} وحدة</span>", "-"
        else:
            mov_type = '<span class="badge bg-primary bg-opacity-10 text-primary">بيع وشحن</span>'
            pay_type = f" (آجل 📝)" if l_dict['is_debt'] else f" (كاش 💵)"
            receiver = f"<a href='/user/{l_dict['receiver_id']}' class='user-link fw-bold'>{l_dict['receiver_name']}</a><br><small class='text-success fw-bold mt-1'>الربح: {l_dict['profit']:g} ل.س {pay_type}</small>"
            sign, color = ("+", "success") if l_dict['amount'] < 0 else ("-", "danger")
            amount_html = f"<span class='text-{color} fw-bold'>{sign} {abs(l_dict['amount']):,.2f} وحدة</span>"

        rows += f"<tr><td class='fw-black text-muted fs-5'>{i}</td><td>{mov_type}</td><td>{receiver}</td><td class='fs-5'>{amount_html}</td><td dir='ltr' class='text-muted small'>{l_dict['date']}</td><td>{actions}</td></tr>"

    pagination_html = f"<div class='d-flex justify-content-center gap-2 mt-4'>"
    if page > 1: pagination_html += f"<a href='?page={page-1}' class='btn btn-outline-primary fw-bold shadow-sm'>السابق</a>"
    pagination_html += f"<span class='badge bg-primary fs-6 shadow-sm py-2 px-3'>صفحة {page} من {total_pages}</span>"
    if page < total_pages: pagination_html += f"<a href='?page={page+1}' class='btn btn-outline-primary fw-bold shadow-sm'>التالي</a>"
    pagination_html += "</div>"

    content = f"""<h4 class="fw-bold mb-4 text-primary"><i class="fas fa-university me-2"></i> الخزينة المركزية ودفتر الأستاذ</h4>

    {{% if request.args.get('success') %}}<div class="alert alert-success fw-bold shadow-sm mb-4"><i class="fas fa-check-circle me-2"></i> تم تحديث رصيد الخزينة بنجاح!</div>{{% endif %}}

    <div class="row g-4 mb-4">
        <div class="col-md-6">
            <div class="card-bank border-primary border-start border-5 h-100 shadow-sm" style="background: linear-gradient(135deg, #1e3a8a, #3b82f6);">
                <div class="text-center py-3">
                    <h5 class="text-white-50 fw-bold mb-2"><i class="fas fa-mobile-alt me-2"></i>رصيد الخزينة المركزية (وحدات)</h5>
                    <h1 class="fw-black text-white m-0" dir="ltr">{admin_units:,.2f}</h1>
                    <button class="btn btn-sm btn-outline-light fw-bold mt-2 rounded-pill px-3" onclick="promptEditAdmin('units', {admin_units})"><i class="fas fa-pen me-1"></i> تعديل رصيد الوحدات</button>
                </div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="card-bank border-info border-start border-5 h-100 shadow-sm" style="background: linear-gradient(135deg, #0f766e, #10b981);">
                <div class="text-center py-3">
                    <h5 class="text-white-50 fw-bold mb-2"><i class="fas fa-receipt me-2"></i>رصيد الخزينة المركزية (فواتير)</h5>
                    <h1 class="fw-black text-white m-0" dir="ltr">{admin_bills:,.2f}</h1>
                    <button class="btn btn-sm btn-outline-light fw-bold mt-2 rounded-pill px-3" onclick="promptEditAdmin('bills', {admin_bills})"><i class="fas fa-pen me-1"></i> تعديل رصيد الفواتير</button>
                </div>
            </div>
        </div>
    </div>

    <div class="card-bank border-primary border-start border-4 bg-white mb-4 shadow-sm">
        <h5 class="fw-bold text-primary mb-3"><i class="fas fa-plus-circle me-2"></i> شراء بضاعة (شحن رصيد الخزينة المركزية)</h5>
        <form action="/topup_admin_wallet" method="POST" class="row g-2 align-items-center">
            <div class="col-md-4">
                <select name="wallet_type" class="form-select border-primary shadow-sm fw-bold" required>
                    <option value="units">محفظة الوحدات 📱</option>
                    <option value="bills">محفظة الفواتير 🧾</option>
                </select>
            </div>
            <div class="col-md-5">
                <input type="number" step="0.01" name="amount" class="form-control border-primary shadow-sm fw-bold" placeholder="الكمية المراد إضافتها لحساب الإدارة..." required>
            </div>
            <div class="col-md-3">
                <button type="submit" class="btn btn-primary w-100 fw-bold shadow-sm"><i class="fas fa-download me-1"></i> شحن الخزينة</button>
            </div>
        </form>
    </div>

    <div class="row g-4 mb-5">
        <div class="col-md-4"><div class="card-bank border-danger border-start border-4 bg-light h-100"><h5 class="fw-bold text-danger mb-3"><i class="fas fa-sim-card me-2"></i> مخزون الشرائح الفعلي</h5><div class="row text-center mb-3"><div class="col-6"><p class="text-muted fw-bold mb-1">سيريتل</p><h4 class="fw-black text-danger">{sim_s:,.2f}</h4></div><div class="col-6"><p class="text-muted fw-bold mb-1">MTN</p><h4 class="fw-black" style="color:#d97706;">{sim_m:,.2f}</h4></div></div><form action="/topup_sim" method="POST" class="row g-2 align-items-center"><div class="col-12"><select name="network" class="form-select border-danger shadow-sm" required><option value="Syriatel">سيريتل</option><option value="MTN">MTN</option></select></div><div class="col-12"><input name="amount" type="number" step="0.01" class="form-control border-danger shadow-sm" placeholder="الكمية (للإيداع أو السحب)"></div><div class="col-6"><button type="submit" name="action" value="add" class="btn btn-success w-100 fw-bold shadow-sm">➕ إيداع</button></div><div class="col-6"><button type="submit" name="action" value="sub" class="btn btn-warning w-100 fw-bold shadow-sm text-dark">➖ سحب</button></div><div class="col-12 mt-1"><button type="submit" name="action" value="reset" class="btn btn-danger w-100 fw-bold shadow-sm" onclick="return confirm('تأكيد تصفير رصيد الشريحة للصفر؟');"><i class="fas fa-trash-alt me-1"></i> تصفير الرصيد</button></div></form></div></div>
        <div class="col-md-4"><div class="card-bank border-success border-start border-4 bg-light h-100"><h5 class="fw-bold text-success mb-3"><i class="fas fa-cash-register me-2"></i> إدارة الكاش (الدرج)</h5><h3 class="fw-black text-center text-dark mb-4">{cash_drawer:,.2f} <small class="fs-6 text-muted">ل.س</small></h3><form action="/update_cash" method="POST" class="row g-2"><div class="col-12"><input type="number" step="0.01" name="amount" class="form-control border-success shadow-sm" placeholder="المبلغ (للإيداع أو السحب)"></div><div class="col-6"><button type="submit" name="action" value="add" class="btn btn-success w-100 fw-bold shadow-sm">➕ إيداع بالدرج</button></div><div class="col-6"><button type="submit" name="action" value="sub" class="btn btn-warning w-100 fw-bold shadow-sm text-dark">➖ سحب من الدرج</button></div><div class="col-12 mt-1"><button type="submit" name="action" value="reset" class="btn btn-danger w-100 fw-bold shadow-sm" onclick="return confirm('تأكيد تصفير الكاش في الدرج ليصبح صفر؟');"><i class="fas fa-trash-alt me-1"></i> تصفير الكاش بالدرج</button></div></form></div></div>
        <div class="col-md-4"><div class="card-bank border-warning border-start border-4 bg-light h-100"><h5 class="fw-bold text-dark mb-3"><i class="fas fa-book-open me-2"></i> تسجيل ذمة مالية (دين حر)</h5><p class="small text-muted fw-bold mb-3">لتسجيل ديون من خارج النظام دون إرسال رصيد وحدات للزبون.</p><form action="/add_free_debt" method="POST" class="row g-2 align-items-center"><div class="col-md-12"><select name="uid" class="form-select border-warning shadow-sm" required><option disabled selected>اختر الزبون لتقييد الدين...</option>{user_options}</select></div><div class="col-md-12"><input name="amount" type="number" step="0.01" class="form-control border-warning shadow-sm" placeholder="المبلغ (ل.س)" required></div><div class="col-md-12"><input name="note" type="text" class="form-control border-warning shadow-sm" placeholder="ملاحظة (سبب الدين)" required></div><div class="col-md-12 mt-2"><button type="submit" class="btn btn-warning text-dark w-100 fw-bold shadow-sm">تسجيل الدين في الدفتر</button></div></form></div></div>
    </div>

    <div class="card-bank"><h5 class="fw-bold text-dark mb-4"><i class="fas fa-book me-2"></i> دفتر الأستاذ (سجل الحركات)</h5><div class="table-responsive"><table class="table text-center align-middle"><thead><tr><th>#</th><th>نوع الحركة</th><th>المستفيد / التفاصيل</th><th>الكمية / المبلغ</th><th>التاريخ والوقت</th><th>إدارة</th></tr></thead><tbody>{rows}</tbody></table></div>{pagination_html}</div>

    <div class="modal fade" id="editAdminWalletModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered modal-sm">
            <div class="modal-content border-0 shadow-lg rounded-4">
                <div class="modal-header bg-primary text-white border-0 rounded-top-4">
                    <h5 class="modal-title fw-bold"><i class="fas fa-pen me-2"></i> تعديل رصيد الخزينة</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <form action="/admin_direct_edit_wallet" method="POST">
                    <div class="modal-body p-4">
                        <input type="hidden" name="target" id="edit_admin_target">
                        <div class="mb-3">
                            <label class="form-label fw-bold text-dark mb-2">الرصيد الإجمالي الجديد</label>
                            <input type="number" step="0.01" name="new_val" id="edit_admin_new_val" class="form-control bg-light border-0 shadow-sm p-3 fw-bold fs-5" required>
                        </div>
                        <p class="small text-danger fw-bold"><i class="fas fa-exclamation-triangle"></i> تنبيه: هذا الإجراء يغير رصيدك الحالي فوراً دون تسجيل حركة مالية.</p>
                    </div>
                    <div class="modal-footer border-0 pb-4 pe-4">
                        <button type="button" class="btn btn-light shadow-sm fw-bold" data-bs-dismiss="modal">إلغاء</button>
                        <button type="submit" class="btn btn-primary px-4 fw-bold shadow-sm"><i class="fas fa-save me-1"></i> حفظ التعديل</button>
                    </div>
                </form>
            </div>
        </div>
    </div>
    <script>
        function promptEditAdmin(target, currentVal) {{
            document.getElementById('edit_admin_target').value = target;
            document.getElementById('edit_admin_new_val').value = currentVal;
            new bootstrap.Modal(document.getElementById('editAdminWalletModal')).show();
        }}
    </script>
    """
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='deposits')

# =============================================================
# 🚚 نظام الموردين والمشتريات (الجديد كلياً)
# =============================================================
@app.route('/merchants', methods=['GET', 'POST'])
def merchants_page():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')

    conn = get_db_connection()
    # تأمين عواميد الجدول
    try: conn.execute("ALTER TABLE merchant_logs ADD COLUMN merchant_name TEXT")
    except: pass
    try: conn.execute("ALTER TABLE merchant_logs ADD COLUMN type TEXT")
    except: pass
    conn.commit()

    msg = ""
    if request.method == 'POST':
        action = request.form.get('action')
        m_name = request.form.get('merchant_name', 'تاجر عام').strip()
        raw_amt = request.form.get('amount', '0').strip().replace(',', '')

        try:
            amt = float(raw_amt)
            if amt > 0:
                if action == 'buy':
                    cost_price = float(get_setting('current_unit_cost') or 1.05)
                    total_debt_inc = amt * cost_price

                    conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, session['user_id']))
                    curr_debt = float((get_setting('merchant_debt') or '0').replace(',', ''))
                    conn.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('merchant_debt', str(curr_debt + total_debt_inc)))

                    conn.execute("INSERT INTO merchant_logs (merchant_name, amount, cost_price, total_debt_increase, type, note, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 (m_name, amt, cost_price, total_debt_inc, 'purchase', 'سحب بضاعة', datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))

                elif action == 'pay':
                    curr_debt = float((get_setting('merchant_debt') or '0').replace(',', ''))
                    conn.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('merchant_debt', str(max(0, curr_debt - amt))))
                    curr_cash = float((get_setting('cash_drawer') or '0').replace(',', ''))
                    conn.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('cash_drawer', str(curr_cash - amt)))

                    conn.execute("INSERT INTO merchant_logs (merchant_name, amount, cost_price, total_debt_increase, type, note, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 (m_name, 0, 0, -amt, 'payment', 'تسديد نقدي', datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))

                elif action == 'receive_user':
                    uid = request.form.get('uid')
                    if uid:
                        conn.execute("UPDATE users SET debt_balance = debt_balance - ? WHERE user_id = ?", (amt, uid))
                        curr_cash = float((get_setting('cash_drawer') or '0').replace(',', ''))
                        conn.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('cash_drawer', str(curr_cash + amt)))
                        conn.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                     (uid, session['user_id'], 0, amt, 'debt_payment', 0, 0, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))

                conn.commit()
                return redirect('/merchants?success=1')
        except:
            msg = "<div class='alert alert-danger fw-bold'>❌ خطأ: يرجى إدخال أرقام صحيحة فقط.</div>"

    if request.args.get('success'):
        msg = "<div class='alert alert-success fw-bold'>✅ تم تسجيل الحركة وتحديث الكاش والأرصدة بنجاح!</div>"

    merchant_debt = float((get_setting('merchant_debt') or '0').replace(',', ''))
    cash_drawer = float((get_setting('cash_drawer') or '0').replace(',', ''))
    debtors = conn.execute("SELECT user_id, real_name, debt_balance FROM users WHERE debt_balance > 0 AND role='user' ORDER BY debt_balance DESC").fetchall()
    total_user_debt = sum([d['debt_balance'] for d in debtors])
    purchases = conn.execute("SELECT * FROM merchant_logs WHERE type='purchase' ORDER BY id DESC LIMIT 50").fetchall()
    payments = conn.execute("SELECT * FROM merchant_logs WHERE type='payment' ORDER BY id DESC LIMIT 50").fetchall()
    conn.close()

    debtors_html = ""
    for d in debtors:
        debtors_html += f"<tr><td class='fw-bold text-primary'>{d['real_name']}</td><td class='fw-black text-danger' dir='ltr'>{d['debt_balance']:,.0f}</td><td><form method='POST' class='d-flex gap-2 justify-content-center'><input type='hidden' name='action' value='receive_user'><input type='hidden' name='uid' value='{d['user_id']}'><input type='number' name='amount' class='form-control form-control-sm border-success w-50' placeholder='المبلغ...' required><button type='submit' class='btn btn-sm btn-success fw-bold' onclick='return confirm(\"تأكيد قبض الدفعة وإضافتها للدرج؟\");'>قبض 💵</button></form></td></tr>"

    if not debtors_html:
        debtors_html = "<tr><td colspan='3' class='text-success fw-bold py-4'>لا يوجد ديون على الزبائن حالياً! 🎉</td></tr>"

    return render_template_string(MERCHANT_HTML_TEMPLATE, purchases=purchases, payments=payments, m_debt=merchant_debt, cash=cash_drawer, total_user_debt=total_user_debt, debtors_html=debtors_html, msg=msg, page='merchants')

@app.route('/clear_merchant_data/<action>', methods=['POST'])
def clear_merchant_data(action):
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    conn = get_db_connection()
    if action == 'debt':
        conn.execute("REPLACE INTO settings (key, value) VALUES ('merchant_debt', '0')")
    elif action == 'purchases':
        conn.execute("DELETE FROM merchant_logs WHERE type='purchase'")
    elif action == 'payments':
        conn.execute("DELETE FROM merchant_logs WHERE type='payment'")
    conn.commit()
    conn.close()
    return redirect('/merchants?success=1')

@app.route('/reset_profits', methods=['POST'])
def reset_profits():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    conn = get_db_connection()
    conn.execute("UPDATE deposit_logs SET profit = 0")
    conn.execute("UPDATE manual_orders SET profit = 0")
    conn.execute("UPDATE transactions SET profit = 0") # 💡 تصفير أرباح الكاش
    conn.commit()
    conn.close()
    return redirect('/reports?success=reset_profits')

@app.route('/delete_merchant_log/<int:log_id>')
def delete_merchant_log(log_id):
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    conn = get_db_connection()
    log = conn.execute("SELECT * FROM merchant_logs WHERE id=?", (log_id,)).fetchone()
    if log:
        amt_impact = log['total_debt_increase']
        unit_amt = log['amount']
        curr_debt = float((get_setting('merchant_debt') or '0').replace(',', ''))
        conn.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('merchant_debt', str(curr_debt - amt_impact)))
        if log['type'] == 'purchase':
            conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (unit_amt, session['user_id']))
        else:
            curr_cash = float((get_setting('cash_drawer') or '0').replace(',', ''))
            conn.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('cash_drawer', str(curr_cash + abs(amt_impact))))
        conn.execute("DELETE FROM merchant_logs WHERE id=?", (log_id,))
        conn.commit()
    conn.close()
    return redirect('/merchants')

MERCHANT_HTML_TEMPLATE = HTML_BASE.replace('{% block content %}{% endblock %}', """
<div class="container-fluid fade-in">
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h3 class="fw-black text-primary"><i class="fas fa-balance-scale me-2"></i> دفتر الديون الشامل (لنا وعلينا)</h3>
        <div class="bg-white px-4 py-2 rounded-pill shadow-sm border border-success">
            <span class="text-success fw-bold me-2"><i class="fas fa-cash-register me-1"></i> الكاش بالدرج:</span>
            <span class="fw-black fs-5" dir="ltr">{{ "{:,.0f}".format(cash) }} <small>ل.س</small></span>
        </div>
    </div>

    {{ msg|safe }}

    <div class="row g-4 mb-4">
        <div class="col-lg-6">
            <div class="card-bank border-danger border-start border-5 h-100 bg-white shadow-sm">
                <div class="text-center mb-4">
                    <p class="text-danger fw-bold mb-1"><i class="fas fa-arrow-down me-1"></i> ديون الموردين (علينا دفعها)</p>
                    <h2 class="fw-black text-danger m-0" dir="ltr">{{ "{:,.0f}".format(m_debt) }} <small class="fs-6">ل.س</small></h2>
                    <form action="/clear_merchant_data/debt" method="POST" class="mt-3">
                        <button type="submit" class="btn btn-sm btn-outline-danger rounded-pill fw-bold" onclick="return confirm('هل أنت متأكد من تصفير ديون الموردين لتصبح صفر؟');"><i class="fas fa-trash-alt me-1"></i> تصفير الرقم بالكامل</button>
                    </form>
                </div>
                <div class="row g-2 mb-4">
                    <div class="col-6">
                        <form method="POST" class="p-3 bg-light rounded border h-100">
                            <input type="hidden" name="action" value="buy">
                            <label class="small fw-bold text-danger mb-2">سحب بضاعة (دين جديد)</label>
                            <input type="text" name="merchant_name" class="form-control form-control-sm mb-2 border-danger" placeholder="اسم التاجر..." required>
                            <input type="number" name="amount" class="form-control form-control-sm mb-2 border-danger" placeholder="الكمية (وحدات)..." required>
                            <button type="submit" class="btn btn-danger w-100 fw-bold btn-sm">تقييد الدين 📝</button>
                        </form>
                    </div>
                    <div class="col-6">
                        <form method="POST" class="p-3 bg-light rounded border h-100">
                            <input type="hidden" name="action" value="pay">
                            <label class="small fw-bold text-success mb-2">تسديد للتاجر (كاش)</label>
                            <input type="text" name="merchant_name" class="form-control form-control-sm mb-2 border-success" placeholder="اسم التاجر..." required>
                            <input type="number" name="amount" class="form-control form-control-sm mb-2 border-success" placeholder="المبلغ كاش..." required>
                            <button type="submit" class="btn btn-success w-100 fw-bold btn-sm">دفع من الدرج 💸</button>
                        </form>
                    </div>
                </div>
            </div>
        </div>
        <div class="col-lg-6">
            <div class="card-bank border-success border-start border-5 h-100 bg-white shadow-sm">
                <div class="text-center mb-4">
                    <p class="text-success fw-bold mb-1"><i class="fas fa-arrow-up me-1"></i> ديون الزبائن (لنا بالسوق)</p>
                    <h2 class="fw-black text-success m-0" dir="ltr">{{ "{:,.0f}".format(total_user_debt) }} <small class="fs-6">ل.س</small></h2>
                </div>
                <p class="fw-bold small text-muted mb-2"><i class="fas fa-users"></i> قائمة المديونين للتحصيل المباشر</p>
                <div class="table-responsive" style="max-height: 250px; overflow-y: auto;">
                    <table class="table text-center align-middle table-hover table-sm">
                        <thead class="table-light" style="position: sticky; top: 0;">
                            <tr><th>الزبون</th><th>عليه (ل.س)</th><th>تحصيل الكاش</th></tr>
                        </thead>
                        <tbody>{{ debtors_html|safe }}</tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div class="row g-4">
        <div class="col-md-6">
            <div class="card-bank shadow-sm">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h6 class="fw-bold text-danger m-0"><i class="fas fa-list me-2"></i> سجل المشتريات (أخذناها)</h6>
                    <form action="/clear_merchant_data/purchases" method="POST">
                        <button type="submit" class="btn btn-sm btn-danger shadow-sm fw-bold" onclick="return confirm('تأكيد مسح جميع سجلات المشتريات لتنظيف الشاشة؟ (لا يؤثر على الأرصدة)');"><i class="fas fa-trash-alt me-1"></i> مسح السجل</button>
                    </form>
                </div>
                <div class="table-responsive" style="max-height: 300px; overflow-y: auto;">
                    <table class="table table-sm text-center align-middle">
                        <thead class="table-light" style="position: sticky; top: 0;"><tr><th>التاجر</th><th>الكمية</th><th>قيمة الدين</th><th>إدارة</th></tr></thead>
                        <tbody>
                            {% for p in purchases %}
                            <tr><td class="fw-bold">{{ p.merchant_name }}</td><td dir="ltr">{{ "{:,.0f}".format(p.amount) }}</td><td class="text-danger fw-bold" dir="ltr">+ {{ "{:,.0f}".format(p.total_debt_increase) }}</td><td><a href="/delete_merchant_log/{{ p.id }}" class="btn btn-sm btn-outline-danger" onclick="return confirm('حذف العملية آلياً (وإلغاء تأثيرها)؟')"><i class="fas fa-trash"></i></a></td></tr>
                            {% endfor %}
                            {% if not purchases %}<tr><td colspan="4" class="text-muted">لا يوجد سجلات</td></tr>{% endif %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="card-bank shadow-sm">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h6 class="fw-bold text-success m-0"><i class="fas fa-check-double me-2"></i> سجل التسديدات (دفعناها)</h6>
                    <form action="/clear_merchant_data/payments" method="POST">
                        <button type="submit" class="btn btn-sm btn-danger shadow-sm fw-bold" onclick="return confirm('تأكيد مسح جميع سجلات التسديدات لتنظيف الشاشة؟ (لا يؤثر على الأرصدة)');"><i class="fas fa-trash-alt me-1"></i> مسح السجل</button>
                    </form>
                </div>
                <div class="table-responsive" style="max-height: 300px; overflow-y: auto;">
                    <table class="table table-sm text-center align-middle">
                        <thead class="table-light" style="position: sticky; top: 0;"><tr><th>التاجر</th><th>المبلغ المدفوع</th><th>الوقت</th><th>إدارة</th></tr></thead>
                        <tbody>
                            {% for py in payments %}
                            <tr><td class="fw-bold">{{ py.merchant_name }}</td><td class="text-success fw-bold" dir="ltr">- {{ "{:,.0f}".format(py.total_debt_increase|abs) }}</td><td class="small text-muted" dir="ltr">{{ py.date[5:16] }}</td><td><a href="/delete_merchant_log/{{ py.id }}" class="btn btn-sm btn-outline-danger" onclick="return confirm('حذف العملية آلياً (وإلغاء تأثيرها)؟')"><i class="fas fa-trash"></i></a></td></tr>
                            {% endfor %}
                            {% if not payments %}<tr><td colspan="4" class="text-muted">لا يوجد سجلات</td></tr>{% endif %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
</div>
""")

# =============================================================
# إدارة العملاء، الوكلاء، الشركات والفئات
# =============================================================
@app.route('/employee_dashboard', methods=['GET', 'POST'])
def employee_dashboard():
    if not session.get('logged_in') or session.get('role') != 'employee': return redirect('/')
    conn = get_db_connection()
    emp_id = session['user_id']
    today = datetime.now(local_tz).strftime("%Y-%m-%d")

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'transfer':
            uid_str, amt_str, w_type, payment_method = request.form.get('uid'), request.form.get('amt'), request.form.get('wallet_type'), request.form.get('payment_method', 'cash')
            if uid_str and amt_str and w_type:
                try:
                    uid, amt = int(uid_str), float(amt_str)

                    # 🛡️ الحماية من الاحتيال (يُمنع الموظف من التحويل لنفسه لاختلاق كاش وهمي)
                    if uid == emp_id:
                        return "<script>alert('❌ عملية احتيال مرفوضة: لا يمكنك بيع الرصيد لنفسك!'); window.location.href='/employee_dashboard';</script>"

                    if amt > 0:
                        # 🛡️ عملية خصم وإضافة آمنة للموظف والزبون معاً (Atomic)
                        cur = conn.cursor()
                        balance_col = 'balance' if w_type == 'units' else 'bills_balance'

                        # خصم من الموظف فقط إذا كان يملك الرصيد
                        cur.execute(f"UPDATE users SET {balance_col} = {balance_col} - ? WHERE user_id = ? AND {balance_col} >= ?", (amt, emp_id, amt))

                        if cur.rowcount == 0:
                            conn.rollback()
                            return redirect('/employee_dashboard?err=nofunds')

                        # تحديث الزبون
                        user = conn.execute("SELECT custom_sell_price FROM users WHERE user_id=?", (uid,)).fetchone()
                        unit_cost = float(get_setting('current_unit_cost') or 1.05)
                        actual_paid = amt * float(user['custom_sell_price'] or 1.05) if user and w_type == 'units' else amt
                        profit = actual_paid - (amt * unit_cost) if w_type == 'units' else 0
                        is_debt = 1 if payment_method == 'debt' else 0

                        cur.execute(f"UPDATE users SET {balance_col} = {balance_col} + ? WHERE user_id = ?", (amt, uid))

                        if payment_method == 'cash':
                            cur.execute("UPDATE users SET emp_cash = emp_cash + ? WHERE user_id = ?", (actual_paid, emp_id))

                        if profit > 0:
                            cur.execute("UPDATE users SET emp_profit = emp_profit + ? WHERE user_id = ?", (profit, emp_id))

                        if is_debt:
                            cur.execute("UPDATE users SET debt_balance = debt_balance + ? WHERE user_id = ?", (actual_paid, uid))

                        cur.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    (uid, emp_id, amt, actual_paid, w_type, profit, is_debt, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
                        conn.commit()
                        return redirect('/employee_dashboard?success=1')
                except ValueError: pass

        elif action == 'settle_debt':
            uid_str, amt_str = request.form.get('debt_uid'), request.form.get('debt_amt')
            if uid_str and amt_str:
                try:
                    uid, amt = int(uid_str), float(amt_str)
                    if amt > 0:
                        conn.execute("UPDATE users SET debt_balance = debt_balance - ? WHERE user_id = ?", (amt, uid))
                        conn.execute("UPDATE users SET emp_cash = emp_cash + ? WHERE user_id = ?", (amt, emp_id))
                        conn.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (uid, emp_id, 0, amt, 'debt_payment', 0, 0, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
                        conn.commit()
                        return redirect('/employee_dashboard?success=debt_paid')
                except ValueError: pass

        elif action == 'take_advance':
            adv_amt_str = request.form.get('adv_amt')
            if adv_amt_str:
                try:
                    adv_amt = float(adv_amt_str)
                    emp = conn.execute("SELECT emp_cash FROM users WHERE user_id=?", (emp_id,)).fetchone()
                    if adv_amt > 0 and emp['emp_cash'] >= adv_amt:
                        # 🌟 سحب من كاش الموظف وزيادة دينه للمدير 🌟
                        conn.execute("UPDATE users SET emp_cash = emp_cash - ?, debt_balance = debt_balance + ? WHERE user_id = ?", (adv_amt, adv_amt, emp_id))
                        conn.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (emp_id, emp_id, 0, adv_amt, 'advance_payment', 0, 1, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
                        conn.commit()
                        return redirect('/employee_dashboard?success=advance_taken')
                    else:
                        return redirect('/employee_dashboard?err=nocash')
                except ValueError: pass

    # --- تجميع بيانات الواجهة ---
    emp_data = conn.execute("SELECT balance, bills_balance, debt_balance, emp_cash, emp_profit FROM users WHERE user_id=?", (emp_id,)).fetchone()
    users_list = conn.execute("SELECT user_id, real_name, balance, bills_balance, debt_balance FROM users WHERE role='user' AND is_approved=1 AND is_banned=0 ORDER BY real_name").fetchall()
    logs = conn.execute("SELECT d.*, u.real_name FROM deposit_logs d JOIN users u ON d.user_id = u.user_id WHERE d.by_admin_id=? AND d.date LIKE ? ORDER BY d.id DESC", (emp_id, f"{today}%")).fetchall()
    conn.close()

    user_options = "<option value='' disabled selected>اختر الزبون المستلم...</option>"
    debt_options = "<option value='' disabled selected>اختر الزبون المديون...</option>"
    for u in users_list:
        user_options += f"<option value='{u['user_id']}'>👤 {u['real_name']} | رصيده: {u['balance']:g}</option>"
        if u['debt_balance'] > 0: debt_options += f"<option value='{u['user_id']}'>👤 {u['real_name']} | دينه: {u['debt_balance']:g} ل.س</option>"

    logs_html = ""
    for l in logs:
        l_dict = dict(l)
        time_only = l_dict['date'][11:16]
        if l_dict['wallet_type'] == 'debt_payment':
            logs_html += f"<tr><td>{time_only}</td><td class='fw-bold text-primary fs-6'>{l_dict['real_name']}</td><td><span class='badge bg-info text-dark'>تسديد ديون</span></td><td><span class='text-success fw-bold'>+ {l_dict['actual_paid']:g} كاش</span></td></tr>"
        elif l_dict['wallet_type'] == 'advance_payment':
            logs_html += f"<tr><td>{time_only}</td><td class='fw-bold text-danger fs-6'>أنت (سلفة)</td><td><span class='badge bg-danger'>سحب كاش</span></td><td><span class='text-danger fw-bold'>- {l_dict['actual_paid']:g} كاش</span></td></tr>"
        else:
            w_icon, pay_type = ("📱" if l_dict['wallet_type'] == 'units' else "🧾", "📝 عالدين" if l_dict['is_debt'] else "💵 كاش")
            logs_html += f"<tr><td>{time_only}</td><td class='fw-bold text-primary fs-6'>{l_dict['real_name']}<br><small class='text-muted'>{pay_type}</small></td><td><span class='badge bg-primary'>مبيع</span></td><td><span class='text-danger fw-bold'>- {l_dict['amount']:g} {w_icon}</span></td></tr>"

    if not logs_html: logs_html = "<tr><td colspan='4' class='text-muted py-4'>لم تقم بأي عملية اليوم.</td></tr>"

    # --- تصميم واجهة الكاشير الجديدة (الرفاعي للاتصالات) ---
    content = f'''
    <style>
        .pos-bg {{ background: #f8fafc; min-height: 100vh; }}
        .pos-card {{ background: #fff; border-radius: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.03); border: 1px solid #e2e8f0; }}
        .pos-btn {{ border-radius: 15px; font-weight: 900; transition: 0.2s; padding: 20px; display: flex; flex-direction: column; align-items: center; justify-content: center; border: none; }}
        .pos-btn:hover {{ transform: translateY(-5px); box-shadow: 0 10px 25px rgba(0,0,0,0.1); }}
        .pos-btn i {{ font-size: 2rem; margin-bottom: 10px; }}
        .cash-drawer-box {{ background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); color: #fff; border-radius: 24px; padding: 30px; position: relative; overflow: hidden; box-shadow: 0 20px 40px rgba(15,23,42,0.2); }}
        .cash-drawer-box::after {{ content: '💵'; position: absolute; right: -20px; bottom: -20px; font-size: 8rem; opacity: 0.1; transform: rotate(-15deg); }}
    </style>

    <div class="d-flex justify-content-between align-items-center mb-4 border-bottom pb-3">
        <div>
            <h3 class="fw-black text-primary m-0"><i class="fas fa-store me-2"></i> {{ app_name }}</h3>
            <p class="text-muted fw-bold small m-0 mt-1">نظام الكاشير ونقاط البيع المستقلة</p>
        </div>
        <a href="/logout" class="btn btn-danger fw-bold rounded-pill px-4 shadow-sm"><i class="fas fa-sign-out-alt me-1"></i> تسجيل خروج</a>
    </div>

    {{% if request.args.get('success') %}}<div class="alert alert-success fw-bold shadow-sm rounded-4 mb-4"><i class="fas fa-check-circle me-2"></i> تمت العملية بنجاح، وتم تحديث صندوقك!</div>{{% endif %}}
    {{% if request.args.get('err') == 'nocash' %}}<div class="alert alert-danger fw-bold shadow-sm rounded-4 mb-4"><i class="fas fa-exclamation-triangle me-2"></i> الكاش الفعلي في درجك لا يكفي للسحب!</div>{{% endif %}}

    <div class="row g-3 mb-4">
        <div class="col-md-4">
            <div class="pos-card p-3 d-flex align-items-center border-start border-primary border-5">
                <div class="bg-primary bg-opacity-10 text-primary rounded-circle p-3 me-3"><i class="fas fa-mobile-alt fs-4"></i></div>
                <div><p class="text-muted fw-bold mb-1 small">بضاعتك المتبقية (وحدات)</p><h4 class="fw-black m-0" dir="ltr">{emp_data['balance']:,.2f}</h4></div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="pos-card p-3 d-flex align-items-center border-start border-success border-5">
                <div class="bg-success bg-opacity-10 text-success rounded-circle p-3 me-3"><i class="fas fa-receipt fs-4"></i></div>
                <div><p class="text-muted fw-bold mb-1 small">بضاعتك المتبقية (فواتير)</p><h4 class="fw-black m-0" dir="ltr">{emp_data['bills_balance']:,.2f}</h4></div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="pos-card p-3 d-flex align-items-center border-start border-warning border-5 bg-warning bg-opacity-10">
                <div class="bg-warning text-dark rounded-circle p-3 me-3"><i class="fas fa-chart-line fs-4"></i></div>
                <div><p class="text-dark fw-bold mb-1 small">إجمالي أرباحك الصافية 🤩</p><h4 class="fw-black m-0 text-dark" dir="ltr">{emp_data['emp_profit']:,.0f} <small class="fs-6">ل.س</small></h4></div>
            </div>
        </div>
    </div>

    <div class="row g-4 mb-4">
        <div class="col-lg-6">
            <div class="cash-drawer-box h-100 d-flex flex-column justify-content-center text-center">
                <p class="text-white-50 fw-bold mb-2 fs-5"><i class="fas fa-cash-register me-2"></i> الكاش الفعلي في درجك الآن</p>
                <h1 class="fw-black m-0" style="font-size: 3.5rem; letter-spacing: 2px;" dir="ltr">{emp_data['emp_cash']:,.0f}</h1>
                <p class="text-warning fw-bold mt-2 mb-0">ليرة سورية (مستقل عن الإدارة)</p>
            </div>
        </div>

        <div class="col-lg-6">
            <div class="row g-3 h-100">
                <div class="col-6">
                    <button class="pos-btn bg-primary text-white w-100 h-100" data-bs-toggle="modal" data-bs-target="#sellModal">
                        <i class="fas fa-shopping-cart"></i> بيع جديد
                    </button>
                </div>
                <div class="col-6">
                    <button class="pos-btn bg-info text-dark w-100 h-100" data-bs-toggle="modal" data-bs-target="#debtModal">
                        <i class="fas fa-hand-holding-usd"></i> قبض دفعة دين
                    </button>
                </div>
                <div class="col-12">
                    <button class="pos-btn bg-danger bg-opacity-10 text-danger w-100 py-3 border border-danger border-opacity-25" data-bs-toggle="modal" data-bs-target="#advanceModal">
                        <i class="fas fa-wallet fs-4 mb-2"></i> سحب سلفة (مصروف شخصي من الدرج)
                    </button>
                </div>
            </div>
        </div>
    </div>

    <div class="row g-4 mb-5">
        <div class="col-lg-8">
            <div class="pos-card p-4 h-100">
                <h5 class="fw-bold mb-4 text-dark"><i class="fas fa-list-alt text-primary me-2"></i> عمليات اليوم (مبيعاتك)</h5>
                <div class="table-responsive" style="max-height: 300px; overflow-y: auto;">
                    <table class="table text-center align-middle">
                        <thead class="table-light" style="position: sticky; top: 0;"><tr><th>الوقت</th><th>العملية / الزبون</th><th>النوع</th><th>المبلغ/الكمية</th></tr></thead>
                        <tbody>{logs_html}</tbody>
                    </table>
                </div>
            </div>
        </div>
        <div class="col-lg-4">
            <div class="pos-card p-4 h-100 text-center d-flex flex-column justify-content-center border-warning border-2" style="background: #fffdf5;">
                <i class="fas fa-flag-checkered text-warning mb-3" style="font-size: 3rem;"></i>
                <h5 class="fw-black mb-3">تسليم الصندوق</h5>
                <p class="text-muted small fw-bold mb-4">بضغطة واحدة سيتم إرسال كشف حساب مفصل للمدير يحتوي على درجك وأرباحك.</p>
                <form action="/close_shift" method="POST" onsubmit="return confirm('تأكيد إنهاء الوردية وإرسال التقرير للإدارة؟');">
                    <button type="submit" class="btn btn-warning btn-lg w-100 fw-black shadow text-dark rounded-pill py-3">تسليم الكاش وإنهاء الوردية 🔒</button>
                </form>
            </div>
        </div>
    </div>

    <div class="modal fade" id="sellModal" tabindex="-1">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content border-0 shadow-lg rounded-4">
                <div class="modal-header bg-primary text-white border-0 rounded-top-4">
                    <h5 class="modal-title fw-bold"><i class="fas fa-shopping-cart me-2"></i> بيع رصيد لزبون</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <form method="POST">
                    <div class="modal-body p-4">
                        <input type="hidden" name="action" value="transfer">
                        <div class="mb-3"><label class="fw-bold text-muted mb-2">الزبون</label><select name="uid" class="form-select bg-light border-0 p-3 shadow-sm" required>{user_options}</select></div>
                        <div class="mb-3"><label class="fw-bold text-muted mb-2">الكمية المباعة</label><input name="amt" type="number" step="0.01" class="form-control bg-light border-0 p-3 shadow-sm" required></div>
                        <div class="row g-3">
                            <div class="col-6"><label class="fw-bold text-muted mb-2">المحفظة</label><select name="wallet_type" class="form-select bg-light border-0 p-3 shadow-sm" required><option value="units">وحدات 📱</option><option value="bills">فواتير 🧾</option></select></div>
                            <div class="col-6"><label class="fw-bold text-muted mb-2">الدفع</label><select name="payment_method" class="form-select bg-light border-0 p-3 shadow-sm text-danger fw-bold" required><option value="cash">كاش (للدرج) 💵</option><option value="debt">آجل (عالدين) 📝</option></select></div>
                        </div>
                    </div>
                    <div class="modal-footer border-0 pb-4 px-4"><button type="button" class="btn btn-light shadow-sm fw-bold w-25" data-bs-dismiss="modal">إلغاء</button><button type="submit" class="btn btn-primary shadow-sm fw-bold flex-grow-1"><i class="fas fa-check me-2"></i> تأكيد البيع وإضافة الكاش</button></div>
                </form>
            </div>
        </div>
    </div>

    <div class="modal fade" id="debtModal" tabindex="-1">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content border-0 shadow-lg rounded-4">
                <div class="modal-header bg-info text-dark border-0 rounded-top-4">
                    <h5 class="modal-title fw-bold"><i class="fas fa-hand-holding-usd me-2"></i> قبض دفعة من مديون</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <form method="POST">
                    <div class="modal-body p-4">
                        <input type="hidden" name="action" value="settle_debt">
                        <div class="mb-3"><label class="fw-bold text-muted mb-2">الزبون المديون</label><select name="debt_uid" class="form-select bg-light border-0 p-3 shadow-sm border-start border-info border-4" required>{debt_options}</select></div>
                        <div class="mb-3"><label class="fw-bold text-muted mb-2">المبلغ المقبوض كاش (ل.س)</label><input name="debt_amt" type="number" step="0.01" class="form-control bg-light border-0 p-3 shadow-sm border-start border-info border-4" required></div>
                    </div>
                    <div class="modal-footer border-0 pb-4 px-4"><button type="button" class="btn btn-light shadow-sm fw-bold w-25" data-bs-dismiss="modal">إلغاء</button><button type="submit" class="btn btn-info shadow-sm fw-bold flex-grow-1 text-white"><i class="fas fa-save me-2"></i> إدخال الكاش لدرجي</button></div>
                </form>
            </div>
        </div>
    </div>

    <div class="modal fade" id="advanceModal" tabindex="-1">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content border-0 shadow-lg rounded-4">
                <div class="modal-header bg-danger text-white border-0 rounded-top-4">
                    <h5 class="modal-title fw-bold"><i class="fas fa-wallet me-2"></i> سحب سلفة من الدرج</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <form method="POST">
                    <div class="modal-body p-4">
                        <input type="hidden" name="action" value="take_advance">
                        <div class="alert alert-warning fw-bold small"><i class="fas fa-info-circle me-1"></i> المبلغ الذي ستسحبه سيُنقص من الكاش الموجود في درجك الآن، وسيُسجل كدين عليك شخصياً.</div>
                        <div class="mb-3"><label class="fw-bold text-muted mb-2">المبلغ المراد سحبه (ل.س)</label><input name="adv_amt" type="number" step="0.01" class="form-control bg-light border-0 p-3 shadow-sm border-start border-danger border-4" required></div>
                    </div>
                    <div class="modal-footer border-0 pb-4 px-4"><button type="button" class="btn btn-light shadow-sm fw-bold w-25" data-bs-dismiss="modal">إلغاء</button><button type="submit" class="btn btn-danger shadow-sm fw-bold flex-grow-1"><i class="fas fa-minus-circle me-2"></i> سحب الكاش من الدرج</button></div>
                </form>
            </div>
        </div>
    </div>
    '''
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='employee_dashboard')

@app.route('/close_shift', methods=['POST'])
def close_shift():
    if not session.get('logged_in') or session.get('role') != 'employee': return redirect('/')
    conn = get_db_connection()
    emp_id = session['user_id']
    today = datetime.now(local_tz).strftime("%Y-%m-%d")

    # 1. جلب بيانات الموظف
    emp_data = conn.execute("SELECT balance, bills_balance, emp_cash, emp_profit, real_name, debt_balance FROM users WHERE user_id=?", (emp_id,)).fetchone()

    # 2. حساب الديون التي سجلها الموظف على الزبائن اليوم (للإحصائية فقط)
    emp_stats = conn.execute("SELECT SUM(CASE WHEN is_debt = 1 THEN actual_paid ELSE 0 END) as debt_given FROM deposit_logs WHERE by_admin_id = ? AND date LIKE ? AND wallet_type IN ('units', 'bills')", (emp_id, f"{today}%")).fetchone()
    debt_today = emp_stats['debt_given'] or 0

    emp_cash = float(emp_data['emp_cash'] or 0)
    emp_profit = float(emp_data['emp_profit'] or 0)

    # 3. الخوارزمية الذكية لفصل الربح عن الكاش
    # (يأخذ الموظف أرباحه من الكاش المتاح، وما يتبقى من أرباح عالدين يُحفظ للغد)
    withdrawn_profit = min(emp_cash, emp_profit)
    net_to_admin = emp_cash - withdrawn_profit
    remaining_profit = emp_profit - withdrawn_profit

    # 4. نقل الكاش الصافي لدرج الإدارة وتصفير درج الموظف
    if emp_cash > 0:
        curr_cash = float((get_setting('cash_drawer') or '0').replace(',', ''))
        conn.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('cash_drawer', str(curr_cash + net_to_admin)))

        conn.execute("UPDATE users SET emp_cash = 0, emp_profit = ? WHERE user_id = ?", (remaining_profit, emp_id))

        # توثيق التسليم في السجل المركزي
        conn.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (1, emp_id, 0, net_to_admin, 'shift_close', withdrawn_profit, 0, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))

    conn.commit()
    conn.close()

    # 5. إرسال التقرير الإداري المفصل عبر التلغرام
    try:
        rem_prof_text = f"\n⚠️ *ملاحظة:* بقي للموظف أرباح غير مقبوضة (بسبب الديون) بقيمة `{remaining_profit:,.0f}` ل.س رُحلت للغد." if remaining_profit > 0 else ""

        msg = f"""📊 *تقرير تقفيل الصندوق (نهاية الوردية)* 🏁

🧑‍💻 *الموظف:* {emp_data['real_name']}
📅 *التاريخ:* {datetime.now(local_tz).strftime('%Y-%m-%d %H:%M')}

📦 *البضاعة المتبقية معه لغداً:*
📱 وحدات: `{emp_data['balance']:,.2f}`
🧾 فواتير: `{emp_data['bills_balance']:,.2f}`

📝 *ديون سجلها على الزبائن اليوم:* `{debt_today:,.0f}` ل.س
⚠️ *سلف أخذها (دينه للمدير):* `{emp_data['debt_balance']:,.0f}` ل.س

💵 *الكاش الإجمالي بدرج الموظف:* `{emp_cash:,.0f}` ل.س
🤩 *أرباح الموظف الإجمالية:* `{emp_profit:,.0f}` ل.س
━━━━━━━━━━━━━━
🤝 *المبلغ الصافي المطلوب استلامه منه الآن:*
*(الكاش الإجمالي - الأرباح التي أخذها لنفسه)*
👉 `{net_to_admin:,.0f}` ل.س 👈
{rem_prof_text}

✅ *ملاحظة إدارية:* تم تصفير درج الموظف، وتمت إضافة المبلغ الصافي ({net_to_admin:,.0f} ل.س) إلى درجك المركزي آلياً."""
        bot.send_message(ADMIN_TG_ID, msg, parse_mode="Markdown")
    except: pass

    return redirect('/employee_dashboard?success=1')

@app.route('/edit_user_name_shop', methods=['POST'])
def edit_user_name_shop():
    if not session.get('logged_in') or session.get('role') != 'admin': 
        return redirect('/')
    
    uid = request.form.get('user_id')
    new_name = request.form.get('new_name')
    new_shop = request.form.get('new_shop', '')
    
    if uid and new_name:
        conn = get_db_connection()
        # تحديث الاسم واسم المحل في قاعدة البيانات
        conn.execute("UPDATE users SET real_name=?, shop_name=? WHERE user_id=?", (new_name.strip(), new_shop.strip(), int(uid)))
        conn.commit()
        conn.close()
        
    return redirect('/users')


@app.route('/users')
def users_page():
    if not session.get('logged_in'): return redirect('/')
    search_query, status_filter = request.args.get('search', '').strip(), request.args.get('status', 'all')
    conn = get_db_connection()
    query, params = "SELECT * FROM users WHERE role IN ('user', 'employee')", []
    if search_query:
        query += " AND (real_name LIKE ? OR phone_contact LIKE ? OR user_id LIKE ?)"
        params.extend([f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"])
    if status_filter == 'active': query += " AND is_approved=1 AND is_banned=0"
    elif status_filter == 'banned': query += " AND is_banned=1"
    elif status_filter == 'frozen': query += " AND is_approved=0 AND is_banned=0"
    query += " ORDER BY user_id DESC"
    users = conn.execute(query, params).fetchall()
    conn.close()

    cards_html = ""
    for u in users:
        u_dict = dict(u)
        is_vip = u_dict.get('is_vip') or 0
        debt = u_dict.get('debt_balance') or 0
        bal = u_dict.get('balance') or 0
        bills_bal = u_dict.get('bills_balance') or 0
        role_txt = 'موظف' if u_dict['role'] == 'employee' else 'عميل'

        status_cls = 'bg-success bg-opacity-10 text-success' if u_dict['is_approved'] and not u_dict['is_banned'] else 'bg-warning bg-opacity-10 text-warning'
        status_txt = 'نشط' if u_dict['is_approved'] and not u_dict['is_banned'] else 'مجمد'
        if u_dict['is_banned']:
            status_cls = 'bg-danger bg-opacity-10 text-danger'
            status_txt = 'محظور'

        icon_cls = "fa-laptop-house text-warning" if u_dict.get('access_method') == 'web' else "fa-user-tie text-white"
        bill_perm_status = u_dict.get('can_pay_bills', 0)
        bill_btn_cls = "text-success bg-success" if bill_perm_status == 1 else "text-muted bg-secondary"
        bill_btn_title = "إلغاء الفواتير" if bill_perm_status == 1 else "تفعيل الفواتير"

        # 👇 تعديلات الحماية وتجهيز الاسم مع المحل 👇
        safe_name = str(u_dict['real_name'] or '').replace("'", "\\'").replace('"', '&quot;')
        shop_name = u_dict.get('shop_name') or ""
        safe_shop = str(shop_name).replace("'", "\\'").replace('"', '&quot;')
        
        # دمج الاسم مع اسم المحل للعرض
        if shop_name:
            display_name = f"{u_dict['real_name']} 🏢 ({shop_name})"
        else:
            display_name = u_dict['real_name']

        vip_btn_cls = "text-warning bg-warning" if is_vip else "text-muted bg-secondary"
        vip_btn_title = "إلغاء الـ VIP" if is_vip else "ترقية لـ VIP 👑"

        cards_html += f"""
        <div class="col-xl-4 col-md-6 user-item" data-name="{u_dict['real_name']}" data-id="{u_dict['user_id']}">
            <div class="user-card-premium">
                <div class="d-flex align-items-start justify-content-between mb-3">
                    <div class="d-flex align-items-center">
                        <div class="avatar-circle me-3 shadow-sm" style="background: var(--primary); width: 50px; height: 50px; border-radius: 15px; display: flex; align-items: center; justify-content: center;">
                            <i class="fas {icon_cls} fs-4"></i>
                        </div>
                        <div>
                            <h6 class="fw-black mb-0 text-dark">{'⭐' if is_vip else ''} {display_name}</h6>
                            <small class="text-muted fw-bold">ID: {u_dict['user_id']} | {role_txt}</small>
                        </div>
                    </div>
                    <span class="status-badge {status_cls}">{status_txt}</span>
                </div>

                <div class="row g-2 mb-3 text-center">
                    <div class="col-4">
                        <div class="p-2 rounded-3 bg-light border">
                            <small class="d-block text-muted" style="font-size: 10px; font-weight: 800;">وحدات</small>
                            <span class="fw-black text-primary">{bal:,.1f}</span>
                        </div>
                    </div>
                    <div class="col-4">
                        <div class="p-2 rounded-3 bg-light border">
                            <small class="d-block text-muted" style="font-size: 10px; font-weight: 800;">فواتير</small>
                            <span class="fw-black text-success">{bills_bal:,.1f}</span>
                        </div>
                    </div>
                    <div class="col-4">
                        <div class="p-2 rounded-3 bg-danger bg-opacity-10 border border-danger border-opacity-25">
                            <small class="d-block text-danger" style="font-size: 10px; font-weight: 800;">ديون</small>
                            <span class="fw-black text-danger">{debt:,.0f}</span>
                        </div>
                    </div>
                </div>

                <div class="d-flex gap-2 justify-content-center border-top pt-3">
                    <button class="quick-action-btn bg-primary bg-opacity-10 text-primary border-0" onclick="openRechargeModal('{u_dict['user_id']}', '{safe_name}')" title="شحن سريع">
                        <i class="fas fa-bolt"></i>
                    </button>
                    <!-- الزر الجديد لتعديل الاسم واسم المحل -->
                    <button class="quick-action-btn bg-success bg-opacity-10 text-success border-0" onclick="openEditNameModal('{u_dict['user_id']}', '{safe_name}', '{safe_shop}')" title="تعديل الاسم والمحل">
                        <i class="fas fa-edit"></i>
                    </button>
                    <button class="quick-action-btn bg-warning bg-opacity-10 text-warning border-0" onclick="showUserLog('{u_dict['user_id']}', '{safe_name}')" title="سجل الشحن والتاريخ">
                        <i class="fas fa-history"></i>
                    </button>
                    <a href="/user/{u_dict['user_id']}" class="quick-action-btn bg-secondary bg-opacity-10 text-secondary border-0" title="كشف حساب كامل">
                        <i class="fas fa-eye"></i>
                    </a>
                    <button class="quick-action-btn bg-info bg-opacity-10 text-info border-0" onclick="openPmModal('{u_dict['user_id']}', '{safe_name}')" title="إرسال رسالة">
                        <i class="fas fa-paper-plane"></i>
                    </button>
                    <a href="/toggle_bills_perm/{u_dict['user_id']}" class="quick-action-btn {bill_btn_cls} bg-opacity-10 border-0" title="{bill_btn_title}">
                        <i class="fas fa-file-invoice"></i>
                    </a>
                    <a href="/toggle_vip/{u_dict['user_id']}" class="quick-action-btn {vip_btn_cls} bg-opacity-10 border-0" title="{vip_btn_title}">
                        <i class="fas fa-crown"></i>
                    </a>
                    <a href="/toggle_freeze/{u_dict['user_id']}" class="quick-action-btn bg-warning bg-opacity-10 text-warning border-0" title="تجميد/تفعيل">
                        <i class="fas fa-snowflake"></i>
                    </a>
                    <a href="/delete_user/{u_dict['user_id']}" class="quick-action-btn bg-danger bg-opacity-10 text-danger border-0" onclick="return confirm('حذف العميل نهائياً؟');" title="حذف">
                        <i class="fas fa-trash"></i>
                    </a>
                </div>
            </div>
        </div>
        """

    content = f"""
    <div class="container-fluid pb-5">
        <div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-3">
            <div>
                <h2 class="fw-black text-primary mb-1">إدارة النخبة 💎</h2>
                <p class="text-muted small fw-bold">تحكم بالعملاء، الموظفين، والديون من مكان واحد.</p>
            </div>
            <div class="d-flex gap-2">
                <a href="/create_web_customer" class="btn btn-warning rounded-pill px-4 fw-black shadow-sm text-dark">
                    <i class="fas fa-laptop-house me-1"></i> زبون ويب
                </a>
                <a href="/manual_debt" class="btn btn-danger rounded-pill px-4 fw-black shadow-sm">
                    <i class="fas fa-plus-circle me-1"></i> دين يدوي
                </a>
            </div>
        </div>

        <div class="mb-4 p-2 border-0 shadow-sm" style="border-radius: 15px; background: white;">
            <div class="input-group">
                <span class="input-group-text bg-transparent border-0"><i class="fas fa-search text-muted"></i></span>
                <input type="text" id="userSearch" class="form-control border-0 bg-transparent fw-bold" placeholder="ابحث فوراً عن اسم العميل، رقمه، أو المعرف..." onkeyup="filterUsers()">
            </div>
        </div>

        <div class="row g-3" id="usersGrid">
            {cards_html}
        </div>

        <div id="noResults" class="text-center py-5" style="display: none;">
            <i class="fas fa-search fs-1 text-muted opacity-50 mb-3"></i>
            <h5 class="text-muted fw-bold">لا يوجد نتائج مطابقة للبحث</h5>
        </div>
    </div>

    <!-- نافذة تعديل الاسم واسم المحل الجديدة -->
    <div class="modal fade" id="editNameModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content shadow-lg border-0" style="border-radius: 30px; background: white;">
                <div class="modal-header bg-success text-white border-0" style="border-radius: 30px 30px 0 0; padding: 20px;">
                    <h5 class="modal-title fw-black"><i class="fas fa-edit me-2"></i> تعديل بيانات العميل</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <form action="/edit_user_name_shop" method="POST">
                    <div class="modal-body p-4 text-center">
                        <input type="hidden" name="user_id" id="edit_user_id_input">
                        
                        <div class="mb-3 text-start">
                            <label class="fw-bold text-muted mb-2">اسم العميل (إجباري):</label>
                            <input type="text" name="new_name" id="edit_name_input" class="form-control form-control-lg fw-black shadow-sm border-0" required style="border-radius: 15px; background: #f8fafc;">
                        </div>
                        
                        <div class="mb-4 text-start">
                            <label class="fw-bold text-muted mb-2">اسم المحل (اختياري):</label>
                            <input type="text" name="new_shop" id="edit_shop_input" class="form-control form-control-lg fw-black shadow-sm border-0" placeholder="مثال: مركز الأمل للاتصالات" style="border-radius: 15px; background: #f8fafc;">
                        </div>
                        
                        <button type="submit" class="btn btn-success btn-lg w-100 rounded-pill fw-black shadow py-3">حفظ التعديلات ✅</button>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <div class="modal fade" id="userLogModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-lg modal-dialog-centered">
            <div class="modal-content text-dark border-0 shadow-lg" style="border-radius: 24px; background: #ffffff;">
                <div class="modal-header border-0 bg-primary text-white" style="border-radius: 24px 24px 0 0; padding: 20px;">
                    <h5 class="modal-title fw-black" id="logModalTitle">سجل شحن المحفظة</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body p-4">
                    <div class="table-responsive" style="max-height: 380px; overflow-y: auto;">
                        <table class="table table-hover align-middle text-center table-sm">
                            <thead class="table-light">
                                <tr>
                                    <th>الحركة</th>
                                    <th>المحفظة المستهدفة وطريقة الدفع</th>
                                    <th>المبلغ المشحون</th>
                                    <th>الحالة</th>
                                    <th>التاريخ والوقت بالضبط</th>
                                </tr>
                            </thead>
                            <tbody id="userLogTableBody">
                                </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="modal fade" id="quickRechargeModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered"> <div class="modal-content recharge-modal-content shadow-lg border-0" style="border-radius: 30px; background: white;">
                <div class="modal-body p-4 text-center">
                    <div class="mb-4 mt-2">
                        <div class="d-inline-flex align-items-center justify-content-center bg-primary bg-opacity-10 text-primary rounded-circle mb-3" style="width: 80px; height: 80px;">
                            <i class="fas fa-bolt-lightning fs-1"></i>
                        </div>
                        <h4 class="fw-black mb-1">شحن رصيد سريع ⚡</h4>
                        <p class="text-muted fw-bold">أنت الآن تشحن لحساب: <span id="modalUserName" class="fw-black text-primary fs-5"></span></p>
                    </div>
                    <form action="/add_bal" method="POST">
                        <input type="hidden" name="uid" id="modalUserId">
                        <div class="mb-3">
    <div class="btn-group w-100 shadow-sm" role="group">
        <input type="radio" class="btn-check" name="action_type" id="act_add" value="add" checked>
        <label class="btn btn-outline-success fw-bold py-2" for="act_add">➕ إيداع (شحن)</label>

        <input type="radio" class="btn-check" name="action_type" id="act_sub" value="sub">
        <label class="btn btn-outline-danger fw-bold py-2" for="act_sub">➖ سحب (خصم)</label>
    </div>
</div>
                        <div class="mb-3">
                            <div class="btn-group w-100 shadow-sm" role="group">
                                <input type="radio" class="btn-check" name="wallet_type" id="w_units" value="units" checked>
                                <label class="btn btn-outline-primary fw-bold py-2" for="w_units">محفظة الوحدات 📱</label>

                                <input type="radio" class="btn-check" name="wallet_type" id="w_bills" value="bills">
                                <label class="btn btn-outline-success fw-bold py-2" for="w_bills">محفظة الفواتير 🧾</label>
                            </div>
                        </div>
                        <div class="mb-3">
                            <input type="number" name="amt" step="0.01" class="form-control form-control-lg text-center fw-black border-0 shadow-sm" placeholder="أدخل الكمية (أرقام فقط)..." required style="border-radius: 15px; background: #f8fafc;">
                        </div>
                        <div class="mb-4">
                            <select name="payment_method" class="form-select form-select-lg fw-bold border-0 shadow-sm text-center" style="border-radius: 15px; background: #f8fafc;">
                                <option value="cash">💵 استلام نقدي (كاش للدرج)</option>
                                <option value="debt">📝 تقييد عالدين (آجل)</option>
                            </select>
                        </div>
                        <div class="d-grid gap-2">
                            <button type="submit" class="btn btn-primary btn-lg rounded-pill fw-black py-3 shadow">تأكيد العملية الآن 🚀</button>
                            <button type="button" class="btn btn-light rounded-pill fw-bold py-2 text-muted" data-bs-dismiss="modal">إلغاء وتراجع</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>

    <script>
        // الكود الجديد لفتح نافذة تعديل الاسم
        function openEditNameModal(id, name, shop) {{
            document.getElementById('edit_user_id_input').value = id;
            document.getElementById('edit_name_input').value = name;
            document.getElementById('edit_shop_input').value = shop;
            var myModal = new bootstrap.Modal(document.getElementById('editNameModal'));
            myModal.show();
        }}

        function openRechargeModal(id, name) {{
            document.getElementById('modalUserId').value = id;
            document.getElementById('modalUserName').innerText = name;
            var myModalEl = document.getElementById('quickRechargeModal');
            var myModal = bootstrap.Modal.getInstance(myModalEl) || new bootstrap.Modal(myModalEl);
            myModal.show();
        }}

        function showUserLog(userId, fullName) {{
            document.getElementById('logModalTitle').innerHTML = '<i class="fas fa-history me-2"></i> كشف إيداعات العميل: <strong class="text-white">' + fullName + '</strong>';
            const tbody = document.getElementById('userLogTableBody');
            tbody.innerHTML = '<tr><td colspan="5" class="text-muted py-4"><i class="fas fa-spinner fa-spin me-2"></i> جاري قراءة سجلات شحن المحفظة والوقت...</td></tr>';

            new bootstrap.Modal(document.getElementById('userLogModal')).show();

            fetch('/get_user_history/' + userId)
                .then(r => r.json())
                .then(data => {{
                    tbody.innerHTML = '';
                    if(data.length === 0) {{
                        tbody.innerHTML = '<tr><td colspan="5" class="text-muted py-4">لا يوجد عمليات إيداع أو شحن مسجلة لهذا العميل.</td></tr>';
                        return;
                    }}
                    data.forEach(log => {{
                        let badge = '<span class="badge bg-success rounded-pill px-2">تم الشحن</span>';
                        let amtColor = log.amount.startsWith('+') ? 'text-success' : 'text-danger';
                        tbody.innerHTML += "<tr><td class='fw-bold text-dark'>" + log.type + "</td><td class='text-primary fw-bold'>" + log.target + "</td><td class='" + amtColor + " fw-black'>" + log.amount + "</td><td>" + badge + "</td><td class='text-muted small fw-bold' dir='ltr'>" + log.date_time + "</td></tr>";
                    }});
                }})
                .catch(() => {{
                    tbody.innerHTML = '<tr><td colspan="5" class="text-danger py-4">فشل اتصال الخادم أثناء جلب السجل.</td></tr>';
                }});
        }}

        function filterUsers() {{
            let input = document.getElementById('userSearch').value.toLowerCase();
            let items = document.getElementsByClassName('user-item');
            let hasVisible = false;
            for (let item of items) {{
                let name = item.getAttribute('data-name').toLowerCase();
                let id = item.getAttribute('data-id').toLowerCase();
                if(name.includes(input) || id.includes(input)) {{
                    item.style.display = "block";
                    hasVisible = true;
                }} else {{
                    item.style.display = "none";
                }}
            }}
            document.getElementById('noResults').style.display = hasVisible ? 'none' : 'block';
        }}
    </script>
    """
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='users')

@app.route('/toggle_agent/<int:uid>')
def toggle_agent(uid):
    if session.get('logged_in') and session.get('role') == 'admin':
        conn = get_db_connection()
        user = conn.execute("SELECT role FROM users WHERE user_id=?", (uid,)).fetchone()
        if user:
            new_role = 'agent' if user['role'] == 'user' else 'user'
            conn.execute("UPDATE users SET role=? WHERE user_id=?", (new_role, uid))
            conn.commit()
        conn.close()
    return redirect(request.referrer or '/users')

@app.route('/toggle_vip/<int:uid>')
def toggle_vip(uid):
    if session.get('logged_in') and session.get('role') == 'admin':
        conn = get_db_connection(); user = conn.execute("SELECT is_vip FROM users WHERE user_id=?", (uid,)).fetchone()
        if user: conn.execute("UPDATE users SET is_vip=? WHERE user_id=?", (0 if dict(user).get('is_vip', 0) == 1 else 1, uid)); conn.commit()
        conn.close()
    return redirect('/users')

@app.route('/send_pm', methods=['POST'])
def send_pm():
    if session.get('logged_in') and session.get('role') == 'admin':
        try:
            uid, msg = request.form.get('uid'), request.form.get('message')
            if uid and msg: bot.send_message(uid, f"رسالة من الإدارة:\n\n{msg}")
        except: pass
    return redirect('/users')

@app.route('/toggle_freeze/<int:uid>')
def toggle_freeze(uid):
    if session.get('logged_in') and session.get('role') == 'admin':
        conn = get_db_connection(); user = conn.execute("SELECT is_approved FROM users WHERE user_id=?", (uid,)).fetchone()
        if user:
            new_status = 0 if user['is_approved'] == 1 else 1
            conn.execute("UPDATE users SET is_approved = ? WHERE user_id=?", (new_status, uid)); conn.commit()
            try: bot.send_message(uid, "✅ *إشعار إداري:*\nتم تفعيل حسابك بنجاح! يمكنك الآن الاستفادة من جميع خدمات النظام." if new_status == 1 else "❄️ *إشعار إداري:*\nتم تجميد حسابك مؤقتاً. يرجى الانتظار لحين التفعيل.", parse_mode="Markdown")
            except: pass
        conn.close()
    return redirect('/users')

@app.route('/toggle_ban/<int:uid>')
def toggle_ban(uid):
    if session.get('logged_in') and session.get('role') == 'admin':
        conn = get_db_connection(); user = conn.execute("SELECT is_banned FROM users WHERE user_id=?", (uid,)).fetchone()
        if user:
            new_status = 0 if user['is_banned'] == 1 else 1
            conn.execute("UPDATE users SET is_banned = ? WHERE user_id=?", (new_status, uid)); conn.commit()
            try: bot.send_message(uid, "🔓 *إشعار إداري:*\nتم رفع الحظر عن حسابك، أهلاً بك مجدداً." if new_status == 0 else "⛔ *إشعار إداري:*\nلقد تم حظر حسابك نهائياً من قبل الإدارة.", parse_mode="Markdown")
            except: pass
        conn.close()
    return redirect('/users')

@app.route('/delete_user/<int:uid>')
def delete_user(uid):
    if session.get('logged_in') and session.get('role') == 'admin':
        conn = get_db_connection()
        user = conn.execute("SELECT balance, bills_balance, debt_balance FROM users WHERE user_id=?", (uid,)).fetchone()

        if user:
            # 🛡️ التحقق من أن حساب الزبون مُصَفّر تماماً (سواء موجب أو سالب) قبل الحذف
            if float(user['balance'] or 0) != 0 or float(user['bills_balance'] or 0) != 0 or float(user['debt_balance'] or 0) != 0:
                conn.close()
                return "<script>alert('❌ إشعار أمان: لا يمكن حذف هذا العميل لأن حسابه غير مُصفّر (قد يكون عليه ديون أو أرصدة عالقة)! قم بتصفير جميع أرصدته وديونه لـ 0 تماماً أولاً.'); window.location.href='/users';</script>"

            # إذا كان حسابه صفراً تماماً، يتم الحذف بأمان
            conn.execute("DELETE FROM users WHERE user_id=?", (uid,))
            conn.commit()

        conn.close()
    return redirect('/users')


@app.route('/add_bal', methods=['POST'])
def add_bal():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    w_type, amt_str, uid_str, action_type, payment_method = request.form.get('wallet_type'), request.form.get('amt'), request.form.get('uid'), request.form.get('action_type', 'add'), request.form.get('payment_method', 'cash')

    if w_type and amt_str and uid_str:
        try:
            amt, uid = float(amt_str), int(uid_str)
            if amt <= 0: return redirect(request.referrer or '/users')

            conn = get_db_connection()
            cur = conn.cursor()
            user = cur.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
            balance_col = 'balance' if w_type == 'units' else 'bills_balance'
            admin_id = session['user_id']

            sell_price = float(user['custom_sell_price'] or 1.05)
            actual_paid = amt * sell_price if w_type == 'units' else amt
            is_debt = 1 if payment_method == 'debt' else 0

            # 💡 تصوير الرصيد قبل العملية
            old_bal = float(user[balance_col] or 0)

            if action_type == 'sub':
                if user[balance_col] < amt:
                    conn.close()
                    return redirect(f"{(request.referrer or '/users').split('?')[0]}?err=insufficient_user_bal")

                # 💡 تصوير الرصيد بعد الخصم
                new_bal = old_bal - amt

                cur.execute(f"UPDATE users SET {balance_col} = {balance_col} - ? WHERE user_id = ?", (amt, uid))
                cur.execute(f"UPDATE users SET {balance_col} = {balance_col} + ? WHERE user_id = ?", (amt, admin_id))

                if is_debt:
                    cur.execute("UPDATE users SET debt_balance = debt_balance - ? WHERE user_id = ?", (actual_paid, uid))
                else:
                    curr_cash = float((get_setting('cash_drawer') or '0').replace(',', ''))
                    cur.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('cash_drawer', str(curr_cash - actual_paid)))

                cur.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date, user_balance_before, user_balance_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (uid, admin_id, -amt, actual_paid, w_type, 0, is_debt, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S"), old_bal, new_bal))

                conn.commit(); conn.close()
                try: bot.send_message(uid, f"⚠️ إشعار إداري:\nتم سحب `{amt:g}` من محفظة ({'الوحدات' if w_type == 'units' else 'الفواتير'}).", parse_mode="Markdown")
                except: pass

            else:
                # 💡 تصوير الرصيد بعد الإيداع
                new_bal = old_bal + amt

                cur.execute(f"UPDATE users SET {balance_col} = {balance_col} - ? WHERE user_id = ? AND {balance_col} >= ?", (amt, admin_id, amt))
                if cur.rowcount == 0:
                    conn.rollback(); conn.close()
                    return f"<script>alert('❌ رصيد الخزينة المركزية لا يكفي! قم بشراء بضاعة أولاً.'); window.location.href='{(request.referrer or '/users').split('?')[0]}';</script>"

                cur.execute(f"UPDATE users SET {balance_col} = {balance_col} + ? WHERE user_id = ?", (amt, uid))

                if is_debt:
                    cur.execute("UPDATE users SET debt_balance = debt_balance + ? WHERE user_id = ?", (actual_paid, uid))
                else:
                    curr_cash = float((get_setting('cash_drawer') or '0').replace(',', ''))
                    cur.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('cash_drawer', str(curr_cash + actual_paid)))

                unit_cost = float(get_setting('current_unit_cost') or 1.05)
                profit = actual_paid - (amt * unit_cost) if w_type == 'units' else 0

                cur.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date, user_balance_before, user_balance_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (uid, admin_id, amt, actual_paid, w_type, profit, is_debt, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S"), old_bal, new_bal))

                conn.commit(); conn.close()
                try: bot.send_message(uid, f"✅ إشعار شحن رصيد:\nتم إضافة `{amt:g}` إلى محفظة ({'الوحدات' if w_type == 'units' else 'الفواتير'}).\n" + (f"\n📝 تم تقييد الفاتورة عالدين بمبلغ: `{actual_paid:g}` ل.س." if is_debt else f"\n💰 تم استلام المبلغ كاش: `{actual_paid:g}` ل.س."), parse_mode="Markdown")
                except: pass
        except Exception as e:
            if 'conn' in locals(): conn.rollback(); conn.close()
            return f"<script>alert('❌ حدث خطأ أثناء العملية! لم يتم إرسال أو سحب الرصيد.'); window.location.href='{(request.referrer or '/users').split('?')[0]}';</script>"
    return redirect((request.referrer or '/users').split('?')[0])

@app.route('/agents', methods=['GET'])
def agents_page():
    if not session.get('logged_in') or session['role'] != 'admin': return redirect('/')
    conn = get_db_connection(); agents = conn.execute("SELECT * FROM users WHERE role='agent'").fetchall(); conn.close()
    rows = ""
    for ag in agents:
        downgrade_btn = f"<div class='mt-2'><a href='/toggle_agent/{ag['user_id']}' class='btn btn-sm btn-outline-danger shadow-sm w-100' onclick='return confirm(\"إرجاع الوكيل إلى زبون عادي؟\");'><i class='fas fa-user-minus me-1'></i> سحب الوكالة وإعادته لزبون</a></div>"
        rows += f"<tr><td class='fw-bold'>{ag['real_name']}</td><td dir='ltr' class='text-muted'>{ag['username']}</td><td dir='ltr' class='text-muted'>{ag['password']}</td><td><span class='badge bg-primary'>{ag['balance']:g}</span></td><td><span class='badge bg-secondary'>{ag['bills_balance']:g}</span></td><td><form action='/add_bal_agent' method='POST' class='d-flex gap-2 justify-content-center'><input type='hidden' name='uid' value='{ag['user_id']}'><select name='wallet_type' class='form-select form-select-sm shadow-sm' style='width: 90px;'><option value='units'>وحدات</option><option value='bills'>فواتير</option></select><input name='amt' placeholder='المبلغ' class='form-control form-control-sm shadow-sm' type='number' step='0.01' required style='width: 90px;'><button type='submit' class='btn btn-sm btn-success shadow-sm'>شحن</button></form>{downgrade_btn}</td></tr>"
    content = f"""<div class="card-bank"><h4 class="fw-bold mb-4 text-primary"><i class="fas fa-user-tie me-2"></i> إدارة الوكلاء</h4><div class="table-responsive"><table class="table text-center align-middle"><thead><tr><th>الاسم</th><th>اليوزر</th><th>الباسورد</th><th>الرصيد (وحدات)</th><th>الرصيد (فواتير)</th><th>إدارة الوكيل</th></tr></thead><tbody>{rows}</tbody></table></div></div>"""
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='agents')

@app.route('/add_bal_agent', methods=['POST'])
def add_bal_agent():
    if session.get('logged_in') and session.get('role') == 'admin':
        w_type, amt_str, uid_str = request.form.get('wallet_type'), request.form.get('amt'), request.form.get('uid')
        if w_type and amt_str and uid_str:
            try:
                admin_id, target_uid, amount = session['user_id'], int(uid_str), float(amt_str)
                conn = get_db_connection()
                bal_col = 'balance' if w_type == 'units' else 'bills_balance'

                # خصم آمن من الإدارة (لا يخصم إذا الرصيد لا يكفي)
                cur = conn.cursor()
                cur.execute(f"UPDATE users SET {bal_col} = {bal_col} - ? WHERE user_id = ? AND {bal_col} >= ?", (amount, admin_id, amount))

                if cur.rowcount == 0:
                    conn.close()
                    return "<script>alert('❌ رصيد الإدارة غير كافٍ لشحن الوكيل!'); window.location.href='/agents';</script>"

                cur.execute(f"UPDATE users SET {bal_col} = {bal_col} + ? WHERE user_id = ?", (amount, target_uid))
                cur.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (target_uid, admin_id, amount, amount, w_type, 0, 0, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit(); conn.close()
            except Exception as e:
                if 'conn' in locals(): conn.rollback(); conn.close()
                logging.error(f"Error in add_bal_agent: {str(e)}")
                return "<script>alert('❌ حدث خطأ أثناء شحن الوكيل!'); window.location.href='/agents';</script>"
    return redirect('/agents')

@app.route('/companies', methods=['GET', 'POST'])
def companies_page():
    if not session.get('logged_in'): return redirect('/')
    conn = get_db_connection()

    # 1. التحديث التلقائي لقاعدة البيانات (لضمان إضافة عمود نوع القسم بدون أخطاء)
    try: conn.execute("ALTER TABLE companies ADD COLUMN category TEXT DEFAULT 'bill'"); conn.commit()
    except: pass

    if request.method == 'POST':
        import html
        # تنظيف النص المدخل من أي أكواد خبيثة
        name = html.escape(request.form.get('name', '').strip())
        category = request.form.get('category', 'bill')
        if name: conn.execute("INSERT INTO companies (name, category) VALUES (?, ?)", (name, category)); conn.commit()

    comps = conn.execute("SELECT * FROM companies ORDER BY id DESC").fetchall()
    conn.close()
    rows = ""
    for c in comps:
        # 2. تحويل السطر إلى قاموس (Dict) لحل مشكلة Internal Server Error
        c_dict = dict(c)
        badge = "<span class='badge bg-success rounded-pill px-3'>يعمل</span>" if c_dict['is_active'] else "<span class='badge bg-warning text-dark rounded-pill px-3'>متوقف</span>"
        cat_badge = "🎮 لعبة/برنامج" if c_dict.get('category') == 'game' else "🧾 فاتورة"
        toggle_btn = f"<a href='/toggle_cmp_status/{c_dict['id']}' class='btn btn-sm btn-{'outline-warning text-dark' if c_dict['is_active'] else 'outline-success'} shadow-sm me-1'>{'إيقاف مؤقت' if c_dict['is_active'] else 'تشغيل'}</a>"
        del_btn = f"<a href='/del_cmp/{c_dict['id']}' class='btn btn-sm btn-danger shadow-sm'><i class='fas fa-trash-alt'></i> حذف نهائي</a>"
        rows += f"<tr><td class='fw-bold text-primary fs-5'><i class='fas fa-building me-2 text-muted'></i> {c_dict['name']}</td><td><span class='badge bg-secondary'>{cat_badge}</span></td><td>{badge}</td><td><div class='d-flex gap-1 justify-content-center'>{toggle_btn}{del_btn}</div></td></tr>"

    content = f"""<h4 class="fw-bold mb-4 text-primary"><i class="fas fa-building me-2"></i> إدارة الشركات والأقسام والألعاب</h4><div class="card-bank mb-4 bg-white"><form method="POST" class="row g-2"><div class="col-md-6"><input name="name" class="form-control form-control-lg bg-light" placeholder="اسم الشركة/اللعبة (مثال: بوبجي، المياه...)" required></div><div class="col-md-3"><select name="category" class="form-select form-select-lg bg-light" required><option value="bill">🧾 قسم فواتير</option><option value="game">🎮 قسم ألعاب وبرامج</option></select></div><div class="col-md-3"><button type="submit" class="btn btn-primary btn-lg w-100 shadow-sm"><i class="fas fa-plus"></i> إضافة قسم</button></div></form></div><div class="card-bank"><div class="table-responsive"><table class="table text-center align-middle"><thead><tr><th>اسم الشركة / القسم</th><th>النوع</th><th>الحالة</th><th>إجراء</th></tr></thead><tbody>{rows}</tbody></table></div></div>"""
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='companies')

@app.route('/toggle_cmp_status/<int:cid>')
def toggle_cmp_status(cid):
    if session.get('logged_in') and session.get('role') == 'admin':
        conn = get_db_connection()
        cmp = conn.execute("SELECT is_active FROM companies WHERE id=?", (cid,)).fetchone()
        if cmp:
            new_stat = 0 if cmp['is_active'] == 1 else 1
            conn.execute("UPDATE companies SET is_active=? WHERE id=?", (new_stat, cid))
            conn.commit()
        conn.close()
    return redirect('/companies')


@app.route('/manual_services', methods=['GET', 'POST'])
def manual_services_page():
    if not session.get('logged_in'): return redirect('/')
    conn = get_db_connection()
    if request.method == 'POST':
        import html
        c_id = request.form.get('company_id')
        name = html.escape(request.form.get('name', '').strip())
        price_str, cost_str = request.form.get('price'), request.form.get('cost')
        exec_time = html.escape(request.form.get('execution_time', '15 دقيقة'))
        work_hours = html.escape(request.form.get('working_hours', '09:00 ص - 10:00 م'))
        if c_id and name and price_str:
            try:
                cost_val, price_val = float(cost_str) if cost_str else 0.0, float(price_str)
                if c_id == 'all':
                    for c in conn.execute("SELECT id FROM companies WHERE is_active=1").fetchall():
                        conn.execute("INSERT INTO manual_services (company_id, name, price, cost, execution_time, working_hours) VALUES (?, ?, ?, ?, ?, ?)", (c['id'], name.strip(), price_val, cost_val, exec_time, work_hours))
                else:
                    conn.execute("INSERT INTO manual_services (company_id, name, price, cost, execution_time, working_hours) VALUES (?, ?, ?, ?, ?, ?)", (int(c_id), name.strip(), price_val, cost_val, exec_time, work_hours))
                conn.commit()
            except ValueError: pass
    comps = conn.execute("SELECT * FROM companies WHERE is_active=1").fetchall()
    services = conn.execute("SELECT s.*, c.name as company_name FROM manual_services s LEFT JOIN companies c ON s.company_id = c.id ORDER BY s.id DESC").fetchall()
    conn.close()

    comp_options = "<option value='all' class='fw-bold text-primary'>🌐 تطبيق على كافة الشركات</option>"
    if comps:
        for c in comps: comp_options += f"<option value='{c['id']}'>{c['name']}</option>"
    else: comp_options = "<option disabled selected>يرجى إضافة شركة أولاً</option>"

    rows = ""
    for s in services:
        badge = "<span class='badge bg-success rounded-pill'>متوفر</span>" if dict(s)['is_active'] else "<span class='badge bg-danger rounded-pill'>مخفي</span>"
        cost_val, safe_name = dict(s).get('cost') or 0, dict(s)['name'].replace('"', '&quot;').replace("'", "\\'")
        exec_time = dict(s).get('execution_time', '15 دقيقة')
        work_hours = dict(s).get('working_hours', '09:00 ص - 10:00 م')
        edit_btn = f"<button type='button' class='btn btn-sm btn-outline-primary rounded-pill px-3 me-1' onclick='openEditSrvModal({s['id']}, \"{safe_name}\", {s['price']}, {cost_val}, \"{exec_time}\", \"{work_hours}\")'><i class='fas fa-pen'></i></button>"
        del_btn = f"<a href='/del_service/{s['id']}' class='btn btn-sm btn-outline-danger rounded-pill px-3' onclick='return confirm(\"حذف هذه الباقة نهائياً؟\");'><i class='fas fa-trash'></i></a>"
        rows += f"<tr><td class='text-muted fw-bold'><i class='fas fa-folder-open me-1'></i> {s['company_name'] or 'غير محدد'}</td><td class='fw-bold'>{s['name']}</td><td class='fw-black text-success fs-5'>{s['price']:g}</td><td class='text-muted'>{cost_val:g}</td><td>{badge}</td><td>{edit_btn}{del_btn}</td></tr>"

    content = f"""<h4 class="fw-bold mb-4 text-primary"><i class="fas fa-box-open me-2"></i> إدارة باقات الفواتير والخدمات</h4><div class="card-bank mb-4 border-success border-start border-4"><form method="POST" class="row g-3 align-items-center"><div class="col-md-3"><label class="form-label text-muted fw-bold small">الشركة المستهدفة</label><select name="company_id" class="form-select bg-light shadow-sm" required>{comp_options}</select></div><div class="col-md-3"><label class="form-label text-muted fw-bold small">وصف الباقة</label><input name="name" class="form-control bg-light shadow-sm" placeholder="مثال: سرعة 2 ميغا" required></div><div class="col-md-2"><label class="form-label text-muted fw-bold small">السعر المخصوم (ل.س)</label><input name="price" type="number" step="0.01" class="form-control bg-light shadow-sm border-success" required></div><div class="col-md-2"><label class="form-label text-muted fw-bold small">التكلفة (ل.س)</label><input name="cost" type="number" step="0.01" class="form-control bg-light shadow-sm border-warning" required></div><div class="col-md-6"><label class="form-label text-muted fw-bold small">مدة التنفيذ المتوقعة</label><input name="execution_time" type="text" value="15 دقيقة" class="form-control bg-light shadow-sm" required></div><div class="col-md-6"><label class="form-label text-muted fw-bold small">أوقات عمل الشركة</label><input name="working_hours" type="text" value="09:00 ص - 10:00 م" class="form-control bg-light shadow-sm" required></div><div class="col-md-12 mt-3"><button type="submit" class="btn btn-success w-100 py-2 shadow-sm fw-bold"><i class="fas fa-plus-circle me-1"></i> إضافة الباقة</button></div></form></div><div class="card-bank"><div class="table-responsive"><table class="table text-center align-middle"><thead><tr><th>الشركة التابعة</th><th>اسم الباقة</th><th>سعر المبيع</th><th>التكلفة (للمرابح)</th><th>الحالة</th><th>إدارة</th></tr></thead><tbody>{rows}</tbody></table></div></div><div class="modal fade" id="editSrvModal" tabindex="-1" aria-hidden="true"><div class="modal-dialog modal-dialog-centered"><div class="modal-content border-0 shadow-lg rounded-4"><div class="modal-header bg-primary text-white border-0 rounded-top-4"><h5 class="modal-title fw-bold"><i class="fas fa-pen me-2"></i> تعديل بيانات الباقة</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><form action="/edit_service" method="POST"><div class="modal-body p-4"><input type="hidden" name="srv_id" id="edit_srv_id"><div class="mb-3"><label class="form-label fw-bold text-muted mb-2">وصف الباقة</label><input type="text" name="name" id="edit_srv_name" class="form-control bg-light border-0 shadow-sm p-3 fw-bold" required></div><div class="row"><div class="col-6 mb-3"><label class="form-label fw-bold text-success mb-2">سعر المبيع (ل.س)</label><input type="number" step="0.01" name="price" id="edit_srv_price" class="form-control bg-light border-0 shadow-sm p-3 fw-bold fs-5" required></div><div class="col-6 mb-3"><label class="form-label fw-bold text-warning mb-2">التكلفة (ل.س)</label><input type="number" step="0.01" name="cost" id="edit_srv_cost" class="form-control bg-light border-0 shadow-sm p-3 fw-bold fs-5" required></div></div><div class="mb-3"><label class="form-label fw-bold text-muted mb-2">مدة التنفيذ</label><input type="text" name="execution_time" id="edit_srv_exec" class="form-control bg-light border-0 shadow-sm p-3 fw-bold" required></div><div class="mb-3"><label class="form-label fw-bold text-muted mb-2">أوقات العمل</label><input type="text" name="working_hours" id="edit_srv_work" class="form-control bg-light border-0 shadow-sm p-3 fw-bold" required></div></div><div class="modal-footer border-0 pb-4 pe-4"><button type="button" class="btn btn-light shadow-sm fw-bold" data-bs-dismiss="modal">إلغاء</button><button type="submit" class="btn btn-primary px-4 fw-bold shadow-sm"><i class="fas fa-save me-1"></i> حفظ التعديلات</button></div></form></div></div></div><script>function openEditSrvModal(id, name, price, cost, exec, work) {{ document.getElementById('edit_srv_id').value = id; document.getElementById('edit_srv_name').value = name; document.getElementById('edit_srv_price').value = price; document.getElementById('edit_srv_cost').value = cost; document.getElementById('edit_srv_exec').value = exec; document.getElementById('edit_srv_work').value = work; new bootstrap.Modal(document.getElementById('editSrvModal')).show(); }}</script>"""
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='manual_services')

@app.route('/edit_service', methods=['POST'])
def edit_service():
    if session.get('logged_in') and session.get('role') == 'admin':
        import html
        s_id = request.form.get('srv_id')
        name = html.escape(request.form.get('name', '').strip())
        price, cost = request.form.get('price'), request.form.get('cost')
        exec_time = html.escape(request.form.get('execution_time', '15 دقيقة'))
        work_hours = html.escape(request.form.get('working_hours', '09:00 ص - 10:00 م'))
        if s_id and name and price:
            try: conn = get_db_connection(); conn.execute("UPDATE manual_services SET name=?, price=?, cost=?, execution_time=?, working_hours=? WHERE id=?", (name, float(price), float(cost or 0), exec_time, work_hours, int(s_id))); conn.commit(); conn.close()
            except: pass
    return redirect('/manual_services')



@app.route('/toggle_network/<network>')
def toggle_network(network):
    if session.get('logged_in') and session.get('role') == 'admin': set_setting(f'status_{network}', '0' if get_setting(f'status_{network}') == '1' else '1')
    return redirect('/categories')

@app.route('/toggle_cat/<int:cid>')
def toggle_cat(cid):
    if session.get('logged_in') and session.get('role') == 'admin':
        try:
            conn = get_db_connection(); cat = conn.execute("SELECT is_active FROM categories WHERE id=?", (cid,)).fetchone()
            if cat: conn.execute("UPDATE categories SET is_active=? WHERE id=?", (0 if cat['is_active'] == 1 else 1, cid)); conn.commit()
            conn.close()
        except: pass
    return redirect('/categories')


@app.route('/edit_cat', methods=['POST'])
def edit_cat():
    if session.get('logged_in') and session.get('role') == 'admin':
        cat_id, amount, ussd_amount = request.form.get('cat_id'), request.form.get('amount'), request.form.get('ussd_amount')
        if cat_id and amount and ussd_amount:
            try: conn = get_db_connection(); conn.execute("UPDATE categories SET amount=?, ussd_amount=? WHERE id=?", (float(amount), str(ussd_amount).strip(), int(cat_id))); conn.commit(); conn.close()
            except: pass
    return redirect('/categories')

@app.route('/clear_cats/<network>')
def clear_cats(network):
    if session.get('logged_in') and session.get('role') == 'admin':
        try: conn = get_db_connection(); conn.execute("DELETE FROM categories WHERE network=?", (network,)); conn.commit(); conn.close()
        except: pass
    return redirect('/categories')

@app.route('/categories', methods=['GET', 'POST'])
def categories_page():
    if not session.get('logged_in'): return redirect('/')
    if request.method == 'POST':
        network, bulk_data = request.form.get('network', 'Syriatel'), request.form.get('bulk_data', '')
        if bulk_data:
            for line in bulk_data.strip().split('\n'):
                line = line.strip()
                if not line: continue
                try:
                    ussd_val = float(line)
                    amount = round(ussd_val / 100.0, 3) if network == 'Syriatel' else round(ussd_val, 3)
                    add_category(network, 'Jahez', amount, str(int(amount)) if amount.is_integer() else str(amount), str(int(ussd_val)) if ussd_val.is_integer() else str(ussd_val))
                except ValueError: pass
        return redirect('/categories')

    conn = get_db_connection()
    s_cats = conn.execute("SELECT * FROM categories WHERE network='Syriatel' ORDER BY amount DESC").fetchall()
    m_cats = conn.execute("SELECT * FROM categories WHERE network='MTN' ORDER BY amount DESC").fetchall()
    conn.close()

    status_s, status_m = get_setting('status_Syriatel') or '1', get_setting('status_MTN') or '1'

    s_rows = ""
    for c in s_cats:
        active_class, eye_icon = ('' if c['is_active'] else 'text-decoration-line-through text-muted', 'fa-eye' if c['is_active'] else 'fa-eye-slash')
        edit_btn = f"<button type='button' class='btn btn-sm btn-outline-primary rounded-pill px-3 me-1' onclick='openEditCatModal({c['id']}, {c['amount']}, \"{c['ussd_amount']}\")'><i class='fas fa-pen'></i></button>"
        s_rows += f"<tr><td class='fw-black text-danger fs-5 {active_class}'>{c['amount']:g}</td><td class='fw-bold text-dark' dir='ltr'>{c['ussd_amount']}</td><td>{edit_btn}<a href='/toggle_cat/{c['id']}' class='btn btn-sm btn-outline-secondary rounded-pill px-3 me-1'><i class='fas {eye_icon}'></i></a><a href='/del_cat/{c['id']}' class='btn btn-sm btn-outline-danger rounded-pill px-3' onclick='return confirm(\"حذف الفئة؟\");'><i class='fas fa-trash'></i></a></td></tr>"
    if not s_rows: s_rows = "<tr><td colspan='3' class='text-muted py-3'>لا يوجد فئات مضافة</td></tr>"

    m_rows = ""
    for c in m_cats:
        active_class, eye_icon = ('' if c['is_active'] else 'text-decoration-line-through text-muted', 'fa-eye' if c['is_active'] else 'fa-eye-slash')
        edit_btn = f"<button type='button' class='btn btn-sm btn-outline-primary rounded-pill px-3 me-1' onclick='openEditCatModal({c['id']}, {c['amount']}, \"{c['ussd_amount']}\")'><i class='fas fa-pen'></i></button>"
        m_rows += f"<tr><td class='fw-black fs-5 {active_class}' style='color: #d97706;'>{c['amount']:g}</td><td class='fw-bold text-dark' dir='ltr'>{c['ussd_amount']}</td><td>{edit_btn}<a href='/toggle_cat/{c['id']}' class='btn btn-sm btn-outline-secondary rounded-pill px-3 me-1'><i class='fas {eye_icon}'></i></a><a href='/del_cat/{c['id']}' class='btn btn-sm btn-outline-warning rounded-pill px-3 text-dark' onclick='return confirm(\"حذف الفئة؟\");'><i class='fas fa-trash'></i></a></td></tr>"
    if not m_rows: m_rows = "<tr><td colspan='3' class='text-muted py-3'>لا يوجد فئات مضافة</td></tr>"

    content = f"""<h4 class="fw-bold mb-4 text-primary"><i class="fas fa-bolt me-2"></i> إدخال الفئات ونظام الطوارئ</h4><div class="card-bank mb-4 bg-white border-0 shadow-sm"><form method="POST" class="row g-3"><div class="col-md-3"><label class="small fw-bold text-muted mb-2">الشبكة:</label><select name="network" class="form-select bg-light border-0 shadow-sm mb-3" style="height: 50px;"><option value="Syriatel">سيريتل (Syriatel)</option><option value="MTN">إم تي إن (MTN)</option></select><button type="submit" class="btn btn-primary w-100 fw-bold shadow-sm" style="height: 50px;"><i class="fas fa-magic me-2"></i> إدخال الفئات</button></div><div class="col-md-9"><label class="small fw-bold text-primary mb-2">الصق عمود أرقام الـ USSD هنا (رقم تحت رقم):</label><textarea name="bulk_data" class="form-control bg-light border-0 shadow-sm border-start border-primary border-4" rows="5" placeholder="أدخل أرقام الموبايل فقط (سيريتل أو MTN)" required></textarea></div></form></div><div class="row g-4"><div class="col-md-6"><div class="card-bank border-danger border-start border-4 h-100"><div class="d-flex justify-content-between align-items-center mb-4"><h5 class="fw-bold text-danger m-0"><i class="fas fa-sim-card me-2"></i> فئات سيريتل</h5><div><a href="/toggle_network/Syriatel" class="btn btn-sm btn-{'success' if status_s=='1' else 'danger'} fw-bold shadow-sm me-2" onclick="return confirm('تأكيد تبديل حالة سيريتل؟');">{'🟢 تعمل (إيقاف)' if status_s=='1' else '🔴 متوقفة (تشغيل)'}</a><a href="/clear_cats/Syriatel" class="btn btn-sm btn-outline-danger fw-bold shadow-sm" onclick="return confirm('حذف جميع الفئات؟');"><i class="fas fa-trash-alt me-1"></i></a></div></div><div class="table-responsive"><table class="table text-center align-middle"><thead class="table-light"><tr><th>للزبون (الكمية)</th><th>للموبايل</th><th>إدارة</th></tr></thead><tbody>{s_rows}</tbody></table></div></div></div><div class="col-md-6"><div class="card-bank border-warning border-start border-4 h-100"><div class="d-flex justify-content-between align-items-center mb-4"><h5 class="fw-bold m-0" style="color: #d97706;"><i class="fas fa-sim-card me-2"></i> فئات إم تي إن</h5><div><a href="/toggle_network/MTN" class="btn btn-sm btn-{'success' if status_m=='1' else 'danger'} fw-bold shadow-sm me-2" onclick="return confirm('تأكيد تبديل حالة MTN؟');">{'🟢 تعمل (إيقاف)' if status_m=='1' else '🔴 متوقفة (تشغيل)'}</a><a href="/clear_cats/MTN" class="btn btn-sm btn-outline-warning text-dark fw-bold shadow-sm" onclick="return confirm('حذف جميع الفئات؟');"><i class="fas fa-trash-alt me-1"></i></a></div></div><div class="table-responsive"><table class="table text-center align-middle"><thead class="table-light"><tr><th>للزبون (الكمية)</th><th>للموبايل</th><th>إدارة</th></tr></thead><tbody>{m_rows}</tbody></table></div></div></div></div><div class="modal fade" id="editCatModal" tabindex="-1" aria-hidden="true"><div class="modal-dialog modal-dialog-centered"><div class="modal-content border-0 shadow-lg rounded-4"><div class="modal-header bg-primary text-white border-0 rounded-top-4"><h5 class="modal-title fw-bold"><i class="fas fa-pen me-2"></i> تعديل بيانات الفئة</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><form action="/edit_cat" method="POST"><div class="modal-body p-4"><input type="hidden" name="cat_id" id="edit_cat_id"><div class="mb-4"><label class="form-label fw-bold text-muted mb-2">الكمية التي ستخصم من محفظة الزبون</label><input type="number" step="0.001" name="amount" id="edit_cat_amount" class="form-control bg-light border-0 shadow-sm p-3 fw-bold fs-5" required></div><div class="mb-3"><label class="form-label fw-bold text-muted mb-2">الكود الفعلي المرسل للموبايل (USSD)</label><input type="text" name="ussd_amount" id="edit_cat_ussd" class="form-control bg-light border-0 shadow-sm p-3 fw-bold fs-5" required dir="ltr"></div></div><div class="modal-footer border-0 pb-4 pe-4"><button type="button" class="btn btn-light shadow-sm fw-bold" data-bs-dismiss="modal">إلغاء</button><button type="submit" class="btn btn-primary px-4 fw-bold shadow-sm"><i class="fas fa-save me-1"></i> حفظ التعديلات</button></div></form></div></div></div><script>function openEditCatModal(id, amount, ussd) {{ document.getElementById('edit_cat_id').value = id; document.getElementById('edit_cat_amount').value = amount; document.getElementById('edit_cat_ussd').value = ussd; new bootstrap.Modal(document.getElementById('editCatModal')).show(); }}</script>"""
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='categories')

# =============================================================
# الإعدادات، النسخ الاحتياطي، وتصفير النظام "الآمن" 🛡️
# =============================================================
@app.route('/trigger_debt_reminders')
def trigger_debt_reminders():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')

    conn = get_db_connection()
    debtors = conn.execute("SELECT user_id, real_name, debt_balance FROM users WHERE debt_balance > 0 AND role='user'").fetchall()
    conn.close()

    count = 0
    for d in debtors:
        try:
            msg = f"""⚠️ *تذكير كشف حساب (ودي)*
الأستاذ: *{d['real_name']}* الورود.. تحية طيبة،

نود إعلامكم بأن رصيد الديون المتبقي بذمتكم حالياً هو:
💰 *{d['debt_balance']:,.2f}* ل.س

يرجى تسديد المبلغ في أقرب وقت لضمان استمرار الخدمة بسلاسة.
✨ *شكراً لثقتكم بنا!* ✨"""
            bot.send_message(d['user_id'], msg, parse_mode="Markdown")
            count += 1
        except: pass

    return f"<script>alert('تم إرسال {count} تذكير بنجاح!'); window.location='/settings';</script>"

@app.route('/backup_db', methods=['POST'])
def backup_db():
    if session.get('logged_in') and session.get('role') == 'admin':
        try:
            import tempfile

            # 1. الاتصال بقاعدة البيانات الحالية
            conn = get_db_connection()

            # 2. إنشاء مسار لملف مؤقت وآمن لتفريغ البيانات فيه
            temp_db_path = os.path.join(tempfile.gettempdir(), f"backup_OneTouch_{datetime.now(local_tz).strftime('%Y%m%d%H%M%S')}.db")
            bck_conn = sqlite3.connect(temp_db_path)

            # 3. أخذ لقطة كاملة وآمنة (Backup) للملف المؤقت
            conn.backup(bck_conn)

            # 4. إغلاق الاتصالات
            bck_conn.close()
            conn.close()

            # 5. إرسال الملف الآمن إلى التلغرام
            with open(temp_db_path, 'rb') as db_file:
                bot.send_document(ADMIN_TG_ID, db_file, caption=f"📦 *نسخة احتياطية سحابية (آمنة ومستقرة)*\nتاريخ النسخة: {datetime.now(local_tz).strftime('%Y-%m-%d %H:%M:%S')}", parse_mode="Markdown")

            # 6. مسح الملف المؤقت من السيرفر لتوفير المساحة
            try:
                os.remove(temp_db_path)
            except:
                pass

            return redirect('/settings?backup=sent')

        except Exception as e:
            return f"<h3 dir='rtl' style='color:red; text-align:center; margin-top:50px;'>❌ فشل الإرسال للتلجرام!</h3><p dir='rtl' style='text-align:center; direction:ltr;'>{str(e)}</p><br><center><a href='/settings'>العودة للإعدادات</a></center>"
    return redirect('/settings')

# ==========================================
# 🔒 نظام تغيير كلمة سر المدير بـ OTP التلغرام
# ==========================================
@app.route('/api/request_pass_code', methods=['POST'])
def request_pass_code():
    if not session.get('logged_in') or session.get('role') != 'admin':
        return jsonify({"status": "error", "msg": "غير مصرح"})
    import random
    code = str(random.randint(100000, 999999))
    session['otp_code'] = code
    try:
        bot.send_message(ADMIN_TG_ID, f"🔐 *طلب تغيير كلمة سر الإدارة!*\n\nرمز التأكيد (OTP) الخاص بك هو: `{code}`\n\n⚠️ إذا لم تطلب هذا الكود، فإن أحدهم يحاول تغيير كلمة السر الخاصة بك من لوحة التحكم!\n\n(لا تشارك هذا الرمز مع أحد).", parse_mode="Markdown")
        return jsonify({"status": "success", "msg": "تم إرسال الكود للتلغرام"})
    except Exception as e:
        return jsonify({"status": "error", "msg": "فشل إرسال الكود. تأكد من عمل البوت."})

@app.route('/api/verify_pass_code', methods=['POST'])
def verify_pass_code():
    if not session.get('logged_in') or session.get('role') != 'admin':
        return jsonify({"status": "error", "msg": "غير مصرح"})
    data = request.get_json()
    user_code = data.get('code', '')
    new_pass = data.get('new_pass', '')

    if not user_code or not new_pass:
        return jsonify({"status": "error", "msg": "يرجى تعبئة الحقول"})

    if session.get('otp_code') and str(session['otp_code']) == str(user_code):
        conn = get_db_connection()
        conn.execute("UPDATE users SET password = ? WHERE role = 'admin'", (new_pass,))
        conn.commit()
        conn.close()
        session.pop('otp_code', None) # مسح الكود للأمان
        return jsonify({"status": "success", "msg": "تم تغيير كلمة السر بنجاح ✅"})
    else:
        return jsonify({"status": "error", "msg": "الكود خاطئ ❌"})


@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    if not session.get('logged_in'): return redirect('/')
    old_maint = get_setting('maintenance') or '0'
    if request.method == 'POST':
        set_setting('whatsapp', request.form.get('whatsapp',''))
        set_setting('copyright', request.form.get('copyright',''))
        set_setting('max_trans_syriatel', request.form.get('max_trans_syriatel','500'))
        set_setting('max_trans_mtn', request.form.get('max_trans_mtn','50000'))
        set_setting('ussd_bal_Syriatel', request.form.get('u_s',''))
        set_setting('ussd_bal_MTN', request.form.get('u_m',''))
        set_setting('status_secret_code', request.form.get('status_secret_code','1'))
        set_setting('open_bill_percent_fee', request.form.get('open_bill_percent_fee','2'))
        set_setting('open_bill_fixed_fee', request.form.get('open_bill_fixed_fee','0'))
        set_setting('open_bill_active', request.form.get('open_bill_active','1'))
        set_setting('current_unit_cost', request.form.get('current_unit_cost','1.05'))
        set_setting('status_app_button', request.form.get('status_app_button','1'))

        # 💡 حفظ حالة زر الكاش
        set_setting('status_cash_bills', request.form.get('status_cash_bills','0'))

        new_maint = request.form.get('maintenance','0')
        set_setting('maintenance', new_maint)
        if old_maint != new_maint:
            conn = get_db_connection()
            users = conn.execute("SELECT user_id FROM users WHERE role='user' AND is_approved=1 AND is_banned=0").fetchall()
            conn.close()
            msg = "🛠️ *إشعار إداري:*\nالنظام الآن في وضع الصيانة والتحديث لإضافة ميزات جديدة. سنعود لخدمتكم قريباً، نعتذر عن هذا التوقف المؤقت." if new_maint == '1' else "✅ *عدنا إليكم!*\nالنظام الآن يعمل بكامل كفاءته وجاهز لاستقبال طلباتكم. شكراً لانتظاركم."

            for u in users:
                markup = types.ReplyKeyboardRemove() if new_maint == '1' else main_menu(u['user_id'])
                try: bot.send_message(u['user_id'], msg, parse_mode="Markdown", reply_markup=markup)
                except: pass
            return redirect(f'/settings?maint={"broadcasted" if new_maint == "1" else "lifted"}')

    wa, cp, maxt_s, maxt_m, maint = get_setting('whatsapp'), get_setting('copyright'), get_setting('max_trans_syriatel') or '500', get_setting('max_trans_mtn') or '50000', get_setting('maintenance') or '0'
    u_s, u_m = get_setting('ussd_bal_Syriatel'), get_setting('ussd_bal_MTN')
    secret_status = get_setting('status_secret_code') or '1'
    open_percent = get_setting('open_bill_percent_fee') or '2'
    open_fixed = get_setting('open_bill_fixed_fee') or '0'
    open_active = get_setting('open_bill_active') or '1'
    u_cost = get_setting('current_unit_cost') or '1.05'
    app_btn = get_setting('status_app_button') or '1'

    # 💡 جلب حالة الزر الجديد
    cash_btn = get_setting('status_cash_bills') or '0'

    content = f"""
    <h4 class="fw-bold mb-4 text-primary"><i class="fas fa-cog me-2"></i> الإعدادات العامة والتسعير</h4>
    <div class="card-bank mb-5">
        <form method="POST">
            <div class="row mb-4">
                <div class="col-md-4 mb-4">
                    <label class="form-label fw-bold text-danger"><i class="fas fa-tools me-1"></i> وضع الصيانة (إيقاف البوت)</label>
                    <select name="maintenance" class="form-select border-danger border-2 shadow-sm p-3 fw-bold">
                        <option value="0" {"selected" if maint=='0' else ""}>🟢 البوت يعمل بشكل طبيعي (متاح للزبائن)</option>
                        <option value="1" {"selected" if maint=='1' else ""}>🔴 تفعيل وضع الصيانة (إيقاف البوت فوراً)</option>
                    </select>
                </div>
                <div class="col-md-4 mb-4">
                    <label class="form-label fw-bold text-success"><i class="fas fa-mobile-alt me-1"></i> زر (تطبيق الموبايل) للزبائن</label>
                    <select name="status_app_button" class="form-select border-success border-2 shadow-sm p-3 fw-bold">
                        <option value="1" {"selected" if app_btn=='1' else ""}>🟢 إظهار الزر في قائمة البوت</option>
                        <option value="0" {"selected" if app_btn=='0' else ""}>🔴 إخفاء الزر من القائمة نهائياً</option>
                    </select>
                </div>
                <div class="col-md-4 mb-4">
                    <label class="form-label fw-bold text-info"><i class="fas fa-money-bill-wave me-1"></i> زر (كاش وفواتير إتـصـالات)</label>
                    <select name="status_cash_bills" class="form-select border-info border-2 shadow-sm p-3 fw-bold">
                        <option value="1" {"selected" if cash_btn=='1' else ""}>🟢 إظهار الزر للزبائن</option>
                        <option value="0" {"selected" if cash_btn=='0' else ""}>🔴 إخفاء الزر مؤقتاً</option>
                    </select>
                </div>
            </div>
            <hr class="text-muted my-4">
            <div class="row mb-4">
                <div class="col-md-12">
                    <label class="form-label fw-bold text-primary"><i class="fas fa-calculator me-1"></i> تكلفة الوحدة عليك (رأس مالك - مخفي عن الزبائن)</label>
                    <input name="current_unit_cost" value="{u_cost}" type="number" step="0.001" class="form-control border-primary border-2 shadow-sm p-3 fw-bold" required>
                </div>
            </div>
            <hr class="text-muted my-4">
            <div class="row mb-4">
                <div class="col-md-12 mb-3">
                    <label class="form-label fw-bold text-success"><i class="fas fa-money-bill-wave me-1"></i> إعدادات الشحن الحر (الدفع المفتوح للفواتير)</label>
                    <select name="open_bill_active" class="form-select border-success shadow-sm mb-3 fw-bold">
                        <option value="1" {"selected" if open_active=='1' else ""}>🟢 ميزة الدفع الحر مفعلة للزباين</option>
                        <option value="0" {"selected" if open_active=='0' else ""}>🔴 متوقفة (مخفية عن الزبائن)</option>
                    </select>
                </div>
                <div class="col-md-6">
                    <label class="form-label fw-bold text-muted">نسبة الربح المئوية (%)</label>
                    <input name="open_bill_percent_fee" value="{open_percent}" type="number" step="0.01" class="form-control bg-light border-0 shadow-sm">
                </div>
                <div class="col-md-6">
                    <label class="form-label fw-bold text-muted">عمولة ثابتة إضافية (ل.س)</label>
                    <input name="open_bill_fixed_fee" value="{open_fixed}" type="number" step="0.01" class="form-control bg-light border-0 shadow-sm">
                </div>
            </div>
            <hr class="text-muted my-4">
            <div class="row mb-4">
                <div class="col-md-6">
                    <label class="form-label fw-bold text-danger">كود استعلام رصيد سيريتل (مخفي وآمن)</label>
                    <input name="u_s" value="{u_s}" class="form-control bg-light border-0 shadow-sm" dir="ltr">
                </div>
                <div class="col-md-6">
                    <label class="form-label fw-bold text-warning" style="color: #d97706!important;">كود استعلام رصيد MTN (مخفي وآمن)</label>
                    <input name="u_m" value="{u_m}" class="form-control bg-light border-0 shadow-sm" dir="ltr">
                </div>
            </div>
            <hr class="text-muted my-4">
            <div class="row mb-4">
                <div class="col-md-12">
                    <label class="form-label fw-bold text-primary"><i class="fas fa-shield-alt me-1"></i> ميزة التحويل عبر الكود السري (8 أرقام)</label>
                    <select name="status_secret_code" class="form-select border-primary shadow-sm p-3 fw-bold">
                        <option value="1" {"selected" if secret_status=='1' else ""}>🟢 مفعلة (يسمح للزبائن بإدخال الكود)</option>
                        <option value="0" {"selected" if secret_status=='0' else ""}>🔴 متوقفة (إيقاف الميزة ورفض الأكواد)</option>
                    </select>
                </div>
            </div>
            <hr class="text-muted my-4">
            <div class="row mb-4">
                <div class="col-md-4">
                    <label class="form-label fw-bold text-secondary">رقم الدعم الفني (WhatsApp)</label>
                    <div class="input-group shadow-sm">
                        <span class="input-group-text bg-light border-0"><i class="fab fa-whatsapp text-success fs-4"></i></span>
                        <input name="whatsapp" value="{wa}" class="form-control border-0 bg-light" dir="ltr">
                    </div>
                </div>
                <div class="col-md-4">
                    <label class="form-label fw-bold text-danger">سقف تحويل سيريتل (للزبون)</label>
                    <div class="input-group shadow-sm">
                        <input name="max_trans_syriatel" value="{maxt_s}" type="number" class="form-control border-0 bg-light border-start border-danger border-4" dir="ltr">
                        <span class="input-group-text bg-light border-0">وحدة</span>
                    </div>
                </div>
                <div class="col-md-4">
                    <label class="form-label fw-bold" style="color: #d97706;">سقف تحويل MTN (للزبون)</label>
                    <div class="input-group shadow-sm">
                        <input name="max_trans_mtn" value="{maxt_m}" type="number" class="form-control border-0 bg-light border-start border-warning border-4" dir="ltr">
                        <span class="input-group-text bg-light border-0">وحدة</span>
                    </div>
                </div>
            </div>
            <div class="mb-4">
                <label class="form-label fw-bold text-secondary">نص حقوق لوحة التحكم</label>
                <input name="copyright" value="{cp}" class="form-control border-0 bg-light shadow-sm p-3">
            </div>
            <button type="submit" class="btn btn-primary px-5 py-2 fw-bold shadow"><i class="fas fa-save me-2"></i> حفظ التغييرات</button>
        </form>
    </div>

    <div class="card-bank border-warning border-start border-5 bg-light mb-4">
        <h5 class="fw-bold text-warning mb-3" style="color: #d97706!important;"><i class="fas fa-key me-2"></i> تغيير كلمة سر الإدارة (بحماية 2FA التلغرام)</h5>
        <p class="text-muted small fw-bold">لن يتم تغيير كلمة السر إلا بعد تأكيد الرمز المرسل إلى حسابك في التلغرام.</p>

        <div id="pass_step_1">
            <div class="mb-3">
                <label class="form-label fw-bold text-dark">كلمة السر الجديدة:</label>
                <input type="text" id="new_admin_pass" class="form-control border-warning shadow-sm p-3" dir="ltr" placeholder="اكتب كلمة السر الجديدة هنا...">
            </div>
            <button type="button" onclick="reqPassCode()" class="btn btn-warning fw-bold shadow text-dark"><i class="fas fa-paper-plane me-2"></i> إرسال كود التأكيد للتلغرام</button>
        </div>

        <div id="pass_step_2" style="display:none;" class="mt-3 p-4 bg-white border border-success border-2 rounded shadow-sm">
            <p class="text-success fw-bold"><i class="fas fa-check-circle me-1"></i> تم إرسال الكود المكون من 6 أرقام إلى حسابك في التلغرام.</p>
            <div class="mb-3">
                <label class="form-label fw-bold text-dark">أدخل كود التأكيد:</label>
                <input type="number" id="admin_pass_code" class="form-control border-success shadow-sm p-3 fs-5 text-center" dir="ltr" placeholder="123456">
            </div>
            <button type="button" onclick="verifyPassCode()" class="btn btn-success w-100 py-2 fw-bold shadow"><i class="fas fa-save me-2"></i> تأكيد وحفظ كلمة السر الجديدة</button>
        </div>

        <script>
        function reqPassCode() {{
            let np = document.getElementById('new_admin_pass').value;
            if(!np) return alert("يرجى كتابة كلمة السر الجديدة أولاً!");
            fetch('/api/request_pass_code', {{method:'POST'}}).then(r=>r.json()).then(d=>{{
                if(d.status==='success'){{
                    document.getElementById('pass_step_1').style.display = 'none';
                    document.getElementById('pass_step_2').style.display = 'block';
                }} else {{ alert(d.msg); }}
            }});
        }}
        function verifyPassCode() {{
            let np = document.getElementById('new_admin_pass').value;
            let c = document.getElementById('admin_pass_code').value;
            if(!c) return alert("يرجى إدخال الكود!");
            fetch('/api/verify_pass_code', {{
                method:'POST', headers:{{'Content-Type':'application/json'}},
                body: JSON.stringify({{new_pass: np, code: c}})
            }}).then(r=>r.json()).then(d=>{{
                alert(d.msg);
                if(d.status==='success') location.reload();
            }});
        }}
        </script>
    </div>

    <div class="card-bank border-primary border-start border-5 bg-light mb-4">
        <h5 class="fw-bold text-primary mb-3"><i class="fas fa-cloud-download-alt me-2"></i> الخزنة السحابية (النسخ الاحتياطي)</h5>
        <form action="/backup_db" method="POST" onsubmit="return confirm('تأكيد إرسال النسخة الاحتياطية إلى التلجرام الخاص بك؟');">
            <button type="submit" class="btn btn-primary px-4 py-2 fw-bold shadow"><i class="fas fa-paper-plane me-2"></i> إرسال نسخة الآن 🔒</button>
        </form>
    </div>

    <div class="card-bank border-info border-start border-5 bg-light mb-4">
        <h5 class="fw-bold text-info mb-3"><i class="fas fa-bell me-2"></i> تذكير الديون (المحصل الآلي)</h5>
        <a href="/trigger_debt_reminders" class="btn btn-info px-4 py-2 fw-bold shadow text-white" onclick="return confirm('إرسال تذكير بالديون لجميع الزبائن الآن؟');"><i class="fas fa-paper-plane me-2"></i> 🚀 إرسال تذكيرات الديون الآن</a>
    </div>

    <div class="card-bank border-danger border-start border-5 bg-light mt-5">
        <h5 class="fw-bold text-danger mb-3"><i class="fas fa-exclamation-triangle me-2"></i> منطقة الخطر (تصفير النظام)</h5>
        <form action="/factory_reset" method="POST" onsubmit="return confirm('هل أنت متأكد من تصفير سجلات المبيعات القديمة؟ (تطمن: حقوق الزبائن والديون لن تتأثر)');">
            <button type="submit" class="btn btn-danger px-4 py-2 fw-bold shadow"><i class="fas fa-trash-alt me-2"></i> تصفير سجلات المبيعات بأمان</button>
        </form>
    </div>
    """
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='settings')

@app.route('/factory_reset', methods=['POST'])
def factory_reset():
    if session.get('logged_in') and session.get('role') == 'admin':
        try:
            conn = get_db_connection()
            # 🛡️ مسح السجلات فقط دون المساس بحقوق الناس والديون 🛡️
            for table in ['transactions', 'manual_orders', 'deposit_logs', 'drafts']:
                conn.execute(f"DELETE FROM {table}")
            try:
                for table in ['transactions', 'manual_orders', 'deposit_logs', 'drafts']:
                    conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
            except: pass
            conn.commit(); conn.close()
            return redirect('/?reset=success')
        except: pass
    return redirect('/')
    # ==========================================
# نظام كشف الحساب (صفحات + PDF) المطور للزبون
# ==========================================
def get_statement_page(user_id, page=1, limit=5):
    conn = get_db_connection()

    # جلب جميع حركات الزبون من الجداول الثلاثة
    trans = conn.execute("SELECT id, 'TRANSFER' as src_type, amount, date, status, phone as detail, network as extra, balance_before, balance_after FROM transactions WHERE user_id=?", (user_id,)).fetchall()
    deps = conn.execute("SELECT id, 'DEPOSIT' as src_type, amount, date, 'COMPLETED' as status, wallet_type as detail, actual_paid as extra, 0 as balance_before, 0 as balance_after FROM deposit_logs WHERE user_id=? AND by_admin_id != 0", (user_id,)).fetchall()
    bills = conn.execute("SELECT id, 'BILL' as src_type, price as amount, date, status, service_name as detail, target_info as extra, 0 as balance_before, 0 as balance_after FROM manual_orders WHERE user_id=?", (user_id,)).fetchall()

    user = conn.execute("SELECT balance, bills_balance FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()

    # دمج وترتيب الحركات حسب التاريخ (الأحدث أولاً)
    all_logs = [dict(x) for x in trans] + [dict(x) for x in deps] + [dict(x) for x in bills]
    all_logs.sort(key=lambda x: x['date'] if x['date'] else "", reverse=True)

    total_logs = len(all_logs)
    total_pages = max(1, (total_logs + limit - 1) // limit)

    offset = (page - 1) * limit
    page_logs = all_logs[offset : offset + limit]

    return page_logs, total_pages, user

@bot.callback_query_handler(func=lambda call: call.data.startswith('stmt_'))
def handle_statement(call):
    user_id = call.message.chat.id
    data = call.data.split('_')
    action = data[1]

    if action == 'page':
        page = int(data[2])
        logs, total_pages, user = get_statement_page(user_id, page)

        if not logs:
            bot.answer_callback_query(call.id, "لا توجد حركات لعرضها.")
            return

        msg_text = f"📊 *كشف حسابك - الصفحة ({page}/{total_pages})*\n━━━━━━━━━━━━━━\n"

        for l in logs:
            date_short = l['date'][:16] if l['date'] else ""
            if l['src_type'] == 'DEPOSIT':
                w_name = "وحدات 📱" if l['detail'] == 'units' else "فواتير 🧾"
                if l['detail'] == 'debt_payment':
                    msg_text += f"🔹 *النوع:* تسديد ديون 💰\n💵 *المبلغ:* `{l['extra']:g}` ل.س\n"
                elif l['detail'] == 'free_debt':
                    msg_text += f"🔹 *النوع:* دين جديد 📝\n💵 *المبلغ:* `{l['extra']:g}` ل.س\n"
                else:
                    msg_text += f"🔹 *النوع:* إيداع {w_name} 📥\n💵 *المبلغ:* `{l['amount']:g}`\n"

            elif l['src_type'] == 'TRANSFER':
                stat = '✅' if l['status']=='SUCCESS' else '⏳' if l['status']=='QUEUED' else '⚙️' if l['status']=='PROCESSING' else '🔎' if l['status']=='MANUAL_CHECK' else '❌'
                bb = l['balance_before'] or 0
                ba = l['balance_after'] or 0
                msg_text += f"🔹 *النوع:* تحويل {l['extra']} 🚀\n📱 *للرقم:* `{l['detail']}`\n💵 *المبلغ:* `{l['amount']:g}` وحدة {stat}\n📉 *كان رصيدك:* `{bb:g}` ➔ *صار:* `{ba:g}`\n"

            elif l['src_type'] == 'BILL':
                stat = '✅' if l['status']=='COMPLETED' else '⏳' if l['status']=='PENDING' else '❌'
                msg_text += f"🔹 *النوع:* فاتورة ({l['detail']}) 🧾\n🎯 *الرقم:* `{l['extra']}`\n💵 *المبلغ:* `{l['amount']:g}` ل.س {stat}\n"

            msg_text += f"📅 *الوقت:* {date_short}\n┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"

        msg_text += f"💰 *رصيدك الحالي:* `{user['balance']:g}` وحدة | `{user['bills_balance']:g}` فواتير"

        markup = types.InlineKeyboardMarkup(row_width=2)
        btns = []
        if page > 1: btns.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"stmt_page_{page-1}"))
        if page < total_pages: btns.append(types.InlineKeyboardButton("التالي ➡️", callback_data=f"stmt_page_{page+1}"))
        if btns: markup.add(*btns)
        markup.add(types.InlineKeyboardButton("📄 تحميل كشف الحساب (PDF)", callback_data="stmt_pdf"))

        bot.edit_message_text(msg_text, chat_id=user_id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

    elif action == 'pdf':
        bot.answer_callback_query(call.id, "⏳ جاري تجهيز ملف الـ PDF، لحظات من فضلك...")
        generate_and_send_pdf(user_id)

def generate_and_send_pdf(user_id):
    # استدعاءات إضافية ضرورية للخط العربي
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import os

    conn = get_db_connection()
    user = conn.execute("SELECT real_name, balance, debt_balance FROM users WHERE user_id=?", (user_id,)).fetchone()

    trans = conn.execute("SELECT id, 'TRANSFER' as src_type, amount, date, status, phone as detail, network as extra, balance_before, balance_after FROM transactions WHERE user_id=?", (user_id,)).fetchall()
    deps = conn.execute("SELECT id, 'DEPOSIT' as src_type, amount, date, 'COMPLETED' as status, wallet_type as detail, actual_paid as extra, 0 as balance_before, 0 as balance_after FROM deposit_logs WHERE user_id=? AND by_admin_id != 0", (user_id,)).fetchall()
    bills = conn.execute("SELECT id, 'BILL' as src_type, price as amount, date, status, service_name as detail, target_info as extra, 0 as balance_before, 0 as balance_after FROM manual_orders WHERE user_id=?", (user_id,)).fetchall()
    conn.close()

    all_logs = [dict(x) for x in trans] + [dict(x) for x in deps] + [dict(x) for x in bills]
    all_logs.sort(key=lambda x: x['date'] if x['date'] else "", reverse=True)
    logs = all_logs[:50]

    if not logs:
        bot.send_message(user_id, "❌ لا توجد حركات كافية لإنشاء كشف حساب.")
        return

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # دالة إصلاح الحروف العربية المقطعة والمعكوسة
    def fix_ar(text):
        if not text: return ""
        return get_display(arabic_reshaper.reshape(str(text)))

    # محاولة تحميل خط يدعم العربي بأمان
    try:
        pdfmetrics.registerFont(TTFont('ArabicFont', 'arial.ttf'))
        font_name = 'ArabicFont'
    except:
        font_name = 'Helvetica' # خط بديل في حال لم ترفع ملف arial.ttf

    c.setFont(font_name, 20)
    c.drawCentredString(width/2, height - 50, "Refaie Center")
    c.setFont(font_name, 14)
    c.drawCentredString(width/2, height - 70, fix_ar("كشف حساب العميل"))

    y = height - 120
    c.setFont(font_name, 12)
    c.drawString(50, y, fix_ar(f"العميل: {user['real_name']}"))
    c.drawString(50, y-20, fix_ar(f"الرصيد الحالي: {user['balance']} وحدة"))
    c.drawString(50, y-40, fix_ar(f"التاريخ: {datetime.now(local_tz).strftime('%Y-%m-%d %H:%M')}"))

    y -= 80
    c.setFont(font_name, 10)
    c.drawString(50, y, fix_ar("التاريخ"))
    c.drawString(150, y, fix_ar("العملية"))
    c.drawString(250, y, fix_ar("الهدف"))
    c.drawString(350, y, fix_ar("المبلغ"))
    c.drawString(450, y, fix_ar("الرصيد بعد"))
    c.line(50, y-5, width-50, y-5)

    y -= 25
    c.setFont(font_name, 10)
    for l in logs:
        if y < 50:
            c.showPage()
            y = height - 50
            c.setFont(font_name, 10) # إعادة تفعيل الخط بالصفحة الجديدة

        date_short = l['date'][:16] if l['date'] else ""
        if l['src_type'] == 'TRANSFER':
            action_type = "تحويل رصيد"
            target = str(l['detail'])
            amt_str = str(l['amount'])
            bal_after = str(l['balance_after']) if l['balance_after'] else "-"
        elif l['src_type'] == 'DEPOSIT':
            action_type = "تسديد دين" if l['detail'] == 'debt_payment' else "إيداع وتغذية"
            target = "المحفظة"
            amt_str = str(l['extra']) if l['detail'] == 'debt_payment' else str(l['amount'])
            bal_after = "-"
        else:
            action_type = "دفع فاتورة"
            target = str(l['extra'])
            amt_str = str(l['amount'])
            bal_after = "-"

        c.drawString(50, y, date_short)
        c.drawString(150, y, fix_ar(action_type))
        c.drawString(250, y, fix_ar(target))
        c.drawString(350, y, amt_str)
        c.drawString(450, y, bal_after)
        y -= 20

    c.save()
    buffer.seek(0)
    buffer.name = f"Statement_{user_id}.pdf"
    bot.send_document(user_id, document=buffer, caption="📄 كشف حسابك التفصيلي لآخر 50 عملية.")
# =============================================================
# التشغيل والويب هوك الأساسي
# =============================================================
@app.route('/ping')
def ping(): return "Server is UP and Running! 🚀", 200

@app.route('/fund_employee', methods=['GET', 'POST'])
def fund_employee():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')

    conn = get_db_connection()
    emp_info = conn.execute("SELECT user_id, balance, bills_balance, emp_cash, emp_profit FROM users WHERE role='employee' LIMIT 1").fetchone()
    if not emp_info: conn.close(); return "<h2 dir='rtl' style='text-align:center; color:red; margin-top:50px;'>لم يتم العثور على حساب الموظف!</h2>"
    emp_id = emp_info['user_id']
    today = datetime.now(local_tz).strftime("%Y-%m-%d")

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'new_fund':
            amt_str, w_type = request.form.get('amt'), request.form.get('wallet_type')
            if amt_str and w_type:
                try:
                    amt = float(amt_str)
                    if amt > 0:
                        balance_col = 'balance' if w_type == 'units' else 'bills_balance'
                        conn.execute(f"UPDATE users SET {balance_col} = {balance_col} + ? WHERE role = 'employee'", (amt,))
                        conn.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                     (emp_id, session['user_id'], amt, 0, f"admin_fund_{w_type}", 0, 0, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
                        conn.commit()
                        return redirect('/fund_employee?success=1')
                except ValueError: pass

        elif action == 'direct_edit':
            target, new_val_str = request.form.get('target'), request.form.get('new_val')
            if target and new_val_str:
                try:
                    new_val = float(new_val_str)
                    if new_val >= 0:
                        if target in ['units', 'bills']:
                            col = 'balance' if target == 'units' else 'bills_balance'
                            conn.execute(f"UPDATE users SET {col} = ? WHERE role = 'employee'", (new_val,))
                        elif target == 'emp_cash':
                            # 🌟 تحديث كاش الموظف في الدرج يدوياً 🌟
                            conn.execute("UPDATE users SET emp_cash = ? WHERE role = 'employee'", (new_val,))
                        conn.commit(); return redirect('/fund_employee?success=updated')
                except ValueError: pass

    emp_data = conn.execute("SELECT balance, bills_balance, emp_cash, emp_profit FROM users WHERE user_id=?", (emp_id,)).fetchone()

    # 1. سجل الإدارة (شحن الموظف)
    recent_funds = conn.execute("SELECT * FROM deposit_logs WHERE user_id = ? AND wallet_type LIKE 'admin_fund_%' ORDER BY id DESC LIMIT 10", (emp_id,)).fetchall()
    funds_html = "".join([f"<tr><td class='text-muted small'>{f['date'][11:16]}</td><td>{'وحدات 📱' if 'units' in f['wallet_type'] else 'فواتير 🧾'}</td><td class='fw-bold text-primary' dir='ltr'>{f['amount']:g}</td></tr>" for f in recent_funds])
    if not funds_html: funds_html = "<tr><td colspan='3' class='text-muted py-3'>لا توجد عمليات شحن سابقة.</td></tr>"

    # 2. سجل مبيعات الموظف التفصيلي للزبائن
    emp_sales = conn.execute("SELECT d.*, u.real_name FROM deposit_logs d JOIN users u ON d.user_id = u.user_id WHERE d.by_admin_id = ? AND d.wallet_type IN ('units', 'bills', 'debt_payment', 'advance_payment') ORDER BY d.id DESC LIMIT 50", (emp_id,)).fetchall()
    conn.close()

    sales_html = ""
    for s in emp_sales:
        s_dict = dict(s)
        time_str = s_dict['date'][11:16] if s_dict['date'] else "---"
        if s_dict['wallet_type'] == 'debt_payment':
            sales_html += f"<tr><td class='text-muted' dir='ltr'>{time_str}</td><td class='fw-bold text-primary fs-6'><a href='/user/{s_dict['user_id']}' class='text-decoration-none'>{s_dict['real_name']}</a></td><td><span class='badge bg-info text-dark'>تسديد ديون</span></td><td><span class='text-success fw-bold'>+ {s_dict['actual_paid']:g} ل.س كاش</span></td><td class='text-muted'>-</td><td class='text-muted'>-</td></tr>"
        elif s_dict['wallet_type'] == 'advance_payment':
            sales_html += f"<tr><td class='text-muted' dir='ltr'>{time_str}</td><td class='fw-bold text-danger fs-6'>سلفة الموظف</td><td><span class='badge bg-danger'>سحب كاش</span></td><td><span class='text-danger fw-bold'>- {s_dict['actual_paid']:g} ل.س</span></td><td class='text-muted'>-</td><td class='text-muted'>-</td></tr>"
        else:
            w_icon = "📱" if s_dict['wallet_type'] == 'units' else "🧾"
            pay_type = "📝 دين" if s_dict['is_debt'] else "💵 كاش"
            b_before = s_dict.get('emp_balance_before') or 0
            b_after = s_dict.get('emp_balance_after') or 0
            sales_html += f"<tr><td class='text-muted' dir='ltr'>{time_str}</td><td class='fw-bold text-primary fs-6'><a href='/user/{s_dict['user_id']}' class='text-decoration-none'>{s_dict['real_name']}</a><br><small class='text-muted'>{pay_type}</small></td><td><span class='badge bg-primary'>بيع رصيد</span></td><td><span class='text-danger fw-bold'>- {s_dict['amount']:g} {w_icon}</span></td><td class='text-muted' dir='ltr'>{b_before:g}</td><td class='fw-black text-danger' dir='ltr'>{b_after:g}</td></tr>"

    if not sales_html: sales_html = "<tr><td colspan='6' class='text-muted py-4'>الموظف لم يقم بأي عملية بيع حتى الآن.</td></tr>"

    content = f"""
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h3 class="fw-black text-primary"><i class="fas fa-wallet me-2"></i> شحن ومراقبة الموظف</h3>
        <a href="/" class="btn btn-outline-secondary fw-bold shadow-sm"><i class="fas fa-home me-1"></i> الرئيسية</a>
    </div>
    {{% if request.args.get('success') %}}<div class="alert alert-success fw-bold shadow-sm mb-4"><i class="fas fa-check-circle me-2"></i> تم تنفيذ العملية بنجاح!</div>{{% endif %}}

    <div class="row g-4 mb-4">
        <div class="col-md-4"><div class="card-bank text-center border-primary border-start border-4 shadow-sm" style="background: #eff6ff;"><p class="text-primary fw-bold mb-2">عهدته الحالية (وحدات)</p><h2 class="fw-black text-dark mb-3" dir="ltr">{emp_data['balance']:,.2f}</h2><button class="btn btn-sm btn-outline-primary fw-bold w-75" onclick="promptEdit('units', {emp_data['balance']})"><i class="fas fa-pen me-1"></i> تعديل الوحدات</button></div></div>
        <div class="col-md-4"><div class="card-bank text-center border-success border-start border-4 shadow-sm" style="background: #f0fdf4;"><p class="text-success fw-bold mb-2">عهدته الحالية (فواتير)</p><h2 class="fw-black text-dark mb-3" dir="ltr">{emp_data['bills_balance']:,.2f}</h2><button class="btn btn-sm btn-outline-success fw-bold w-75" onclick="promptEdit('bills', {emp_data['bills_balance']})"><i class="fas fa-pen me-1"></i> تعديل الفواتير</button></div></div>
        <div class="col-md-4"><div class="card-bank text-center border-warning border-start border-4 shadow-sm" style="background: #fffbeb;"><p class="text-warning text-dark fw-bold mb-2">كاش الموظف بالدرج 💵</p><h2 class="fw-black text-dark mb-3" dir="ltr">{emp_data['emp_cash']:,.0f}</h2><button class="btn btn-sm btn-outline-warning text-dark fw-bold w-75" onclick="promptEdit('emp_cash', {emp_data['emp_cash']})"><i class="fas fa-pen me-1"></i> ضبط الكاش</button></div></div>
    </div>

    <div class="row g-4 mb-4">
        <div class="col-lg-5">
            <div class="card-bank border-primary border-start border-5 h-100">
                <h5 class="fw-bold mb-4 text-primary"><i class="fas fa-plus-circle me-2"></i> إضافة رصيد (شحن جديد)</h5>
                <form method="POST"><input type="hidden" name="action" value="new_fund">
                    <div class="mb-3"><label class="form-label text-muted fw-bold">الكمية المراد إضافتها</label><input name="amt" type="number" step="0.01" class="form-control border-primary shadow-sm p-3" required></div>
                    <div class="mb-4"><label class="form-label text-muted fw-bold">إلى أي محفظة؟</label><select name="wallet_type" class="form-select bg-light shadow-sm p-3" required><option value="units">محفظة الوحدات 📱</option><option value="bills">محفظة الفواتير 🧾</option></select></div>
                    <button type="submit" class="btn btn-primary btn-lg w-100 fw-bold shadow"><i class="fas fa-bolt me-2"></i> تنفيذ الشحن الفوري</button>
                </form>
            </div>
        </div>
        <div class="col-lg-7">
            <div class="card-bank h-100">
                <h5 class="fw-bold mb-4 text-dark"><i class="fas fa-history me-2"></i> سجل المبالغ التي شحنتها للموظف</h5>
                <div class="table-responsive"><table class="table text-center align-middle"><thead class="table-light"><tr><th>الوقت</th><th>المحفظة</th><th>الكمية المشحونة</th></tr></thead><tbody>{funds_html}</tbody></table></div>
            </div>
        </div>
    </div>

    <div class="card-bank border-top border-dark border-5 mb-5 shadow-sm">
        <h5 class="fw-bold mb-4 text-dark"><i class="fas fa-list-check me-2"></i> سجل مراقبة مبيعات الموظف التفصيلي (الرصيد قبل وبعد)</h5>
        <div class="table-responsive">
            <table class="table text-center align-middle">
                <thead class="table-light"><tr><th>التاريخ والوقت</th><th>اسم الزبون المستلم</th><th>نوع العملية</th><th>المبلغ المحول / المقبوض</th><th>عهدة الموظف (قبل)</th><th>عهدة الموظف (بعد)</th></tr></thead>
                <tbody>{sales_html}</tbody>
            </table>
        </div>
    </div>

    <div class="modal fade" id="editWalletModal" tabindex="-1" aria-hidden="true"><div class="modal-dialog modal-dialog-centered"><div class="modal-content border-0 shadow-lg rounded-4"><div class="modal-header bg-primary text-white border-0 rounded-top-4"><h5 class="modal-title fw-bold"><i class="fas fa-pen me-2"></i> إعداد القيمة الصحيحة</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><form method="POST"><div class="modal-body p-4"><input type="hidden" name="action" value="direct_edit"><input type="hidden" name="target" id="edit_target"><p class="text-muted small fw-bold mb-3">أدخل الرقم النهائي الصحيح.</p><div class="mb-3"><label class="form-label fw-bold text-dark mb-2">الرقم الفعلي الآن:</label><input type="number" step="0.01" name="new_val" id="edit_new_val" class="form-control bg-light border-0 shadow-sm p-3 fw-bold fs-5" required></div></div><div class="modal-footer border-0 pb-4 pe-4"><button type="button" class="btn btn-light shadow-sm fw-bold" data-bs-dismiss="modal">إلغاء</button><button type="submit" class="btn btn-danger px-4 fw-bold shadow-sm"><i class="fas fa-save me-1"></i> حفظ التعديل</button></div></form></div></div></div>
    <script>function promptEdit(target, currentVal) {{ document.getElementById('edit_target').value = target; document.getElementById('edit_new_val').value = currentVal; new bootstrap.Modal(document.getElementById('editWalletModal')).show(); }}</script>
    """
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='fund_employee')

@app.route('/logout')
def logout(): session.clear(); return redirect('/')

@app.route('/' + API_TOKEN, methods=['POST'])
def getMessage():
    try:
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        # رجعناها لطبيعتها لأن البوت صار مبرمج يتعامل مع الطابور بسرعة من التعديل الأول
        bot.process_new_updates([update])
    except Exception as e:
        pass 
    return "!", 200

@app.route("/set_webhook")
def webhook():
    try:
        # مسح الاتصال القديم وتأخير ثانية لتجنب حظر تلغرام
        bot.remove_webhook()
        time.sleep(1)

        # إنشاء الاتصال الجديد
        url = "https://" + request.host + "/" + API_TOKEN
        bot.set_webhook(url=url)

        # إعادة ضبط أزرار القائمة (القائمة الجانبية في التلغرام)
        public_commands = [
            telebot.types.BotCommand("start", "القائمة الرئيسية"),
            telebot.types.BotCommand("cancel", "إلغاء العملية الحالية")
        ]
        bot.set_my_commands(public_commands)

        return f"<h2 dir='rtl' style='text-align:center; color:green; margin-top:50px;'>✅ تم ربط البوت بنجاح! السيرفر يعمل بشكل ممتاز.<br><small style='color:gray;'>{url}</small></h2>", 200

    except Exception as e:
        # في حال رفض تلغرام الاتصال، لن ينهار الموقع بخطأ 500، بل سيعطيك سبب المشكلة
        return f"<h2 dir='rtl' style='text-align:center; color:red; margin-top:50px;'>❌ سيرفر تلغرام يرفض الاتصال مؤقتاً!<br>السبب: {str(e)}<br>انتظر دقيقة واحدة ثم قم بتحديث هذه الصفحة.</h2>", 200
    # 1. أوامر للزباين العاديين (بدون جرد)
    public_commands = [
        telebot.types.BotCommand("start", "القائمة الرئيسية"),
        telebot.types.BotCommand("cancel", "إلغاء العملية الحالية")
    ]
    bot.set_my_commands(public_commands)

    # 2. أوامر مخصصة للمدير فقط (بتظهر بحسابك أنت بس)
    try:
        admin_commands = public_commands + [telebot.types.BotCommand("report", "📊 جرد الصندوق المركزي")]
        bot.set_my_commands(admin_commands, scope=telebot.types.BotCommandScopeChat(ADMIN_TG_ID))
    except: pass

    return f"✅ تم تفعيل الويب هوك وتحديث القوائم بنجاح!<br>الرابط: {url}"


# قالب مخصص للزبائن (تصميم تطبيق موبايل احترافي - يدعم الوضعين)
CUSTOMER_HTML_BASE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>الرفاعي| Premium</title>

    <link rel="manifest" href="/manifest.json">
    <meta name="theme-color" content="#0B0F19" id="theme-color-meta">
    <link rel="apple-touch-icon" href="https://cdn-icons-png.flaticon.com/512/2830/2830284.png">

    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;800;900&display=swap" rel="stylesheet">

    <style>
        /* المتغيرات الذكية للوضع الليلي (الأساسي) */
        :root {
            --bg-main: #0a0f1d;
            --card-bg: rgba(255, 255, 255, 0.04);
            --card-border: rgba(255, 255, 255, 0.05);
            --gold: #f59e0b;
            --gold-grad: linear-gradient(135deg, #fbbf24 0%, #d97706 100%);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --input-bg: rgba(0,0,0,0.25);
            --nav-bg: rgba(15, 23, 42, 0.85);
            --icon-bg: rgba(255,255,255,0.05);
        }

        /* المتغيرات الذكية للوضع النهاري (Light Mode) */
        body.light-mode {
            --bg-main: #f1f5f9;
            --card-bg: #ffffff;
            --card-border: rgba(0, 0, 0, 0.1);
            --text-main: #0f172a;
            --text-muted: #64748b;
            --input-bg: #f8fafc;
            --nav-bg: rgba(255, 255, 255, 0.95);
            --icon-bg: #f1f5f9;
        }

        body { background-color: var(--bg-main); color: var(--text-main); font-family: 'Cairo', sans-serif; overflow-x: hidden; padding-bottom: 100px; -webkit-tap-highlight-color: transparent; transition: background-color 0.3s, color 0.3s; }

        .premium-bg { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: -1; background: radial-gradient(circle at 15% 50%, rgba(217, 119, 6, 0.08), transparent 35%), radial-gradient(circle at 85% 30%, rgba(59, 130, 246, 0.08), transparent 35%); }

        .glass-card { background: var(--card-bg); backdrop-filter: blur(20px); border: 1px solid var(--card-border); border-radius: 28px; padding: 25px; box-shadow: 0 10px 40px rgba(0, 0, 0, 0.05); transition: 0.3s;}

        .wallet-card { background: var(--gold-grad); color: #000; border-radius: 28px; padding: 30px 25px; position: relative; overflow: hidden; box-shadow: 0 15px 35px rgba(217, 119, 6, 0.25); border: 1px solid rgba(255,255,255,0.4); }
        .wallet-card::before { content: ''; position: absolute; top: -50px; right: -20px; width: 150px; height: 150px; background: rgba(255,255,255,0.3); border-radius: 50%; }
        .wallet-card::after { content: ''; position: absolute; bottom: -50px; left: -20px; width: 150px; height: 150px; background: rgba(0,0,0,0.1); border-radius: 50%; }

        .premium-input { background: var(--input-bg) !important; border: 1px solid var(--card-border) !important; color: var(--text-main) !important; border-radius: 20px !important; padding: 18px 20px !important; font-weight: bold; font-size: 1.15rem; box-shadow: inset 0 2px 10px rgba(0,0,0,0.02); transition: 0.3s;}
        .premium-input:focus { box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.3) !important; border-color: var(--gold) !important; }
        .premium-input::placeholder { color: var(--text-muted) !important; opacity: 0.7; }
        select.premium-input option { background: var(--bg-main); color: var(--text-main); }

        .btn-gold { background: var(--gold-grad); color: #000; font-weight: 900; border-radius: 20px; padding: 18px; border: none; transition: 0.3s; box-shadow: 0 8px 20px rgba(217, 119, 6, 0.3); }
        .btn-gold:hover { transform: translateY(-3px); box-shadow: 0 12px 25px rgba(217, 119, 6, 0.5); color: #000; }

        .bottom-nav { position: fixed; bottom: 20px; left: 20px; right: 20px; background: var(--nav-bg); backdrop-filter: blur(25px); border-radius: 30px; border: 1px solid var(--card-border); box-shadow: 0 15px 40px rgba(0,0,0,0.1); display: flex; justify-content: space-around; padding: 12px 5px; z-index: 1000; transition: 0.3s;}
        .nav-item { text-align: center; color: var(--text-muted); text-decoration: none; font-size: 11px; font-weight: 800; transition: 0.3s; cursor: pointer; flex: 1; padding: 10px 0; border-radius: 20px; }
        .nav-item i { font-size: 22px; display: block; margin-bottom: 5px; transition: 0.3s; }
        .nav-item.active { color: var(--gold); background: rgba(217, 119, 6, 0.1); }
        .nav-item.active i { transform: scale(1.2); text-shadow: 0 0 15px rgba(251, 191, 36, 0.6); }

        /* أدوات التوافق مع الألوان */
        .text-white, .text-dark { color: var(--text-main) !important; transition: 0.3s; }
        .bg-dark { background-color: var(--icon-bg) !important; transition: 0.3s; }
        .border-secondary { border-color: var(--card-border) !important; }
        .modal-content { background-color: var(--bg-main) !important; border: 1px solid var(--card-border) !important; }
        .toast { background-color: var(--card-bg) !important; color: var(--text-main) !important; }

        .text-gold { color: var(--gold) !important; }
        .hide-scroll::-webkit-scrollbar { display: none; }
        .hide-scroll { -ms-overflow-style: none; scrollbar-width: none; }
        .fw-black { font-weight: 900; }
        .fade-in { animation: fadeIn 0.4s ease-out; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(15px); } to { opacity: 1; transform: translateY(0); } }
        .app-section { display: none; }
        .app-section.active { display: block; }
        /* ستايل أزرار الشركات الدائرية الفخمة */
        .company-btn {
            width: 75px;
            height: 75px;
            border-radius: 50% !important;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            font-weight: 900;
            border: 2px solid var(--card-border) !important;
            background: var(--card-bg) !important;
            color: var(--text-main) !important;
            transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            white-space: normal;
            text-align: center;
            line-height: 1.2;
            padding: 5px;
            box-shadow: 0 4px 10px rgba(0,0,0,0.1);
        }

        /* تأثير الإضاءة عند الاختيار (الوضع الليلي: ذهبي) */
        .company-btn.active-glow {
            transform: scale(1.15);
            border-color: var(--gold) !important;
            box-shadow: 0 0 20px var(--gold);
            background: var(--gold-grad) !important;
            color: #000 !important;
        }

        /* تأثير الإضاءة عند الاختيار (الوضع النهاري: أزرق) */
        body.light-mode .company-btn.active-glow {
            border-color: #3b82f6 !important;
            box-shadow: 0 0 20px rgba(59, 130, 246, 0.6);
            background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%) !important;
            color: white !important;
        }
        /* تثبيت شكل زر الدفع ليكون واضحاً دائماً (تم الإصلاح) */
        .btn-gold {
            background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%) !important;
            color: #ffffff !important;
            border: none !important;
            box-shadow: 0 8px 20px rgba(59, 130, 246, 0.4) !important;
            transition: 0.3s all ease;
        }

        .btn-gold:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(59, 130, 246, 0.4) !important;
            filter: brightness(1.1);
        }

        /* تعديل لون حقل الإدخال بالوضع النهاري ليكون واضحاً */
        body.light-mode .premium-input {
            background: #ffffff !important;
            color: #000000 !important;
            border: 1px solid #e2e8f0 !important;
        }
    </style>
</head>
<body>
    <div class="premium-bg"></div>
    <div class="container py-4">
        {content}
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js'); });
        }
    </script>
</body>
</html>
"""

# 1. شاشة تسجيل الدخول المعزولة والمحصنة
# ==========================================
# إعدادات تطبيق الويب (PWA)
# ==========================================
@app.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "مركز الرفاعي للإتصالات",
        "short_name": "ون تاتش",
        "start_url": "/portal",
        "display": "standalone",
        "background_color": "#f4f7fa",
        "theme_color": "#0d6efd",
        "icons": [
            {
                "src": "https://cdn-icons-png.flaticon.com/512/2830/2830284.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable"
            }
        ]
    })

@app.route('/sw.js')
def service_worker():
    sw_code = """
    self.addEventListener('install', (e) => {
        console.log('[Service Worker] تم التثبيت بنجاح');
    });
    self.addEventListener('fetch', (e) => {
        // فارغ حالياً - يكفي لجعل المتصفح يقبل تثبيت التطبيق
    });
    """
    return app.response_class(sw_code, mimetype='application/javascript')
@app.route('/portal', methods=['GET', 'POST'])
def portal_login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        conn = get_db_connection()
        # فحص ذكي: يبحث عن اليوزرنيم (لزبائن الويب) أو الآيدي (لزبائن التلغرام)
        user = conn.execute("SELECT * FROM users WHERE (username=? OR user_id=?) AND password=?",
                             (username, username, password)).fetchone()
        conn.close()

        if user:
            if user['is_banned'] == 1:
                error = "⛔ هذا الحساب محظور من قبل الإدارة."
            elif user['is_approved'] == 0:
                error = "❄️ الحساب مجمد حالياً، تواصل مع الإدارة لتفعيله."
            else:
                # تسجيل الدخول بنجاح
                session['user_id'] = user['user_id']
                session['username'] = user['real_name']
                session['role'] = user['role']
                session['logged_in'] = True
                session.permanent = True

                # التوجيه الصحيح:
                if user['role'] == 'user':
                    return redirect('/customer_dashboard')
                else:
                    return redirect('/') # للمدير والموظف
        else:
            error = "❌ خطأ في اسم المستخدم أو كلمة المرور."

    content = f"""
    <style>
        .portal-bg {{ position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: #0B0F19; z-index: -2; overflow: hidden; }}
        .portal-bg::before {{ content: ''; position: absolute; width: 45vw; height: 45vw; background: #d97706; border-radius: 50%; top: -20%; right: -15%; filter: blur(120px); opacity: 0.25; animation: drift 10s infinite alternate; }}
        .portal-bg::after {{ content: ''; position: absolute; width: 35vw; height: 35vw; background: #3b82f6; border-radius: 50%; bottom: -10%; left: -10%; filter: blur(100px); opacity: 0.15; animation: drift 8s infinite alternate-reverse; }}
        @keyframes drift {{ 0% {{ transform: translate(0,0) scale(1); }} 100% {{ transform: translate(50px, 50px) scale(1.1); }} }}
        .portal-glass {{ background: rgba(255, 255, 255, 0.03); backdrop-filter: blur(30px); -webkit-backdrop-filter: blur(30px); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 30px; padding: 45px 30px; box-shadow: 0 30px 60px rgba(0, 0, 0, 0.5); width: 100%; max-width: 400px; position: relative; z-index: 10; text-align: center; margin: auto; }}
        .portal-input {{ background: rgba(0,0,0,0.3) !important; color: #fff !important; border: 1px solid rgba(255,255,255,0.05) !important; border-radius: 16px !important; padding: 18px !important; transition: 0.3s; font-size: 1.1rem; }}
        .portal-input:focus {{ border-color: #f59e0b !important; box-shadow: 0 0 20px rgba(245,158,11,0.2) !important; outline: none; }}
        .portal-input::placeholder {{ color: #64748b !important; }}
        .portal-btn {{ background: linear-gradient(135deg, #fbbf24 0%, #d97706 100%); color: #000; border: none; border-radius: 16px; padding: 18px; font-weight: 900; font-size: 1.2rem; transition: 0.3s; width: 100%; box-shadow: 0 10px 25px rgba(217, 119, 6, 0.3); }}
        .portal-btn:hover {{ transform: translateY(-3px); box-shadow: 0 15px 30px rgba(217, 119, 6, 0.5); }}
    </style>
    <div class="portal-bg"></div>
    <div class="d-flex justify-content-center align-items-center" style="min-height: 100vh; padding: 15px; font-family: 'Cairo', sans-serif; position: relative;">
        <div class="portal-glass fade-in">
            <div class="mb-4">
                <div class="rounded-circle d-inline-flex justify-content-center align-items-center shadow-lg" style="width: 85px; height: 85px; background: linear-gradient(135deg, #fbbf24, #d97706); border: 2px solid rgba(255,255,255,0.2);">
                    <i class="fas fa-crown fa-2x" style="color: #000;"></i>
                </div>
            </div>
            <h3 class="fw-black text-white mb-2">بوابة الزبائن</h3>
            <h6 class="text-white-50 mb-4 fw-bold">مركز الرفاعي للإتصالات</h6>
            {{% if error %}}<div class="alert alert-danger text-center fw-bold rounded-4" style="background: rgba(239,68,68,0.2); border: 1px solid rgba(239,68,68,0.4); color: #fca5a5;">{{error}}</div>{{% endif %}}
            <form method="POST">
                <div class="mb-3">
                    <input type="text" name="username" class="form-control portal-input text-center fw-bold" placeholder="اسم المستخدم" required>
                </div>
                <div class="mb-4">
                    <input type="password" name="password" class="form-control portal-input text-center fw-bold" placeholder="كلمة المرور" required>
                </div>
                <button type="submit" class="portal-btn"><i class="fas fa-sign-in-alt me-2"></i> دخول للحساب</button>
            </form>
        </div>
    </div>
    """
    return render_template_string(CUSTOMER_HTML_BASE.replace('{content}', content))

@app.route('/api/customer_sync')
def customer_sync():
    if not session.get('logged_in') or session.get('role') != 'user':
        return jsonify({"status": "error"})

    search = request.args.get('search', '').strip()
    conn = get_db_connection()
    user = conn.execute("SELECT balance, bills_balance, debt_balance FROM users WHERE user_id = ?", (session['user_id'],)).fetchone()

    # الاستعلام المحدث لجلب الرصيد قبل وبعد التحويل
    query = "SELECT id, phone, network, amount, status, IFNULL(ussd_response, '') as reason, IFNULL(date, '---') as date, balance_before, balance_after FROM transactions WHERE user_id = ?"
    params = [session['user_id']]

    if search:
        query += " AND phone LIKE ?"
        params.append(f"%{search}%")
    query += " ORDER BY id DESC LIMIT 100"

    reqs = conn.execute(query, params).fetchall()
    favs = conn.execute("SELECT name, phone FROM user_favorites WHERE user_id = ? ORDER BY id DESC", (session['user_id'],)).fetchall()
    conn.close()

    return jsonify({
        "status": "success",
        "balance": user['balance'] or 0,
        "bills_balance": user['bills_balance'] or 0,
        "debt_balance": user['debt_balance'] or 0,
        "recent_reqs": [dict(r) for r in reqs],
        "favorites": [dict(f) for f in favs]
    })

@app.route('/api/debt_history')
def api_debt_history():
    if not session.get('logged_in') or session.get('role') != 'user':
        return jsonify({"status": "error"})

    conn = get_db_connection()
    # جلب رصيد الدين الحالي للزبون كبداية
    user = conn.execute("SELECT debt_balance FROM users WHERE user_id = ?", (session['user_id'],)).fetchone()
    current_debt = float(user['debt_balance'] or 0)

    # جلب العمليات (من الأحدث للأقدم)
    logs = conn.execute("""
        SELECT actual_paid, date, wallet_type, is_debt FROM deposit_logs
        WHERE user_id = ? AND (is_debt = 1 OR wallet_type = 'debt_payment' OR wallet_type = 'free_debt')
        ORDER BY id DESC LIMIT 50
    """, (session['user_id'],)).fetchall()
    conn.close()

    history = []
    running_debt = current_debt

    for l in logs:
        paid = float(l['actual_paid'] or 0)
        debt_after = running_debt

        # حساب الدين قبل هذه العملية (عملية عكسية لأننا نمشي من الأحدث للأقدم)
        if l['wallet_type'] == 'debt_payment':
            debt_before = running_debt + paid  # كان دينه أكبر قبل التسديد
        elif l['wallet_type'] == 'free_debt' or l['is_debt'] == 1:
            debt_before = running_debt - paid  # كان دينه أقل قبل الدين الجديد
        else:
            debt_before = running_debt

        history.append({
            "actual_paid": paid,
            "date": l['date'],
            "wallet_type": l['wallet_type'],
            "debt_before": debt_before,
            "debt_after": debt_after
        })
        # تجهيز الرصيد للعملية التي قبلها
        running_debt = debt_before

    return jsonify({"status": "success", "history": history})

@app.route('/customer_dashboard', methods=['GET', 'POST'])
def customer_dashboard():
    if not session.get('logged_in') or session.get('role') != 'user':
        return redirect('/portal')

    user_id = session['user_id']
    conn = get_db_connection()
    msg = ""

    if request.args.get('status') == 'success_transfer':
        msg = "<div class='alert alert-success fw-bold text-center fade-in bg-success bg-opacity-25 text-white border-0 rounded-4'>✅ تم إرسال طلب الشحن بنجاح! جاري التحويل الآلي...</div>"
    elif request.args.get('status') == 'success_bill':
        msg = "<div class='alert alert-success fw-bold text-center fade-in bg-success bg-opacity-25 text-white border-0 rounded-4'>✅ تم استلام طلب الفاتورة بنجاح! قيد التنفيذ من قبل الإدارة...</div>"

    if request.method == 'POST':
        form_type = request.form.get('form_type')
        user = conn.execute("SELECT balance, bills_balance, real_name FROM users WHERE user_id = ?", (user_id,)).fetchone()

        if form_type == 'mobile_transfer':
            target_phone = request.form.get('target_phone').strip()
            prefix = target_phone[:3]

            if len(target_phone) == 8 and target_phone.isdigit():
                network = "Syriatel"
            elif len(target_phone) == 10 and prefix in ['093', '098', '099']:
                network = "Syriatel"
            elif len(target_phone) == 10 and prefix in ['094', '095', '096']:
                network = "MTN"
            else:
                network = ""

            if not network:
                msg = "<div class='alert alert-danger fw-bold text-center bg-danger bg-opacity-25 text-white border-0 rounded-4'>⚠️ عذراً، الرقم غير صحيح أو غير تابع لشبكاتنا.</div>"
            else:
                # ==========================================
                # 🛑 حاجز فحص حالة الشبكة (لـ الويب آب) 🛑
                # ==========================================
                net_status = get_setting(f'status_{network}')
                if str(net_status) == '0':
                    msg = f"<div class='alert alert-danger fw-bold text-center bg-danger bg-opacity-25 text-white border-0 rounded-4'>⚠️ عذراً! شبكة {network} متوقفة حالياً للصيانة ⚠️</div>"
                else:
                    try:
                        target_amount = float(request.form.get('amount'))
                        cats = conn.execute("SELECT amount, ussd_amount FROM categories WHERE network=? AND is_active=1", (network,)).fetchall()

                        if not cats:
                            msg = f"<div class='alert alert-danger fw-bold text-center bg-danger bg-opacity-25 text-white border-0 rounded-4'>⚠️ فئات {network} غير متوفرة حالياً.</div>"
                        else:
                            denoms = [float(c['amount']) for c in cats]
                            amt_to_ussd = {float(c['amount']): c['ussd_amount'] for c in cats}
                            combo = find_best_denominations(target_amount, denoms)

                            if len(combo) > 5:
                                msg = "<div class='alert alert-danger fw-bold text-center bg-danger bg-opacity-25 text-white border-0 rounded-4'>⚠️ المبلغ كبير جداً، يرجى تقسيمه.</div>"
                            else:
                                total_sum = sum(combo)
                                if user['balance'] < total_sum:
                                    msg = f"<div class='alert alert-danger fw-bold text-center bg-danger bg-opacity-25 text-white border-0 rounded-4'>⚠️ رصيد الوحدات لا يكفي. المطلوب: {total_sum:g}</div>"
                                else:
                                    # 🛑 فحص رصيد شريحة الإدارة قبل الإرسال 🛑
                                    sim_bal = float(get_setting(f'sim_balance_{network}') or 0)
                                    if total_sum > sim_bal:
                                        msg = f"<div class='alert alert-danger fw-bold text-center bg-danger bg-opacity-25 text-white border-0 rounded-4'>⚠️ عذراً! رصيد شبكة {network} الأساسي لا يكفي حالياً.</div>"
                                    else:
                                        new_bal = user['balance'] - total_sum
                                        conn.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_bal, user_id))
                                        for c in combo:
                                            conn.execute("INSERT INTO transactions (user_id, type, network, service_type, phone, amount, ussd_amount, status, ussd_response, profit, date, balance_before, balance_after) VALUES (?, 'TRANSFER', ?, 'Jahez', ?, ?, ?, 'QUEUED', 'Waiting', 0, ?, ?, ?)",
                                                         (user_id, network, target_phone, c, amt_to_ussd[c], datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S"), user['balance'], new_bal))
                                        conn.commit()
                                        return redirect('/customer_dashboard?status=success_transfer')
                    except: pass

        elif form_type == 'bill_payment':
            srv_id = request.form.get('service_id')
            target_info = request.form.get('target_info').strip()

            try:
                srv = conn.execute("SELECT s.*, c.name as comp_name FROM manual_services s JOIN companies c ON s.company_id = c.id WHERE s.id=?", (srv_id,)).fetchone()
                if srv:
                    price = float(srv['price'])
                    profit = price - float(srv['cost'])

                    if user['bills_balance'] < price:
                        msg = f"<div class='alert alert-danger fw-bold text-center bg-danger bg-opacity-25 text-white border-0 rounded-4'>⚠️ رصيد الفواتير لا يكفي. سعر الباقة: {price:g} ل.س</div>"
                    else:
                        conn.execute("UPDATE users SET bills_balance = bills_balance - ? WHERE user_id = ?", (price, user_id))
                        full_service_name = f"{srv['comp_name']} - {srv['name']}"
                        conn.execute("INSERT INTO manual_orders (user_id, service_name, target_info, price, status, profit, date) VALUES (?, ?, ?, ?, 'PENDING', ?, ?)",
                                     (user_id, full_service_name, target_info, price, profit, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
                        conn.commit()

                        try:
                            bot.send_message(ADMIN_TG_ID, f"🔔 *طلب فاتورة جديد (من الويب)*\n👤 الزبون: {user['real_name']}\n🏢 الخدمة: {full_service_name}\n🎯 الحساب: `{target_info}`\n💰 السعر: `{price:g}` ل.س\n\nيرجى الدخول للوحة (الطلبات الواردة) للتنفيذ.", parse_mode="Markdown")
                        except: pass

                        return redirect('/customer_dashboard?status=success_bill')
            except: pass

    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    requests_list = conn.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user_id,)).fetchall()
    import json
    services = conn.execute("SELECT s.id, s.name, s.price, c.name as comp_name FROM manual_services s JOIN companies c ON s.company_id = c.id WHERE s.is_active=1 AND c.is_active=1 ORDER BY c.id").fetchall()
    conn.close()

    # تحويل البيانات لـ JSON ذكي بدل القائمة المنسدلة العقيمة
    companies_data = {}
    for s in services:
        comp = s['comp_name']
        if comp not in companies_data:
            companies_data[comp] = []
        companies_data[comp].append({'id': s['id'], 'name': s['name'], 'price': s['price']})
    companies_js_json = json.dumps(companies_data, ensure_ascii=False)

    reqs_js_array = [f'"{r["id"]}": "{r["status"]}"' for r in requests_list]
    reqs_js_obj = "{" + ", ".join(reqs_js_array) + "}"

    content = f"""
    <div class="toast-container position-fixed top-0 start-50 translate-middle-x p-3" style="z-index: 10000; width: 95%;">
      <div id="liveToast" class="toast align-items-center text-white border-0 shadow-lg rounded-pill" role="alert" aria-live="assertive" aria-atomic="true" data-bs-delay="4000">
        <div class="d-flex p-1"><div class="toast-body fw-bold fs-6 text-center w-100" id="toastMessage"></div></div>
      </div>
    </div>

    <div class="d-flex justify-content-between align-items-center mb-4 fade-in">
        <div class="d-flex align-items-center gap-3">
            <div class="bg-dark rounded-circle border border-secondary d-flex align-items-center justify-content-center" style="width: 55px; height: 55px; box-shadow: 0 0 15px rgba(251,191,36,0.2);">
                <i class="fas fa-crown text-gold fs-4"></i>
            </div>
            <div>
                <p class="m-0 text-muted small fw-bold">أهلاً بك يا VIP،</p>
                <h5 class="fw-black text-white m-0">{user['real_name']}</h5>
            </div>
        </div>
        <div class="d-flex gap-2">
            <button onclick="toggleTheme()" id="themeBtn" class="btn bg-dark rounded-circle border border-secondary d-flex align-items-center justify-content-center shadow-sm" style="width: 45px; height: 45px;"><i class="fas fa-sun text-warning"></i></button>
            <a href="/logout" class="btn bg-dark rounded-circle border border-secondary text-danger d-flex align-items-center justify-content-center shadow-sm" style="width: 45px; height: 45px;"><i class="fas fa-power-off"></i></a>
        </div>
    </div>

    {msg}

    <div id="tab_home" class="app-section active fade-in">
        <div class="wallet-card mb-4">
            <div class="d-flex justify-content-between align-items-center mb-3 position-relative z-1">
                <span class="fw-bold" style="color: rgba(0,0,0,0.7);"><i class="fas fa-wallet me-2"></i>الرصيد المتاح (وحدات)</span>
                <i class="fas fa-gem fs-2" style="color: rgba(0,0,0,0.3);"></i>
            </div>
            <h1 class="fw-black mb-4 position-relative z-1" id="val_units" style="font-size: 2.8rem; letter-spacing: 1px;">{user['balance']:,.0f}</h1>
            <div class="row text-center mt-3 pt-3 border-top border-dark border-opacity-10 position-relative z-1">
                <div class="col-6 border-end border-dark border-opacity-10">
                    <span class="d-block small fw-bold mb-1" style="color: rgba(0,0,0,0.6);">الفواتير 🧾</span>
                    <h5 class="fw-black m-0" id="val_bills">{user['bills_balance']:,.0f}</h5>
                </div>
                <div class="col-6" onclick="openDebtDetails()" style="cursor: pointer;">
                    <span class="d-block small fw-bold mb-1" style="color: rgba(0,0,0,0.6);">الديون 📝 <i class="fas fa-info-circle small opacity-50"></i></span>
                    <h5 class="fw-black m-0" id="val_debt">{user['debt_balance']:,.0f}</h5>
                </div>
            </div>
        </div>

        <div class="mb-4">
            <div class="d-flex justify-content-between align-items-center mb-3">
                <h6 class="fw-black text-white m-0">قائمة السريعة <i class="fas fa-bolt text-gold ms-1"></i></h6>
                <button class="btn btn-sm btn-outline-warning text-gold fw-bold rounded-pill px-3" onclick="openAddFavModal()"><i class="fas fa-plus"></i> إضافة رقم</button>
            </div>
            <div id="favStories" class="d-flex gap-3 overflow-auto pb-3 hide-scroll" style="flex-wrap: nowrap; -webkit-overflow-scrolling: touch;">
                <span class="text-muted small w-100 text-center mt-2">جاري تحميل المفضلة...</span>
            </div>
        </div>
    </div>

    <div id="tab_transfer" class="app-section fade-in">
        <div class="glass-card mb-4">
            <div class="text-center mb-4">
                <div class="bg-dark rounded-circle d-flex align-items-center justify-content-center mx-auto mb-3 text-gold border border-warning shadow-sm" style="width:75px; height:75px;"><i class="fas fa-paper-plane fs-2"></i></div>
                <h4 class="fw-black text-white">إرسال رصيد</h4>
                <p class="text-muted small fw-bold">سيريتل أو إم تي إن</p>
            </div>
            <form method="POST">
                <input type="hidden" name="form_type" value="mobile_transfer">
                <div class="mb-3 position-relative">
                    <i class="fas fa-mobile-alt position-absolute text-gold fs-5" style="top: 20px; right: 20px;"></i>
                    <input type="text" name="target_phone" list="historyPhones" class="form-control premium-input" style="padding-right: 55px !important;" dir="ltr" placeholder="09xxxxxxxx" autocomplete="off" required>
                    <datalist id="historyPhones"></datalist>
                </div>
                <div class="mb-4 position-relative">
                    <i class="fas fa-coins position-absolute text-gold fs-5" style="top: 20px; right: 20px;"></i>
                    <input type="number" name="amount" class="form-control premium-input" style="padding-right: 55px !important;" dir="ltr" placeholder="المبلغ" required>
                </div>
                <button type="submit" class="btn-gold w-100 fs-5 mt-2">إرسال الرصيد الآن 🚀</button>
            </form>
        </div>
    </div>

<div id="tab_bills" class="app-section fade-in">
        <div class="glass-card mb-4">
            <div class="text-center mb-4">
                <div class="bg-dark rounded-circle d-flex align-items-center justify-content-center mx-auto mb-3 text-primary border border-primary shadow-sm" style="width:75px; height:75px;"><i class="fas fa-file-invoice-dollar fs-2"></i></div>
                <h4 class="fw-black text-white">تسديد الفواتير</h4>
                <p class="text-muted small fw-bold">اختر الشركة لتظهر الباقات المتاحة</p>
            </div>

            <div class="mb-4 pb-2 border-bottom border-secondary border-opacity-25">
                <div id="companySelector" class="d-flex gap-2 overflow-auto pb-2 hide-scroll" style="flex-wrap: nowrap; -webkit-overflow-scrolling: touch;">
                    </div>
            </div>

            <form method="POST">
                <input type="hidden" name="form_type" value="bill_payment">
                <input type="hidden" name="service_id" id="selectedServiceId" required>

                <div id="packagesContainer" class="row g-3 mb-4" style="display: none;">
                    </div>

                <div class="mb-2 fade-in" id="targetInfoContainer" style="display: none;">
                    <div class="position-relative mb-3">
                        <i class="fas fa-at position-absolute text-primary fs-5" style="top: 18px; right: 20px;"></i>
                        <input type="text" name="target_info" class="form-control premium-input text-center fw-black fs-5" placeholder="رقم الحساب أو الهاتف" required>
                    </div>
                    <button type="submit" class="btn-gold w-100 fs-5" style="border-radius: 20px; padding: 12px;">دفع الفاتورة الآن 🚀</button>                </div>
            </form>
        </div>
    </div>
    <div id="tab_history" class="app-section fade-in">
        <div class="glass-card h-100 p-3">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <h5 class="fw-black m-0 text-white"><i class="fas fa-history text-warning me-2"></i> سجل العمليات</h5>
                <input type="text" id="phoneSearch" class="form-control premium-input form-control-sm w-50 text-white" style="padding: 10px 15px !important;" placeholder="بحث بالرقم...">
            </div>
            <div id="reqTableBody" class="hide-scroll" style="max-height: 60vh; overflow-y: auto; padding-right: 5px;"></div>
            <div id="paginationControls" class="mt-3"></div>
        </div>
    </div>

    <div class="bottom-nav">
        <div class="nav-item active" onclick="switchAppTab('tab_home', this)"><i class="fas fa-home"></i>الرئيسية</div>
        <div class="nav-item" onclick="switchAppTab('tab_transfer', this)"><i class="fas fa-paper-plane"></i>تحويل</div>
        <div class="nav-item" onclick="switchAppTab('tab_bills', this)"><i class="fas fa-receipt"></i>فواتير</div>
        <div class="nav-item" onclick="switchAppTab('tab_history', this)"><i class="fas fa-history"></i>السجل</div>
    </div>

    <div class="modal fade" id="addFavModal" tabindex="-1">
      <div class="modal-dialog modal-dialog-centered modal-sm">
        <div class="modal-content border border-warning shadow-lg" style="border-radius: 28px;">
          <div class="modal-header border-0 pb-0">
            <h5 class="modal-title fw-black text-white">إضافة رقم سريع</h5>
            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
          </div>
          <div class="modal-body p-4">
            <input type="text" id="fav_name_input" class="form-control premium-input mb-3" placeholder="اسم الزبون...">
            <input type="text" id="fav_phone_input" class="form-control premium-input mb-4 text-start" dir="ltr" placeholder="رقم الموبايل...">
            <button type="button" onclick="saveNewFavorite()" class="btn-gold w-100 fs-5">حفظ في القائمة ⭐</button>
          </div>
        </div>
      </div>
    </div>

<div class="modal fade" id="debtDetailsModal" tabindex="-1" aria-hidden="true">
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content border-0 shadow-lg" style="border-radius: 28px; background: var(--bg-main);">
          <div class="modal-header border-0 pb-0">
            <h5 class="modal-title fw-black text-white"><i class="fas fa-book me-2"></i> كشف الديون والمدفوعات</h5>
            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
          </div>
          <div class="modal-body p-4">
            <div id="debt_list_container" class="hide-scroll" style="max-height: 400px; overflow-y: auto;">
                </div>
          </div>
        </div>
      </div>
    </div>

    <script>
        let currentBal = {user['balance'] or 0};
        let currentBills = {user['bills_balance'] or 0};
        let currentReqs = {reqs_js_obj};
        let searchVal = "";
        let currentPage = 1;
        let itemsPerPage = 10;
        let allTransactions = [];
// --- نظام الفواتير الذكي ---
        let companiesData = {companies_js_json};

function renderCompanies() {{
            let compHtml = "";
            Object.keys(companiesData).forEach(comp => {{
                compHtml += `<div class="text-center px-1">
                    <button type='button' class='company-btn' id="btn_${{comp}}" onclick='selectCompany("${{comp}}")'>
                        ${{comp}}
                    </button>
                </div>`;
            }});
            document.getElementById('companySelector').innerHTML = compHtml;
        }}

        function selectCompany(compName) {{
            document.getElementById('selectedServiceId').value = "";

            // إزالة الإضاءة من كل الأزرار
            document.querySelectorAll('.company-btn').forEach(btn => {{
                btn.classList.remove('active-glow');
            }});

            // إضافة الإضاءة والنبض للشركة المختارة فقط
            let activeBtn = document.getElementById("btn_" + compName);
            if(activeBtn) activeBtn.classList.add('active-glow');

            // رسم بطاقات الباقات بشكل زجاجي فخم
            let pkgs = companiesData[compName];
            let pkgHtml = "";
            pkgs.forEach(p => {{
                pkgHtml += `
                <div class="col-6">
                    <div class="p-3 rounded-4 text-center pkg-card d-flex flex-column justify-content-center"
                         style="background: var(--card-bg); border: 1px solid var(--card-border); cursor: pointer; transition: 0.3s; height: 100%; min-height: 100px; box-shadow: 0 4px 15px rgba(0,0,0,0.05);"
                         onclick="selectPackage('${{p.id}}', this)">
                        <h6 class="fw-bold mb-2 text-wrap lh-sm" style="color: var(--text-main); font-size: 13px;">${{p.name}}</h6>
                        <div><span class="fw-black text-warning fs-5" dir="ltr">${{parseFloat(p.price).toLocaleString()}}</span> <small style="font-size: 10px; color: var(--text-muted);">ل.س</small></div>
                    </div>
                </div>`;
            }});
            document.getElementById('packagesContainer').innerHTML = pkgHtml;
            document.getElementById('packagesContainer').style.display = 'flex';
            document.getElementById('targetInfoContainer').style.display = 'none'; // إخفاء زر الدفع مؤقتاً
        }}
        function selectPackage(pkgId, element) {{
            document.getElementById('selectedServiceId').value = pkgId;

            document.querySelectorAll('.pkg-card').forEach(card => {{
                card.style.borderColor = 'var(--card-border)';
                card.style.transform = 'scale(1)';
                card.style.boxShadow = 'none';
                card.style.background = 'var(--card-bg)';
            }});

            element.style.borderColor = '#3b82f6';
            element.style.transform = 'scale(1.03)';
            element.style.boxShadow = '0 8px 20px rgba(59, 130, 246, 0.2)';
            element.style.background = 'rgba(59, 130, 246, 0.05)';

            document.getElementById('targetInfoContainer').style.display = 'block';
        }}

        setTimeout(renderCompanies, 200);
        // ---------------------------
        function toggleTheme() {{
            let body = document.body;
            body.classList.toggle('light-mode');
            let isLight = body.classList.contains('light-mode');
            localStorage.setItem('appTheme', isLight ? 'light' : 'dark');

            let btn = document.getElementById('themeBtn');
            if(isLight) {{
                btn.innerHTML = '<i class="fas fa-moon text-dark"></i>';
                document.getElementById('theme-color-meta').content = "#f1f5f9";
            }} else {{
                btn.innerHTML = '<i class="fas fa-sun text-warning"></i>';
                document.getElementById('theme-color-meta').content = "#0B0F19";
            }}
        }}

        if(localStorage.getItem('appTheme') === 'light') {{
            document.body.classList.add('light-mode');
            document.getElementById('theme-color-meta').content = "#f1f5f9";
            let btn = document.getElementById('themeBtn');
            if(btn) btn.innerHTML = '<i class="fas fa-moon text-dark"></i>';
        }}

        function switchAppTab(tabId, element) {{
            document.querySelectorAll('.app-section').forEach(sec => sec.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            document.querySelectorAll('.nav-item').forEach(nav => nav.classList.remove('active'));
            element.classList.add('active');
        }}

        document.getElementById('phoneSearch').oninput = function() {{ searchVal = this.value; currentPage = 1; updateData(); }};
        function changePage(dir) {{ currentPage += dir; renderTable(); }}

        function renderTable() {{
            let start = (currentPage - 1) * itemsPerPage;
            let pageData = allTransactions.slice(start, start + itemsPerPage);
            let rows = "";

            pageData.forEach(r => {{
                let statusInfo = "";
                let borderColor = "rgba(255,255,255,0.05)";

                if(r.status === 'SUCCESS') {{
                    statusInfo = '<span class="badge bg-success bg-opacity-25 text-success border border-success rounded-pill px-3 py-2"><i class="fas fa-check-circle me-1"></i> ناجح</span>';
                    borderColor = "#10b981";
                }} else if(r.status === 'QUEUED' || r.status === 'PROCESSING') {{
                    statusInfo = '<span class="badge bg-warning bg-opacity-25 text-warning border border-warning rounded-pill px-3 py-2"><i class="fas fa-spinner fa-spin me-1"></i> معلق</span>';
                    borderColor = "#f59e0b";
                }} else {{
                    statusInfo = '<span class="badge bg-danger bg-opacity-25 text-danger border border-danger rounded-pill px-3 py-2"><i class="fas fa-times-circle me-1"></i> فشل</span>';
                    borderColor = "#ef4444";
                }}

                let phoneNum = r.phone || "بدون رقم";
                let rawDate = r.date || "---";
                let dateDisplay = rawDate.length > 16 ? rawDate.substring(0, 16) : rawDate;

                let bBefore = r.balance_before !== null ? r.balance_before.toLocaleString() : "---";
                let bAfter = r.balance_after !== null ? r.balance_after.toLocaleString() : "---";

                let reasonHtml = (r.status !== 'SUCCESS' && r.status !== 'QUEUED' && r.status !== 'PROCESSING' && r.reason && r.reason !== 'Waiting')
                    ? '<div class="mt-3 p-2 rounded-3 bg-danger bg-opacity-10 border border-danger border-opacity-25 text-danger text-start" dir="rtl" style="font-size: 12px; font-weight: bold;"><i class="fas fa-exclamation-triangle me-1"></i> سبب الفشل: ' + r.reason + '</div>'
                    : "";

                rows += '<div class="mb-3 p-3 rounded-4" style="background: var(--card-bg); border: 1px solid var(--card-border); border-right: 4px solid ' + borderColor + ';">' +
                            '<div class="d-flex justify-content-between align-items-center mb-2">' +
                                '<div style="font-size: 11px; color: var(--text-muted); font-weight: bold;"><i class="far fa-clock text-warning me-1"></i> ' + dateDisplay + '</div>' +
                                '<div>' + statusInfo + '</div>' +
                            '</div>' +
                            '<div class="d-flex justify-content-between align-items-center mb-2">' +
                                '<div class="fw-bold fs-5" dir="ltr" style="letter-spacing: 1px; color: var(--text-main);">' + phoneNum + '</div>' +
                                '<div class="fw-black text-warning fs-4" dir="ltr">' + r.amount + '</div>' +
                            '</div>' +
                            reasonHtml +
                            '<div class="mt-2 pt-2 d-flex justify-content-between align-items-center" style="border-top: 1px dashed var(--card-border); font-size: 11px; color: var(--text-muted);">' +
                                '<span><i class="fas fa-wallet me-1"></i>كان رصيدك: <strong style="color: var(--text-main);">' + bBefore + '</strong></span>' +
                                '<span><i class="fas fa-arrow-left text-warning mx-2"></i>صار: <strong class="text-warning">' + bAfter + '</strong></span>' +
                            '</div>' +
                        '</div>';
            }});

            document.getElementById('reqTableBody').innerHTML = rows || "<div class='text-center py-5 fw-bold' style='color: var(--text-muted);'>لا يوجد حركات مسجلة</div>";

            let totalPages = Math.ceil(allTransactions.length / itemsPerPage) || 1;
            let prevDisabled = currentPage <= 1 ? 'disabled' : '';
            let nextDisabled = currentPage >= totalPages ? 'disabled' : '';

            document.getElementById('paginationControls').innerHTML =
                '<div class="d-flex justify-content-between align-items-center mt-3 px-2">' +
                    '<button onclick="changePage(-1)" class="btn btn-sm btn-outline-warning rounded-circle d-flex align-items-center justify-content-center" style="width:40px; height:40px;" ' + prevDisabled + '><i class="fas fa-chevron-right"></i></button>' +
                    '<span class="small fw-bold px-3 py-2 rounded-pill border border-warning shadow-sm" style="background: var(--nav-bg); color: var(--gold);">' + currentPage + ' / ' + totalPages + '</span>' +
                    '<button onclick="changePage(1)" class="btn btn-sm btn-outline-warning rounded-circle d-flex align-items-center justify-content-center" style="width:40px; height:40px;" ' + nextDisabled + '><i class="fas fa-chevron-left"></i></button>' +
                '</div>';
        }}

        function updateData() {{
            fetch('/api/customer_sync?search=' + searchVal).then(res => res.json()).then(data => {{
                if(data.status === 'success') {{
                    if(data.balance !== currentBal) {{ currentBal = data.balance; document.getElementById('val_units').innerText = data.balance.toLocaleString(); }}
                    if(data.bills_balance !== currentBills) {{ currentBills = data.bills_balance; document.getElementById('val_bills').innerText = data.bills_balance.toLocaleString(); }}
                    document.getElementById('val_debt').innerText = data.debt_balance.toLocaleString();

                    let favHtml = "";
                    if (data.favorites && data.favorites.length > 0) {{
                        data.favorites.forEach(f => {{
                            let isSyr = ['093', '098', '099'].includes(f.phone.substring(0, 3)) || f.phone.length === 8;
                            let colorClass = isSyr ? 'border-danger text-danger' : 'border-warning text-warning';
                            favHtml += '<div onclick="fillTargetPhone(\\'' + f.phone + '\\')" class="text-center flex-shrink-0" style="width: 80px; cursor:pointer;">' +
                                '<div class="bg-dark rounded-circle shadow-sm d-flex align-items-center justify-content-center mx-auto mb-2 ' + colorClass + '" style="width:60px; height:60px; border: 2px solid; box-shadow: 0 4px 15px rgba(0,0,0,0.1);">' +
                                    '<i class="fas fa-user fs-4"></i>' +
                                '</div>' +
                                '<small class="fw-bold d-block text-truncate text-white" style="font-size:11px;">' + f.name + '</small>' +
                            '</div>';
                        }});
                    }} else {{ favHtml = "<span class='text-muted small w-100 text-center mt-2'>لا يوجد أرقام مفضلة.</span>"; }}
                    document.getElementById('favStories').innerHTML = favHtml;

                    allTransactions = data.recent_reqs;

                    let uniquePhones = [...new Set(allTransactions.map(t => t.phone))];
                    let optionsHtml = "";
                    uniquePhones.forEach(p => {{ optionsHtml += '<option value="' + p + '">'; }});
                    let dl = document.getElementById('historyPhones');
                    if (dl.innerHTML !== optionsHtml) {{ dl.innerHTML = optionsHtml; }}

                    allTransactions.forEach(r => {{
                        if(currentReqs[r.id] && currentReqs[r.id] !== r.status) {{
                            if(r.status === 'SUCCESS') {{
                                showToast('تم تحويل ' + r.amount + ' للرقم ' + r.phone + ' بنجاح ✅', 'bg-success');
                            }} else if(r.status !== 'QUEUED' && r.status !== 'PROCESSING') {{
                                showToast('فشل التحويل للرقم ' + r.phone + ' ❌', 'bg-danger');
                            }}
                        }}
                        currentReqs[r.id] = r.status;
                    }});

                    renderTable();
                }}
            }});
        }}

        function openDebtDetails() {{
            let container = document.getElementById('debt_list_container');
            container.innerHTML = '<div class="text-center py-4"><i class="fas fa-spinner fa-spin fs-2 text-warning"></i></div>';
            new bootstrap.Modal(document.getElementById('debtDetailsModal')).show();

            fetch('/api/debt_history').then(res => res.json()).then(data => {{
                if(data.status === 'success') {{
                    let html = "";
                    data.history.forEach(l => {{
                        let typeText = (l.wallet_type === 'debt_payment') ? "دفعة مسددة (نزل من حسابك) ✓" : "دين جديد مسجل عليك ✖";
                        let typeColor = (l.wallet_type === 'debt_payment') ? "text-success" : "text-danger";
                        let icon = (l.wallet_type === 'debt_payment') ? "fa-check-circle" : "fa-minus-circle";

                        let dBefore = parseFloat(l.debt_before).toLocaleString();
                        let dAfter = parseFloat(l.debt_after).toLocaleString();

                        html += '<div class="p-3 rounded-4 mb-3" style="background: var(--card-bg); border: 1px solid var(--card-border);">' +
                                    '<div class="d-flex justify-content-between align-items-center mb-2">' +
                                        '<div>' +
                                            '<span class="fw-bold ' + typeColor + '"><i class="fas ' + icon + ' me-1"></i> ' + typeText + '</span>' +
                                            '<div class="text-muted fw-bold mt-1" style="font-size:11px;"><i class="far fa-clock text-warning me-1"></i> ' + l.date + '</div>' +
                                        '</div>' +
                                        '<div class="fw-black fs-4 text-white" dir="ltr">' + parseFloat(l.actual_paid).toLocaleString() + ' <small class="opacity-50 fs-6">ل.س</small></div>' +
                                    '</div>' +
                                    '<div class="mt-2 pt-2 d-flex justify-content-between align-items-center" style="border-top: 1px dashed var(--card-border); font-size: 12px; color: var(--text-muted);">' +
                                        '<span><i class="fas fa-book me-1"></i>كان دينك: <strong style="color: var(--text-main);">' + dBefore + '</strong></span>' +
                                        '<span><i class="fas fa-arrow-left text-warning mx-2"></i>صار: <strong class="text-danger">' + dAfter + '</strong></span>' +
                                    '</div>' +
                                '</div>';
                    }});
                    container.innerHTML = html || '<p class="text-center text-muted py-4 fw-bold">لا يوجد سجل ديون لعرضه.</p>';
                }}
            }});
        }}

        function openAddFavModal() {{ new bootstrap.Modal(document.getElementById('addFavModal')).show(); }}
        function saveNewFavorite() {{
            let name = document.getElementById('fav_name_input').value;
            let phone = document.getElementById('fav_phone_input').value;
            if(!name || !phone) return;
            fetch('/api/add_favorite', {{ method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{name: name, phone: phone}}) }})
            .then(res => res.json()).then(data => {{
                if(data.status === 'success') {{
                    showToast(data.msg, 'bg-success');
                    document.getElementById('fav_name_input').value = ""; document.getElementById('fav_phone_input').value = "";
                    bootstrap.Modal.getInstance(document.getElementById('addFavModal')).hide(); updateData();
                }} else {{ alert(data.msg); }}
            }});
        }}

        function fillTargetPhone(phoneNum) {{
            document.getElementsByName('target_phone')[0].value = phoneNum;
            document.querySelectorAll('.nav-item')[1].click();
        }}

        // الحل النووي: برمجة الصوت محلياً (بدون روابط خارجية)
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();

        // فك القفل فوراً عند أول لمسة للشاشة
        document.body.addEventListener('touchstart', () => {{ if (audioCtx.state === 'suspended') audioCtx.resume(); }}, {{ once: true }});
        document.body.addEventListener('click', () => {{ if (audioCtx.state === 'suspended') audioCtx.resume(); }}, {{ once: true }});

        // دالة توليد رنة (Bling) احترافية
        function playSuccessSound() {{
            try {{
                if (audioCtx.state === 'suspended') audioCtx.resume();
                const oscillator = audioCtx.createOscillator();
                const gainNode = audioCtx.createGain();

                oscillator.type = 'sine';
                oscillator.frequency.setValueAtTime(880, audioCtx.currentTime); // تردد النغمة
                oscillator.frequency.exponentialRampToValueAtTime(1200, audioCtx.currentTime + 0.1);

                gainNode.gain.setValueAtTime(0.5, audioCtx.currentTime);
                gainNode.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.2);

                oscillator.connect(gainNode);
                gainNode.connect(audioCtx.destination);

                oscillator.start();
                oscillator.stop(audioCtx.currentTime + 0.2);
            }} catch(e) {{}}
        }}

        function showToast(msg, bgClass) {{
            let toastEl = document.getElementById('liveToast');
            toastEl.className = 'toast align-items-center text-white border-0 shadow-lg rounded-pill mt-3 ' + bgClass;
            let icon = bgClass === 'bg-success' ? 'fa-check-circle' : 'fa-exclamation-circle';
            document.getElementById('toastMessage').innerHTML = '<i class="fas ' + icon + ' me-2 fs-5"></i> ' + msg;
            new bootstrap.Toast(toastEl).show();

            // تشغيل الرنة إذا كانت العملية ناجحة
            if(bgClass === 'bg-success') {{
                playSuccessSound();
            }}
        }}

        setInterval(updateData, 3000); updateData();

        setTimeout(() => {{
            document.querySelectorAll('.alert.fade-in').forEach(a => a.style.display = 'none');
        }}, 6000);
    </script>
    """
    return render_template_string(CUSTOMER_HTML_BASE.replace('{content}', content))

# =============================================================
# ردود الشبكة والإشعارات الجماعية
# =============================================================
@app.route('/admin_ussd_settings', methods=['GET', 'POST'])
def admin_ussd_settings():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')

    conn = get_db_connection()
    # 🛡️ تأمين الأعمدة الجديدة للأرباح والعمولات
    try: conn.execute("ALTER TABLE ussd_codes ADD COLUMN secret_pin TEXT DEFAULT ''")
    except: pass
    try: conn.execute("ALTER TABLE ussd_codes ADD COLUMN success_keyword TEXT DEFAULT 'بنجاح'")
    except: pass
    try: conn.execute("ALTER TABLE ussd_codes ADD COLUMN failure_keyword TEXT DEFAULT 'فشل'")
    except: pass
    try: conn.execute("ALTER TABLE ussd_codes ADD COLUMN app_timeout INTEGER DEFAULT 20")
    except: pass
    try: conn.execute("ALTER TABLE ussd_codes ADD COLUMN request_interval INTEGER DEFAULT 5")
    except: pass
    try: conn.execute("ALTER TABLE ussd_codes ADD COLUMN custom_percent_fee REAL DEFAULT 0")
    except: pass
    try: conn.execute("ALTER TABLE ussd_codes ADD COLUMN custom_fixed_fee REAL DEFAULT 0")
    except: pass

    required_codes = [
        'transfer_syriatel', 'transfer_mtn',
        'check_bal_syriatel', 'check_bal_mtn',
        'cash_syriatel', 'cash_mtn',
        'bill_syriatel', 'bill_mtn'
    ]
    for rc in required_codes:
        conn.execute("INSERT OR IGNORE INTO ussd_codes (service_name, ussd_format) VALUES (?, '')", (rc,))
    conn.commit()

    if request.method == 'POST':
        for key in request.form:
            if key.startswith('code_'):
                srv_name = key.replace('code_', '')
                ussd_val = request.form[key].strip()
                pin_val = request.form.get(f'pin_{srv_name}', '').strip()
                success_val = request.form.get(f'success_{srv_name}', '').strip()
                failure_val = request.form.get(f'failure_{srv_name}', '').strip()

                try: timeout_val = int(request.form.get(f'timeout_{srv_name}', 20) or 20)
                except: timeout_val = 20
                try: interval_val = int(request.form.get(f'interval_{srv_name}', 5) or 5)
                except: interval_val = 5

                # 💡 التقاط العمولات والأرباح
                try: perc_val = float(request.form.get(f'perc_{srv_name}', 0) or 0)
                except: perc_val = 0
                try: fixed_val = float(request.form.get(f'fixed_{srv_name}', 0) or 0)
                except: fixed_val = 0

                conn.execute("""UPDATE ussd_codes SET
                                ussd_format=?, secret_pin=?, success_keyword=?,
                                failure_keyword=?, app_timeout=?, request_interval=?,
                                custom_percent_fee=?, custom_fixed_fee=?
                                WHERE service_name=?""",
                             (ussd_val, pin_val, success_val, failure_val, timeout_val, interval_val, perc_val, fixed_val, srv_name))
        conn.commit()

    codes = conn.execute("SELECT * FROM ussd_codes").fetchall()
    responses_data = conn.execute("SELECT id, network, phone, amount, ussd_response, status FROM transactions WHERE ussd_response IS NOT NULL AND ussd_response != '' AND ussd_response != 'Waiting' ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()

    names_ar = {
        'transfer_syriatel': '🔴 تحويل وحدات سيريتل', 'transfer_mtn': '🟡 تحويل وحدات MTN',
        'check_bal_syriatel': '🔴 استعلام رصيد سيريتل', 'check_bal_mtn': '🟡 استعلام رصيد MTN',
        'cash_syriatel': '🔴 تحويل كاش سيريتل', 'cash_mtn': '🟡 تحويل كاش MTN',
        'bill_syriatel': '🔴 دفع فواتير سيريتل', 'bill_mtn': '🟡 دفع فواتير MTN'
    }

    rows = ""
    for c in codes:
        c_dict = dict(c)
        display_name = names_ar.get(c_dict['service_name'], c_dict['service_name'])

        ussd_format_val = c_dict.get('ussd_format') if c_dict.get('ussd_format') is not None else ""
        secret_pin_val = c_dict.get('secret_pin') if c_dict.get('secret_pin') is not None else ""
        success_keyword_val = c_dict.get('success_keyword') if c_dict.get('success_keyword') is not None else "بنجاح"
        failure_keyword_val = c_dict.get('failure_keyword') if c_dict.get('failure_keyword') is not None else "فشل"
        app_timeout_val = c_dict.get('app_timeout') if c_dict.get('app_timeout') is not None else 20
        request_interval_val = c_dict.get('request_interval') if c_dict.get('request_interval') is not None else 5

        perc_v = c_dict.get('custom_percent_fee') or 0
        fixed_v = c_dict.get('custom_fixed_fee') or 0

        rows += f"""
        <tr class="align-middle">
            <td class="fw-bold text-dark text-start" style="min-width:180px;">{display_name}</td>
            <td><input name="code_{c_dict['service_name']}" value="{ussd_format_val}" class="form-control form-control-sm text-start" dir="ltr" placeholder="المعادلة"></td>
            <td><input name="pin_{c_dict['service_name']}" value="{secret_pin_val}" type="text" class="form-control form-control-sm text-center fw-bold text-danger" dir="ltr" placeholder="PIN"></td>
            <td style="width:70px;"><input name="perc_{c_dict['service_name']}" value="{perc_v:g}" type="number" step="0.01" class="form-control form-control-sm text-center fw-bold border-warning bg-warning bg-opacity-10 text-dark"></td>
            <td style="width:80px;"><input name="fixed_{c_dict['service_name']}" value="{fixed_v:g}" type="number" step="0.01" class="form-control form-control-sm text-center fw-bold border-warning bg-warning bg-opacity-10 text-dark"></td>
            <td><input name="success_{c_dict['service_name']}" value="{success_keyword_val}" class="form-control form-control-sm text-center text-success fw-bold" placeholder="نجاح"></td>
            <td><input name="failure_{c_dict['service_name']}" value="{failure_keyword_val}" class="form-control form-control-sm text-center text-danger fw-bold" placeholder="فشل"></td>
            <td><input name="timeout_{c_dict['service_name']}" value="{app_timeout_val}" type="number" class="form-control form-control-sm text-center fw-bold" style="width:60px; margin:0 auto;"></td>
            <td><input name="interval_{c_dict['service_name']}" value="{request_interval_val}" type="number" class="form-control form-control-sm text-center fw-bold" style="width:60px; margin:0 auto;"></td>
        </tr>
        """

    resp_rows = ""
    for r in responses_data:
        r_dict = dict(r)
        badge_color = "success" if r_dict['status'] == 'SUCCESS' else "warning" if r_dict['status'] == 'MANUAL_CHECK' else "danger"
        resp_rows += f"<tr><td class='fw-bold text-secondary'>#{r_dict['id']}</td><td><span class='badge bg-{badge_color} shadow-sm'>{r_dict['network']}</span></td><td dir='ltr' class='fw-bold'>{r_dict['phone']}</td><td>{r_dict['amount']}</td><td class='text-start' dir='rtl'><small><code>{r_dict['ussd_response']}</code></small></td></tr>"

    if not resp_rows: resp_rows = "<tr><td colspan='5' class='text-muted py-4 fw-bold'>لا يوجد ردود مسجلة بعد</td></tr>"

    content = f"""
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h4 class='fw-bold text-primary m-0'><i class="fas fa-cogs me-2"></i> إدارة الأكواد ونظام الشبكة المتقدم</h4>
        <div class="bg-warning bg-opacity-10 text-dark px-3 py-2 rounded-pill fw-bold border border-warning shadow-sm"><i class="fas fa-info-circle me-1"></i> يتم تطبيق العمولات تلقائياً على كل فاتورة جديدة</div>
    </div>

    <div class='alert alert-info fw-bold small shadow-sm mb-4'>
        <i class="fas fa-comment-dots me-1 text-success"></i> <b>طريقة كتابة المعادلات:</b> استخدم المتغيرات التالية بالتنسيق تماماً:
        <span dir="ltr" class="text-danger">{{{{phone}}}}</span> للرقم ،
        <span dir="ltr" class="text-danger">{{{{amount}}}}</span> للمبلغ ،
        <span dir="ltr" class="text-danger">{{{{pin}}}}</span> لكلمة السر الرمزية.<br>
        <i class="fas fa-info-circle me-1 mt-2"></i> <b>الكلمات المفتاحية:</b> يمكنك وضع أكثر من كلمة للفصل بينها بفاصلة أجنبية (مثال: بنجاح,تم,رصيدك الجديد).
    </div>

    <form method='POST'>
        <div class='card-bank shadow-sm mb-5 p-0' style="overflow: hidden;">
            <div class="table-responsive">
                <table class='table table-bordered table-striped align-middle text-center m-0' style="font-size:12px;">
                    <thead class="table-dark">
                        <tr>
                            <th>الخدمة والشبكة</th>
                            <th>المعادلة الهيكلية</th>
                            <th>الرمز (PIN)</th>
                            <th class="text-warning">ربح %</th>
                            <th class="text-warning">رسم ثابت</th>
                            <th>💬 كلمات النجاح</th>
                            <th>💬 كلمات الفشل</th>
                            <th>⏱️ مهلة (ثا)</th>
                            <th>⏳ فاصل</th>
                        </tr>
                    </thead>
                    <tbody>{rows}</tbody>
                </table>
            </div>
            <div class="p-3 bg-light border-top text-end">
                <button type='submit' class='btn btn-primary btn-lg fw-bold px-5 shadow-sm'><i class="fas fa-save me-2"></i> حفظ الإعدادات والمعادلات بالكامل</button>
            </div>
        </div>
    </form>

    <h4 class='fw-bold text-dark mb-4 mt-5'><i class="fas fa-satellite-dish me-2 text-primary"></i> رادار استجابة الرسايل والشبكة الحية</h4>
    <div class='card-bank shadow-sm'>
        <div class="table-responsive" style="max-height: 400px; overflow-y: auto;">
            <table class='table table-hover table-striped align-middle text-center m-0'>
                <thead class="table-dark" style="position: sticky; top: 0; z-index: 1;">
                    <tr><th>رقم الطلب</th><th>الشبكة</th><th>الهدف</th><th>الكمية</th><th>رد الشركة الفعلي المستلم (USSD / SMS)</th></tr>
                </thead>
                <tbody>
                    {resp_rows}
                </tbody>
            </table>
        </div>
    </div>
    """
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='ussd_logs')


@app.route('/delete_sms/<int:sms_id>')
def delete_sms(sms_id):
    if session.get('logged_in') and session.get('role') == 'admin':
        conn = get_db_connection()
        conn.execute("DELETE FROM sms_logs WHERE id=?", (sms_id,))
        conn.commit()
        conn.close()
    return redirect(request.referrer or '/sms_inbox')

@app.route('/sms_inbox')
def sms_inbox():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')

    tab = request.args.get('tab', 'all')
    search = request.args.get('search', '').strip()
    try: page = int(request.args.get('page', 1))
    except ValueError: page = 1
    per_page = 10
    offset = (page - 1) * per_page

    query_base = "FROM sms_logs WHERE 1=1"
    params = []

    # 🔍 فلترة التبويبات (فصل الشركات)
    if tab == 'syriatel':
        query_base += " AND (sender LIKE '%Abili%' OR sender LIKE '%Syriatel%' COLLATE NOCASE)"
    elif tab == 'mtn':
        query_base += " AND sender LIKE '%MTN%' COLLATE NOCASE"
    elif tab == 'other':
        query_base += " AND sender NOT LIKE '%Abili%' AND sender NOT LIKE '%Syriatel%' AND sender NOT LIKE '%MTN%' COLLATE NOCASE"

    # 🔍 فلترة البحث اليدوي
    if search:
        query_base += " AND (message LIKE ? OR sender LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])

    conn = get_db_connection()
    total_count = conn.execute(f"SELECT count(*) {query_base}", params).fetchone()[0]
    total_pages = max(1, (total_count + per_page - 1) // per_page)

    # جلب 10 رسائل فقط للصفحة الحالية
    logs = conn.execute(f"SELECT * {query_base} ORDER BY id DESC LIMIT ? OFFSET ?", params + [per_page, offset]).fetchall()
    conn.close()

    rows = ""
    for l in logs:
        del_btn = f"<a href='/delete_sms/{l['id']}' class='btn btn-sm btn-outline-danger shadow-sm' onclick='return confirm(\"هل أنت متأكد من حذف هذه الرسالة نهائياً؟\");'><i class='fas fa-trash'></i> حذف</a>"
        rows += f"<tr><td dir='ltr' class='text-muted small'>{l['date']}</td><td class='fw-bold text-primary'>{l['sender']}</td><td class='text-start fw-bold'>{l['message']}</td><td>{del_btn}</td></tr>"

    if not rows: rows = "<tr><td colspan='4' class='text-muted py-4 fw-bold'>لا يوجد رسائل في هذا القسم حالياً.</td></tr>"

    # 📄 أزرار الصفحات
    pagination_html = f"<div class='d-flex justify-content-center gap-2 mt-4'>"
    if page > 1: pagination_html += f"<a href='?tab={tab}&search={search}&page={page-1}' class='btn btn-outline-primary fw-bold'>السابق</a>"
    pagination_html += f"<span class='badge bg-primary py-2 px-3 fs-6'>صفحة {page} من {total_pages}</span>"
    if page < total_pages: pagination_html += f"<a href='?tab={tab}&search={search}&page={page+1}' class='btn btn-outline-primary fw-bold'>التالي</a>"
    pagination_html += "</div>"

    # 🖥️ تصميم واجهة الرادار الجديدة
    content = f'''
    <h4 class="fw-bold mb-4 text-primary"><i class="fas fa-envelope-open-text me-2"></i> صندوق رسائل الموبايل (الرادار)</h4>

    <div class="card-bank border-primary border-start border-4 bg-white mb-4 shadow-sm">
        <form method="GET" class="row g-2 align-items-center">
            <input type="hidden" name="tab" value="{tab}">
            <div class="col-md-9">
                <input type="text" name="search" value="{search}" class="form-control form-control-lg bg-light border-0" placeholder="ابحث في نص الرسالة أو اسم المرسل...">
            </div>
            <div class="col-md-3">
                <button type="submit" class="btn btn-primary btn-lg w-100 fw-bold"><i class="fas fa-search me-2"></i> بحث ذكي</button>
            </div>
        </form>
    </div>

    <ul class="nav nav-pills nav-fill bg-white p-2 rounded-4 shadow-sm mb-4 border">
        <li class="nav-item"><a class="nav-link fw-bold fs-5 {'active shadow' if tab == 'all' else 'text-muted'}" href="?tab=all&search={search}">الكل 📥</a></li>
        <li class="nav-item"><a class="nav-link fw-bold fs-5 {'active shadow bg-danger text-white' if tab == 'syriatel' else 'text-muted'}" href="?tab=syriatel&search={search}">🔴 سيريتل / Abili</a></li>
        <li class="nav-item"><a class="nav-link fw-bold fs-5 {'active shadow bg-warning text-dark' if tab == 'mtn' else 'text-muted'}" href="?tab=mtn&search={search}">🟡 إم تي إن (MTN)</a></li>
        <li class="nav-item"><a class="nav-link fw-bold fs-5 {'active shadow bg-secondary text-white' if tab == 'other' else 'text-muted'}" href="?tab=other&search={search}">رسائل أخرى 💬</a></li>
    </ul>

    <div class="card-bank">
        <div class="table-responsive">
            <table class="table align-middle text-center">
                <thead class="table-light"><tr><th>التاريخ والوقت</th><th>المرسل</th><th class='text-start w-50'>نص الرسالة</th><th>إدارة</th></tr></thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
        {pagination_html}
    </div>
    '''
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='ussd_logs')

@app.route('/broadcast', methods=['GET', 'POST'])
def broadcast_page():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    msg_sent, count = False, 0
    if request.method == 'POST':
        text_message = request.form.get('message', '').strip()
        if text_message:
            conn = get_db_connection()
            users = conn.execute("SELECT user_id FROM users WHERE role='user' AND is_approved=1 AND is_banned=0").fetchall()
            conn.close()
            for u in users:
                try:
                    bot.send_message(u['user_id'], text_message)
                    count += 1
                except:
                    pass
            msg_sent = True
    alert = f"<div class='alert alert-success fw-bold'><i class='fas fa-check-circle me-2'></i> تم الإرسال إلى {count} عميل!</div>" if msg_sent else ""
    content = f"""<h4 class="fw-bold mb-4 text-primary"><i class="fas fa-bullhorn me-2"></i> الإشعارات الجماعية</h4>{alert}<div class="card-bank border-warning border-start border-5"><form method="POST"><label class="form-label fw-bold text-muted mb-2">نص الإشعار:</label><textarea name="message" class="form-control bg-light mb-4 shadow-sm border-0 p-3" rows="6" required></textarea><button type="submit" class="btn btn-warning btn-lg px-5 fw-bold shadow-sm text-dark"><i class="fas fa-paper-plane me-2"></i> إرسال للكل</button></form></div>"""
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='broadcast')

# =============================================================
# صفحة إنشاء زبون ويب (بدون تلغرام)
# =============================================================
@app.route('/create_web_customer', methods=['GET', 'POST'])
def create_web_customer():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    msg = ""
    if request.method == 'POST':
        real_name = request.form.get('real_name')
        username = request.form.get('username')
        password = request.form.get('password')
        phone = request.form.get('phone')
        if real_name and username and password:
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT MIN(user_id) FROM users")
                min_id = cursor.fetchone()[0]
                # إعطاء آيدي سالب للزباين الوهميين عشان ما يتعارضوا مع آيديات التلغرام
                new_id = (min_id - 1) if min_id and min_id < 0 else -1

                conn.execute("INSERT INTO users (user_id, real_name, username, password, role, is_approved, access_method, phone_contact, joined_date) VALUES (?, ?, ?, ?, 'user', 1, 'web', ?, ?)",
                             (new_id, real_name, username.replace(" ", "").lower(), password, phone, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit()
                msg = "<div class='alert alert-success fw-bold'>✅ تم إنشاء حساب زبون الويب بنجاح!</div>"
            except sqlite3.IntegrityError:
                msg = "<div class='alert alert-danger fw-bold'>❌ اسم المستخدم موجود مسبقاً! يرجى اختيار اسم آخر.</div>"
            except Exception as e:
                msg = f"<div class='alert alert-danger fw-bold'>❌ خطأ: {e}</div>"
            conn.close()

    content = f"""
    <div class="card-bank border-warning border-start border-5 shadow-sm" style="max-width: 600px; margin: 0 auto;">
        <h4 class="fw-bold mb-4 text-warning" style="color: #d97706!important;"><i class="fas fa-laptop-house me-2"></i> إضافة زبون ويب (بدون تلغرام)</h4>
        {msg}
        <form method="POST">
            <div class="mb-3">
                <label class="form-label fw-bold text-muted">الاسم الثلاثي / اسم المحل</label>
                <input type="text" name="real_name" class="form-control bg-light border-0 shadow-sm p-3" required>
            </div>
            <div class="mb-3">
                <label class="form-label fw-bold text-muted">اسم المستخدم (للدخول)</label>
                <input type="text" name="username" class="form-control bg-light border-0 shadow-sm p-3 text-start" dir="ltr" placeholder="مثال: ahmad123" required>
            </div>
            <div class="mb-3">
                <label class="form-label fw-bold text-muted">كلمة المرور</label>
                <input type="text" name="password" class="form-control bg-light border-0 shadow-sm p-3 text-start" dir="ltr" required>
            </div>
            <div class="mb-4">
                <label class="form-label fw-bold text-muted">رقم الهاتف للتواصل (اختياري)</label>
                <input type="text" name="phone" class="form-control bg-light border-0 shadow-sm p-3">
            </div>
            <div class="d-flex gap-2">
                <a href="/users" class="btn btn-light fw-bold shadow-sm w-50 py-3">إلغاء وعودة</a>
                <button type="submit" class="btn btn-warning text-dark fw-black shadow-sm w-50 py-3"><i class="fas fa-save me-1"></i> إنشاء الحساب</button>
            </div>
        </form>
    </div>
    """
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='users')


# ==========================================================
# 👑 تطبيق الإدارة الملكي - Refaie PREMIUM 👑
# ==========================================================

ADMIN_MOBILE_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>إدارة الرفاعي| Refaie</title>
    <meta name="theme-color" content="#0f172a">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;700;900&display=swap" rel="stylesheet">

    <style>
        :root { --bg: #0f172a; --gold: #fbbf24; --card: rgba(30, 41, 59, 0.6); --text: #f8fafc; }
        body { background-color: var(--bg); color: var(--text); font-family: 'Cairo', sans-serif; overflow-x: hidden; padding-bottom: 90px; }

        .glass { background: var(--card); backdrop-filter: blur(15px); border-radius: 20px; border: 1px solid rgba(255,255,255,0.05); }
        .gold-gradient { background: linear-gradient(135deg, #fbbf24 0%, #d97706 100%); color: #000 !important; }

        /* شريط التنقل السفلي الاحترافي */
        .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; background: rgba(15, 23, 42, 0.9); backdrop-filter: blur(20px); border-top: 1px solid rgba(251, 191, 36, 0.2); display: flex; justify-content: space-around; padding: 12px 5px; z-index: 2000; }
        .nav-item { text-align: center; color: #64748b; font-size: 10px; font-weight: 900; cursor: pointer; flex: 1; transition: 0.3s; }
        .nav-item i { font-size: 22px; display: block; margin-bottom: 4px; }
        .nav-item.active { color: var(--gold); transform: translateY(-3px); }

        /* أقسام التطبيق */
        .app-section { display: none; animation: slideUp 0.4s ease-out; }
        .app-section.active { display: block; }
        @keyframes slideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }

        .stat-card { padding: 15px; text-align: center; border-radius: 18px; }
        .badge-notify { position: absolute; top: -5px; right: 25%; font-size: 10px; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0% { transform: scale(1); } 50% { transform: scale(1.2); } 100% { transform: scale(1); } }
    </style>
</head>
<body>
    <div class="container py-4">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <div>
                <h5 class="fw-black m-0 text-warning">الرفاعي<span class="small fw-normal text-white-50">| الإدارة</span></h5>
                <p class="small m-0 opacity-50">مركز  للإتصالات</p>
            </div>
            <div class="dropdown">
                <div class="bg-white rounded-circle shadow-sm d-flex align-items-center justify-content-center" style="width:40px; height:40px;" data-bs-toggle="dropdown">
                    <i class="fas fa-user-shield text-dark"></i>
                </div>
                <ul class="dropdown-menu dropdown-menu-end shadow border-0">
                    <li><a class="dropdown-item fw-bold" href="/admin_center"><i class="fas fa-desktop me-2"></i> نسخة الكمبيوتر</a></li>
                    <li><hr class="dropdown-divider"></li>
                    <li><a class="dropdown-item fw-bold text-danger" href="/logout"><i class="fas fa-power-off me-2"></i> تسجيل خروج</a></li>
                </ul>
            </div>
        </div>

        <div id="tab_home" class="app-section active">
            <div class="row g-2 mb-4">
                <div class="col-6"><div class="glass stat-card border-start border-warning border-4"><i class="fas fa-coins text-warning mb-1"></i><div class="small opacity-50">رصيد الزبائن</div><h5 class="fw-black m-0" id="s_bal">0</h5></div></div>
                <div class="col-6"><div class="glass stat-card border-start border-danger border-4"><i class="fas fa-hand-holding-usd text-danger mb-1"></i><div class="small opacity-50">الديون الكلية</div><h5 class="fw-black m-0 text-danger" id="s_debt">0</h5></div></div>
            </div>
            <h6 class="fw-bold mb-3"><i class="fas fa-history text-warning me-2"></i> آخر الحركات</h6>
            <div class="glass p-3" id="recent_box">جاري التحميل...</div>
        </div>

        <div id="tab_orders" class="app-section">
            <h5 class="fw-black mb-3">طلبات الفواتير المعلقة <span class="badge bg-danger rounded-pill ms-2" id="o_count">0</span></h5>
            <div id="orders_box"></div>
        </div>

        <div id="tab_users" class="app-section">
            <div class="glass p-3 mb-3">
                <input type="text" id="userSearch" class="form-control bg-dark text-white border-0 rounded-pill px-3" placeholder="بحث عن زبون بالاسم أو الرقم...">
            </div>
            <div id="users_box"></div>
        </div>

        <div id="tab_settings" class="app-section">
            <div class="glass p-4">
                <h5 class="fw-black mb-4 border-bottom pb-2 border-secondary">إعدادات النظام</h5>
                <div class="mb-4">
                    <label class="small fw-bold text-warning d-block mb-2">وضع الصيانة (إيقاف البوت)</label>
                    <button id="btn_maint" onclick="toggleSetting('maintenance')" class="btn w-100 fw-black py-2 rounded-pill shadow-sm">جاري الفحص...</button>
                </div>
                <div class="mb-4">
                    <label class="small fw-bold text-success d-block mb-2">زر تحميل التطبيق للزبائن</label>
                    <button id="btn_app" onclick="toggleSetting('status_app_button')" class="btn w-100 fw-black py-2 rounded-pill shadow-sm">جاري الفحص...</button>
                </div>
                <div class="bg-dark bg-opacity-50 p-3 rounded-4 mb-4">
                    <p class="small text-muted mb-2">تكلفة الوحدة الحالية (رأس المال):</p>
                    <div class="input-group">
                        <input type="number" step="0.001" id="unit_cost_val" class="form-control bg-transparent text-white border-warning">
                        <button onclick="saveUnitCost()" class="btn btn-warning fw-bold px-3">حفظ</button>
                    </div>
                </div>
                <button onclick="location.href='/backup_db'" class="btn btn-outline-info w-100 fw-bold py-3"><i class="fas fa-cloud-upload-alt me-2"></i> أخذ نسخة احتياطية للتلغرام</button>
            </div>
        </div>
    </div>

    <div class="bottom-nav">
        <div class="nav-item active" onclick="showTab('tab_home', this)"><i class="fas fa-chart-line"></i>الرادار</div>
        <div class="nav-item position-relative" onclick="showTab('tab_orders', this)">
            <span class="badge rounded-pill bg-danger badge-notify" id="n_badge" style="display:none;">0</span>
            <i class="fas fa-shopping-cart"></i>الطلبات
        </div>
        <div class="nav-item" onclick="showTab('tab_users', this)"><i class="fas fa-users-cog"></i>الزبائن</div>
        <div class="nav-item" onclick="showTab('tab_settings', this)"><i class="fas fa-user-cog"></i>الإعدادات</div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        const alertSnd = new Audio('https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3');
        let lastCount = 0;

        function showTab(t, e) {
            document.querySelectorAll('.app-section').forEach(s => s.classList.remove('active'));
            document.getElementById(t).classList.add('active');
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            e.classList.add('active');
            if(t === 'tab_users') fetchUsers();
            if(t === 'tab_settings') fetchSettings();
        }

        function fetchAll() {
            fetch('/api/admin_sync').then(r => r.json()).then(d => {
                document.getElementById('s_bal').innerText = d.stats.balance.toLocaleString();
                document.getElementById('s_debt').innerText = d.stats.debt.toLocaleString();

                // تحديث الطلبات
                let oH = "";
                d.pending_orders.forEach(o => {
                    oH += `<div class="glass p-3 mb-2 border-end border-warning border-4">
                        <div class="d-flex justify-content-between mb-1"><span class="small text-warning">${o.service_name}</span><span class="small opacity-50">${o.date.slice(11,16)}</span></div>
                        <div class="fw-bold mb-2">${o.real_name} ➔ <span class="text-info">${o.target_info}</span></div>
                        <div class="d-flex gap-2">
                            <button onclick="orderAct(${o.id},'accept')" class="btn btn-success btn-sm w-100 fw-bold">تنفيذ ✅</button>
                            <button onclick="orderAct(${o.id},'reject')" class="btn btn-outline-danger btn-sm w-100 fw-bold">رفض ❌</button>
                        </div>
                    </div>`;
                });
                document.getElementById('orders_box').innerHTML = oH || '<p class="text-center opacity-50 py-5">لا يوجد طلبات حالياً</p>';
                document.getElementById('o_count').innerText = d.pending_orders.length;
                let nb = document.getElementById('n_badge');
                if(d.pending_orders.length > 0) { nb.innerText = d.pending_orders.length; nb.style.display = 'block'; } else { nb.style.display = 'none'; }
                if(d.pending_orders.length > lastCount) alertSnd.play().catch(e=>{});
                lastCount = d.pending_orders.length;

                // آخر الحركات
                let rH = "";
                d.recent_trans.forEach(t => {
                    rH += `<div class="d-flex justify-content-between align-items-center mb-2 border-bottom border-secondary border-opacity-10 pb-1">
                        <div class="small fw-bold">${t.real_name}<br><span class="opacity-50">${t.phone}</span></div>
                        <div class="text-end">
                            <span class="fw-black text-warning d-block">${t.amount}</span>
                            <span class="text-white-50" style="font-size:9px;"><i class="far fa-clock"></i> ${t.date.slice(11,16)}</span>
                        </div>
                    </div>`;
                });
                document.getElementById('recent_box').innerHTML = rH;
            });
        }

        function fetchUsers() {
            let q = document.getElementById('userSearch').value;
            fetch('/api/admin_users?q='+q).then(r=>r.json()).then(d=>{
                let uH = "";
                d.users.forEach(u => {
                    uH += `<div class="glass p-3 mb-2">
                        <div class="d-flex justify-content-between"><span class="fw-black">${u.real_name}</span><span class="text-warning fw-bold">${u.balance} و</span></div>
                        <div class="small opacity-50 mb-2">دين: ${u.debt_balance} ل.س | فواتير: ${u.bills_balance}</div>
                        <div class="d-flex gap-1">
                            <button onclick="location.href='/user/${u.user_id}'" class="btn btn-sm btn-primary flex-grow-1 fw-bold">كشف وتعديل</button>
                        </div>
                    </div>`;
                });
                document.getElementById('users_box').innerHTML = uH;
            });
        }
        document.getElementById('userSearch').oninput = fetchUsers;

        function fetchSettings() {
            fetch('/api/admin_settings').then(r=>r.json()).then(d=>{
                let m = document.getElementById('btn_maint');
                m.innerText = d.maintenance === '1' ? '🔴 وضع الصيانة مفعل' : '🟢 البوت يعمل بشكل طبيعي';
                m.className = d.maintenance === '1' ? 'btn btn-danger w-100 fw-black py-2 rounded-pill shadow-sm' : 'btn btn-success w-100 fw-black py-2 rounded-pill shadow-sm';

                let a = document.getElementById('btn_app');
                a.innerText = d.app_btn === '1' ? '🟢 زر التطبيق ظاهر للزباين' : '🔴 زر التطبيق مخفي';
                a.className = d.app_btn === '1' ? 'btn btn-success w-100 fw-black py-2 rounded-pill shadow-sm' : 'btn btn-danger w-100 fw-black py-2 rounded-pill shadow-sm';
                document.getElementById('unit_cost_val').value = d.unit_cost;
            });
        }

        function toggleSetting(k) {
            fetch('/api/admin_toggle_setting?k='+k).then(r=>r.json()).then(d=>fetchSettings());
        }
        function saveUnitCost() {
            let v = document.getElementById('unit_cost_val').value;
            fetch('/api/admin_save_cost?v='+v).then(r=>r.json()).then(d=>alert('تم حفظ التكلفة بنجاح!'));
        }
        function orderAct(id, a) {
            fetch('/api/admin_action', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({order_id:id, action:a})}).then(r=>r.json()).then(d=>fetchAll());
        }

        setInterval(fetchAll, 4000); fetchAll();
    </script>
</body>
</html>
"""

@app.route('/admin_app')
def admin_mobile_app():
    if not session.get('logged_in') or session.get('role') != 'admin':
        return redirect('/portal')
    return ADMIN_MOBILE_HTML

@app.route('/api/admin_users')
def api_admin_users():
    if not session.get('logged_in') or session.get('role') != 'admin': return jsonify({"status": "error"})
    q = request.args.get('q', '').strip()
    conn = get_db_connection()
    if q:
        users = conn.execute("SELECT user_id, real_name, balance, debt_balance, bills_balance FROM users WHERE role='user' AND (real_name LIKE ? OR user_id LIKE ?) LIMIT 20", (f'%{q}%', f'%{q}%')).fetchall()
    else:
        users = conn.execute("SELECT user_id, real_name, balance, debt_balance, bills_balance FROM users WHERE role='user' ORDER BY balance DESC LIMIT 20").fetchall()
    conn.close()
    return jsonify({"users": [dict(u) for u in users]})

@app.route('/api/admin_settings')
def api_admin_settings():
    if not session.get('logged_in') or session.get('role') != 'admin': return jsonify({"status": "error"})
    return jsonify({
        "maintenance": get_setting('maintenance') or '0',
        "app_btn": get_setting('status_app_button') or '1',
        "unit_cost": get_setting('current_unit_cost') or '1.05'
    })

@app.route('/api/admin_toggle_setting')
def api_admin_toggle():
    if not session.get('logged_in') or session.get('role') != 'admin': return jsonify({"status": "error"})
    k = request.args.get('k')
    cur = get_setting(k) or '0'
    new_v = '1' if cur == '0' else '0'
    set_setting(k, new_v)
    return jsonify({"status": "success"})

@app.route('/api/admin_save_cost')
def api_admin_save_cost():
    v = request.args.get('v')
    set_setting('current_unit_cost', v)
    return jsonify({"status": "success"})

@app.route('/api/admin_sync')
def api_admin_sync():
    if not session.get('logged_in') or session.get('role') != 'admin': return jsonify({"status": "error"})
    
    check_stuck_transactions() # 👈 تفعيل المراقب الزمني
    
    # 🛡️ ميزة النسخ الاحتياطي التلقائي (تعمل مرة واحدة يومياً بشكل مخفي)
    today_date = datetime.now(local_tz).strftime("%Y-%m-%d")
    if get_setting('last_auto_backup') != today_date:
        set_setting('last_auto_backup', today_date)
        import threading, tempfile
        def background_backup():
            try:
                temp_db = os.path.join(tempfile.gettempdir(), f"AutoBackup_{today_date}.db")
                bck_conn = sqlite3.connect(temp_db)
                safe_conn = sqlite3.connect(DB_NAME, timeout=20)
                safe_conn.backup(bck_conn)
                safe_conn.close(); bck_conn.close()
                with open(temp_db, 'rb') as f:
                    bot.send_document(ADMIN_TG_ID, f, caption=f"🔄 *نسخة احتياطية تلقائية (يومية)*\nالتاريخ: {datetime.now(local_tz).strftime('%Y-%m-%d %H:%M')}\n\n✅ تم حفظ بيانات اليوم بنجاح.", parse_mode="Markdown")
                os.remove(temp_db)
            except: pass
        threading.Thread(target=background_backup).start()

    conn = get_db_connection()
    users_count = conn.execute("SELECT count(*) FROM users WHERE role='user' AND is_approved=1").fetchone()[0]
    tot_bal = conn.execute("SELECT sum(balance) FROM users WHERE role='user'").fetchone()[0] or 0
    tot_debt = conn.execute("SELECT sum(debt_balance) FROM users WHERE role='user'").fetchone()[0] or 0
    orders = conn.execute("SELECT o.*, u.real_name FROM manual_orders o JOIN users u ON o.user_id = u.user_id WHERE o.status='PENDING' ORDER BY o.id ASC").fetchall()
    trans = conn.execute("SELECT t.*, u.real_name FROM transactions t JOIN users u ON t.user_id = u.user_id ORDER BY t.id DESC LIMIT 5").fetchall()
    conn.close()
    return jsonify({
        "status": "success",
        "stats": {"users": users_count, "balance": tot_bal, "debt": tot_debt},
        "pending_orders": [dict(o) for o in orders],
        "recent_trans": [dict(t) for t in trans]
    })

# ==========================================
# أداة المدير: إضافة دين قديم يدوياً (بدون رصيد)
# ==========================================
@app.route('/manual_debt', methods=['GET', 'POST'])
def manual_debt():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect('/')
    conn = get_db_connection()
    if request.method == 'POST':
        uid = request.form.get('uid')
        amt = float(request.form.get('amt', 0))
        if uid and amt > 0:
            conn.execute("UPDATE users SET debt_balance = debt_balance + ? WHERE user_id = ?", (amt, int(uid)))
            conn.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (int(uid), session['user_id'], 0, amt, 'old_debt', 0, 1, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit(); conn.close()
            return "<script>alert('✅ تم تسجيل الدين القديم بنجاح!'); window.location.href='/users';</script>"
    users = conn.execute("SELECT user_id, real_name, role FROM users WHERE role IN ('user', 'employee') ORDER BY real_name").fetchall()
    conn.close()
    options = "".join([f"<option value='{u['user_id']}'>👤 {u['real_name']} ({'موظف' if u['role']=='employee' else 'زبون'})</option>" for u in users])
    return f"""
    <html dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
    <link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css' rel='stylesheet'>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <title>تسجيل دين قديم</title></head>
    <body class='bg-light p-4 d-flex align-items-center justify-content-center' style='min-height: 100vh;'>
    <div class='container' style='max-width: 500px;'><div class='card shadow-lg border-0 rounded-4'>
        <div class='card-header text-white fw-bold text-center py-3' style='background: linear-gradient(135deg, #dc2626 0%, #ef4444 100%); border-radius: 15px 15px 0 0;'><i class="fas fa-book-dead fs-4 mb-2 d-block"></i> تسجيل دين قديم / يدوي</div>
        <div class='card-body p-4'><form method='POST'>
            <div class='mb-3'><label class='fw-bold text-muted mb-2'>اختر الموظف أو الزبون:</label><select name='uid' class='form-select border-danger' required><option value='' disabled selected>اختر من القائمة...</option>{options}</select></div>
            <div class='mb-4'><label class='fw-bold text-muted mb-2'>مبلغ الدين (ل.س):</label><input type='number' name='amt' class='form-control border-danger form-control-lg' placeholder='مثال: 50000' required></div>
            <button type='submit' class='btn btn-danger w-100 fw-bold py-3 mb-2 shadow-sm'>تسجيل الدين في الدفتر 📝</button>
            <a href='/users' class='btn btn-light border w-100 fw-bold py-2 text-muted'>إلغاء والعودة</a>
        </form></div></div></div></body></html>
    """

# ==========================================
# 🧾 واجهة مراجعة الطلب (فاتورة ما قبل التحويل)
# ==========================================
@app.route('/api/preview_transfer', methods=['POST'])
def api_preview_transfer():
    try:
        data = request.json
        u, p, target, amount, net = data.get('username'), data.get('password'), data.get('target_phone'), data.get('amount'), data.get('network')
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username=? AND password=?", (u, p)).fetchone()

        if not user: conn.close(); return jsonify({"status": "error", "message": "بيانات الدخول غير صحيحة"})

        # 🛑 ميزة سقف الدين التلقائي (تم إصلاح المسافات)
        user_dict = dict(user)
        current_debt = float(user_dict.get('debt_balance', 0) or 0)
        limit = float(user_dict.get('debt_limit', 50000) or 50000)

        if current_debt >= limit:
            conn.close()
            return jsonify({"status": "error", "message": f"❌ تجاوزت سقف الدين ({limit:g} ل.س). يرجى التسديد للاستمرار."})

        net_status = get_setting(f'status_{net}')
        if str(net_status) == '0': conn.close(); return jsonify({"status": "error", "message": f"عذراً! شبكة {net} متوقفة"})

        try:
            amt = float(amount)
            if amt <= 0: raise ValueError
        except:
            conn.close(); return jsonify({"status": "error", "message": "المبلغ غير صحيح"})

        cats = conn.execute("SELECT amount, ussd_amount FROM categories WHERE network=? AND is_active=1", (net,)).fetchall()
        if not cats: conn.close(); return jsonify({"status": "error", "message": "الفئات غير متوفرة"})

        denoms = [float(c['amount']) for c in cats]
        amt_map = {float(c['amount']): float(c['ussd_amount']) for c in cats}

        combo = find_best_denominations(amt, denoms)

        if not combo: conn.close(); return jsonify({"status": "error", "message": "لا يمكن تحويل هذا المبلغ بالفئات المتاحة"})
        if len(combo) > 5: conn.close(); return jsonify({"status": "error", "message": "المبلغ متجاوز للحد المسموح (يحتاج لتقسيم كبير)"})

        total_deduction = sum(combo)
        total_sent = sum(amt_map[c] for c in combo)
        difference = total_deduction - amt

        if float(user['balance']) < total_deduction:
            conn.close(); return jsonify({"status": "error", "message": f"رصيدك غير كافٍ. المطلوب: {total_deduction:g} وحدة"})

        conn.close()
        return jsonify({
            "status": "success",
            "network": net,
            "target": target,
            "requested": f"{amt:g}",
            "sent": f"{total_sent:g}",
            "deduction": f"{total_deduction:g}",
            "difference": f"{difference:g}"
        })
    except Exception as e:
        if 'conn' in locals(): conn.close()
        return jsonify({"status": "error", "message": "خطأ في معالجة الطلب"})

# ==========================================
# 💸 واجهة التحويل (محدثة مع رسائل الخطأ والتجاوز)
# ==========================================
@app.route('/api/transfer', methods=['POST'])
def api_transfer():
    try:
        data = request.json
        u, p, target, amount, net = data.get('username'), data.get('password'), data.get('target_phone'), data.get('amount'), data.get('network')
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username=? AND password=?", (u, p)).fetchone()

        if not user:
            conn.close()
            return jsonify({"status": "error", "message": "بيانات الدخول غير صحيحة ❌"})

        net_status = get_setting(f'status_{net}')
        if str(net_status) == '0':
            conn.close()
            return jsonify({"status": "error", "message": f"عذراً! شبكة {net} متوقفة حالياً للصيانة ⚠️"})

        try:
            amt = float(amount)
            if amt <= 0: raise ValueError
        except:
            conn.close()
            return jsonify({"status": "error", "message": "الرجاء إدخال مبلغ صحيح أكبر من الصفر ⚠️"})

        cats = conn.execute("SELECT amount, ussd_amount FROM categories WHERE network=? AND is_active=1", (net,)).fetchall()
        if not cats:
            conn.close()
            return jsonify({"status": "error", "message": "فئات الشحن غير متوفرة حالياً بالخادم ❌"})

        denoms = [float(c['amount']) for c in cats]
        amt_map = {float(c['amount']): c['ussd_amount'] for c in cats}

        combo = find_best_denominations(amt, denoms)

        if not combo:
            conn.close()
            return jsonify({"status": "error", "message": "لا يمكن تحويل هذا المبلغ بالفئات المتاحة ❌"})

        if len(combo) > 5:
            conn.close()
            return jsonify({"status": "error", "message": "❌ المبلغ متجاوز للحد المسموح (يحتاج لتقسيم كبير جداً، يرجى تجزئة الطلب)"})

        total_req = sum(combo)

        sim_bal_setting = get_setting(f'sim_balance_{net}')
        if sim_bal_setting and total_req > float(sim_bal_setting):
             conn.close()
             return jsonify({"status": "error", "message": "عذراً، رصيد المركز الأساسي لا يكفي حالياً لتنفيذ طلبك ⚠️"})

        # 💡 تصوير الرصيد قبل الخصم
        old_bal = float(user['balance'] or 0)
        new_bal = old_bal - total_req

        cur = conn.cursor()
        cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?", (total_req, user['user_id'], total_req))

        if cur.rowcount == 0:
            conn.rollback()
            conn.close()
            return jsonify({"status": "error", "message": "❌ رصيدك الحالي غير كافٍ لإتمام التحويل، أو توجد عملية أخرى جارية."})

        # 💡 حفظ الرصيد (قبل وبعد) ضمن الدفعات المجزأة بدقة
        running_bal = old_bal
        for c in combo:
            step_new_bal = running_bal - c
            cur.execute("INSERT INTO transactions (user_id, type, network, phone, amount, ussd_amount, status, date, balance_before, balance_after) VALUES (?, 'TRANSFER', ?, ?, ?, ?, 'QUEUED', ?, ?, ?)",
                         (user['user_id'], net, target, c, amt_map[c], datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S"), running_bal, step_new_bal))
            running_bal = step_new_bal

        conn.commit()

        if sim_bal_setting and float(sim_bal_setting) < 10000:
            try:
                alert_msg = f"⚠️ تنبيه عاجل!\nرصيد شريحة {net} الأساسي انخفض إلى: {sim_bal_setting} وحدة.\nيرجى الشحن فوراً لتجنب توقف الطلبات."
                bot.send_message(ADMIN_TG_ID, alert_msg)
            except:
                pass

        conn.close()
        return jsonify({"status": "success", "message": "تم إرسال الطلب بنجاح"})

    except Exception as e:
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return jsonify({"status": "error", "message": "حدث خطأ غير متوقع بالخادم، لم يتم خصم الرصيد ⚠️"})


@app.route('/api/pay_bill', methods=['POST'])
def api_pay_bill():
    try:
        data = request.json
        username, password, srv_id, target = data.get('username'), data.get('password'), data.get('service_id'), data.get('target_info')
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password)).fetchone()
        srv = conn.execute("SELECT s.*, c.name as comp_name FROM manual_services s JOIN companies c ON s.company_id = c.id WHERE s.id=?", (srv_id,)).fetchone()

        if not user or not srv:
            conn.close()
            return jsonify({"status": "error", "message": "خطأ في البيانات ❌"})

        price = float(srv['price'])

        # 💡 تصوير رصيد الفواتير قبل وبعد
        old_bal = float(user['bills_balance'] or 0)
        new_bal = old_bal - price

        cur = conn.cursor()
        cur.execute("UPDATE users SET bills_balance = bills_balance - ? WHERE user_id = ? AND bills_balance >= ?", (price, user['user_id'], price))

        if cur.rowcount == 0:
            conn.rollback()
            conn.close()
            return jsonify({"status": "error", "message": "❌ رصيد الفواتير لا يكفي، أو توجد عملية جارية."})

        # 💡 توثيق الرصيد في السجل المحاسبي للفواتير
        cur.execute("INSERT INTO manual_orders (user_id, service_name, target_info, price, status, profit, date, balance_before, balance_after) VALUES (?, ?, ?, ?, 'PENDING', ?, ?, ?, ?)",
                    (user['user_id'], f"{srv['comp_name']} - {srv['name']}", target, price, price - float(srv['cost']), datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S"), old_bal, new_bal))

        conn.commit()

        try:
            bot.send_message(ADMIN_TG_ID, f"🔔 *طلب فاتورة جديد (من الويب)*\n👤 الزبون: {user['real_name']}\n🏢 الخدمة: {srv['comp_name']} - {srv['name']}\n🎯 الحساب: `{target}`\n💰 السعر: `{price:g}` ل.س\n\nيرجى التنفيذ.", parse_mode="Markdown")
        except:
            pass

        conn.close()
        return jsonify({"status": "success", "message": "تم استلام الطلب بنجاح ✅"})
    except Exception as e:
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return jsonify({"status": "error", "message": "حدث خطأ بالخادم، لم يتم خصم الرصيد ⚠️"})

# ==========================================
# 📱 واجهات الفواتير (نفس الكود السابق مع تأمين المسارات)
# ==========================================
@app.route('/api/companies_services', methods=['POST'])
def api_comps_srvs():
    data = request.json
    conn = get_db_connection()
    user = conn.execute("SELECT bills_balance FROM users WHERE username=? AND password=?", (data.get('username'), data.get('password'))).fetchone()
    if not user: conn.close(); return jsonify({"status": "error"})
    comps = conn.execute("SELECT id, name FROM companies WHERE is_active=1").fetchall()
    srvs = conn.execute("SELECT id, company_id, name, price FROM manual_services WHERE is_active=1").fetchall()
    conn.close()
    return jsonify({"status": "success", "bills_balance": user['bills_balance'], "companies": [dict(c) for c in comps], "services": [dict(s) for s in srvs]})

# ==========================================
# 📱 واجهات برمجة التطبيقات المطورة (Refaie API)
# ==========================================

@app.route('/api/login', methods=['POST'])
def api_login():
    try:
        data = request.get_json(silent=True) or {}
        username, password = data.get('username'), data.get('password')
        if not username or not password: return jsonify({"status": "error", "message": "الرجاء إدخال بيانات الدخول"})
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE (username = ? OR user_id = ?) AND password = ?", (username, username, password)).fetchone()
        if user:
            user_dict = dict(user)
            conn.close()
            return jsonify({"status": "success", "user_id": user_dict.get('user_id', ''), "real_name": user_dict.get('real_name', 'زبون ون تاتش'), "balance": str(user_dict.get('balance', 0)), "bills_balance": str(user_dict.get('bills_balance', 0))})
        else:
            conn.close()
            return jsonify({"status": "error", "message": "بيانات الدخول غير صحيحة ❌"})
    except Exception as e:
        if 'conn' in locals(): conn.close()
        return jsonify({"status": "error", "message": f"عطل برمجي: {str(e)}"})

@app.route('/api/sync_data', methods=['POST'])
def api_sync_data():
    data = request.json
    username, password = data.get('username'), data.get('password')
    conn = get_db_connection()
    user = conn.execute("SELECT balance, bills_balance, debt_balance FROM users WHERE username=? AND password=?", (username, password)).fetchone()
    conn.close()
    if user:
        return jsonify({"status": "success", "balance": f"{user['balance']:g}", "bills_balance": f"{user['bills_balance']:g}", "debt_balance": f"{user['debt_balance'] or 0:g}"})
    return jsonify({"status": "error"})


@app.route('/api/add_favorite', methods=['POST'])
def api_add_favorite():
    data = request.get_json()
    if not data: return jsonify({"status": "error"})
    user_id = None
    username = data.get('username')
    password = data.get('password')
    if username and password:
        conn = get_db_connection()
        user = conn.execute("SELECT user_id FROM users WHERE username=? AND password=?", (username, password)).fetchone()
        conn.close()
        if user: user_id = user['user_id']
    elif session.get('logged_in') and session.get('role') == 'user':
        user_id = session['user_id']
    if not user_id: return jsonify({"status": "error", "message": "غير مصرح لك"})
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    if not name or not phone: return jsonify({"status": "error", "message": "بيانات ناقصة"})
    try:
        conn = get_db_connection()
        count = conn.execute("SELECT count(*) FROM user_favorites WHERE user_id=?", (user_id,)).fetchone()[0]
        if count >= 50: conn.close(); return jsonify({"status": "error", "message": "المفضلة ممتلئة"})
        conn.execute("INSERT INTO user_favorites (user_id, name, phone) VALUES (?, ?, ?)", (user_id, name, phone))
        conn.commit(); conn.close()
        return jsonify({"status": "success", "message": "تم الحفظ في المفضلة بنجاح ✅"})
    except Exception as e:
        if 'conn' in locals(): conn.close()
        return jsonify({"status": "error", "message": "حدث خطأ أثناء الحفظ"})

@app.route('/api/favorites', methods=['POST'])
def api_favorites():
    data = request.json
    username, password = data.get('username'), data.get('password')
    conn = get_db_connection()
    user = conn.execute("SELECT user_id FROM users WHERE username=? AND password=?", (username, password)).fetchone()
    if not user: conn.close(); return jsonify({"status": "error"})
    try:
        favs = conn.execute("SELECT name, phone FROM user_favorites WHERE user_id=?", (user['user_id'],)).fetchall()
        conn.close()
        fav_list = []
        for f in favs:
            net = "Syriatel" if str(f['phone']).startswith(('093','098','099')) or len(str(f['phone']))==8 else "MTN"
            fav_list.append({"name": f['name'], "phone": f['phone'], "network": net})
        return jsonify({"status": "success", "favorites": fav_list})
    except Exception as e:
        if 'conn' in locals(): conn.close()
        return jsonify({"status": "success", "favorites": []})

@app.route('/api/debts', methods=['POST'])
def api_debts():
    data = request.json
    username, password = data.get('username'), data.get('password')
    conn = get_db_connection()
    user = conn.execute("SELECT user_id, debt_balance FROM users WHERE username=? AND password=?", (username, password)).fetchone()
    if not user: conn.close(); return jsonify({"status": "error"})
    try:
        logs = conn.execute("SELECT actual_paid as amount, date, wallet_type FROM deposit_logs WHERE user_id = ? AND (is_debt = 1 OR wallet_type = 'free_debt') ORDER BY id DESC LIMIT 30", (user['user_id'],)).fetchall()
        conn.close()
        debts = []
        for l in logs:
            desc = "دين ناتج عن شحن/فاتورة" if l['wallet_type'] != 'free_debt' else "دين يدوي (خارجي)"
            debts.append({"amount": f"{l['amount']:g}", "description": desc, "date": l['date'][:10] if l['date'] else "", "time": l['date'][11:16] if l['date'] else ""})
        return jsonify({"status": "success", "total_debt": f"{user['debt_balance'] or 0:g}", "debts": debts})
    except Exception as e:
        if 'conn' in locals(): conn.close()
        return jsonify({"status": "success", "total_debt": "0", "debts": []})

@app.route('/api/history', methods=['POST'])
def api_history():
    data = request.json
    username, password = data.get('username'), data.get('password')
    conn = get_db_connection()
    user = conn.execute("SELECT user_id FROM users WHERE username=? AND password=?", (username, password)).fetchone()
    if not user: conn.close(); return jsonify({"status": "error"})

    logs = conn.execute("SELECT network, phone, amount, status, ussd_response, date FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 40", (user['user_id'],)).fetchall()
    conn.close()

    history = []
    for l in logs:
        st = l['status']
        if st == 'SUCCESS': s_ar = "✅ ناجحة"
        elif st == 'REFUNDED': s_ar = "↩️ تم الاسترداد"
        elif st == 'FAILED' or st == 'CANCELLED': s_ar = "❌ فاشلة"
        elif st == 'QUEUED': s_ar = "⏳ بانتظار التنفيذ"
        elif st == 'MANUAL_CHECK': s_ar = "🔎 مراجعة يدوية"
        else: s_ar = "⚙️ معالجة"

        history.append({
            "network": l['network'], "phone": l['phone'], "amount": f"{l['amount']:g}",
            "status": s_ar, "fail_reason": l['ussd_response'] if st in ['FAILED', 'REFUNDED', 'MANUAL_CHECK'] else "",
            "date": l['date']
        })
    return jsonify({"status": "success", "history": history})


# ==========================================
# ⏳ واجهة الانتظار المشتركة (محدثة: حساب الوقت كمدة زمنية واضحة)
# ==========================================
@app.route('/api/pending', methods=['POST'])
def api_pending():
    data = request.json
    username, password = data.get('username'), data.get('password')
    conn = get_db_connection()
    user = conn.execute("SELECT user_id FROM users WHERE username=? AND password=?", (username, password)).fetchone()

    if not user:
        if conn: conn.close()
        return jsonify({"status": "error", "message": "خطأ في بيانات الدخول"})

    today_str = datetime.now(local_tz).strftime("%Y-%m-%d")
    pending_list = []

    try:
        # 1. جلب تحويلات الوحدات
        unit_logs = conn.execute("SELECT id, network, phone, amount, status, date FROM transactions WHERE user_id=? AND status IN ('QUEUED', 'MANUAL_CHECK') ORDER BY id ASC", (user['user_id'],)).fetchall()

        for l in unit_logs:
            txn_id = l['id']
            # حساب رقم الدور اليومي
            queue_no = conn.execute("SELECT COUNT(*) FROM transactions WHERE date LIKE ? AND id <= ?", (f"{today_str}%", txn_id)).fetchone()[0]

            # حساب عدد الطلبات المعلقة اللي *قبل* هاد الطلب (استخدمنا < بدل <=)
            total_ahead = conn.execute("SELECT COUNT(*) FROM transactions WHERE status IN ('QUEUED', 'MANUAL_CHECK') AND id < ?", (txn_id,)).fetchone()[0]

            # تحويل الثواني لنص عربي مفهوم
            wait_seconds = total_ahead * 20
            if wait_seconds == 0:
                est_time = "الآن (جاري التنفيذ 🚀)"
            elif wait_seconds < 60:
                est_time = f"بعد {wait_seconds} ثانية"
            else:
                minutes = wait_seconds // 60
                seconds = wait_seconds % 60
                if seconds == 0:
                    est_time = f"بعد {minutes} دقيقة"
                else:
                    est_time = f"بعد {minutes} دقيقة و {seconds} ثانية"

            pending_list.append({
                "type": "UNIT",
                "title": f"تحويل وحدات - {l['network']}",
                "target": str(l['phone']),
                "amount": f"{l['amount']:g}",
                "status": "⏳ بانتظار التنفيذ" if l['status'] == 'QUEUED' else "🔎 مراجعة يدوية",
                "queue": str(queue_no),
                "time": est_time
            })

        # 2. جلب طلبات الفواتير والخدمات اليدوية
        bill_logs = conn.execute("SELECT id, service_name, target_info, price, status, date FROM manual_orders WHERE user_id=? AND status='PENDING' ORDER BY id ASC", (user['user_id'],)).fetchall()

        for b in bill_logs:
            pending_list.append({
                "type": "BILL",
                "title": f"فاتورة: {b['service_name']}",
                "target": str(b['target_info']),
                "amount": f"{b['price']:g}",
                "status": "⚙️ قيد المعالجة",
                "queue": "-",
                "time": "خلال دقائق"
            })

        conn.close()
        return jsonify({"status": "success", "pending": pending_list})

    except Exception as e:
        if conn: conn.close()
        return jsonify({"status": "error", "message": str(e)})

@app.route('/get_user_history/<int:uid>')
def get_user_history_api(uid):
    if not session.get('logged_in'): return jsonify([])
    conn = get_db_connection()

    # 💡 التعديل هنا: سحب عمود platform من الداتا بيز
    deps = conn.execute("SELECT amount, actual_paid, wallet_type, is_debt, date, platform FROM deposit_logs WHERE user_id=? AND amount != 0 ORDER BY id DESC LIMIT 100", (uid,)).fetchall()
    conn.close()

    combined_logs = []
    for d in deps:
        # 💡 استخراج ختم المنصة وتحويله لأيقونة أنيقة للنافذة
        platform = dict(d).get('platform', 'web')
        plat_icon = "<br><small class='text-primary' style='font-size:10px;'><i class='fab fa-telegram-plane'></i> تلغرام</small>" if platform == 'telegram' else "<br><small class='text-secondary' style='font-size:10px;'><i class='fas fa-laptop'></i> ويب</small>"

        # تحديد اسم المحفظة المستهدفة بالشحن
        t_name = "محفظة الوحدات 📱" if d['wallet_type'] == 'units' else "محفظة الفواتير 🧾" if d['wallet_type'] == 'bills' else "شحن مباشر"
        pay_type = " (آجل 📝)" if d['is_debt'] == 1 else " (كاش 💵)"

        # إذا كان المبلغ بالسالب يعني سحب رصيد، بالموجب يعني إيداع وشحن
        if d['amount'] > 0:
            type_txt = f"📥 شحن رصيد{plat_icon}"
            amt_txt = f"+ {d['amount']:g}"
        else:
            type_txt = f"📤 سحب رصيد{plat_icon}"
            amt_txt = f"- {abs(d['amount']):g}"
            pay_type = "" # السحب ليس فيه كاش أو دين

        combined_logs.append({
            'type': type_txt,
            'target': t_name + pay_type,
            'amount': amt_txt,
            'status': 'SUCCESS',
            'date_time': d['date']
        })

    return jsonify(combined_logs)


@app.route('/api/receive_sms', methods=['POST'])
def api_receive_sms():
    try:
        data = request.get_json(silent=True) or {}
        sender = data.get('sender', 'بدون مرسل')
        message = data.get('message', '').strip()

        if message:
            conn = get_db_connection()

            # 🛡️ جدار الحماية الأول (درع الوقت): منع الرسايل المكررة من الشبكة
            # إذا وصلت رسالة مطابقة حرفياً خلال آخر 10 ثواني، نعتبرها تكرار شبكة ونتجاهلها
            ten_seconds_ago = (datetime.now(local_tz) - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
            is_duplicate = conn.execute("SELECT id FROM sms_logs WHERE sender=? AND message=? AND date >= ?", (sender, message, ten_seconds_ago)).fetchone()

            if is_duplicate:
                conn.close()
                return jsonify({"status": "ignored", "reason": "Duplicate SMS from Network"})

            # تسجيل الرسالة الشرعية
            now_str = datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("INSERT INTO sms_logs (sender, message, date) VALUES (?, ?, ?)", (sender, message, now_str))
            conn.commit()

            # ==========================================
            # 🤖 نظام التحقق المزدوج العكسي (الرادار المنقذ)
            # ==========================================
            # جلب الطلبات اللي قيد التنفيذ فقط (استبعاد الطلبات النائمة QUEUED)
            time_limit = (datetime.now(local_tz) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            pending_trans = conn.execute("SELECT * FROM transactions WHERE status IN ('MANUAL_CHECK', 'PROCESSING') AND date >= ? ORDER BY id ASC", (time_limit,)).fetchall()

            for t in pending_trans:
                full_phone = str(t['phone'])
                short_phone = full_phone[1:] if full_phone.startswith('0') else full_phone

                amt_int = int(float(t['amount']))
                amt_str1 = str(amt_int)
                amt_str2 = f"{amt_int:,}" # مثل 1,500

                if short_phone in message and (amt_str1 in message or amt_str2 in message):
                    tid = t['id']
                    network = t['network']
                    uid = t['user_id']

                    # 1. خصم الرصيد المركزي
                    sim_bal = float(get_setting(f'sim_balance_{network}') or 0)
                    set_setting(f'sim_balance_{network}', str(sim_bal - amt_int))

                    # 2. نقاط الولاء للزبون
                    points_to_add = int(amt_int / 1000)
                    conn.execute("UPDATE users SET loyalty_points = loyalty_points + ? WHERE user_id = ?", (points_to_add, uid))

                    # 3. إغلاق الطلب بنجاح
                    radar_note = f"تم التأكيد بفضل الرادار المتأخر 📡: {message}"
                    conn.execute("UPDATE transactions SET status='SUCCESS', ussd_response=? WHERE id=?", (radar_note, tid))
                    conn.commit()

                    # 4. إشعار الزبون
                    try:
                        bot.send_message(uid, f"╭━━━ ✅ تم التحويل ━━━╮\n🎯 الرقم: `{full_phone}`\n💰 المبلغ: *{amt_int:g}* وحدة\n┣━━━━━━━━━━━━━┫\n📝 تم تأكيد عمليتك المعلقة آلياً بعد وصول إشعار الشبكة.\n╰━━━━━━━━━━━━━╯", parse_mode="Markdown")
                    except: pass

                    # 5. إشعار المدير
                    try:
                        bot.send_message(ADMIN_TG_ID, f"🤖 *تدخل الرادار الآلي!*\nتم إنقاذ الطلب `#{tid}` وتحويله لـ ناجح ✅ تلقائياً.\nالرقم: `{full_phone}` | المبلغ: `{amt_int}`", parse_mode="Markdown")
                    except: pass

                    # 🛡️ جدار الحماية الثاني: كسر الحلقة!
                    # رسالة الـ SMS الواحدة تنقذ طلباً واحداً فقط، وتتوقف الدالة لمنع نجاح الطلبات المتشابهة.
                    break
            # ==========================================

            conn.close()

        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

 # --- دوال تنفيذ الإدارة الخفية (شحن وتسديد) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_') or call.data == "close_admin")
def admin_actions(call):
    if call.from_user.id not in ADMIN_TELEGRAM_IDS: return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)

    if call.data == "close_admin":
        return bot.delete_message(call.message.chat.id, call.message.message_id)

    action = call.data
    if action in ["admin_add_balance", "admin_pay_debt"]:
        prefix = "tgta_" if action == "admin_add_balance" else "tgtp_"
        title = "🟢 *إضافة رصيد للزبون*" if action == "admin_add_balance" else "🔴 *تسديد دفعة (تنزيل دين)*"

        # جلب أسماء الزبائن من قاعدة البيانات
        conn = get_db_connection()
        users = conn.execute("SELECT user_id, real_name FROM users").fetchall()
        conn.close()

        if not users:
            return bot.edit_message_text("⚠️ لا يوجد زبائن مسجلين حالياً.", call.message.chat.id, call.message.message_id)

        # إنشاء أزرار بأسماء الزبائن
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = []
        for u in users:
            name = u['real_name'] or str(u['user_id'])
            buttons.append(types.InlineKeyboardButton(name, callback_data=f"{prefix}{u['user_id']}"))

        markup.add(*buttons)
        markup.add(types.InlineKeyboardButton("❌ إغلاق", callback_data="close_admin"))

        bot.edit_message_text(f"{title}\n\n👇 يرجى اختيار الزبون من القائمة:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('tgta_') or call.data.startswith('tgtp_'))
def admin_select_user_action(call):
    if call.from_user.id not in ADMIN_TELEGRAM_IDS: return

    action_type = "add_balance" if call.data.startswith('tgta_') else "pay_debt"
    target_id = call.data.split('_')[1]

    user_row = get_user(target_id)
    if not user_row:
        return bot.answer_callback_query(call.id, "⚠️ لم يتم العثور على بيانات الزبون.")

    user = dict(user_row)
    user_steps[call.from_user.id] = {'admin_target': target_id, 'admin_action': action_type}

    if action_type == "add_balance":
        text = f"👤 الزبون: *{user.get('real_name', 'غير معروف')}*\n📱 رصيد الوحدات الحالي: `{float(user.get('balance', 0) or 0):g}`\n\n💰 أرسل المبلغ المراد **إضافته** للرصيد (أرقام فقط):"
    else:
        text = f"👤 الزبون: *{user.get('real_name', 'غير معروف')}*\n🔴 الديون الحالية: `{float(user.get('debt_balance', 0) or 0):g}`\n\n💰 أرسل المبلغ المراد **خصمه** من ديون الزبون (أرقام فقط):"

    msg = bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_admin_amount)

def process_admin_amount(message):
    if check_abort(message): return
    uid = message.from_user.id
    data = user_steps.get(uid)
    if not data: return

    try:
        amt = float(message.text)
        if amt <= 0: raise ValueError
    except:
        msg = bot.reply_to(message, "⚠️ يرجى إدخال مبلغ صحيح (أرقام فقط):")
        return bot.register_next_step_handler(msg, process_admin_amount)

    target_id = data['admin_target']
    action_type = data['admin_action']
    user = dict(get_user(target_id))

    # حفظ كمية الوحدات في ذاكرة البوت لنستخدمها بعد اختيار طريقة الدفع
    user_steps[uid]['amount'] = amt

    if action_type == "add_balance":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("💵 كاش (مقبوض)", callback_data="paymeth_cash"),
            types.InlineKeyboardButton("📝 آجل (تقييد دين)", callback_data="paymeth_debt")
        )
        bot.reply_to(message, f"💰 الكمية: `{amt:g}` وحدة.\n\nكيف تمت المحاسبة مع الزبون؟", reply_markup=markup, parse_mode="Markdown")

    elif action_type == "pay_debt":
        conn = get_db_connection()
        cur = conn.cursor()

        # 💡 تصوير أرشيف الديون قبل التعديل
        user_row = cur.execute("SELECT debt_balance, real_name FROM users WHERE user_id = ?", (target_id,)).fetchone()
        old_debt = float(user_row['debt_balance'] or 0)
        remaining_debt = old_debt - amt

        # 1. تنزيل الدين من حساب الزبون
        cur.execute("UPDATE users SET debt_balance = debt_balance - ? WHERE user_id = ?", (amt, target_id))

        # 2. تحديث كاش الدرج المركزي آلياً بالموقع عند القبض بالتلغرام
        curr_cash = float((get_setting('cash_drawer') or '0').replace(',', ''))
        cur.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('cash_drawer', str(curr_cash + amt)))

        # 3. 💡 قفل الفجوة: تسجيل قيد التسديد الموثق بصور الحساب وختم التلغرام
        cur.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date, user_balance_before, user_balance_after, platform) VALUES (?, ?, 0, ?, 'debt_payment', 0, 0, ?, ?, ?, 'telegram')",
                    (target_id, uid, amt, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S"), old_debt, remaining_debt))

        conn.commit()
        conn.close()

        bot.reply_to(message, f"✅ تم تنزيل `{amt:g}` ل.س من ديون الزبون *{user_row['real_name']}*.\nالديون المتبقية: `{remaining_debt:g}` ل.س وتوثقت بالويب.", parse_mode="Markdown")

        try:
            if remaining_debt <= 0:
                status_msg = "🎉 *مبروك! حسابك نظيف*\n\nتم تسديد كامل ديونك وأصبح حسابك (صفر ديون). شكراً لالتزامك."
            else:
                status_msg = f"🧾 *إشعار تسديد دفعة*\n\nتم تسجيل دفعة نقدية بقيمة `{amt:g}` ل.س من ديونك.\nالديون المتبقية عليك: `{remaining_debt:g}` ل.س."
            bot.send_message(target_id, status_msg, parse_mode="Markdown")
        except: pass

        user_steps.pop(uid, None)


@bot.callback_query_handler(func=lambda call: call.data.startswith('paymeth_'))
def admin_payment_method(call):
    if call.from_user.id not in ADMIN_TELEGRAM_IDS: return

    uid = call.from_user.id
    data = user_steps.get(uid)
    if not data or 'amount' not in data:
        return bot.answer_callback_query(call.id, "⏳ انتهت الجلسة.")

    target_id = data['admin_target']
    amt = data['amount']
    method = call.data.split('_')[1]

    if method == "cash":
        conn = get_db_connection()
        cur = conn.cursor()

        # 💡 تصوير الرصيد الحالي وحساب العوائد والربح الصافي
        user_row = cur.execute("SELECT balance, custom_sell_price, real_name FROM users WHERE user_id = ?", (target_id,)).fetchone()
        old_bal = float(user_row['balance'] or 0)
        new_bal = old_bal + amt

        sell_price = float(user_row['custom_sell_price'] or 1.05)
        actual_paid = amt * sell_price

        unit_cost = float(get_setting('current_unit_cost') or 1.03)
        profit = actual_paid - (amt * unit_cost)

        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, target_id))

        # تحديث كاش الدرج المركزي آلياً ليطابق الموقع
        curr_cash = float((get_setting('cash_drawer') or '0').replace(',', ''))
        cur.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('cash_drawer', str(curr_cash + actual_paid)))

        # 💡 قفل الفجوة: تسجيل الحركة وختمها بأنها من (التلغرام)
        cur.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date, user_balance_before, user_balance_after, platform) VALUES (?, ?, ?, ?, 'units', ?, 0, ?, ?, ?, 'telegram')",
                    (target_id, uid, amt, actual_paid, profit, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S"), old_bal, new_bal))

        conn.commit()
        conn.close()

        bot.edit_message_text(f"✅ تم شحن `{amt:g}` وحدة للزبون *{user_row['real_name']}*\nطريقة الدفع: 💵 كاش (وتم ترحيل الأرباح للويب)", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        try:
            bot.send_message(target_id, f"🎉 *إشعار شحن رصيد*\n\nتم إضافة `{amt:g}` وحدة إلى رصيدك (💵 كاش).\nرصيدك الجديد أصبح: `{new_bal:g}` وحدة.", parse_mode="Markdown")
        except: pass
        user_steps.pop(uid, None)

    elif method == "debt":
        conn = get_db_connection()
        cur = conn.cursor()

        # 💡 تصوير الرصيد الحالي وحساب الديون والربح الصافي
        user_row = cur.execute("SELECT balance, debt_balance, custom_sell_price, real_name FROM users WHERE user_id = ?", (target_id,)).fetchone()
        old_bal = float(user_row['balance'] or 0)
        new_bal = old_bal + amt

        custom_price = float(user_row['custom_sell_price'] or 1.05)
        debt_val = amt * custom_price

        unit_cost = float(get_setting('current_unit_cost') or 1.03)
        profit = debt_val - (amt * unit_cost)

        cur.execute("UPDATE users SET balance = balance + ?, debt_balance = debt_balance + ? WHERE user_id = ?", (amt, debt_val, target_id))
        new_debt = float(user_row['debt_balance'] or 0) + debt_val

        # 💡 قفل الفجوة: تسجيل الحركة وختمها بأنها من (التلغرام)
        cur.execute("INSERT INTO deposit_logs (user_id, by_admin_id, amount, actual_paid, wallet_type, profit, is_debt, date, user_balance_before, user_balance_after, platform) VALUES (?, ?, ?, ?, 'units', ?, 1, ?, ?, ?, 'telegram')",
                    (target_id, uid, amt, debt_val, profit, datetime.now(local_tz).strftime("%Y-%m-%d %H:%M:%S"), old_bal, new_bal))

        conn.commit()
        conn.close()

        bot.edit_message_text(f"✅ تم شحن `{amt:g}` وحدة للزبون *{user_row['real_name']}*\n📝 تم تسجيل دين بقيمة `{debt_val:g}` ل.س وتوثيق الحركة بالويب.", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        try:
            msg_to_user = f"🎉 *إشعار شحن (آجل)*\n\nتم إضافة `{amt:g}` وحدة.\nتم تقييد مبلغ: `{debt_val:g}` ل.س كدين.\n\nالديون الحالية: `{new_debt:g}` ل.س."
            bot.send_message(target_id, msg_to_user, parse_mode="Markdown")
        except: pass
        user_steps.pop(uid, None)

    # --- دالة الرد على تذاكر الزبائن من قبل المدير ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('reply_ticket_'))
def admin_reply_ticket(call):
    if call.from_user.id not in ADMIN_TELEGRAM_IDS: return

    target_id = call.data.split('_')[2]

    # 💡 السر هنا: البوت سيستخرج نص المشكلة أو الرقم من رسالة الإشعار نفسها
    try:
        original_text = call.message.text.split("📝 المشكلة أو الرقم:\n")[1]
    except:
        original_text = "غير محدد"

    msg = bot.edit_message_text(f"📌 *أنت ترد الآن على:*\n{original_text}\n\n✏️ اكتب ردك الآن (مثال: تم التأكد، محول نظامي):", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

    # نمرر نص المشكلة للخطوة التالية
    bot.register_next_step_handler(msg, process_admin_ticket_reply, target_id, original_text)

def process_admin_ticket_reply(message, target_id, original_text):
    if check_abort(message): return

    # تنسيق الرسالة التي ستصل للزبون (تتضمن المشكلة + الرد)
    reply_text = f"📨 *رد من إدارة Refaie:*\n\n📌 *بخصوص استفسارك عن:*\n`{original_text}`\n\n💬 *الرد:*\n{message.text}"

    try:
        bot.send_message(target_id, reply_text, parse_mode="Markdown")
        bot.reply_to(message, "✅ تم إرسال الرد للزبون بنجاح (مرفقاً بالرقم الذي استفسر عنه).", reply_markup=main_menu(message.from_user.id))
    except Exception as e:
        bot.reply_to(message, "⚠️ تعذر إرسال الرد. ربما قام الزبون بحظر البوت أو إيقافه.", reply_markup=main_menu(message.from_user.id))

# ==========================================
# 📡 نظام مراقبة اتصال الموبايل بالإنترنت
# ==========================================
@app.route('/api/heartbeat', methods=['GET'])
def api_heartbeat():
    # تسجيل وقت آخر اتصال ناجح من الموبايل
    set_setting('last_mobile_heartbeat', str(time.time()))

    # إذا كان مفصول ورجع اتصل، منرسلك رسالة تبشيرية
    if get_setting('mobile_offline_alert_sent') == '1':
        set_setting('mobile_offline_alert_sent', '0')
        try:
            bot.send_message(ADMIN_TG_ID, "✅ *بشرى سارة!*\nعاد الموبايل للاتصال بالإنترنت، ونظام الاستقبال يعمل الآن بشكل طبيعي 🚀", parse_mode="Markdown")
        except: pass

    return jsonify({"status": "success"})

@app.route('/api/check_mobile_status', methods=['GET'])
def check_mobile_status():
    # ... كود فحص اتصال الموبايل ...
    return "Check complete", 200

@app.route('/api/get_network_configs', methods=['POST'])
def api_get_network_configs():
    conn = get_db_connection()
    # جلب جميع ملفات الأكواد والمعادلات والكلمات المفتاحية وإرسالها للتطبيق
    rows = conn.execute("SELECT service_name, ussd_format, secret_pin, success_keyword, failure_keyword, app_timeout, request_interval FROM ussd_codes").fetchall()
    conn.close()

    configs = {}
    for r in rows:
        configs[r['service_name']] = {
            "ussd_format": r['ussd_format'] if r['ussd_format'] else "",
            "secret_pin": r['secret_pin'] if r['secret_pin'] else "",
            "success_keywords": r['success_keyword'] if r['success_keyword'] else "بنجاح",
            "failure_keywords": r['failure_keyword'] if r['failure_keyword'] else "فشل",
            "app_timeout": r['app_timeout'] if r['app_timeout'] else 20,
            "request_interval": r['request_interval'] if r['request_interval'] else 5
        }

    return jsonify({"status": "success", "network_configs": configs})

# =========================================================
# قسم إدارة خزينة الكاش المركزية (تحديث الكاش والفواتير)
# =========================================================

@app.route('/admin/cash', methods=['GET', 'POST'])
def admin_cash_management():
    conn = get_db_connection()
    # تفعيل نظام الأسماء لسهولة التعامل مع الأعمدة
    try:
        conn.row_factory = sqlite3.Row
    except:
        pass
    cursor = conn.cursor()

    # تحديث تلقائي ذكي لقاعدة البيانات (إضافة أعمدة الخزينة المركزية إذا لم تكن موجودة)
    try:
        cursor.execute("ALTER TABLE settings ADD COLUMN sim_cash_Syriatel REAL DEFAULT 0;")
        conn.commit()
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE settings ADD COLUMN sim_cash_MTN REAL DEFAULT 0;")
        conn.commit()
    except Exception:
        pass

    message = None

    # في حال قام الإدمن بتحديث الأرصدة وحفظها من لوحة التحكم
    if request.method == 'POST':
        cash_syriatel = request.form.get('cash_syriatel', 0, type=float)
        cash_mtn = request.form.get('cash_mtn', 0, type=float)

        # فحص إذا كان جدول الإعدادات يحتوي على بيانات مسبقاً
        cursor.execute("SELECT COUNT(*) FROM settings")
        count = cursor.fetchone()[0]

        if count == 0:
            cursor.execute("INSERT INTO settings (sim_cash_Syriatel, sim_cash_MTN) VALUES (?, ?)", (cash_syriatel, cash_mtn))
        else:
            cursor.execute("UPDATE settings SET sim_cash_Syriatel = ?, sim_cash_MTN = ?", (cash_syriatel, cash_mtn))

        conn.commit()
        message = "✅ تم تحديث أرصدة الخزينة المركزية بنجاح!"

    # جلب القيم الحالية من قاعدة البيانات لعرضها في لوحة التحكم
    cursor.execute("SELECT sim_cash_Syriatel, sim_cash_MTN FROM settings LIMIT 1")
    settings_row = cursor.fetchone()

    current_syriatel = 0
    current_mtn = 0

    if settings_row:
        try:
            current_syriatel = settings_row['sim_cash_Syriatel']
            current_mtn = settings_row['sim_cash_MTN']
        except:
            # طريقة احتياطية في حال لم يتم تفعيل row_factory بنجاح
            current_syriatel = settings_row[0] if len(settings_row) > 0 else 0
            current_mtn = settings_row[1] if len(settings_row) > 1 else 0

    conn.close()

    # تصميم واجهة لوحة تحكم الخزينة متناسقة ومريحة للموبايل والكمبيوتر
    html_content = '''
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>إدارة الخزينة المركزية للكاش - الرفاعي تليكوم</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }
            .container { max-width: 600px; background: white; margin: 40px auto; padding: 30px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }
            h2 { text-align: center; color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; margin-bottom: 20px; }
            .alert { background-color: #d4edda; color: #155724; padding: 12px; border-radius: 5px; margin-bottom: 20px; text-align: center; font-weight: bold; border: 1px solid #c3e6cb; }
            .card { background: #f8f9fa; border: 1px solid #e9ecef; padding: 20px; border-radius: 6px; margin-bottom: 20px; }
            .card-title { font-weight: bold; font-size: 16px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
            .syriatel { color: #e74c3c; border-right: 4px solid #e74c3c; padding-right: 10px; }
            .mtn { color: #f39c12; border-right: 4px solid #f39c12; padding-right: 10px; }
            .badge { background: #333; color: white; padding: 6px 12px; border-radius: 4px; font-size: 15px; font-weight: bold; }
            .syriatel .badge { background: #e74c3c; }
            .mtn .badge { background: #f39c12; }
            label { display: block; margin-bottom: 8px; font-weight: bold; color: #555; }
            input[type="number"] { width: 100%; padding: 12px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; font-size: 18px; text-align: center; font-weight: bold; color: #2c3e50; }
            button { width: 100%; background-color: #2ecc71; color: white; padding: 14px; border: none; border-radius: 4px; font-size: 18px; cursor: pointer; font-weight: bold; margin-top: 10px; transition: background 0.2s; }
            button:hover { background-color: #27ae60; }
            .footer { text-align: center; margin-top: 25px; font-size: 13px; color: #7f8c8d; border-top: 1px solid #eee; padding-top: 15px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>💰 إدارة خزينة الكاش المركزية</h2>

            {% if message %}
                <div class="alert">{{ message }}</div>
            {% endif %}

            <form method="POST">
                <div class="card">
                    <div class="card-title syriatel">
                        <span>🔴 كاش وفواتير سيريتل (Syriatel)</span>
                        <span class="badge">{{ current_syriatel }} ل.س</span>
                    </div>
                    <label for="cash_syriatel">تعديل رصيد الخزينة الحالي المتوفر في خطك:</label>
                    <input type="number" step="any" id="cash_syriatel" name="cash_syriatel" value="{{ current_syriatel }}" required>
                </div>

                <div class="card">
                    <div class="card-title mtn">
                        <span>🟡 كاش وفواتير MTN</span>
                        <span class="badge">{{ current_mtn }} ل.س</span>
                    </div>
                    <label for="cash_mtn">تعديل رصيد الخزينة الحالي المتوفر في خطك:</label>
                    <input type="number" step="any" id="cash_mtn" name="cash_mtn" value="{{ current_mtn }}" required>
                </div>

                <button type="submit">💾 حفظ التغييرات وتحديث الخزائن</button>
            </form>

            <div class="footer">نظام الرفاعي تليكوم لإدارة الخدمات المؤتمتة</div>
        </div>
    </body>
    </html>
    '''
    return render_template_string(html_content, message=message, current_syriatel=current_syriatel, current_mtn=current_mtn)

# =========================================================
# قسم الخزينة المركزية للكاش والفواتير (تحديث: 4 أرصدة)
# =========================================================

old_deposit_btn = '<a href="/deposits" class="{{ \'active\' if page == \'deposits\' else \'\' }}"><i class="fas fa-university ms-2"></i> الخزينة ودفتر الأستاذ</a>'
new_cash_btn = old_deposit_btn + '\n    <a href="/admin_cash" class="{{ \'active\' if page == \'admin_cash\' else \'\' }}"><i class="fas fa-money-bill-wave ms-2"></i> خزينة الكاش والفواتير</a>'

if "admin_cash" not in HTML_BASE:
    HTML_BASE = HTML_BASE.replace(old_deposit_btn, new_cash_btn)

@app.route('/admin_cash', methods=['GET', 'POST'])
def admin_cash_page():
    if not session.get('logged_in') or session.get('role') != 'admin':
        return redirect('/')

    if request.method == 'POST':
        set_setting('sim_cash_Syriatel', request.form.get('cash_syriatel', '0'))
        set_setting('sim_cash_MTN', request.form.get('cash_mtn', '0'))
        set_setting('sim_bill_Syriatel', request.form.get('bill_syriatel', '0'))
        set_setting('sim_bill_MTN', request.form.get('bill_mtn', '0'))
        return redirect('/admin_cash?success=1')

    current_cash_syr = float(get_setting('sim_cash_Syriatel') or 0)
    current_cash_mtn = float(get_setting('sim_cash_MTN') or 0)
    current_bill_syr = float(get_setting('sim_bill_Syriatel') or 0)
    current_bill_mtn = float(get_setting('sim_bill_MTN') or 0)

    alert_msg = ""
    if request.args.get('success'):
        alert_msg = "<div class='alert alert-success fw-bold shadow-sm mb-4'><i class='fas fa-check-circle me-2'></i> تم تحديث أرصدة الخزينة بنجاح!</div>"

    content = f"""
    <div class="d-flex justify-content-between align-items-center mb-4">
        <h3 class="fw-black text-primary m-0"><i class="fas fa-money-bill-wave me-2"></i> إدارة خزينة الكاش والفواتير المركزية</h3>
        <p class="text-muted fw-bold m-0">رصيد الخطوط الفعلي</p>
    </div>

    {alert_msg}

    <div class="row g-4 mb-4">
        <div class="col-md-6">
            <div class="card-bank border-danger border-start border-5 h-100 shadow-sm" style="background: linear-gradient(135deg, #fef2f2, #fecaca);">
                <div class="text-center py-3">
                    <h6 class="text-danger fw-bold mb-2"><i class="fas fa-money-bill me-2"></i>رصيد كاش سيريتل</h6>
                    <h3 class="fw-black text-danger m-0" dir="ltr">{current_cash_syr:,.2f}</h3>
                </div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="card-bank border-warning border-start border-5 h-100 shadow-sm" style="background: linear-gradient(135deg, #fffbeb, #fde68a);">
                <div class="text-center py-3">
                    <h6 class="text-warning text-dark fw-bold mb-2" style="color: #d97706!important;"><i class="fas fa-money-bill me-2"></i>رصيد كاش MTN</h6>
                    <h3 class="fw-black text-dark m-0" dir="ltr">{current_cash_mtn:,.2f}</h3>
                </div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="card-bank border-danger border-start border-5 h-100 shadow-sm" style="background: linear-gradient(135deg, #fff5f5, #ffe3e3);">
                <div class="text-center py-3">
                    <h6 class="text-danger fw-bold mb-2"><i class="fas fa-file-invoice-dollar me-2"></i>رصيد فواتير سيريتل</h6>
                    <h3 class="fw-black text-danger m-0" dir="ltr">{current_bill_syr:,.2f}</h3>
                </div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="card-bank border-warning border-start border-5 h-100 shadow-sm" style="background: linear-gradient(135deg, #fffdf2, #fef0bd);">
                <div class="text-center py-3">
                    <h6 class="text-warning text-dark fw-bold mb-2" style="color: #b45309!important;"><i class="fas fa-file-invoice-dollar me-2"></i>رصيد فواتير MTN</h6>
                    <h3 class="fw-black text-dark m-0" dir="ltr">{current_bill_mtn:,.2f}</h3>
                </div>
            </div>
        </div>
    </div>

    <div class="card-bank bg-white border-primary border-start border-4 shadow-sm p-4">
        <h5 class="fw-bold text-dark mb-4"><i class="fas fa-pen me-2 text-primary"></i> تحديث أرصدة الخزينة يدوياً</h5>
        <form method="POST" class="row g-3 align-items-end">
            <div class="col-md-3">
                <label class="form-label fw-bold text-danger fs-7">كاش سيريتل:</label>
                <input type="number" step="0.01" name="cash_syriatel" value="{current_cash_syr}" class="form-control border-danger shadow-sm fw-bold text-center" required>
            </div>
            <div class="col-md-3">
                <label class="form-label fw-bold text-warning fs-7" style="color: #d97706!important;">كاش MTN:</label>
                <input type="number" step="0.01" name="cash_mtn" value="{current_cash_mtn}" class="form-control border-warning shadow-sm fw-bold text-center" required>
            </div>
            <div class="col-md-3">
                <label class="form-label fw-bold text-danger fs-7">فواتير سيريتل:</label>
                <input type="number" step="0.01" name="bill_syriatel" value="{current_bill_syr}" class="form-control border-danger shadow-sm fw-bold text-center" required>
            </div>
            <div class="col-md-3">
                <label class="form-label fw-bold text-warning fs-7" style="color: #b45309!important;">فواتير MTN:</label>
                <input type="number" step="0.01" name="bill_mtn" value="{current_bill_mtn}" class="form-control border-warning shadow-sm fw-bold text-center" required>
            </div>
            <div class="col-md-12 mt-4">
                <button type="submit" class="btn btn-primary btn-lg w-100 fw-bold shadow-sm"><i class="fas fa-save me-1"></i> حفظ وتحديث الجميع</button>
            </div>
        </form>
    </div>
    """
    return render_template_string(HTML_BASE.replace('{% block content %}{% endblock %}', content), page='admin_cash')

# =========================================================
# دوال الخزينة المركزية (محدثة لـ 4 أرصدة)
# =========================================================
def check_central_cash(network, service_name, amount):
    amount = float(amount)
    is_bill = 'فاتورة' in service_name or 'bill' in service_name.lower()

    if 'syriatel' in network.lower() or 'سيريتل' in network:
        key = 'sim_bill_Syriatel' if is_bill else 'sim_cash_Syriatel'
        return float(get_setting(key) or 0) >= amount
    elif 'mtn' in network.lower():
        key = 'sim_bill_MTN' if is_bill else 'sim_cash_MTN'
        return float(get_setting(key) or 0) >= amount
    return True

def deduct_central_cash(network, service_name, amount):
    amount = float(amount)
    is_bill = 'فاتورة' in service_name or 'bill' in service_name.lower()

    if 'syriatel' in network.lower() or 'سيريتل' in network:
        key = 'sim_bill_Syriatel' if is_bill else 'sim_cash_Syriatel'
        current = float(get_setting(key) or 0)
        set_setting(key, str(current - amount))
    elif 'mtn' in network.lower():
        key = 'sim_bill_MTN' if is_bill else 'sim_cash_MTN'
        current = float(get_setting(key) or 0)
        set_setting(key, str(current - amount))

@bot.callback_query_handler(func=lambda call: call.data == 'admin_zero_balance')
def admin_zero_bal_start(call):
    msg = bot.edit_message_text("🧹 *تصفير حساب زبون*\n\nأرسل رقم الزبون (أو الـ ID) للبحث عنه:", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_zero_balance_search)

def process_zero_balance_search(message):
    target_user_id = message.text.strip()
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # البحث عن الزبون
        user = cur.execute("SELECT * FROM users WHERE user_id = ?", (target_user_id,)).fetchone()
        
        if user:
            # سحب الاسم والرصيد (تأكد إنو اسم العمود بقاعدة بياناتك هو 'name' أو عدله حسب اللي عندك)
            try:
                user_name = user['name'] 
            except:
                user_name = user.get('first_name', 'اسم غير مسجل') # بديل في حال اختلاف اسم العمود
                
            current_balance = user['balance']
            
            # إنشاء أزرار التأكيد
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ نعم، صفر الحساب", callback_data=f"confirm_zero_{target_user_id}"),
                types.InlineKeyboardButton("❌ إلغاء العملية", callback_data="close_admin")
            )
            
            # عرض اسم الزبون ورصيده لمدير المركز قبل التصفير
            bot.reply_to(message, f"👤 **الزبون:** {user_name}\n💰 **الرصيد الحالي:** {current_balance} ل.س\n\n⚠️ **هل أنت متأكد من تصفير حسابه بالكامل؟**", reply_markup=markup, parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ لم يتم العثور على زبون بهذا الرقم.")
            
        conn.close()
    except Exception as e:
        bot.reply_to(message, "⚠️ حدث خطأ أثناء جلب بيانات الزبون.")

# الدالة النهائية اللي بتنفذ التصفير بعد ما تكبس "نعم"
@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_zero_'))
def confirm_zero_action(call):
    target_user_id = call.data.split('_')[2]
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = 0 WHERE user_id = ?", (target_user_id,))
    conn.commit()
    conn.close()
    
    bot.edit_message_text(f"✅ **تم تصفير الحساب بنجاح!**\nالرصيد الحالي أصبح: 0", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
