import os
import sqlite3
from datetime import datetime, timezone, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ======================== НАСТРОЙКИ ========================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "Не задана переменная окружения BOT_TOKEN.\n"
        "Установи её так:\n"
        "  export BOT_TOKEN='123456:ABC...'      (Linux/macOS)\n"
        "  set BOT_TOKEN=123456:ABC...           (Windows CMD)\n"
        "  $env:BOT_TOKEN='123456:ABC...'        (PowerShell)\n"
        "или создай файл .env рядом с bot.py:\n"
        "  BOT_TOKEN=123456:ABC..."
    )

DB_PATH = os.environ.get("DB_PATH", "cats.db")

TZ = timezone(timedelta(hours=3))
TZ_LABEL = "МСК"

ALLOWED_USERS = set()   # например: {123456789, 987654321}

REMINDER_HOURS = 10
REMINDER_CHECK_MIN = 30
WATER_REMINDER_HOURS = 24
LITTER_REMINDER_HOURS = 24

STATS_DAYS = 7
CHART_BAR_WIDTH = 15
HISTORY_LIMIT = 15

CATS = ["Гафи", "Луна"]
PIN_REFRESH_SEC = 30
# ==========================================================

# ------------- ТЕКСТЫ КНОПОК -------------
BTN_FEED = "🐱 Покормил котов"
BTN_OTHER = "🧹 Прочие дела"
BTN_WEIGHT = "⚖️ Вес котов"
BTN_HISTORY = "📜 История / ⚙️ Настройки"

BTN_WATER = "💧 Поменял воду"
BTN_LITTER = "🧹 Убрал лоток"
BTN_BACK = "🔙 В меню"

BTN_LAST = "🕐 Последнее"
BTN_FEED_HISTORY = "📜 История"
BTN_STATS = "📊 Статистика"
BTN_CHART = "📈 График"
BTN_WATER_HISTORY = "📋 История воды"
BTN_LITTER_HISTORY = "📋 История лотка"
BTN_REFRESH = "🔄 Обновить сводку"
BTN_UNDO = "❌ Отменить последнее"

BTN_WEIGHT_HISTORY = "📊 История веса"
BTN_WEIGHT_CAT = {cat: f"⚖️ {cat}" for cat in CATS}

MENU_TEXT = (
    "🐾 <b>Бот учёта кормления котов</b>\n\n"
    "Жми «Покормил котов», когда кормишь — запомню время, "
    "уведомлю второго человека и напомню, если долго не кормили. "
    "Остальное — в кнопках внизу."
)
OTHER_TEXT = "🧹 <b>Прочие дела</b>\n\nВыбери действие:"
WEIGHT_TEXT = "⚖️ <b>Вес котов</b>\n\nВыбери кота, чтобы записать вес:"
HISTORY_TEXT = "📜 <b>История и настройки</b>\n\nВыбери действие:"


# ---------------- БАЗА ДАННЫХ ----------------
def init_db() -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS feedings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, username TEXT, fed_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS weights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cat TEXT NOT NULL, user_id INTEGER NOT NULL,
            username TEXT, weight REAL NOT NULL, recorded_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS water_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, username TEXT, changed_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS litter_cleanings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, username TEXT, cleaned_at TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = con.execute("SELECT value FROM meta WHERE key = 'bot_start'").fetchone()
    if not row:
        con.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            ("bot_start", datetime.now(timezone.utc).isoformat()),
        )
    con.commit()
    con.close()


def save_feeding(user_id, username):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO feedings (user_id, username, fed_at) VALUES (?, ?, ?)",
        (user_id, username, datetime.now(timezone.utc).isoformat()),
    )
    con.commit(); con.close()


def _fed_dict(row):
    if not row:
        return None
    return {"id": row[0], "user_id": row[1], "username": row[2], "fed_at": row[3]}


def get_last_feeding():
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id, user_id, username, fed_at FROM feedings ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    return _fed_dict(row)


def get_history(limit=15):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT id, user_id, username, fed_at FROM feedings ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    con.close()
    return [_fed_dict(r) for r in rows]


def delete_last_feeding():
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id, user_id, username, fed_at FROM feedings ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row:
        con.execute("DELETE FROM feedings WHERE id = ?", (row[0],))
        con.commit()
    con.close()
    return _fed_dict(row)


def get_all_user_ids():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT DISTINCT user_id FROM feedings").fetchall()
    con.close()
    return [r[0] for r in rows]


