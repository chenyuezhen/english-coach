#!/usr/bin/env python3
"""English Coach 服务端

零第三方依赖（仅标准库），面向多用户：
  · 卡点上报（网页 / API Key 外部调用）
  · 按天查看上报内容
  · 每日考试（由上报内容生成，错题按掌握度回炉）
  · 周统计
  · 用户管理
"""
import hashlib
import json
import os
import random
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DB_PATH = os.environ.get("DB_PATH", "/data/coach.db")
LLM_BASE = os.environ.get("LLM_BASE_URL", "").rstrip("/")
LLM_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
PORT = int(os.environ.get("PORT", "8080"))
TZ = timezone(timedelta(hours=8))
HERE = os.path.dirname(os.path.abspath(__file__))

_lock = threading.RLock()


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def today_str():
    return datetime.now(TZ).strftime("%Y-%m-%d")


# ---------------- 存储 ----------------

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    api_key TEXT NOT NULL UNIQUE,
    xp INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    app TEXT,
    selected TEXT,
    intent_zh TEXT,
    type TEXT,
    topic TEXT,
    canonical TEXT,
    severity INTEGER,
    why_wrong TEXT,
    levels TEXT,
    pattern TEXT,
    raw TEXT
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    topic TEXT NOT NULL,
    canonical TEXT,
    latest_sentence TEXT,
    levels TEXT,
    pattern TEXT,
    why_wrong TEXT,
    occurrences INTEGER NOT NULL DEFAULT 1,
    level INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    correct_count INTEGER NOT NULL DEFAULT 0,
    last_review TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, topic)
);
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    score INTEGER,
    total INTEGER,
    UNIQUE(user_id, day)
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    card_id INTEGER,
    qtype TEXT NOT NULL DEFAULT 'pick',
    stem TEXT NOT NULL,
    context TEXT,
    options TEXT NOT NULL,
    answer_index INTEGER NOT NULL,
    explanation TEXT,
    user_choice INTEGER,
    is_correct INTEGER,
    blank TEXT,
    answer_text TEXT,
    user_text TEXT
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    days INTEGER,
    markdown TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_questions_paper ON questions(paper_id);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_events_user_day ON events(user_id, day);
"""


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120000).hex()
    return f"{salt}${digest}"


def verify_pw(pw, stored):
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120000).hex() == digest


def init_db():
    with _lock, db() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
        if "user_id" not in cols:
            # 旧库升级：补列并归到第一个用户
            conn.execute("ALTER TABLE events ADD COLUMN user_id INTEGER")
            conn.execute("ALTER TABLE events ADD COLUMN day TEXT")
        plan_cols = {r["name"] for r in conn.execute("PRAGMA table_info(plans)")}
        if plan_cols and "user_id" not in plan_cols:
            conn.execute("ALTER TABLE plans ADD COLUMN user_id INTEGER")
        q_cols = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
        if q_cols and "qtype" not in q_cols:
            conn.execute("ALTER TABLE questions ADD COLUMN qtype TEXT NOT NULL DEFAULT 'pick'")
        if q_cols:
            for col in ("blank", "answer_text", "user_text"):
                if col not in q_cols:
                    conn.execute(f"ALTER TABLE questions ADD COLUMN {col} TEXT")
        u_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "xp" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN xp INTEGER NOT NULL DEFAULT 0")
        conn.executescript(INDEXES)
        if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
            # 首次启动创建管理员。密码从环境变量读；没设就随机生成并打印到日志，
            # 避免仓库里存在可用的默认口令。
            init_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
            generated = False
            if not init_pw:
                init_pw = secrets.token_urlsafe(9)
                generated = True
            conn.execute(
                "INSERT INTO users (username, display_name, password_hash, role, api_key, created_at)"
                " VALUES (?,?,?,?,?,?)",
                ("admin", "管理员", hash_pw(init_pw), "admin",
                 "ek_" + secrets.token_hex(16), now_iso()),
            )
            if generated:
                print(f"[init] 管理员账号 admin / 初始密码 {init_pw}", flush=True)
                print("[init] 登录后请到「成员」页改密码", flush=True)
        admin = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if admin:
            conn.execute(
                "UPDATE events SET user_id=? WHERE user_id IS NULL", (admin["id"],)
            )
            conn.execute(
                "UPDATE events SET day=substr(ts,1,10) WHERE day IS NULL"
            )
            conn.execute("UPDATE plans SET user_id=? WHERE user_id IS NULL", (admin["id"],))
        backfill_cards(conn)


def backfill_cards(conn):
    """为已有 events 但缺少 cards 的旧数据补建知识点。"""
    rows = conn.execute(
        """SELECT e.user_id, e.topic, COUNT(*) cnt, MAX(e.id) max_id
           FROM events e
           WHERE NOT EXISTS (
             SELECT 1 FROM cards c WHERE c.user_id=e.user_id AND c.topic=e.topic
           )
           GROUP BY e.user_id, e.topic"""
    ).fetchall()
    for r in rows:
        last = conn.execute(
            "SELECT * FROM events WHERE id=?", (r["max_id"],)
        ).fetchone()
        conn.execute(
            """INSERT INTO cards (user_id, topic, canonical, latest_sentence, levels, pattern,
               why_wrong, occurrences, level, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,0,?,?)""",
            (
                r["user_id"],
                r["topic"],
                last["canonical"],
                last["selected"],
                last["levels"],
                last["pattern"],
                last["why_wrong"],
                r["cnt"],
                last["ts"],
                last["ts"],
            ),
        )


# ---------------- LLM ----------------

def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def llm_json(system, user, max_tokens=2000, retries=2):
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            LLM_BASE + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + LLM_KEY},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read().decode())
            choice = body["choices"][0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError("输出被截断")
            return _extract_json(choice["message"]["content"])
        except Exception as e:
            last_err = e
            if attempt < retries:
                payload["max_tokens"] = int(max_tokens * (attempt + 2))
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败: {last_err}")


REPORT_SYSTEM = """你给一个中文母语者改英文。他写邮件或 Slack 时卡住了，把当时写的句子和想表达的意思发给你。

