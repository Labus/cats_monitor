import os
import sqlite3
from datetime import datetime, timezone, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)

# Подхватываем .env, если установлен python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # работаем без .env — только на реальных переменных окружения

# ======================== НАСТРОЙКИ ========================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "Не задана переменная окружения BOT_TOKEN.\n"
        "Установи её так:\n"
        "  export BOT_TOKEN='123456:ABC...'      (Linux/macOS)\n"
        "  set BOT_TOKEN=123456:ABC...           (Windows CMD)\n"
        "  $env:BOT_TOKEN='123456:ABC...'        (PowerShell)\n"
        "или создай файл .env рядом с bot.py со строкой:\n"
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
HISTORY_LIMIT = 15        # сколько записей воды/лотка показывать

CATS = ["Гафи", "Луна"]
PIN_REFRESH_MIN = 10
# ==========================================================

MENU_TEXT = (
    "🐾 Бот учёта кормления котов.\n\n"
    "Нажми кнопку, когда покормишь котов — я запомню время, "
    "уведомлю второго человека, напомню, если долго не кормили, "
    "покажу статистику, график и помогу вести вес каждого кота."
)


# ---------------- БАЗА ДАННЫХ ----------------
def init_db() -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS feedings (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            username TEXT,
            fed_at   TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS weights (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cat         TEXT NOT NULL,
            user_id     INTEGER NOT NULL,
            username    TEXT,
            weight      REAL NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS water_changes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            username   TEXT,
            changed_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS litter_cleanings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            username   TEXT,
            cleaned_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    row = con.execute(
        "SELECT value FROM meta WHERE key = 'bot_start'"
    ).fetchone()
    if not row:
        con.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            ("bot_start", datetime.now(timezone.utc).isoformat()),
        )
    con.commit()
    con.close()


def save_feeding(user_id: int, username: str) -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO feedings (user_id, username, fed_at) VALUES (?, ?, ?)",
        (user_id, username, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
    con.close()


def _row_to_dict(row):
    if not row:
        return None
    return {"id": row[0], "user_id": row[1], "username": row[2], "fed_at": row[3]}


def get_last_feeding():
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id, user_id, username, fed_at FROM feedings ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    return _row_to_dict(row)


def get_history(limit: int = 15):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT id, user_id, username, fed_at FROM feedings ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    con.close()
    return [_row_to_dict(r) for r in rows]


def delete_last_feeding():
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id, user_id, username, fed_at FROM feedings ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row:
        con.execute("DELETE FROM feedings WHERE id = ?", (row[0],))
        con.commit()
    con.close()
    return _row_to_dict(row)


def get_all_user_ids():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT DISTINCT user_id FROM feedings").fetchall()
    con.close()
    return [r[0] for r in rows]


def get_meta(key: str):
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    con.close()
    return row[0] if row else None


def set_meta(key: str, value) -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value))
    )
    con.commit()
    con.close()


# ---------------- ВЕС ----------------
def save_weight(cat: str, user_id: int, username: str, weight: float) -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO weights (cat, user_id, username, weight, recorded_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (cat, user_id, username, float(weight),
         datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
    con.close()


def get_last_weight(cat: str):
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT weight, recorded_at FROM weights WHERE cat = ? "
        "ORDER BY id DESC LIMIT 1",
        (cat,),
    ).fetchone()
    con.close()
    return row


def get_weight_history(cat: str, limit: int = 10):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT weight, recorded_at FROM weights WHERE cat = ? "
        "ORDER BY id DESC LIMIT ?",
        (cat, limit),
    ).fetchall()
    con.close()
    return rows