def get_meta(key):
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    con.close()
    return row[0] if row else None


def set_meta(key, value):
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))
    con.commit(); con.close()


def save_weight(cat, user_id, username, weight):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO weights (cat, user_id, username, weight, recorded_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (cat, user_id, username, float(weight),
         datetime.now(timezone.utc).isoformat()),
    )
    con.commit(); con.close()


def get_last_weight(cat):
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT weight, recorded_at FROM weights WHERE cat = ? ORDER BY id DESC LIMIT 1",
        (cat,),
    ).fetchone()
    con.close()
    return row


def get_weight_history(cat, limit=10):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT weight, recorded_at FROM weights WHERE cat = ? "
        "ORDER BY id DESC LIMIT ?",
        (cat, limit),
    ).fetchall()
    con.close()
    return rows


def save_water_change(user_id, username):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO water_changes (user_id, username, changed_at) VALUES (?, ?, ?)",
        (user_id, username, datetime.now(timezone.utc).isoformat()),
    )
    con.commit(); con.close()


def get_last_water_change():
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT user_id, username, changed_at FROM water_changes "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    return row


def get_water_count(days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    con = sqlite3.connect(DB_PATH)
    n = con.execute(
        "SELECT COUNT(*) FROM water_changes WHERE changed_at >= ?", (cutoff,)
    ).fetchone()[0]
    con.close()
    return n


def get_water_total():
    con = sqlite3.connect(DB_PATH)
    n = con.execute("SELECT COUNT(*) FROM water_changes").fetchone()[0]
    con.close()
    return n


def get_water_history(limit=HISTORY_LIMIT):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT username, changed_at FROM water_changes ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    con.close()
    return rows


def save_litter_cleaning(user_id, username):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO litter_cleanings (user_id, username, cleaned_at) VALUES (?, ?, ?)",
        (user_id, username, datetime.now(timezone.utc).isoformat()),
    )
    con.commit(); con.close()


def get_last_litter_cleaning():
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT user_id, username, cleaned_at FROM litter_cleanings "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    return row


