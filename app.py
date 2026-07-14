from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, FileResponse
import sqlite3
import uvicorn
import uuid
import json
import os
import urllib.request
from config import BOT_TOKEN

app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

PHOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)


def get_db():
    conn = sqlite3.connect("splitwise.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            tx_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            currency TEXT DEFAULT 'USD'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS members (
            chat_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            tg_user_id INTEGER,
            username TEXT,
            photo_url TEXT,
            first_seen INTEGER,
            left_at INTEGER,
            PRIMARY KEY (chat_id, user_name)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id TEXT PRIMARY KEY,
            title TEXT,
            photo_file TEXT,
            member_count INTEGER DEFAULT 0,
            updated_at INTEGER
        )
    """)
    # Migrate existing tables: add columns if missing (for DBs created before these columns existed)
    try:
        c.execute("PRAGMA table_info(expenses)")
        cols = [r[1] for r in c.fetchall()]
        if "currency" not in cols:
            c.execute("ALTER TABLE expenses ADD COLUMN currency TEXT DEFAULT 'USD'")
    except Exception as e:
        print(f"migration expenses: {e}")
    try:
        c.execute("PRAGMA table_info(members)")
        cols = [r[1] for r in c.fetchall()]
        if "left_at" not in cols:
            c.execute("ALTER TABLE members ADD COLUMN left_at INTEGER")
    except Exception as e:
        print(f"migration members: {e}")
    conn.commit()
    conn.close()


init_db()


# Approximate exchange rates vs EUR
EUR_RATES = {
    "EUR": 1.0,
    "USD": 0.92,
    "GBP": 1.18,
    "GEL": 0.34,
    "RUB": 0.0096,
}

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "GEL": "₾", "RUB": "₽"}


def cur_sym(code):
    return CURRENCY_SYMBOLS.get(code, code + " ") + " "


def convert_to(default_cur, amount, from_cur):
    if from_cur == default_cur:
        return round(amount, 2)
    from_rate = EUR_RATES.get(from_cur)
    to_rate = EUR_RATES.get(default_cur)
    if from_rate is None or to_rate is None:
        return round(amount, 2)
    return round(amount * from_rate / to_rate, 2)


def _net_transfers(currency_balances):
    debtors = [[u, abs(b)] for u, b in currency_balances.items() if b < -0.01]
    creditors = [[u, b] for u, b in currency_balances.items() if b > 0.01]
    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)
    transfers = []
    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        d_user, d_amt = debtors[i]
        c_user, c_amt = creditors[j]
        transfer_amt = round(min(d_amt, c_amt), 2)
        transfers.append({"from": d_user, "to": c_user, "amount": transfer_amt})
        debtors[i][1] -= transfer_amt
        creditors[j][1] -= transfer_amt
        if debtors[i][1] < 0.01:
            i += 1
        if creditors[j][1] < 0.01:
            j += 1
    return transfers


# ----- Telegram notification helper -----
def send_telegram_message(chat_id, text):
    if not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def mention(user_name, members_map):
    """Build an HTML mention for a user_name: '@username' if known, else just the display name."""
    m = members_map.get(user_name)
    if m and m.get("username"):
        return f'@{m["username"]}'
    return user_name


def get_members_map(cursor, chat_id):
    cursor.execute("SELECT user_name, username, tg_user_id, photo_url FROM members WHERE chat_id=?", (chat_id,))
    return {r["user_name"]: {"username": r["username"], "tg_user_id": r["tg_user_id"], "photo_url": r["photo_url"]} for r in cursor.fetchall()}


def fetch_chat_info(chat_id):
    """Call Telegram getChat API. Returns (title, photo_file_id) or (None, None) if chat not found / bot not in chat."""
    if not BOT_TOKEN or not chat_id or chat_id == "default" or not chat_id.startswith("-"):
        return None, None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat?chat_id={chat_id}"
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            result = data.get("result", {})
            title = result.get("title")
            photo = result.get("photo")
            small_file_id = photo["small_file_id"] if photo else None
            return title, small_file_id
    except Exception:
        pass
    return None, None


def download_and_save_group_photo(chat_id, small_file_id):
    """Download a group photo via Telegram getFile API and save it to disk. Returns filename or None."""
    if not BOT_TOKEN or not small_file_id:
        return None
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={small_file_id}"
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            file_path = data["result"]["file_path"]
            dl_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            file_name = f"group_{chat_id}.jpg"
            dest = os.path.join(PHOTOS_DIR, file_name)
            urllib.request.urlretrieve(dl_url, dest)
            return file_name
    except Exception:
        pass
    return None


def ensure_group_title_and_photo(chat_id, conn):
    """If the group's title is missing in DB, fetch it from Telegram and update. Returns the title or None."""
    c = conn.cursor()
    c.execute("SELECT title, photo_file FROM groups WHERE chat_id=?", (chat_id,))
    r = c.fetchone()
    if r and r["title"] and r["photo_file"]:
        return r["title"]

    title, small_file_id = fetch_chat_info(chat_id)
    if title:
        photo_file = download_and_save_group_photo(chat_id, small_file_id) if small_file_id else None
        if r:
            c.execute("UPDATE groups SET title=?, photo_file=COALESCE(?, photo_file) WHERE chat_id=?", (title, photo_file, chat_id))
        else:
            c.execute("INSERT INTO groups (chat_id, title, photo_file, member_count, updated_at) VALUES (?, ?, ?, 0, strftime('%s','now'))", (chat_id, title, photo_file))
        conn.commit()
        return title
    elif not r:
        # Chat doesn't exist or bot was kicked — no groups row, nothing to clean
        return None
    else:
        # Group row exists but getChat failed — bot was likely kicked or group deleted
        return r["title"]