# ---------------- ВОДА ----------------
def save_water_change(user_id: int, username: str) -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO water_changes (user_id, username, changed_at) "
        "VALUES (?, ?, ?)",
        (user_id, username, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
    con.close()


def get_last_water_change():
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT user_id, username, changed_at FROM water_changes "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    return row


def get_water_count(days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    con = sqlite3.connect(DB_PATH)
    n = con.execute(
        "SELECT COUNT(*) FROM water_changes WHERE changed_at >= ?", (cutoff,)
    ).fetchone()[0]
    con.close()
    return n


def get_water_total() -> int:
    con = sqlite3.connect(DB_PATH)
    n = con.execute("SELECT COUNT(*) FROM water_changes").fetchone()[0]
    con.close()
    return n


def get_water_history(limit: int = HISTORY_LIMIT):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT username, changed_at FROM water_changes "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    con.close()
    return rows


# ---------------- ЛОТОК ----------------
def save_litter_cleaning(user_id: int, username: str) -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO litter_cleanings (user_id, username, cleaned_at) "
        "VALUES (?, ?, ?)",
        (user_id, username, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
    con.close()


def get_last_litter_cleaning():
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT user_id, username, cleaned_at FROM litter_cleanings "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    return row


def get_litter_count(days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    con = sqlite3.connect(DB_PATH)
    n = con.execute(
        "SELECT COUNT(*) FROM litter_cleanings WHERE cleaned_at >= ?", (cutoff,)
    ).fetchone()[0]
    con.close()
    return n


def get_litter_total() -> int:
    con = sqlite3.connect(DB_PATH)
    n = con.execute("SELECT COUNT(*) FROM litter_cleanings").fetchone()[0]
    con.close()
    return n


def get_litter_history(limit: int = HISTORY_LIMIT):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT username, cleaned_at FROM litter_cleanings "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    con.close()
    return rows


# ---------------- СТАТИСТИКА ----------------
def get_stats_by_day(days: int = STATS_DAYS):
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


def get_stats_by_hour(days: int = STATS_DAYS):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT fed_at FROM feedings WHERE fed_at >= ?", (cutoff,)
    ).fetchall()
    con.close()
    counts = [0] * 24
    for (iso,) in rows:
        dt = datetime.fromisoformat(iso).astimezone(TZ)
        counts[dt.hour] += 1
    return list(enumerate(counts))


def get_stats_by_user(days=None):
    con = sqlite3.connect(DB_PATH)
    query = """
        SELECT user_id,
               (SELECT username FROM feedings f2
                WHERE f2.user_id = f1.user_id ORDER BY id DESC LIMIT 1) AS name,
               COUNT(*) AS cnt
        FROM feedings f1
    """
    params = []
    if days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query += " WHERE fed_at >= ?"
        params.append(cutoff)
    query += " GROUP BY user_id ORDER BY cnt DESC, name ASC"
    rows = con.execute(query, params).fetchall()
    con.close()
    return rows


def get_total_count(days=None) -> int:
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
def format_dt(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str).astimezone(TZ)
    return dt.strftime(f"%d.%m.%Y %H:%M ({TZ_LABEL})")


def time_ago(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    sec = int((datetime.now(timezone.utc) - dt).total_seconds())
    if sec < 60:
        return f"{sec} сек назад"
    mins = sec // 60
    if mins < 60:
        return f"{mins} мин назад"
    hours = mins // 60
    if hours < 24:
        return f"{hours} ч {mins % 60} мин назад"
    days = hours // 24
    return f"{days} д {hours % 24} ч назад"


def hours_since(iso_str: str) -> float:
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def human_date(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str).astimezone(TZ)
    delta = (datetime.now(TZ).date() - dt.date()).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "вчера"
    if delta < 7:
        return f"{delta} дн. назад"
    return dt.strftime("%d.%m.%Y")


def human_dt(iso_str: str) -> str:
    """'сегодня 14:30', 'вчера 09:15', '3 дн. назад 20:00', '12.10.2026 20:00'."""
    dt = datetime.fromisoformat(iso_str).astimezone(TZ)
    delta = (datetime.now(TZ).date() - dt.date()).days
    hm = dt.strftime("%H:%M")
    if delta == 0:
        return f"сегодня {hm}"
    if delta == 1:
        return f"вчера {hm}"
    if delta < 7:
        return f"{delta} дн. назад {hm}"
    return dt.strftime("%d.%m.%Y %H:%M")


def weight_arrow(cur: float, prev: float) -> str:
    diff = cur - prev
    if diff > 0.05:
        return " ▲"
    if diff < -0.05:
        return " ▼"
    return " →"


# ---------------- ЗАКРЕП ----------------
def get_known_chats() -> list:
    s = get_meta("known_chats") or ""
    return [int(x) for x in s.split(",") if x.strip()]


def add_known_chat(chat_id: int) -> None:
    chats = set(get_known_chats())
    chats.add(int(chat_id))
    set_meta("known_chats", ",".join(str(c) for c in sorted(chats)))


def build_pinned_text() -> str:
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
    if lw:
        lines.append(f"   💧 Вода: {time_ago(lw[2])} ({lw[1]})")
    else:
        lines.append("   💧 Вода: нет данных")
    ll = get_last_litter_cleaning()
    if ll:
        lines.append(f"   🧹 Лоток: {time_ago(ll[2])} ({ll[1]})")
    else:
        lines.append("   🧹 Лоток: нет данных")

    lines.append("")
    lines.append("⚖️ <b>Вес котов:</b>")
    for cat in CATS:
        row = get_last_weight(cat)
        if row:
            w, iso = row
            lines.append(f"   • <b>{cat}</b>: {w:.1f} кг <i>({human_date(iso)})</i>")
        else:
            lines.append(f"   • <b>{cat}</b>: нет данных")

    return "\n".join(lines)


async def update_pin_for_chat(context, chat_id: int, text: str) -> None:
    old_id_str = get_meta(f"pinned_msg_{chat_id}")
    if old_id_str:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=int(old_id_str),
                text=text, parse_mode="HTML",
            )
            return
        except Exception as e:
            err = str(e).lower()
            if "message is not modified" in err:
                return
            print(f"[pin] edit failed {chat_id}: {e}")
            try:
                await context.bot.unpin_chat_message(
                    chat_id=chat_id, message_id=int(old_id_str)
                )
            except Exception:
                pass

    try:
        msg = await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="HTML"
        )
        set_meta(f"pinned_msg_{chat_id}", msg.message_id)
        try:
            await context.bot.pin_chat_message(
                chat_id=chat_id, message_id=msg.message_id,
                disable_notification=True,
            )
        except Exception as e:
            print(f"[pin] pin failed {chat_id}: {e} (бот должен быть админом в группе)")
    except Exception as e:
        print(f"[pin] send failed {chat_id}: {e}")


async def update_all_pins(context) -> None:
    text = build_pinned_text()
    for chat_id in get_known_chats():
        await update_pin_for_chat(context, chat_id, text)


async def pinned_refresh_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await update_all_pins(context)


# ---------------- КЛАВИАТУРЫ ----------------
def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🐱 Покормил котов", callback_data="feed")],
            [InlineKeyboardButton("🕐 Последнее", callback_data="last"),
             InlineKeyboardButton("📜 История", callback_data="history")],
            [InlineKeyboardButton("📊 Статистика", callback_data="stats"),
             InlineKeyboardButton("📈 График", callback_data="chart")],
            [InlineKeyboardButton("⚖️ Вес котов", callback_data="weight")],
            [InlineKeyboardButton("💧 Поменял воду", callback_data="water"),
             InlineKeyboardButton("🧹 Убрал лоток", callback_data="litter")],
            [InlineKeyboardButton("📋 История воды", callback_data="water_history"),
             InlineKeyboardButton("📋 История лотка", callback_data="litter_history")],
            [InlineKeyboardButton("❌ Отменить последнее", callback_data="undo")],
        ]
    )