def get_litter_count(days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    con = sqlite3.connect(DB_PATH)
    n = con.execute(
        "SELECT COUNT(*) FROM litter_cleanings WHERE cleaned_at >= ?", (cutoff,)
    ).fetchone()[0]
    con.close()
    return n


def get_litter_total():
    con = sqlite3.connect(DB_PATH)
    n = con.execute("SELECT COUNT(*) FROM litter_cleanings").fetchone()[0]
    con.close()
    return n


def get_litter_history(limit=HISTORY_LIMIT):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT username, cleaned_at FROM litter_cleanings ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    con.close()
    return rows


def get_stats_by_day(days=STATS_DAYS):
    result = []
    today = datetime.now(TZ).date()
    con = sqlite3.connect(DB_PATH)
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        start_local = datetime(d.year, d.month, d.day, tzinfo=TZ)
        end_local = start_local + timedelta(days=1)
        start_utc = start_local.astimezone(timezone.utc).isoformat()
        end_utc = end_local.astimezone(timezone.utc).isoformat()
        count = con.execute(
            "SELECT COUNT(*) FROM feedings WHERE fed_at >= ? AND fed_at < ?",
            (start_utc, end_utc),
        ).fetchone()[0]
        result.append((d, count))
    con.close()
    return result


def get_stats_by_hour(days=STATS_DAYS):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT fed_at FROM feedings WHERE fed_at >= ?", (cutoff,)).fetchall()
    con.close()
    counts = [0] * 24
    for (iso,) in rows:
        dt = datetime.fromisoformat(iso).astimezone(TZ)
        counts[dt.hour] += 1
    return list(enumerate(counts))


def get_stats_by_user(days=None):
    con = sqlite3.connect(DB_PATH)
    q = """
        SELECT user_id,
               (SELECT username FROM feedings f2
                WHERE f2.user_id = f1.user_id ORDER BY id DESC LIMIT 1) AS name,
               COUNT(*) AS cnt
        FROM feedings f1
    """
    params = []
    if days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        q += " WHERE fed_at >= ?"
        params.append(cutoff)
    q += " GROUP BY user_id ORDER BY cnt DESC, name ASC"
    rows = con.execute(q, params).fetchall()
    con.close()
    return rows


def get_total_count(days=None):
    con = sqlite3.connect(DB_PATH)
    if days is None:
        n = con.execute("SELECT COUNT(*) FROM feedings").fetchone()[0]
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        n = con.execute(
            "SELECT COUNT(*) FROM feedings WHERE fed_at >= ?", (cutoff,)
        ).fetchone()[0]
    con.close()
    return n


# ---------------- ФОРМАТИРОВАНИЕ ----------------
def format_dt(iso):
    return datetime.fromisoformat(iso).astimezone(TZ).strftime(f"%d.%m.%Y %H:%M ({TZ_LABEL})")


def time_ago(iso):
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    sec = int((datetime.now(timezone.utc) - dt).total_seconds())
    if sec < 60:
        return "только что"
    mins = sec // 60
    if mins < 60:
        return f"{mins} мин назад"
    hours = mins // 60
    if hours < 24:
        return f"{hours} ч {mins % 60} мин назад"
    days = hours // 24
    return f"{days} д {hours % 24} ч назад"


def hours_since(iso):
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def human_date(iso):
    dt = datetime.fromisoformat(iso).astimezone(TZ)
    delta = (datetime.now(TZ).date() - dt.date()).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "вчера"
    if delta < 7:
        return f"{delta} дн. назад"
    return dt.strftime("%d.%m.%Y")


def human_dt(iso):
    dt = datetime.fromisoformat(iso).astimezone(TZ)
    delta = (datetime.now(TZ).date() - dt.date()).days
    hm = dt.strftime("%H:%M")
    if delta == 0:
        return f"сегодня {hm}"
    if delta == 1:
        return f"вчера {hm}"
    if delta < 7:
        return f"{delta} дн. назад {hm}"
    return dt.strftime("%d.%m.%Y %H:%M")


def weight_arrow(cur, prev):
    d = cur - prev
    if d > 0.05:
        return " ▲"
    if d < -0.05:
        return " ▼"
    return " →"


# ---------------- ЗАКРЕП ----------------
def get_known_chats():
    s = get_meta("known_chats") or ""
    return [int(x) for x in s.split(",") if x.strip()]


def add_known_chat(chat_id):
    chats = set(get_known_chats())
    chats.add(int(chat_id))
    set_meta("known_chats", ",".join(str(c) for c in sorted(chats)))


def build_pinned_text():
    lines = ["📌 <b>Сводка по котам</b>", ""]
    last = get_last_feeding()
    if not last:
        lines.append("🍽 Пока никто не кормил котов 😿")
    else:
        lines.append("🍽 <b>Последнее кормление:</b>")
        lines.append(f"   🕐 {format_dt(last['fed_at'])}")
        lines.append(f"   ⏳ {time_ago(last['fed_at'])}")
        lines.append(f"   👤 {last['username']}")
    lines.append("")
    lines.append("🏠 <b>Уход за домом:</b>")
    lw = get_last_water_change()
    ll = get_last_litter_cleaning()
    lines.append(f"   💧 Вода: {time_ago(lw[2])} ({lw[1]})" if lw else "   💧 Вода: нет данных")
    lines.append(f"   🧹 Лоток: {time_ago(ll[2])} ({ll[1]})" if ll else "   🧹 Лоток: нет данных")
    lines.append("")
    lines.append("⚖️ <b>Вес котов:</b>")
    for cat in CATS:
        row = get_last_weight(cat)
        if row:
            w, iso = row
            lines.append(f"   • <b>{cat}</b>: {w:.1f} кг <i>({human_date(iso)})</i>")
        else:
            lines.append(f"   • <b>{cat}</b>: нет данных")
    now_str = datetime.now(TZ).strftime("%H:%M:%S")
    lines.append("")
    lines.append(f"<i>🔄 обновлено в {now_str} ({TZ_LABEL})</i>")
    return "\n".join(lines)


async def update_pin_for_chat(context, chat_id, text):
    old = get_meta(f"pinned_msg_{chat_id}")
    if old:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=int(old), text=text, parse_mode="HTML"
            )
            return
        except Exception as e:
            err = str(e).lower()
            if "message is not modified" in err:
                return
            print(f"[pin] edit failed {chat_id}: {e}")
            # сообщение могло быть удалено/чат закрыт — забудем про него
            if any(x in err for x in (
                "message to edit not found",
                "message can't be edited",
                "message_id_invalid",
                "chat not found",
            )):
                set_meta(f"pinned_msg_{chat_id}", "")
            else:
                try:
                    await context.bot.unpin_chat_message(
                        chat_id=chat_id, message_id=int(old)
                    )
                except Exception:
                    pass

    try:
        msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        set_meta(f"pinned_msg_{chat_id}", msg.message_id)
        try:
            await context.bot.pin_chat_message(
                chat_id=chat_id, message_id=msg.message_id, disable_notification=True
            )
        except Exception as e:
            err = str(e).lower()
            print(f"[pin] pin failed {chat_id}: {e}")
            # Если нет прав — скажем в чат, чтобы человек понял, что делать
            if "not enough rights" in err or "chat_admin_required" in err:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="⚠️ Не могу закрепить сводку — сделайте бота админом с правом «Закреплять сообщения».",
                    )
                except Exception:
                    pass
    except Exception as e:
        print(f"[pin] send failed {chat_id}: {e}")