写法：
- 直接用陈述句说这句哪儿不对，然后给能用的说法。不铺垫，不总结，不夸也不劝。
- 一句话说完。宁可短，不要凑。
- 不用这些词：体现了、值得注意、总体而言、旨在、有效提升、确保、希望能、助力、赋能。
- 不评价他的水平，不说"建议多练"。
- 中文部分用日常说法，英文部分用真实会写在邮件里的句子。

只输出 JSON，不要 markdown 围栏。

{
  "type": "vocab|collocation|register|grammar|scenario",
  "topic": "4-8 字中文主题，用于聚合，例如 礼貌催确认/委婉拒绝/对上级汇报",
  "canonical": "这个卡点一句话，中文，例如 催老板但别显得在压他",
  "severity": 2,
  "why_wrong": "一句话中文，指出具体哪里别扭",
  "levels": {"peer": "", "manager": "", "formal_email": ""},
  "pattern": "可套用的句型，用 + 标出可替换处"
}

why_wrong 的写法参考：「ASAP 对老板像在催单，please kindly 是中文礼貌词硬翻的，两个叠起来更怪。」
levels 三档都是能直接粘出去的完整英文，每句不超过 20 词。severity：1 小别扭，2 影响读感，3 明显失礼。"""

EXAM_PICK_SYSTEM = """给一个在补英文表达的人出今日小测的单项选择题。素材是他自己写过的句子。

考的是"这个场合该怎么说"，不是语法术语。场景要具体，选项之间的差别要真实存在，不要凑荒谬选项。

只输出 JSON，不要 markdown 围栏。

{"questions":[{"card_id":1,"stem":"场景，中文，25-45 字，说清对象和处境","context":"用户原句或可空串","options":["","","",""],"answer_index":0,"explanation":"一句话中文，说最佳项好在哪，干扰项错在哪"}]}

规则：
- 每题 4 个选项，1 个最佳，3 个是真实会踩的坑：中式直译、语域错配、搭配错
- stem 直接描述处境，不写"请问下列哪个"
- explanation 一句话，不用"综上所述"
- card_id 原样使用输入值
- 题目数等于 payload 里的 count"""

EXAM_FILL_SYSTEM = """给一个在补英文表达的人出今日小测的填空题。素材是他自己写过的句子。

考的是"这个场合该怎么说"，不是语法术语。

只输出 JSON，不要 markdown 围栏。

{"questions":[{"card_id":2,"stem":"场景，中文，25-45 字","context":"用户原句或可空串","blank":"一个完整英文句子，把要考的词或短语替换成 ___","answer_text":"标准答案|可接受的其他写法","explanation":"一句话中文，说清为什么该这么填"}]}