def confirm_undo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Да, отменить", callback_data="undo_confirm")],
            [InlineKeyboardButton("↩️ Нет, назад", callback_data="undo_cancel")],
        ]
    )


def chart_keyboard(period: str = "days") -> InlineKeyboardMarkup:
    days_label = "✓ По дням" if period == "days" else "По дням"
    hours_label = "✓ По часам" if period == "hours" else "По часам"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(days_label, callback_data="chart_days"),
             InlineKeyboardButton(hours_label, callback_data="chart_hours")],
            [InlineKeyboardButton("🔙 В меню", callback_data="back_to_menu")],
        ]
    )


def weight_menu_keyboard() -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(f"⚖️ {cat}", callback_data=f"weight_set_{cat}")
        for cat in CATS
    ]
    return InlineKeyboardMarkup(
        [
            row,
            [InlineKeyboardButton("📊 История веса", callback_data="weight_history")],
            [InlineKeyboardButton("🔙 В меню", callback_data="back_to_menu")],
        ]
    )


def weight_history_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⚖️ Записать вес", callback_data="weight")],
            [InlineKeyboardButton("🔙 В меню", callback_data="back_to_menu")],
        ]
    )


def care_history_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💧 Поменял воду", callback_data="water"),
             InlineKeyboardButton("🧹 Убрал лоток", callback_data="litter")],
            [InlineKeyboardButton("🔙 В меню", callback_data="back_to_menu")],
        ]
    )