@app.get("/")
def read_root():
    with open("index.html", "r") as f:
        return HTMLResponse(f.read(), headers={
            "ngrok-skip-browser-warning": "true"
        })


@app.post("/api/add")
async def add_expense(req: Request):
    data = await req.json()
    chat_id = str(data.get("chat_id", "default"))
    payer = data.get("user", "Unknown")
    total_amount = float(data.get("amount", 0))
    desc = data.get("desc", "")
    splits = data.get("splits", {})
    currency = str(data.get("currency", "USD"))

    tx_id = str(uuid.uuid4())

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO expenses (chat_id, tx_id, user_name, amount, description, currency) VALUES (?, ?, ?, ?, ?, ?)",
                   (chat_id, tx_id, payer, total_amount, desc, currency))

    split_users = []
    for user, share in splits.items():
        if float(share) > 0:
            cursor.execute("INSERT INTO expenses (chat_id, tx_id, user_name, amount, description, currency) VALUES (?, ?, ?, ?, ?, ?)",
                           (chat_id, tx_id, user, -float(share), f"Share: {desc}", currency))
            if user != payer:
                split_users.append(user)

    conn.commit()
    members_map = get_members_map(cursor, chat_id)
    conn.close()

    # Notification: "NAME added a new expense for @u1 and @u2"
    if chat_id != "default" and chat_id.startswith("-"):
        try:
            recipients = [mention(u, members_map) for u in split_users]
            if recipients:
                if len(recipients) == 1:
                    who = recipients[0]
                elif len(recipients) == 2:
                    who = f"{recipients[0]} and {recipients[1]}"
                else:
                    who = ", ".join(recipients[:-1]) + f" and {recipients[-1]}"
                payer_mention = mention(payer, members_map)
                msg = f"{payer_mention} added a new expense \"{desc}\" for {who} ({cur_sym(currency)}{total_amount:.2f})"
                send_telegram_message(chat_id, msg)
        except Exception:
            pass

    return {"status": "ok"}