async def update_all_pins(context):
    text = build_pinned_text()
    for chat_id in get_known_chats():
        await update_pin_for_chat(context, chat_id, text)


async def pinned_refresh_job(context: ContextTypes.DEFAULT_TYPE):
    await update_all_pins(context)


# ---------------- КЛАВИАТУРЫ (ReplyKeyboard) ----------------
def main_reply_kb():
    return ReplyKeyboardMarkup(
        [[BTN_FEED], [BTN_OTHER, BTN_WEIGHT], [BTN_HISTORY]],
        resize_keyboard=True,
        input_field_placeholder="Выбери действие",
    )


def other_reply_kb():
    return ReplyKeyboardMarkup(
        [[BTN_WATER, BTN_LITTER], [BTN_BACK]],
        resize_keyboard=True,
        input_field_placeholder="Прочие дела",
    )


def weight_reply_kb():
    cat_row = list(BTN_WEIGHT_CAT.values())
    return ReplyKeyboardMarkup(
        [cat_row, [BTN_WEIGHT_HISTORY], [BTN_BACK]],
        resize_keyboard=True,
        input_field_placeholder="Вес котов",
    )


def history_settings_reply_kb():
    return ReplyKeyboardMarkup(
        [
            [BTN_LAST, BTN_FEED_HISTORY],
            [BTN_STATS, BTN_CHART],
            [BTN_WATER_HISTORY, BTN_LITTER_HISTORY],
            [BTN_REFRESH],
            [BTN_UNDO],
            [BTN_BACK],
        ],
        resize_keyboard=True,
        input_field_placeholder="История и настройки",
    )


# ---------------- INLINE (только для toggles и confirm) ----------------
def chart_inline_kb(period="days"):
    days = "✓ По дням" if period == "days" else "По дням"
    hours = "✓ По часам" if period == "hours" else "По часам"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(days, callback_data="chart_days"),
        InlineKeyboardButton(hours, callback_data="chart_hours"),
    ]])


def undo_inline_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, отменить", callback_data="undo_confirm"),
        InlineKeyboardButton("↩️ Нет", callback_data="undo_cancel"),
    ]])


# ---------------- СООБЩЕНИЯ ----------------
def render_bars(data, label_fn, width=CHART_BAR_WIDTH):
    mx = max((c for _, c in data), default=0) or 1
    rows = []
    for key, c in data:
        bar = "█" * int(round(c / mx * width))
        rows.append(f"{label_fn(key)}  {bar:<{width}} {c}")
    return rows


def build_chart_message(period="days"):
    if period == "hours":
        data = get_stats_by_hour(STATS_DAYS)
        title = f"📈 Кормления по часам за {STATS_DAYS} дн. ({TZ_LABEL})"
        label_fn = lambda h: f"{h:02d}:00"
    else:
        data = get_stats_by_day(STATS_DAYS)
        title = f"📈 Кормления по дням за {STATS_DAYS} дн."
        label_fn = lambda d: d.strftime("%d.%m")
    total = sum(c for _, c in data)
    rows = render_bars(data, label_fn)
    return f"<b>{title}</b>\n\n<pre>{chr(10).join(rows)}</pre>\n\nВсего: <b>{total}</b>"