# ---------------- СБОРКА СООБЩЕНИЙ ----------------
def render_bars(data, label_fn, width: int = CHART_BAR_WIDTH):
    max_val = max((c for _, c in data), default=0) or 1
    rows = []
    for key, c in data:
        bar = "█" * int(round(c / max_val * width))
        rows.append(f"{label_fn(key)}  {bar:<{width}} {c}")
    return rows


def build_chart_message(period: str = "days") -> str:
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
    return (
        f"<b>{title}</b>\n\n"
        f"<pre>{chr(10).join(rows)}</pre>\n\n"
        f"Всего: <b>{total}</b>"
    )


def build_stats_message() -> str:
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
        avg = total_week / STATS_DAYS
        lines.append(f"За {STATS_DAYS} дн.: <b>{total_week}</b> "
                     f"(в среднем {avg:.1f}/день)")
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
            week_cnt = by_user_week.get(uid, 0)
            lines.append(f"{prefix} {name} — <b>{cnt}</b> ({week_cnt})")

    lines.append("")
    lines.append(f"🍽 Всего кормлений за всё время: <b>{total_all}</b>")

    lines.append("")
    lines.append(f"🏠 <b>Уход за домом</b> (за {STATS_DAYS} дн.):")
    if last_w:
        lines.append(
            f"💧 Вода — <b>{water_week}</b> раз "
            f"(последний: {time_ago(last_w[2])}, {last_w[1]})"
        )
    else:
        lines.append("💧 Вода — пока не записывали")
    if last_l:
        lines.append(
            f"🧹 Лоток — <b>{litter_week}</b> раз "
            f"(последний: {time_ago(last_l[2])}, {last_l[1]})"
        )
    else:
        lines.append("🧹 Лоток — пока не записывали")

    return "\n".join(lines)


def build_weight_history_message() -> str:
    lines = ["⚖️ <b>История веса котов</b>"]
    for cat in CATS:
        hist = get_weight_history(cat, 10)
        lines.append("")
        if not hist:
            lines.append(f"🐱 <b>{cat}</b>: нет данных")
            continue
        w0, iso0 = hist[0]
        lines.append(
            f"🐱 <b>{cat}</b> — последний: <b>{w0:.1f} кг</b> ({human_date(iso0)})"
        )
        for i, (w, iso) in enumerate(hist):
            arrow = ""
            if i + 1 < len(hist):
                arrow = weight_arrow(w, hist[i + 1][0])
            lines.append(f"   • {human_date(iso)} — {w:.1f} кг{arrow}")
    return "\n".join(lines)