规则：
- 只挖一个词或短语（不超过 4 个词），必须唯一可判
- blank 里只出现一处 ___
- answer_text 用 | 分隔多个可接受写法，第一个是最标准的
- 空要考真正容易出错的地方，不要挖 the / a 这种无意义的词
- stem 直接描述处境，不写"请问下列哪个"
- explanation 一句话，不用"综上所述"
- card_id 原样使用输入值
- 题目数等于 payload 里的 count"""

PLAN_SYSTEM = """给一个在补英文表达的人写本周重点。素材是他自己的卡点记录和掌握度。

写法：
- 像同事在便签上给他划重点：短句，具体，能照着做。
- 不复述数据，不评价态度，不鼓励。
- 只说三件事：卡在哪、为什么、这周练什么。
- 不用这些词：综上所述、总体而言、建议您、旨在、有效提升、全面提升、显著改善。

只输出 JSON，不要 markdown 围栏。

{"window":"日期范围","summary":"2 句话，说这段时间最明显的问题","focus":[{"topic":"","diagnosis":"一句中文说清病灶","your_sentence":"他的原句","better":{"peer":"","manager":"","formal_email":""},"pattern":"","drill":"60 秒能做完的输出练习，中文写，用他自己的场景"}],"repeat_alert":["反复出现没改掉的，最多 2 条"],"next_week":"这周只做一件事，一句话"}

focus 最多 3 条，按影响排序。drill 要写清给谁写、写什么、不许用什么。"""


# ---------------- 用户与会话 ----------------

def auth_user(handler):
    auth = handler.headers.get("Authorization") or ""
    token = auth[7:] if auth.startswith("Bearer ") else ""
    if not token:
        token = (handler.headers.get("X-API-Key") or "").strip()
    if not token:
        return None
    with _lock, db() as conn:
        if token.startswith("ek_"):
            row = conn.execute("SELECT * FROM users WHERE api_key=?", (token,)).fetchone()
        else:
            row = conn.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
                (token,),
            ).fetchone()
    return dict(row) if row else None


def create_session(user_id):
    token = secrets.token_urlsafe(24)
    with _lock, db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
            (token, user_id, now_iso()),
        )
    return token


def public_user(u):
    return {
        "id": u["id"],
        "username": u["username"],
        "display_name": u["display_name"],
        "role": u["role"],
        "api_key": u["api_key"],
        "xp": u.get("xp", 0) if isinstance(u, dict) else (u["xp"] if "xp" in u.keys() else 0),
        "created_at": u["created_at"],
    }


# ---------------- 上报 ----------------

def upsert_card(conn, user_id, res, selected):
    topic = (res.get("topic") or "").strip() or "未分类"
    ts = now_iso()
    row = conn.execute(
        "SELECT * FROM cards WHERE user_id=? AND topic=?", (user_id, topic)
    ).fetchone()
    levels = json.dumps(res.get("levels", {}), ensure_ascii=False)
    if row:
        conn.execute(
            """UPDATE cards SET canonical=?, latest_sentence=?, levels=?, pattern=?,
               why_wrong=?, occurrences=occurrences+1, updated_at=? WHERE id=?""",
            (
                res.get("canonical"),
                selected or row["latest_sentence"],
                levels,
                res.get("pattern"),
                res.get("why_wrong"),
                ts,
                row["id"],
            ),
        )
        return row["id"]
    cur = conn.execute(
        """INSERT INTO cards (user_id, topic, canonical, latest_sentence, levels, pattern,
           why_wrong, occurrences, level, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,1,0,?,?)""",
        (
            user_id,
            topic,
            res.get("canonical"),
            selected,
            levels,
            res.get("pattern"),
            res.get("why_wrong"),
            ts,
            ts,
        ),
    )
    return cur.lastrowid


def do_report(user, payload):
    selected = (payload.get("selected") or "").strip()
    intent = (payload.get("intent_zh") or "").strip()
    app_name = (payload.get("app") or "").strip()
    if not selected and not intent:
        raise ValueError("selected 与 intent_zh 至少需要一项")

    prompt = json.dumps(
        {
            "我写的英文": selected or "（还没写出来，只有中文意图）",
            "我的中文意图": intent or "（无）",
            "所在应用": app_name or "未知",
        },
        ensure_ascii=False,
    )
    res = llm_json(REPORT_SYSTEM, prompt)
    ts = now_iso()
    with _lock, db() as conn:
        cur = conn.execute(
            """INSERT INTO events (user_id, ts, day, app, selected, intent_zh, type, topic,
               canonical, severity, why_wrong, levels, pattern, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user["id"],
                ts,
                ts[:10],
                app_name,
                selected,
                intent,
                res.get("type"),
                res.get("topic"),
                res.get("canonical"),
                res.get("severity"),
                res.get("why_wrong"),
                json.dumps(res.get("levels", {}), ensure_ascii=False),
                res.get("pattern"),
                json.dumps(res, ensure_ascii=False),
            ),
        )
        event_id = cur.lastrowid
        card_id = upsert_card(conn, user["id"], res, selected)
    return {"id": event_id, "card_id": card_id, "ts": ts, **res}


