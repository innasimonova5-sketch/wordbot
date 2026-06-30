import sqlite3
import datetime
from contextlib import contextmanager

DB_PATH = "wordbot.db"

# Интервалы повторения в днях (упрощённый SM-2 / Лейтнер)
INTERVALS = [0, 1, 3, 7, 16, 35, 90]


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                created_at TEXT
            )
        """)
        # Общий словарь, который загружает учитель
        c.execute("""
            CREATE TABLE IF NOT EXISTS common_words (
                word_id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL,
                translation TEXT NOT NULL,
                added_by INTEGER
            )
        """)
        # Личный список слов ученика + прогресс изучения
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                translation TEXT NOT NULL,
                stage INTEGER DEFAULT 0,
                due_date TEXT NOT NULL,
                correct_count INTEGER DEFAULT 0,
                wrong_count INTEGER DEFAULT 0,
                UNIQUE(user_id, word)
            )
        """)
        conn.commit()


def add_user(user_id, username, full_name):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name, created_at) VALUES (?,?,?,?)",
            (user_id, username, full_name, datetime.datetime.utcnow().isoformat())
        )


def add_common_word(word, translation, added_by):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO common_words (word, translation, added_by) VALUES (?,?,?)",
            (word.strip(), translation.strip(), added_by)
        )


def get_common_words(limit=50):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM common_words ORDER BY word_id DESC LIMIT ?", (limit,)).fetchall()
        return rows


def add_user_word(user_id, word, translation):
    today = datetime.date.today().isoformat()
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO user_words (user_id, word, translation, stage, due_date) VALUES (?,?,?,0,?)",
                (user_id, word.strip(), translation.strip(), today)
            )
            return True
        except sqlite3.IntegrityError:
            return False  # уже есть такое слово у этого ученика


def get_due_words(user_id, limit=10):
    today = datetime.date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM user_words WHERE user_id=? AND due_date<=? ORDER BY due_date LIMIT ?",
            (user_id, today, limit)
        ).fetchall()
        return rows


def count_due_words(user_id):
    today = datetime.date.today().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM user_words WHERE user_id=? AND due_date<=?",
            (user_id, today)
        ).fetchone()
        return row["c"]


def get_random_distractors(user_id, exclude_word_id, count=3):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT translation FROM user_words WHERE user_id=? AND id!=? ORDER BY RANDOM() LIMIT ?",
            (user_id, exclude_word_id, count)
        ).fetchall()
        return [r["translation"] for r in rows]


def update_progress(word_id, correct: bool):
    with get_conn() as conn:
        row = conn.execute("SELECT stage, correct_count, wrong_count FROM user_words WHERE id=?", (word_id,)).fetchone()
        if not row:
            return
        stage = row["stage"]
        if correct:
            stage = min(stage + 1, len(INTERVALS) - 1)
            correct_count = row["correct_count"] + 1
            wrong_count = row["wrong_count"]
        else:
            stage = 0
            correct_count = row["correct_count"]
            wrong_count = row["wrong_count"] + 1

        due_date = (datetime.date.today() + datetime.timedelta(days=INTERVALS[stage])).isoformat()
        conn.execute(
            "UPDATE user_words SET stage=?, due_date=?, correct_count=?, wrong_count=? WHERE id=?",
            (stage, due_date, correct_count, wrong_count, word_id)
        )


def get_stats(user_id):
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM user_words WHERE user_id=?", (user_id,)).fetchone()["c"]
        learned = conn.execute(
            "SELECT COUNT(*) c FROM user_words WHERE user_id=? AND stage>=?",
            (user_id, len(INTERVALS) - 2)
        ).fetchone()["c"]
        due = count_due_words(user_id)
        return {"total": total, "learned": learned, "due": due}


def get_all_user_ids():
    with get_conn() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
        return [r["user_id"] for r in rows]