def build_water_history_message() -> str:
    rows = get_water_history(HISTORY_LIMIT)
    lines = ["💧 <b>История смены воды</b>", ""]
    if not rows:
        lines.append("Пока никто не менял воду 😿")
        return "\n".join(lines)

    for name, iso in rows:
        lines.append(f"• {human_dt(iso)} — {name}")

    total = get_water_total()
    week = get_water_count(STATS_DAYS)
    lines.append("")
    lines.append(
        f"Всего: <b>{total}</b> раз, за {STATS_DAYS} дн.: <b>{week}</b>"
    )
    if len(rows) >= 2:
        # средний интервал по показанным записям
        newest = datetime.fromisoformat(rows[0][1])
        oldest = datetime.fromisoformat(rows[-1][1])
        span = (newest - oldest).total_seconds() / 3600
        if span > 0:
            avg = span / (len(rows) - 1)
            lines.append(f"Средний интервал: ~{avg:.1f} ч")
    return "\n".join(lines)


def build_litter_history_message() -> str:
    rows = get_litter_history(HISTORY_LIMIT)
    lines = ["🧹 <b>История уборки лотка</b>", ""]
    if not rows:
        lines.append("Пока никто не убирал лоток 😿")
        return "\n".join(lines)

    for name, iso in rows:
        lines.append(f"• {human_dt(iso)} — {name}")

    total = get_litter_total()
    week = get_litter_count(STATS_DAYS)
    lines.append("")
    lines.append(
        f"Всего: <b>{total}</b> раз, за {STATS_DAYS} дн.: <b>{week}</b>"
    )
    if len(rows) >= 2:
        newest = datetime.fromisoformat(rows[0][1])
        oldest = datetime.fromisoformat(rows[-1][1])
        span = (newest - oldest).total_seconds() / 3600
        if span > 0:
            avg = span / (len(rows) - 1)
            lines.append(f"Средний интервал: ~{avg:.1f} ч")
    return "\n".join(lines)


# ---------------- ВСПОМОГАТЕЛЬНОЕ ----------------
def get_recipients() -> set:
    r = set(ALLOWED_USERS)
    r.update(get_all_user_ids())
    return r


async def notify_partner(context, feeder_id: int, feeder_name: str, fed_at_iso: str):
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
            await context.bot.send_message(
                chat_id=uid, text=text, reply_markup=main_keyboard()
            )
        except Exception as e:
            print(f"[notify] не удалось отправить {uid}: {e}")


# ---------------- НАПОМИНАНИЯ ----------------
async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    last = get_last_feeding()
    if last:
        hours = hours_since(last["fed_at"])
        if hours >= REMINDER_HOURS:
            reminded = get_meta("reminded_feeding_id")
            if not (reminded is not None and int(reminded) == last["id"]):
                text = (
                    f"⏰ Напоминание: котов не кормили уже {hours:.1f} ч!\n\n"
                    f"🕐 Последний раз: {format_dt(last['fed_at'])} — "
                    f"{last['username']}\n"
                    f"⏳ {time_ago(last['fed_at'])}"
                )
                sent = False
                for uid in get_recipients():
                    try:
                        await context.bot.send_message(
                            chat_id=uid, text=text, reply_markup=main_keyboard()
                        )
                        sent = True
                    except Exception as e:
                        print(f"[reminder] не удалось отправить {uid}: {e}")
                if sent:
                    set_meta("reminded_feeding_id", last["id"])


async def care_reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    last_w = get_last_water_change()
    ref_w = last_w[2] if last_w else get_meta("bot_start")
    if ref_w:
        h = hours_since(ref_w)
        if h >= WATER_REMINDER_HOURS and get_meta("reminded_water_at") != ref_w:
            text = f"💧 Пора поменять воду! Прошло {h:.0f} ч."
            for uid in get_recipients():
                try:
                    await context.bot.send_message(
                        chat_id=uid, text=text, reply_markup=main_keyboard()
                    )
                except Exception:
                    pass
            set_meta("reminded_water_at", ref_w)

    last_l = get_last_litter_cleaning()
    ref_l = last_l[2] if last_l else get_meta("bot_start")
    if ref_l:
        h = hours_since(ref_l)
        if h >= LITTER_REMINDER_HOURS and get_meta("reminded_litter_at") != ref_l:
            text = f"🧹 Пора убрать лоток! Прошло {h:.0f} ч."
            for uid in get_recipients():
                try:
                    await context.bot.send_message(
                        chat_id=uid, text=text, reply_markup=main_keyboard()
                    )
                except Exception:
                    pass
            set_meta("reminded_litter_at", ref_l)