def build_stats_message():
    by_day = get_stats_by_day(STATS_DAYS)
    by_user_all = get_stats_by_user(None)
    by_user_week = {uid: cnt for uid, _, cnt in get_stats_by_user(STATS_DAYS)}
    total_all = get_total_count(None)
    total_week = sum(c for _, c in by_day)
    water_week = get_water_count(STATS_DAYS)
    litter_week = get_litter_count(STATS_DAYS)
    last_w = get_last_water_change()
    last_l = get_last_litter_cleaning()

    lines = ["📊 <b>Статистика кормлений</b>", ""]
    if total_week:
        lines.append(f"За {STATS_DAYS} дн.: <b>{total_week}</b> (в среднем {total_week / STATS_DAYS:.1f}/день)")
    else:
        lines.append(f"За последние {STATS_DAYS} дней кормлений не было 😿")
    lines.append("")
    lines.append(f"🏆 <b>Кто сколько кормил</b> (всего / {STATS_DAYS} дн.):")
    if not by_user_all:
        lines.append("Пока никто не кормил 😿")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, name, cnt) in enumerate(by_user_all):
            prefix = medals[i] if i < len(medals) else "•"
            lines.append(f"{prefix} {name} — <b>{cnt}</b> ({by_user_week.get(uid, 0)})")
    lines.append("")
    lines.append(f"🍽 Всего кормлений за всё время: <b>{total_all}</b>")
    lines.append("")
    lines.append(f"🏠 <b>Уход за домом</b> (за {STATS_DAYS} дн.):")
    lines.append(
        f"💧 Вода — <b>{water_week}</b> раз (последний: {time_ago(last_w[2])}, {last_w[1]})"
        if last_w else "💧 Вода — пока не записывали"
    )
    lines.append(
        f"🧹 Лоток — <b>{litter_week}</b> раз (последний: {time_ago(last_l[2])}, {last_l[1]})"
        if last_l else "🧹 Лоток — пока не записывали"
    )
    return "\n".join(lines)


def build_weight_history_message():
    lines = ["⚖️ <b>История веса котов</b>"]
    for cat in CATS:
        hist = get_weight_history(cat, 10)
        lines.append("")
        if not hist:
            lines.append(f"🐱 <b>{cat}</b>: нет данных")
            continue
        w0, iso0 = hist[0]
        lines.append(f"🐱 <b>{cat}</b> — последний: <b>{w0:.1f} кг</b> ({human_date(iso0)})")
        for i, (w, iso) in enumerate(hist):
            arrow = weight_arrow(w, hist[i + 1][0]) if i + 1 < len(hist) else ""
            lines.append(f"   • {human_date(iso)} — {w:.1f} кг{arrow}")
    return "\n".join(lines)


def build_water_history_message():
    rows = get_water_history()
    lines = ["💧 <b>История смены воды</b>", ""]
    if not rows:
        return "\n".join(lines + ["Пока никто не менял воду 😿"])
    for name, iso in rows:
        lines.append(f"• {human_dt(iso)} — {name}")
    lines.append("")
    lines.append(f"Всего: <b>{get_water_total()}</b> раз, за {STATS_DAYS} дн.: <b>{get_water_count(STATS_DAYS)}</b>")
    if len(rows) >= 2:
        span = (datetime.fromisoformat(rows[0][1]) - datetime.fromisoformat(rows[-1][1])).total_seconds() / 3600
        if span > 0:
            lines.append(f"Средний интервал: ~{span / (len(rows) - 1):.1f} ч")
    return "\n".join(lines)


def build_litter_history_message():
    rows = get_litter_history()
    lines = ["🧹 <b>История уборки лотка</b>", ""]
    if not rows:
        return "\n".join(lines + ["Пока никто не убирал лоток 😿"])
    for name, iso in rows:
        lines.append(f"• {human_dt(iso)} — {name}")
    lines.append("")
    lines.append(f"Всего: <b>{get_litter_total()}</b> раз, за {STATS_DAYS} дн.: <b>{get_litter_count(STATS_DAYS)}</b>")
    if len(rows) >= 2:
        span = (datetime.fromisoformat(rows[0][1]) - datetime.fromisoformat(rows[-1][1])).total_seconds() / 3600
        if span > 0:
            lines.append(f"Средний интервал: ~{span / (len(rows) - 1):.1f} ч")
    return "\n".join(lines)


# ---------------- ВСПОМОГАТЕЛЬНОЕ ----------------
def get_recipients():
    r = set(ALLOWED_USERS)
    r.update(get_all_user_ids())
    return r


async def notify_partner(context, feeder_id, feeder_name, fed_at_iso):
    others = get_recipients() - {feeder_id}
    if not others:
        return
    text = (
        f"🐱 {feeder_name} покормил(а) котов!\n"
        f"🕐 {format_dt(fed_at_iso)}\n"
        f"⏳ {time_ago(fed_at_iso)}"
    )
    for uid in others:
        try:
            await context.bot.send_message(chat_id=uid, text=text, reply_markup=main_reply_kb())
        except Exception as e:
            print(f"[notify] {uid}: {e}")