# ---------------- 查询 ----------------

def list_events(user_id, day=None, limit=200):
    with _lock, db() as conn:
        if day:
            rows = conn.execute(
                "SELECT * FROM events WHERE user_id=? AND day=? ORDER BY id DESC LIMIT ?",
                (user_id, day, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["levels"] = json.loads(d.get("levels") or "{}")
        except Exception:
            d["levels"] = {}
        d.pop("raw", None)
        out.append(d)
    return out


def month_days(user_id, month):
    with _lock, db() as conn:
        rows = conn.execute(
            """SELECT day, COUNT(*) cnt FROM events
               WHERE user_id=? AND day LIKE ? GROUP BY day""",
            (user_id, month + "%"),
        ).fetchall()
        exams = conn.execute(
            "SELECT day, score, total FROM papers WHERE user_id=? AND day LIKE ?",
            (user_id, month + "%"),
        ).fetchall()
    return {
        "month": month,
        "days": {r["day"]: r["cnt"] for r in rows},
        "exams": {r["day"]: {"score": r["score"], "total": r["total"]} for r in exams},
    }


def card_summary(user_id):
    with _lock, db() as conn:
        rows = conn.execute(
            """SELECT id, topic, canonical, level, occurrences, attempts, correct_count,
                      last_review, latest_sentence, pattern
               FROM cards WHERE user_id=? ORDER BY level ASC, occurrences DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- 考试 ----------------

def pick_cards(user_id, n=6):
    """优先掌握度低的，其次是重复卡点；掌握满级的不再出。"""
    with _lock, db() as conn:
        rows = conn.execute(
            """SELECT * FROM cards WHERE user_id=? AND level < 5
               ORDER BY level ASC, occurrences DESC, updated_at DESC LIMIT ?""",
            (user_id, n),
        ).fetchall()
    return [dict(r) for r in rows]


def _norm_fill(s):
    """填空题比对用：去首尾空白、压空白、忽略大小写和结尾标点。"""
    s = re.sub(r"\s+", " ", str(s or "").strip().lower())
    return s.strip(" .!?;:,'\"")


def _grade_fill(q, answer):
    """answer_text 里用 | 分隔多个可接受写法。"""
    if answer is None or str(answer).strip() == "":
        return False
    candidates = [c for c in (q["answer_text"] or "").split("|") if c.strip()]
    return _norm_fill(answer) in {_norm_fill(c) for c in candidates}


def get_or_create_paper(user, force=False):
    day = today_str()
    with _lock, db() as conn:
        row = conn.execute(
            "SELECT * FROM papers WHERE user_id=? AND day=?", (user["id"], day)
        ).fetchone()
        if row and not force:
            return load_paper(row["id"])
        if row:
            conn.execute("DELETE FROM questions WHERE paper_id=?", (row["id"],))
            conn.execute("DELETE FROM papers WHERE id=?", (row["id"],))

    cards = pick_cards(user["id"])
    if not cards:
        return {"empty": True, "message": "还没有可考的内容，先上报几条卡点"}

    n = len(cards)
    # 一半选择题、一半填空题；总数是奇数时选择题多一题
    n_fill = n // 2
    n_pick = n - n_fill

    def card_payload(chunk, count):
        return json.dumps(
            {
                "count": count,
                "cards": [
                    {
                        "card_id": c["id"],
                        "topic": c["topic"],
                        "canonical": c["canonical"],
                        "user_sentence": c["latest_sentence"],
                        "levels": json.loads(c["levels"] or "{}"),
                        "pattern": c["pattern"],
                        "level": c["level"],
                        "occurrences": c["occurrences"],
                    }
                    for c in chunk
                ],
            },
            ensure_ascii=False,
        )

    # 两种题型分两次调用：一次让模型同时出两种，它常常只出选择题
    res_pick = llm_json(EXAM_PICK_SYSTEM, card_payload(cards[:n_pick], n_pick), max_tokens=3500)
    res_fill = (
        llm_json(EXAM_FILL_SYSTEM, card_payload(cards[n_pick:], n_fill), max_tokens=3500)
        if n_fill
        else {"questions": []}
    )

    qs = []
    for q in res_pick.get("questions", []):
        opts = q.get("options")
        if not isinstance(opts, list) or len(opts) < 4:
            continue
        q["qtype"] = "pick"
        q["options"] = opts[:4]
        qs.append(q)
    for q in res_fill.get("questions", []):
        blank = (q.get("blank") or "").strip()
        ans = (q.get("answer_text") or "").strip()
        # 必须恰好一个空位、且有标准答案，否则这题没法判
        if blank.count("___") != 1 or not ans:
            continue
        q["qtype"] = "fill"
        q["options"] = []
        q["blank"] = blank
        q["answer_text"] = ans
        qs.append(q)
    if not qs:
        raise RuntimeError("出题失败，稍后重试")

    # 打散顺序，避免永远「先选择后填空」
    random.shuffle(qs)

    with _lock, db() as conn:
        cur = conn.execute(
            "INSERT INTO papers (user_id, day, created_at, total) VALUES (?,?,?,?)",
            (user["id"], day, now_iso(), len(qs)),
        )
        pid = cur.lastrowid
        for q in qs:
            idx = int(q.get("answer_index", 0) or 0)
            idx = 0 if idx < 0 or idx > 3 else idx
            conn.execute(
                """INSERT INTO questions (paper_id, card_id, qtype, stem, context, options,
                   answer_index, explanation, blank, answer_text) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    pid,
                    q.get("card_id"),
                    q.get("qtype") or "pick",
                    q.get("stem") or "",
                    q.get("context") or "",
                    json.dumps(q.get("options") or [], ensure_ascii=False),
                    idx,
                    q.get("explanation") or "",
                    (q.get("blank") or "").strip() or None,
                    (q.get("answer_text") or "").strip() or None,
                ),
            )
    return load_paper(pid, reveal=False)


def load_paper(pid, reveal=None):
    with _lock, db() as conn:
        p = conn.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
        qs = conn.execute(
            "SELECT * FROM questions WHERE paper_id=? ORDER BY id", (pid,)
        ).fetchall()
    if not p:
        return {}
    submitted = p["submitted_at"] is not None
    show = submitted if reveal is None else reveal
    keys = set(qs[0].keys()) if qs else set()
    items = []
    for q in qs:
        d = {
            "id": q["id"],
            "qtype": q["qtype"] if "qtype" in q.keys() else "pick",
            "stem": q["stem"],
            "context": q["context"],
            "options": json.loads(q["options"]),
            "user_choice": q["user_choice"],
            "is_correct": bool(q["is_correct"]) if q["is_correct"] is not None else None,
            "blank": q["blank"] if "blank" in keys else None,
            "user_text": q["user_text"] if "user_text" in keys else None,
        }
        if show:
            d["answer_index"] = q["answer_index"]
            d["explanation"] = q["explanation"]
            if "answer_text" in keys:
                d["answer_text"] = q["answer_text"]
        items.append(d)
    return {
        "paper_id": p["id"],
        "day": p["day"],
        "submitted": submitted,
        "score": p["score"],
        "total": p["total"],
        "created_at": p["created_at"],
        "submitted_at": p["submitted_at"],
        "questions": items,
    }


def submit_paper(user, paper_id, answers):
    with _lock, db() as conn:
        p = conn.execute(
            "SELECT * FROM papers WHERE id=? AND user_id=?", (paper_id, user["id"])
        ).fetchone()
        if not p:
            raise ValueError("试卷不存在")
        if p["submitted_at"]:
            return load_paper(p["id"], reveal=True)
        qs = conn.execute("SELECT * FROM questions WHERE paper_id=?", (paper_id,)).fetchall()
        score = 0
        for q in qs:
            raw = answers.get(str(q["id"]), answers.get(q["id"]))
            is_fill = (q["qtype"] if "qtype" in q.keys() else "pick") == "fill"
            if is_fill:
                ok = _grade_fill(q, raw)
                conn.execute(
                    "UPDATE questions SET user_text=?, is_correct=? WHERE id=?",
                    ((raw or "").strip(), 1 if ok else 0, q["id"]),
                )
            else:
                choice = int(raw) if raw is not None and str(raw).lstrip("-").isdigit() else -1
                ok = choice == q["answer_index"]
                conn.execute(
                    "UPDATE questions SET user_choice=?, is_correct=? WHERE id=?",
                    (choice, 1 if ok else 0, q["id"]),
                )
            score += 1 if ok else 0
            if q["card_id"]:
                card = conn.execute("SELECT * FROM cards WHERE id=?", (q["card_id"],)).fetchone()
                if card:
                    if ok:
                        conn.execute(
                            """UPDATE cards SET level=MIN(5, level+1), attempts=attempts+1,
                               correct_count=correct_count+1, last_review=? WHERE id=?""",
                            (now_iso(), card["id"]),
                        )
                    else:
                        conn.execute(
                            """UPDATE cards SET level=MAX(0, level-2), attempts=attempts+1,
                               last_review=? WHERE id=?""",
                            (now_iso(), card["id"]),
                        )
        conn.execute(
            "UPDATE papers SET submitted_at=?, score=? WHERE id=?",
            (now_iso(), score, paper_id),
        )
        # XP：答对 10 分，全对额外 20；每天首次交卷 +15
        xp = score * 10
        if score == len(qs):
            xp += 20
        conn.execute("UPDATE users SET xp = xp + ? WHERE id=?", (xp, user["id"]))
    return load_paper(paper_id, reveal=True)


def exam_history(user_id, limit=30):
    with _lock, db() as conn:
        rows = conn.execute(
            """SELECT id, day, score, total, submitted_at, created_at FROM papers
               WHERE user_id=? ORDER BY id DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- 统计 ----------------

def weekly_stats(user_id, weeks=8):
    today = datetime.now(TZ).date()
    out = []
    for i in range(weeks - 1, -1, -1):
        start = today - timedelta(days=today.weekday() + 7 * i)
        end = start + timedelta(days=6)
        s, e = start.isoformat(), end.isoformat()
        with _lock, db() as conn:
            ev = conn.execute(
                "SELECT COUNT(*) c, COUNT(DISTINCT day) d FROM events WHERE user_id=? AND day BETWEEN ? AND ?",
                (user_id, s, e),
            ).fetchone()
            ex = conn.execute(
                "SELECT COUNT(*) c, SUM(score) s, SUM(total) t FROM papers WHERE user_id=? AND day BETWEEN ? AND ? AND submitted_at IS NOT NULL",
                (user_id, s, e),
            ).fetchone()
            active = conn.execute(
                """SELECT COUNT(DISTINCT day) c FROM (
                     SELECT day FROM events WHERE user_id=? AND day BETWEEN ? AND ?
                     UNION SELECT day FROM papers WHERE user_id=? AND day BETWEEN ? AND ? AND submitted_at IS NOT NULL
                   )""",
                (user_id, s, e, user_id, s, e),
            ).fetchone()
        out.append(
            {
                "week_start": s,
                "week_end": e,
                "reports": ev["c"],
                "days_active": active["c"],
                "exams": ex["c"] or 0,
                "questions": ex["t"] or 0,
                "correct": ex["s"] or 0,
                "accuracy": round((ex["s"] or 0) / ex["t"] * 100) if ex["t"] else None,
            }
        )
    total_reports = sum(w["reports"] for w in out)
    total_q = sum(w["questions"] for w in out)
    total_c = sum(w["correct"] for w in out)
    with _lock, db() as conn:
        cards = conn.execute(
            "SELECT COUNT(*) c, SUM(level>=4) m FROM cards WHERE user_id=?", (user_id,)
        ).fetchone()
        active_days = {
            r["day"]
            for r in conn.execute(
                """SELECT day FROM events WHERE user_id=?
                   UNION SELECT day FROM papers WHERE user_id=? AND submitted_at IS NOT NULL""",
                (user_id, user_id),
            ).fetchall()
        }
        urow = conn.execute("SELECT xp FROM users WHERE id=?", (user_id,)).fetchone()
        xp = urow["xp"] if urow else 0
    streak = 0
    d = today
    while d.isoformat() in active_days:
        streak += 1
        d -= timedelta(days=1)
    return {
        "weeks": out,
        "totals": {
            "reports": total_reports,
            "questions": total_q,
            "correct": total_c,
            "accuracy": round(total_c / total_q * 100) if total_q else None,
            "cards": cards["c"],
            "mastered": cards["m"] or 0,
            "streak": streak,
            "xp": xp,
            "level": xp // 200 + 1,
            "level_xp": xp % 200,
            "level_next": 200,
        },
        "cards": card_summary(user_id),
    }


# ---------------- 计划 ----------------

def do_analyze(user, days):
    since = (datetime.now(TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    with _lock, db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE user_id=? AND ts>=?", (user["id"], since)
        ).fetchone()["c"]
        if total == 0:
            return {"empty": True, "message": f"最近 {days} 天没有上报记录"}
        top = conn.execute(
            """SELECT topic, type, COUNT(*) cnt, MAX(severity) sev FROM events
               WHERE user_id=? AND ts>=? GROUP BY topic, type ORDER BY cnt DESC, sev DESC LIMIT 12""",
            (user["id"], since),
        ).fetchall()
        samples = conn.execute(
            """SELECT selected, intent_zh, topic, why_wrong FROM events
               WHERE user_id=? AND ts>=? ORDER BY id DESC LIMIT 12""",
            (user["id"], since),
        ).fetchall()
        cards = conn.execute(
            "SELECT topic, level, occurrences FROM cards WHERE user_id=? ORDER BY level ASC LIMIT 12",
            (user["id"],),
        ).fetchall()
    payload = json.dumps(
        {
            "window_days": days,
            "total_events": total,
            "top_topics": [dict(r) for r in top],
            "samples": [dict(r) for r in samples],
            "weak_cards": [dict(r) for r in cards],
        },
        ensure_ascii=False,
    )
    plan = llm_json(PLAN_SYSTEM, payload, max_tokens=4000)
    plan.setdefault("window", f"最近 {days} 天")
    md = render_markdown(plan, total)
    with _lock, db() as conn:
        conn.execute(
            "INSERT INTO plans (user_id, ts, days, markdown, data) VALUES (?,?,?,?,?)",
            (user["id"], now_iso(), days, md, json.dumps(plan, ensure_ascii=False)),
        )
    return {"ts": now_iso(), "markdown": md, "data": plan}


def render_markdown(plan, total):
    lines = [
        f"# 英语表达加固计划 · {plan.get('window','')}",
        "",
        f"窗口内上报 **{total}** 次。",
        "",
        "## 判断",
        "",
        plan.get("summary", ""),
        "",
    ]
    for i, f in enumerate(plan.get("focus", []), 1):
        lines += [
            f"## 重点 {i}：{f.get('topic','')}",
            "",
            f"**问题** {f.get('diagnosis','')}",
            "",
            f"**你当时写的** `{f.get('your_sentence','')}`",
            "",
        ]
        b = f.get("better", {})
        for label, k in (("平级", "peer"), ("对上级", "manager"), ("正式邮件", "formal_email")):
            if b.get(k):
                lines.append(f"- **{label}**：{b[k]}")
        lines += ["", f"**句型** {f.get('pattern','')}", "", f"**今天练这个** {f.get('drill','')}", ""]
    if plan.get("repeat_alert"):
        lines += ["## 反复没改掉的", ""] + [f"- {x}" for x in plan["repeat_alert"]] + [""]
    if plan.get("next_week"):
        lines += ["## 下周只做一件事", "", plan["next_week"], ""]
    return "\n".join(lines)


def latest_plan(user_id):
    with _lock, db() as conn:
        row = conn.execute(
            "SELECT ts, days, markdown, data FROM plans WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["data"] = json.loads(d["data"] or "{}")
    return d


# ---------------- HTTP ----------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "EnglishCoach/2.0"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (datetime.now(TZ).strftime("%H:%M:%S"), fmt % args), flush=True)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, default=str)
        raw = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode() or "{}") if n else {}

    def _q(self):
        return parse_qs(urlparse(self.path).query)

    def _auth(self):
        u = auth_user(self)
        if not u:
            self._send(401, {"error": "未登录或凭证无效"})
        return u

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path == "/app.js":
                with open(os.path.join(HERE, "app.js"), "rb") as f:
                    return self._send(200, f.read(), "application/javascript; charset=utf-8")
            if path in ("/favicon.png", "/apple-touch-icon.png"):
                fp = os.path.join(HERE, path.lstrip("/"))
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        return self._send(200, f.read(), "image/png")
            if path == "/healthz":
                return self._send(200, {"ok": True, "model": LLM_MODEL})

            user = self._auth()
            if not user:
                return
            q = self._q()
            if path == "/api/me":
                return self._send(200, {"user": public_user(user)})
            if path == "/api/events":
                return self._send(
                    200,
                    {"events": list_events(user["id"], q.get("date", [None])[0], int(q.get("limit", ["200"])[0]))},
                )
            if path == "/api/days":
                month = q.get("month", [today_str()[:7]])[0]
                return self._send(200, month_days(user["id"], month))
            if path == "/api/cards":
                return self._send(200, {"cards": card_summary(user["id"])})
            if path == "/api/exam/today":
                return self._send(200, get_or_create_paper(user))
            if path == "/api/exam/history":
                return self._send(200, {"papers": exam_history(user["id"])})
            if path == "/api/stats":
                return self._send(200, weekly_stats(user["id"], int(q.get("weeks", ["8"])[0])))
            if path == "/api/plan/latest":
                return self._send(200, latest_plan(user["id"]) or {})
            if path == "/api/users":
                if user["role"] != "admin":
                    return self._send(403, {"error": "需要管理员权限"})
                with _lock, db() as conn:
                    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
                return self._send(200, {"users": [public_user(dict(r)) for r in rows]})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/login":
                body = self._json()
                with _lock, db() as conn:
                    row = conn.execute(
                        "SELECT * FROM users WHERE username=?", (body.get("username", ""),)
                    ).fetchone()
                if not row or not verify_pw(body.get("password", ""), row["password_hash"]):
                    return self._send(401, {"error": "用户名或密码错误"})
                return self._send(200, {"token": create_session(row["id"]), "user": public_user(dict(row))})

            user = self._auth()
            if not user:
                return
            if path == "/api/logout":
                auth = self.headers.get("Authorization") or ""
                if auth.startswith("Bearer "):
                    with _lock, db() as conn:
                        conn.execute("DELETE FROM sessions WHERE token=?", (auth[7:],))
                return self._send(200, {"ok": True})
            if path == "/api/report":
                return self._send(200, do_report(user, self._json()))
            if path == "/api/analyze":
                days = int(self._q().get("days", ["7"])[0])
                return self._send(200, do_analyze(user, days))
            if path == "/api/exam/submit":
                b = self._json()
                return self._send(200, submit_paper(user, int(b.get("paper_id", 0)), b.get("answers", {})))
            if path == "/api/exam/regenerate":
                return self._send(200, get_or_create_paper(user, force=True))
            if path == "/api/users":
                if user["role"] != "admin":
                    return self._send(403, {"error": "需要管理员权限"})
                b = self._json()
                uname = (b.get("username") or "").strip()
                pw = b.get("password") or ""
                if not uname or len(pw) < 6:
                    return self._send(400, {"error": "用户名必填，密码至少 6 位"})
                try:
                    with _lock, db() as conn:
                        conn.execute(
                            "INSERT INTO users (username, display_name, password_hash, role, api_key, created_at)"
                            " VALUES (?,?,?,?,?,?)",
                            (
                                uname,
                                b.get("display_name") or uname,
                                hash_pw(pw),
                                "member",
                                "ek_" + secrets.token_hex(16),
                                now_iso(),
                            ),
                        )
                except sqlite3.IntegrityError:
                    return self._send(409, {"error": "用户名已存在"})
                return self._send(200, {"ok": True})
            if path == "/api/users/delete":
                if user["role"] != "admin":
                    return self._send(403, {"error": "需要管理员权限"})
                uid = int(self._json().get("id", 0))
                if uid == user["id"]:
                    return self._send(400, {"error": "不能删除自己"})
                with _lock, db() as conn:
                    conn.execute("DELETE FROM users WHERE id=?", (uid,))
                    conn.execute("DELETE FROM events WHERE user_id=?", (uid,))
                    conn.execute("DELETE FROM cards WHERE user_id=?", (uid,))
                    conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": str(e)})


if __name__ == "__main__":
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    init_db()
    print(f"listening :{PORT} model={LLM_MODEL} db={DB_PATH}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