# ---------------- ХЕНДЛЕРЫ ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    add_known_chat(update.effective_chat.id)
    await update.message.reply_text(
        MENU_TEXT, reply_markup=main_keyboard(), parse_mode="HTML"
    )
    await update_pin_for_chat(
        context, update.effective_chat.id, build_pinned_text()
    )


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await update.message.reply_text(
        f"Твой user_id: <code>{u.id}</code>\n"
        f"Имя: {u.full_name}\n"
        f"Username: @{u.username if u.username else '—'}",
        parse_mode="HTML",
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cat = context.user_data.get("awaiting_weight_cat")
    if not cat:
        return

    text = update.message.text.strip().replace(",", ".")
    try:
        weight = float(text)
        if not (0.1 <= weight <= 30):
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Не понял. Пришли число в кг, например: 4.2"
        )
        return

    user = update.effective_user
    name = user.first_name or (user.username or "Кто-то")
    save_weight(cat, user.id, name, weight)
    context.user_data.pop("awaiting_weight_cat", None)

    hist = get_weight_history(cat, 5)
    lines = [
        f"✅ Записано: <b>{cat}</b> — <b>{weight:.1f} кг</b>",
        "",
        f"📜 История {cat}:",
    ]
    for i, (w, iso) in enumerate(hist):
        arrow = ""
        if i + 1 < len(hist):
            arrow = weight_arrow(w, hist[i + 1][0])
        lines.append(f"   • {human_date(iso)} — {w:.1f} кг{arrow}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    await update_all_pins(context)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = query.from_user

    if ALLOWED_USERS and user.id not in ALLOWED_USERS:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    context.user_data.pop("awaiting_weight_cat", None)

    data = query.data
    await query.answer()

    # ---------- ГРАФИК ----------
    if data in ("chart", "chart_days", "chart_hours"):
        period = "hours" if data == "chart_hours" else "days"
        text = build_chart_message(period)
        kb = chart_keyboard(period)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    # ---------- ВЕС ----------
    if data == "weight":
        text = "⚖️ <b>Вес котов</b>\n\nВыбери кота, чтобы записать текущий вес:"
        try:
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=weight_menu_keyboard()
            )
        except Exception:
            await query.message.reply_text(
                text, parse_mode="HTML", reply_markup=weight_menu_keyboard()
            )
        return

    if data.startswith("weight_set_"):
        cat = data[len("weight_set_"):]
        if cat not in CATS:
            await query.answer("Неизвестный кот", show_alert=True)
            return
        context.user_data["awaiting_weight_cat"] = cat
        last = get_last_weight(cat)
        hint = ""
        if last:
            hint = (
                f"\n\n<i>Прошлый вес: {last[0]:.1f} кг "
                f"({human_date(last[1])})</i>"
            )
        await query.message.reply_text(
            f"⚖️ Введи вес для <b>{cat}</b> в кг (например: 4.2){hint}",
            parse_mode="HTML",
            reply_markup=ForceReply(selective=True),
        )
        return

    if data == "weight_history":
        try:
            await query.edit_message_text(
                build_weight_history_message(),
                parse_mode="HTML",
                reply_markup=weight_history_keyboard(),
            )
        except Exception:
            await query.message.reply_text(
                build_weight_history_message(),
                parse_mode="HTML",
                reply_markup=weight_history_keyboard(),
            )
        return

    # ---------- ВОДА / ЛОТОК ----------
    if data == "water":
        name = user.first_name or (user.username or "Кто-то")
        prev = get_last_water_change()
        save_water_change(user.id, name)
        text = f"✅ 💧 Вода поменяна, <b>{name}</b>!"
        if prev:
            text += f"\nПредыдущий раз: {time_ago(prev[2])} ({prev[1]})"
        await query.message.reply_text(
            text, parse_mode="HTML", reply_markup=main_keyboard()
        )
        await update_all_pins(context)
        return

    if data == "litter":
        name = user.first_name or (user.username or "Кто-то")
        prev = get_last_litter_cleaning()
        save_litter_cleaning(user.id, name)
        text = f"✅ 🧹 Лоток убран, <b>{name}</b>!"
        if prev:
            text += f"\nПредыдущий раз: {time_ago(prev[2])} ({prev[1]})"
        await query.message.reply_text(
            text, parse_mode="HTML", reply_markup=main_keyboard()
        )
        await update_all_pins(context)
        return

    if data == "water_history":
        text = build_water_history_message()
        kb = care_history_keyboard()
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    if data == "litter_history":
        text = build_litter_history_message()
        kb = care_history_keyboard()
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    # ---------- ОТМЕНА ----------
    if data == "undo":
        last = get_last_feeding()
        if not last:
            await query.message.reply_text(
                "Нечего отменять — история пуста.", reply_markup=main_keyboard()
            )
            return
        await query.edit_message_reply_markup(reply_markup=confirm_undo_keyboard())
        return

    if data == "undo_cancel":
        await query.edit_message_reply_markup(reply_markup=main_keyboard())
        return

    if data == "undo_confirm":
        deleted = delete_last_feeding()
        if not deleted:
            text = "❌ Нечего отменять — история пуста."
        else:
            text = (
                f"❌ Удалено кормление:\n"
                f"🕐 {format_dt(deleted['fed_at'])}\n"
                f"🍽 {deleted['username']}"
            )
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
        await query.edit_message_text(text, reply_markup=main_keyboard())
        await update_all_pins(context)
        return

    # ---------- НАЗАД ----------
    if data == "back_to_menu":
        try:
            await query.edit_message_text(
                MENU_TEXT, parse_mode="HTML", reply_markup=main_keyboard()
            )
        except Exception:
            await query.message.reply_text(
                MENU_TEXT, parse_mode="HTML", reply_markup=main_keyboard()
            )
        return

    # ---------- ОСНОВНЫЕ ----------
    if data == "feed":
        name = user.first_name or (user.username or "Кто-то")
        save_feeding(user.id, name)
        last = get_last_feeding()
        text = (
            "✅ Записано!\n\n"
            f"🕐 {format_dt(last['fed_at'])}\n"
            f"🍽 Кормил(а): {last['username']}\n"
            f"⏳ {time_ago(last['fed_at'])}"
        )
        await query.message.reply_text(text, reply_markup=main_keyboard())
        await notify_partner(context, user.id, last["username"], last["fed_at"])
        await update_all_pins(context)

    elif data == "last":
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
        await query.message.reply_text(text, reply_markup=main_keyboard())

    elif data == "history":
        rows = get_history(15)
        if not rows:
            text = "История пуста 😿"
        else:
            lines = ["📜 Последние 15 кормлений:\n"]
            for r in rows:
                lines.append(
                    f"• {format_dt(r['fed_at'])} — {r['username']} "
                    f"({time_ago(r['fed_at'])})"
                )
            text = "\n".join(lines)
        await query.message.reply_text(text, reply_markup=main_keyboard())

    elif data == "stats":
        await query.message.reply_text(
            build_stats_message(), reply_markup=main_keyboard(), parse_mode="HTML"
        )


# ---------------- ЗАПУСК ----------------
def main() -> None:
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", whoami))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    if app.job_queue is not None:
        app.job_queue.run_repeating(
            reminder_job, interval=REMINDER_CHECK_MIN * 60, first=60
        )
        app.job_queue.run_repeating(
            care_reminder_job, interval=REMINDER_CHECK_MIN * 60, first=120
        )
        app.job_queue.run_repeating(
            pinned_refresh_job, interval=PIN_REFRESH_MIN * 60, first=180
        )
    else:
        print('⚠️ JobQueue недоступен — установи: pip install "python-telegram-bot[job-queue]"')

    print("Бот запущен. Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