# ---------------- НАПОМИНАНИЯ ----------------
async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    last = get_last_feeding()
    if not last:
        return
    hours = hours_since(last["fed_at"])
    if hours < REMINDER_HOURS:
        return
    reminded = get_meta("reminded_feeding_id")
    if reminded is not None and int(reminded) == last["id"]:
        return
    text = (
        f"⏰ Напоминание: котов не кормили уже {hours:.1f} ч!\n\n"
        f"🕐 Последний раз: {format_dt(last['fed_at'])} — {last['username']}\n"
        f"⏳ {time_ago(last['fed_at'])}"
    )
    sent = False
    for uid in get_recipients():
        try:
            await context.bot.send_message(chat_id=uid, text=text, reply_markup=main_reply_kb())
            sent = True
        except Exception as e:
            print(f"[reminder] {uid}: {e}")
    if sent:
        set_meta("reminded_feeding_id", last["id"])


async def care_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    lw = get_last_water_change()
    ref = lw[2] if lw else get_meta("bot_start")
    if ref:
        h = hours_since(ref)
        if h >= WATER_REMINDER_HOURS and get_meta("reminded_water_at") != ref:
            for uid in get_recipients():
                try:
                    await context.bot.send_message(
                        chat_id=uid, text=f"💧 Пора поменять воду! Прошло {h:.0f} ч.",
                        reply_markup=main_reply_kb(),
                    )
                except Exception:
                    pass
            set_meta("reminded_water_at", ref)

    ll = get_last_litter_cleaning()
    ref = ll[2] if ll else get_meta("bot_start")
    if ref:
        h = hours_since(ref)
        if h >= LITTER_REMINDER_HOURS and get_meta("reminded_litter_at") != ref:
            for uid in get_recipients():
                try:
                    await context.bot.send_message(
                        chat_id=uid, text=f"🧹 Пора убрать лоток! Прошло {h:.0f} ч.",
                        reply_markup=main_reply_kb(),
                    )
                except Exception:
                    pass
            set_meta("reminded_litter_at", ref)


# ---------------- ДЕЙСТВИЯ (общие для reply-кнопок) ----------------
async def do_feed(update, context):
    user = update.effective_user
    name = user.first_name or (user.username or "Кто-то")
    save_feeding(user.id, name)
    last = get_last_feeding()
    text = (
        "✅ Записано!\n\n"
        f"🕐 {format_dt(last['fed_at'])}\n"
        f"🍽 Кормил(а): {last['username']}\n"
        f"⏳ {time_ago(last['fed_at'])}"
    )
    await update.message.reply_text(text, reply_markup=main_reply_kb())
    await notify_partner(context, user.id, last["username"], last["fed_at"])
    await update_all_pins(context)


async def do_water(update, context):
    user = update.effective_user
    name = user.first_name or (user.username or "Кто-то")
    prev = get_last_water_change()
    save_water_change(user.id, name)
    text = f"✅ 💧 Вода поменяна, <b>{name}</b>!"
    if prev:
        text += f"\nПредыдущий раз: {time_ago(prev[2])} ({prev[1]})"
    await update.message.reply_text(text, reply_markup=other_reply_kb(), parse_mode="HTML")
    await update_all_pins(context)


async def do_litter(update, context):
    user = update.effective_user
    name = user.first_name or (user.username or "Кто-то")
    prev = get_last_litter_cleaning()
    save_litter_cleaning(user.id, name)
    text = f"✅ 🧹 Лоток убран, <b>{name}</b>!"
    if prev:
        text += f"\nПредыдущий раз: {time_ago(prev[2])} ({prev[1]})"
    await update.message.reply_text(text, reply_markup=other_reply_kb(), parse_mode="HTML")
    await update_all_pins(context)


async def do_last(update, context):
    last = get_last_feeding()
    if not last:
        text = "Пока никто не кормил котов 😿"
    else:
        text = (
            "🕐 Последнее кормление:\n\n"
            f"{format_dt(last['fed_at'])}\n"
            f"🍽 Кормил(а): {last['username']}\n"
            f"⏳ {time_ago(last['fed_at'])}"
        )
    await update.message.reply_text(text, reply_markup=history_settings_reply_kb())