@app.post("/api/delete")
async def delete_expense(req: Request):
    data = await req.json()
    tx_id = data.get("tx_id")
    chat_id = str(data.get("chat_id", "default"))

    conn = get_db()
    conn.cursor().execute("DELETE FROM expenses WHERE tx_id=? AND chat_id=?", (tx_id, chat_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.post("/api/settle")
async def settle_debt(req: Request):
    data = await req.json()
    chat_id = str(data.get("chat_id", "default"))
    from_user = data.get("from_user")
    to_user = data.get("to_user")
    amount = float(data.get("amount", 0))
    currency = str(data.get("currency", "EUR"))

    tx_id = str(uuid.uuid4())
    desc = "Settled debt"

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO expenses (chat_id, tx_id, user_name, amount, description, currency) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, tx_id, from_user, amount, desc, currency))
    cursor.execute("INSERT INTO expenses (chat_id, tx_id, user_name, amount, description, currency) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, tx_id, to_user, -amount, desc, currency))
    conn.commit()
    members_map = get_members_map(cursor, chat_id)
    conn.close()

    # Notification: "NAME paid / covered expense"
    if chat_id != "default" and chat_id.startswith("-"):
        try:
            from_m = mention(from_user, members_map)
            to_m = mention(to_user, members_map)
            msg = f"{from_m} paid {to_m} {cur_sym(currency)}{amount:.2f} to settle up"
            send_telegram_message(chat_id, msg)
        except Exception:
            pass

    return {"status": "ok"}


@app.get("/api/groups")
def get_groups(current_user: str = Query(""), tg_user_id: str = Query("")):
    """Return all split groups the current_user is a member of."""
    conn = get_db()
    c = conn.cursor()

    if tg_user_id:
        c.execute("""
            SELECT g.chat_id, g.title, g.photo_file, g.member_count,
                   (SELECT COUNT(*) FROM members m2 WHERE m2.chat_id = g.chat_id AND (m2.tg_user_id = ? OR m2.user_name = ?) AND m2.left_at IS NULL) as in_group
            FROM groups g
            WHERE g.chat_id IN (
                SELECT chat_id FROM members WHERE (tg_user_id = ? OR user_name = ?) AND left_at IS NULL
            )
            ORDER BY g.updated_at DESC
        """, (int(tg_user_id), current_user, int(tg_user_id), current_user))
    else:
        c.execute("""
            SELECT g.chat_id, g.title, g.photo_file, g.member_count,
                   (SELECT COUNT(*) FROM members m2 WHERE m2.chat_id = g.chat_id AND m2.user_name = ? AND m2.left_at IS NULL) as in_group
            FROM groups g
            WHERE g.chat_id IN (
                SELECT chat_id FROM members WHERE user_name = ? AND left_at IS NULL
            )
            ORDER BY g.updated_at DESC
        """, (current_user, current_user))

    rows = c.fetchall()

    # Also include chat_ids where user appears in expenses but no groups row exists yet
    # Exclude groups the user has explicitly left
    c.execute("""
        SELECT DISTINCT chat_id FROM expenses e
        WHERE e.user_name = ? AND e.chat_id != 'default'
        AND e.chat_id NOT IN (SELECT chat_id FROM members WHERE user_name = ? AND left_at IS NOT NULL)
    """, (current_user, current_user))
    expense_only = [r["chat_id"] for r in c.fetchall()]
    known = {r["chat_id"] for r in rows}
    extra = [cid for cid in expense_only if cid not in known]

    out = []
    dead_chat_ids = []
    for r in rows:
        chat_id = r["chat_id"]
        # If title is missing, try to fetch it from Telegram
        if not r["title"]:
            fetched_title = ensure_group_title_and_photo(chat_id, conn)
            if fetched_title:
                r = dict(r)  # convert sqlite Row to mutable dict
                r["title"] = fetched_title
            else:
                # Chat doesn't exist — skip it
                dead_chat_ids.append(chat_id)
                continue
        # compute net balance for current_user in this group (converted to group default)
        c.execute("SELECT currency, COUNT(*) as cnt FROM expenses WHERE chat_id=? AND amount > 0 AND tx_id != 'join' AND description != 'Settled debt' GROUP BY currency ORDER BY cnt DESC LIMIT 1", (chat_id,))
        gdef_row = c.fetchone()
        gdef_cur = gdef_row["currency"] if gdef_row else "EUR"
        c.execute("SELECT currency, SUM(amount) as s FROM expenses WHERE chat_id=? AND user_name=? GROUP BY currency", (chat_id, current_user))
        net = 0.0
        for erow in c.fetchall():
            net += convert_to(gdef_cur, erow["s"] or 0, erow["currency"] or "EUR")
        net = round(net, 2)
        # total group spend (sum of positive expenses in default currency)
        c.execute("SELECT currency, SUM(amount) as s FROM expenses WHERE chat_id=? AND amount > 0 AND tx_id != 'join' AND description != 'Settled debt' GROUP BY currency", (chat_id,))
        total = 0.0
        for srow in c.fetchall():
            total += convert_to(gdef_cur, srow["s"] or 0, srow["currency"] or "EUR")
        total = round(total, 2)
        title = r["title"] or ("Group " + chat_id.replace("-100", "").replace("-", "")[-6:])
        out.append({
            "chat_id": chat_id,
            "title": title,
            "photo_file": r["photo_file"],
            "member_count": r["member_count"] or 0,
            "net": net,
            "total_spend": total,
            "default_currency": gdef_cur,
        })

    for chat_id in extra:
        # Try to fetch real title from Telegram
        fetched_title = ensure_group_title_and_photo(chat_id, conn)
        if not fetched_title:
            # Chat doesn't exist or bot was kicked — skip
            continue
        # Determine group default currency
        c.execute("SELECT currency, COUNT(*) as cnt FROM expenses WHERE chat_id=? AND amount > 0 AND tx_id != 'join' AND description != 'Settled debt' GROUP BY currency ORDER BY cnt DESC LIMIT 1", (chat_id,))
        gdef_erow = c.fetchone()
        gdef_ecur = gdef_erow["currency"] if gdef_erow else "EUR"
        c.execute("SELECT currency, SUM(amount) as s FROM expenses WHERE chat_id=? AND user_name=? GROUP BY currency", (chat_id, current_user))
        net = 0.0
        for erow in c.fetchall():
            net += convert_to(gdef_ecur, erow["s"] or 0, erow["currency"] or "EUR")
        net = round(net, 2)
        c.execute("SELECT currency, SUM(amount) as s FROM expenses WHERE chat_id=? AND amount > 0 AND tx_id != 'join' AND description != 'Settled debt' GROUP BY currency", (chat_id,))
        total = 0.0
        for srow in c.fetchall():
            total += convert_to(gdef_ecur, srow["s"] or 0, srow["currency"] or "EUR")
        total = round(total, 2)
        c.execute("SELECT COUNT(DISTINCT user_name) as n FROM expenses WHERE chat_id=?", (chat_id,))
        mc = c.fetchone()["n"]
        c.execute("SELECT photo_file FROM groups WHERE chat_id=?", (chat_id,))
        gphoto = c.fetchone()
        photo_file = gphoto["photo_file"] if gphoto else None
        out.append({
            "chat_id": chat_id,
            "title": fetched_title,
            "photo_file": photo_file,
            "member_count": mc,
            "net": net,
            "total_spend": total,
            "default_currency": gdef_ecur,
        })

    # Clean up dead groups (bot was kicked or group was deleted)
    for dead_id in dead_chat_ids:
        c.execute("DELETE FROM groups WHERE chat_id=?", (dead_id,))
        c.execute("DELETE FROM members WHERE chat_id=?", (dead_id,))
        conn.commit()

    conn.close()
    return {"groups": out}


@app.get("/api/group_photo")
def group_photo(chat_id: str = Query("")):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT photo_file FROM groups WHERE chat_id=?", (chat_id,))
    r = c.fetchone()
    conn.close()
    if r and r["photo_file"]:
        path = os.path.join(PHOTOS_DIR, r["photo_file"])
        if os.path.exists(path):
            return FileResponse(path, media_type="image/jpeg")
    return Response(status_code=404)


@app.post("/api/leave_group")
async def leave_group(req: Request):
    """Mark the current user as having left a group so it disappears from their wallet."""
    data = await req.json()
    chat_id = str(data.get("chat_id", ""))
    user_name = data.get("user_name", "")
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE members SET left_at=strftime('%s','now') WHERE chat_id=? AND user_name=?", (chat_id, user_name))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/api/balances")
def get_balances(
    chat_id: str = "default",
    current_user: str = "",
    tg_user_id: str = "",
    username: str = "",
    photo_url: str = "",
    chat_title: str = "",
):
    conn = get_db()
    cursor = conn.cursor()

    # Register or update membership for the current user.
    # Send a "joined" notification the first time we see this user in this group.
    joined_now = False
    if current_user:
        cursor.execute("SELECT left_at FROM members WHERE chat_id=? AND user_name=?", (chat_id, current_user))
        existing = cursor.fetchone()
        if not existing:
            joined_now = True
            cursor.execute(
                "INSERT INTO members (chat_id, user_name, tg_user_id, username, photo_url, first_seen, left_at) VALUES (?, ?, ?, ?, ?, strftime('%s','now'), NULL)",
                (chat_id, current_user, int(tg_user_id) if tg_user_id else None, username or None, photo_url or None),
            )
        else:
            # update mutable fields and re-join if previously left
            cursor.execute(
                "UPDATE members SET tg_user_id=COALESCE(?, tg_user_id), username=COALESCE(?, username), photo_url=COALESCE(?, photo_url), left_at=NULL WHERE chat_id=? AND user_name=?",
                (int(tg_user_id) if tg_user_id else None, username or None, photo_url or None, chat_id, current_user),
            )
            if existing["left_at"]:
                joined_now = True  # re-joining

        # Also make sure user appears in expenses (existing behavior) so balances include them
        cursor.execute("SELECT 1 FROM expenses WHERE chat_id=? AND user_name=?", (chat_id, current_user))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO expenses (chat_id, tx_id, user_name, amount, description) VALUES (?, ?, ?, ?, ?)", (chat_id, "join", current_user, 0.0, "Joined group"))

    # Upsert group row
    # FIX (bug 1): never let a client-supplied title clobber a title we
    # already have stored (it's usually just a placeholder — see index.html).
    if chat_id and chat_id != "default":
        cursor.execute("SELECT title FROM groups WHERE chat_id=?", (chat_id,))
        existing_group = cursor.fetchone()
        if not existing_group:
            cursor.execute("INSERT INTO groups (chat_id, title, photo_file, member_count, updated_at) VALUES (?, ?, NULL, 0, strftime('%s','now'))", (chat_id, chat_title or None))
        elif chat_title and not existing_group["title"]:
            cursor.execute("UPDATE groups SET title=? WHERE chat_id=?", (chat_title, chat_id))
        # update member_count
        cursor.execute("SELECT COUNT(*) as n FROM members WHERE chat_id=?", (chat_id,))
        mc = cursor.fetchone()["n"]
        cursor.execute("UPDATE groups SET member_count=?, updated_at=strftime('%s','now') WHERE chat_id=?", (mc, chat_id))

    conn.commit()

    if joined_now and chat_id != "default" and chat_id.startswith("-"):
        try:
            msg = f"{current_user} joined the split group"
            send_telegram_message(chat_id, msg)
        except Exception:
            pass

    cursor.execute("SELECT DISTINCT user_name FROM expenses WHERE chat_id=?", (chat_id,))
    users = [r["user_name"] for r in cursor.fetchall()]

    cursor.execute("SELECT user_name, currency, SUM(amount) as balance FROM expenses WHERE chat_id=? GROUP BY user_name, currency", (chat_id,))
    balances_cur = {}
    all_currencies = set()
    for row in cursor.fetchall():
        cur = row["currency"] or "EUR"
        all_currencies.add(cur)
        balances_cur.setdefault(row["user_name"], {})[cur] = round(row["balance"], 2)

    # Determine default currency (most popular by positive expense count)
    # FIX (bug 2/3): deterministic tie-break — previously ties resolved
    # essentially at random.
    cursor.execute(
        "SELECT currency, COUNT(*) as cnt, SUM(amount) as total FROM expenses "
        "WHERE chat_id=? AND amount > 0 AND tx_id != 'join' AND description != 'Settled debt' "
        "GROUP BY currency ORDER BY cnt DESC, total DESC, currency ASC",
        (chat_id,),
    )
    cur_rows = cursor.fetchall()
    if cur_rows:
        default_currency = cur_rows[0]["currency"] or "EUR"
        currency_counts = {r["currency"] or "EUR": r["cnt"] for r in cur_rows}
        currencies = [r["currency"] or "EUR" for r in cur_rows]
    else:
        default_currency = "EUR"
        currency_counts = {}
        currencies = ["EUR"]

    for c in all_currencies:
        if c not in currencies:
            currencies.append(c)

    balances = {}
    for u in users:
        net = 0.0
        for cur, amt in balances_cur.get(u, {}).items():
            net += convert_to(default_currency, amt, cur)
        balances[u] = round(net, 2)

    cursor.execute("SELECT id, tx_id, user_name, amount, description, currency FROM expenses WHERE chat_id=? AND amount > 0 AND tx_id != 'join' AND description != 'Settled debt' ORDER BY id DESC LIMIT 20", (chat_id,))
    recent = [{"id": r["id"], "tx_id": r["tx_id"], "user": r["user_name"], "amount": r["amount"], "desc": r["description"], "currency": r["currency"] or "EUR"} for r in cursor.fetchall()]

    # Per-user actual spend (sum of each person's share, converted to default currency)
    cursor.execute(
        "SELECT user_name, currency, SUM(ABS(amount)) as total "
        "FROM expenses "
        "WHERE chat_id=? AND amount < 0 AND tx_id != 'join' AND description != 'Settled debt' "
        "GROUP BY user_name, currency",
        (chat_id,),
    )
    expense_shares = {}
    for row in cursor.fetchall():
        u = row["user_name"]
        cur = row["currency"] or "EUR"
        share = row["total"] or 0
        expense_shares[u] = round(expense_shares.get(u, 0) + convert_to(default_currency, share, cur), 2)

    cursor.execute("SELECT id, tx_id, user_name, amount, currency FROM expenses WHERE chat_id=? AND description = 'Settled debt' ORDER BY id DESC LIMIT 40", (chat_id,))
    rows = cursor.fetchall()
    by_tx = {}
    for r in rows:
        by_tx.setdefault(r["tx_id"], []).append(r)
    settled_history = []
    for tx_id, entries in by_tx.items():
        pos = next((e for e in entries if e["amount"] > 0), None)
        neg = next((e for e in entries if e["amount"] < 0), None)
        if pos and neg:
            settled_history.append({
                "id": max(pos["id"], neg["id"]),
                "tx_id": tx_id,
                "from": pos["user_name"],
                "to": neg["user_name"],
                "amount": round(pos["amount"], 2),
                "currency": pos["currency"] or "EUR",
            })
    settled_history.sort(key=lambda x: x["id"], reverse=True)
    settled_history = settled_history[:10]

    # Build user info list with photo_url
    members_map = get_members_map(cursor, chat_id)
    users_info = []
    for u in users:
        m = members_map.get(u, {})
        users_info.append({
            "name": u,
            "photo_url": m.get("photo_url"),
            "username": m.get("username"),
            "tg_user_id": m.get("tg_user_id"),
        })

    # Fetch stored group title — if missing, try to get it from Telegram API
    group_title = None
    if chat_id and chat_id != "default":
        cursor.execute("SELECT title FROM groups WHERE chat_id=?", (chat_id,))
        gr = cursor.fetchone()
        if gr and gr["title"]:
            group_title = gr["title"]
        else:
            group_title = ensure_group_title_and_photo(chat_id, conn)

    conn.close()

    # Per-currency transfers
    transfers_all = []
    for cur in currencies:
        cur_balances = {}
        for u in users:
            cur_balances[u] = balances_cur.get(u, {}).get(cur, 0.0)
        cur_transfers = _net_transfers(cur_balances)
        for t in cur_transfers:
            t["currency"] = cur
        transfers_all.extend(cur_transfers)

    for u in users:
        if u not in balances:
            balances[u] = 0.0

    # Net transfers in default currency
    debtors = [[u, abs(b)] for u, b in balances.items() if b < -0.01]
    creditors = [[u, b] for u, b in balances.items() if b > 0.01]
    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)
    transfers = []
    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        d_user, d_amt = debtors[i]
        c_user, c_amt = creditors[j]
        transfer_amt = round(min(d_amt, c_amt), 2)
        transfers.append({"from": d_user, "to": c_user, "amount": transfer_amt, "currency": default_currency})
        debtors[i][1] -= transfer_amt
        creditors[j][1] -= transfer_amt
        if debtors[i][1] < 0.01:
            i += 1
        if creditors[j][1] < 0.01:
            j += 1

    return {
        "balances": balances,
        "balances_per_currency": balances_cur,
        "expenses": recent,
        "users": users,
        "users_info": users_info,
        "transfers": transfers,
        "transfers_by_currency": transfers_all,
        "settlements": settled_history,
        "group_title": group_title,
        "default_currency": default_currency,
        "currency_counts": currency_counts,
        "currencies": currencies,
        "expense_shares": expense_shares,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)