async def do_feed_history(update, context):
    rows = get_history(15)
    if not rows:
        text = "История пуста 😿"
    else:
        lines = ["📜 Последние 15 кормлений:\n"]
        for r in rows:
            lines.append(f"• {format_dt(r['fed_at'])} — {r['username']} ({time_ago(r['fed_at'])})")
        text = "\n".join(lines)
    await update.message.reply_text(text, reply_markup=history_settings_reply_kb())


async def do_stats(update, context):
    await update.message.reply_text(
        build_stats_message(),
        reply_markup=history_settings_reply_kb(),
        parse_mode="HTML",
    )


async def do_chart(update, context):
    await update.message.reply_text(
        build_chart_message("days"),
        reply_markup=chart_inline_kb("days"),
        parse_mode="HTML",
    )


async def do_water_history(update, context):
    await update.message.reply_text(
        build_water_history_message(),
        reply_markup=history_settings_reply_kb(),
        parse_mode="HTML",
    )


async def do_litter_history(update, context):
    await update.message.reply_text(
        build_litter_history_message(),
        reply_markup=history_settings_reply_kb(),
        parse_mode="HTML",
    )


async def do_refresh(update, context):
    await update_all_pins(context)
    await update.message.reply_text("🔄 Сводка обновлена ✅", reply_markup=history_settings_reply_kb())


async def do_undo_prompt(update, context):
    last = get_last_feeding()
    if not last:
        await update.message.reply_text(
            "Нечего отменять — история пуста.",
            reply_markup=history_settings_reply_kb(),
        )
        return
    await update.message.reply_text(
        f"❌ Отменить это кормление?\n\n"
        f"🕐 {format_dt(last['fed_at'])}\n🍽 {last['username']}",
        reply_markup=undo_inline_kb(),
    )


async def do_weight_history(update, context):
    await update.message.reply_text(
        build_weight_history_message(),
        reply_markup=weight_reply_kb(),
        parse_mode="HTML",
    )


async def do_weight_set(update, context, cat):
    context.user_data["awaiting_weight_cat"] = cat
    last = get_last_weight(cat)
    hint = ""
    if last:
        hint = f"\n\n<i>Прошлый вес: {last[0]:.1f} кг ({human_date(last[1])})</i>"
    await update.message.reply_text(
        f"⚖️ Введи вес для <b>{cat}</b> в кг (например: 4.2){hint}",
        parse_mode="HTML",
        reply_markup=weight_reply_kb(),
    )


async def show_main(update, context):
    await update.message.reply_text(MENU_TEXT, reply_markup=main_reply_kb(), parse_mode="HTML")


async def show_other(update, context):
    await update.message.reply_text(OTHER_TEXT, reply_markup=other_reply_kb(), parse_mode="HTML")


async def show_weight(update, context):
    await update.message.reply_text(WEIGHT_TEXT, reply_markup=weight_reply_kb(), parse_mode="HTML")


async def show_history_settings(update, context):
    await update.message.reply_text(
        HISTORY_TEXT, reply_markup=history_settings_reply_kb(), parse_mode="HTML"
    )


# ---------------- КАРТА КНОПОК ----------------
async def _dispatch_by_text(update, context, text):
    if text == BTN_FEED:
        await do_feed(update, context)
    elif text == BTN_OTHER:
        await show_other(update, context)
    elif text == BTN_WEIGHT:
        await show_weight(update, context)
    elif text == BTN_HISTORY:
        await show_history_settings(update, context)
    elif text == BTN_BACK:
        await show_main(update, context)
    elif text == BTN_WATER:
        await do_water(update, context)
    elif text == BTN_LITTER:
        await do_litter(update, context)
    elif text == BTN_LAST:
        await do_last(update, context)
    elif text == BTN_FEED_HISTORY:
        await do_feed_history(update, context)
    elif text == BTN_STATS:
        await do_stats(update, context)
    elif text == BTN_CHART:
        await do_chart(update, context)
    elif text == BTN_WATER_HISTORY:
        await do_water_history(update, context)
    elif text == BTN_LITTER_HISTORY:
        await do_litter_history(update, context)
    elif text == BTN_REFRESH:
        await do_refresh(update, context)
    elif text == BTN_UNDO:
        await do_undo_prompt(update, context)
    elif text == BTN_WEIGHT_HISTORY:
        await do_weight_history(update, context)
    elif text in BTN_WEIGHT_CAT.values():
        for cat, btn in BTN_WEIGHT_CAT.items():
            if btn == text:
                await do_weight_set(update, context, cat)
                return


ALL_BUTTON_TEXTS = {
    BTN_FEED, BTN_OTHER, BTN_WEIGHT, BTN_HISTORY,
    BTN_WATER, BTN_LITTER, BTN_BACK,
    BTN_LAST, BTN_FEED_HISTORY, BTN_STATS, BTN_CHART,
    BTN_WATER_HISTORY, BTN_LITTER_HISTORY, BTN_REFRESH, BTN_UNDO,
    BTN_WEIGHT_HISTORY,
    *BTN_WEIGHT_CAT.values(),
}


# ---------------- ХЕНДЛЕРЫ ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_known_chat(update.effective_chat.id)
    await show_main(update, context)
    await update_pin_for_chat(context, update.effective_chat.id, build_pinned_text())


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.message.reply_text(
        f"Твой user_id: <code>{u.id}</code>\n"
        f"Имя: {u.full_name}\n"
        f"Username: @{u.username if u.username else '—'}",
        parse_mode="HTML",
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # 1. Известная кнопка — сбрасываем «ввод веса» и обрабатываем
    if text in ALL_BUTTON_TEXTS:
        context.user_data.pop("awaiting_weight_cat", None)
        await _dispatch_by_text(update, context, text)
        return

    # 2. Ввод веса, если ждём
    cat = context.user_data.get("awaiting_weight_cat")
    if cat:
        try:
            weight = float(text.replace(",", "."))
            if not (0.1 <= weight <= 30):
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Не понял. Пришли число в кг, например: 4.2",
                reply_markup=weight_reply_kb(),
            )
            return
        user = update.effective_user
        name = user.first_name or (user.username or "Кто-то")
        save_weight(cat, user.id, name, weight)
        context.user_data.pop("awaiting_weight_cat", None)

        hist = get_weight_history(cat, 5)
        lines = [f"✅ Записано: <b>{cat}</b> — <b>{weight:.1f} кг</b>", "", f"📜 История {cat}:"]
        for i, (w, iso) in enumerate(hist):
            arrow = weight_arrow(w, hist[i + 1][0]) if i + 1 < len(hist) else ""
            lines.append(f"   • {human_date(iso)} — {w:.1f} кг{arrow}")

        await update.message.reply_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=weight_reply_kb()
        )
        await update_all_pins(context)
        return

    # 3. Неизвестный ввод
    await update.message.reply_text(
        "Используй кнопки меню ниже 👇", reply_markup=main_reply_kb()
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    if ALLOWED_USERS and user.id not in ALLOWED_USERS:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    data = query.data
    await query.answer()

    # График: переключение периода (inline на конкретном сообщении)
    if data in ("chart_days", "chart_hours"):
        period = "hours" if data == "chart_hours" else "days"
        try:
            await query.edit_message_text(
                build_chart_message(period),
                parse_mode="HTML",
                reply_markup=chart_inline_kb(period),
            )
        except Exception:
            pass
        return

    # Подтверждение отмены
    if data == "undo_cancel":
        try:
            await query.edit_message_text(
                "↩️ Отмена отменена. Ничего не удалено.",
                reply_markup=None,
            )
        except Exception:
            pass
        return

    if data == "undo_confirm":
        deleted = delete_last_feeding()
        if not deleted:
            text = "❌ Нечего отменять — история пуста."
        else:
            text = f"❌ Удалено кормление:\n🕐 {format_dt(deleted['fed_at'])}\n🍽 {deleted['username']}"
            if deleted["user_id"] != user.id:
                try:
                    await context.bot.send_message(
                        chat_id=deleted["user_id"],
                        text=(
                            f"ℹ️ {user.first_name} отменил(а) твою запись о кормлении\n"
                            f"🕐 {format_dt(deleted['fed_at'])}"
                        ),
                    )
                except Exception:
                    pass
        try:
            await query.edit_message_text(text, reply_markup=None)
        except Exception:
            pass
        await update_all_pins(context)
        return


# ---------------- ЗАПУСК ----------------
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", whoami))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    if app.job_queue is not None:
        app.job_queue.run_repeating(reminder_job, interval=REMINDER_CHECK_MIN * 60, first=60)
        app.job_queue.run_repeating(care_reminder_job, interval=REMINDER_CHECK_MIN * 60, first=120)
        app.job_queue.run_repeating(pinned_refresh_job, interval=PIN_REFRESH_SEC, first=20)
    else:
        print('⚠️ JobQueue недоступен — установи: pip install "python-telegram-bot[job-queue]"')

    print("Бот запущен. Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
