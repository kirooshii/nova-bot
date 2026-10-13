import hashlib
import hmac
import json
import sqlite3
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import app as app_module

BOT_TOKEN = "test:token"
CHAT_ID = "-100123"


def sign_init_data(fields, bot_token=BOT_TOKEN):
    payload = dict(fields)
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(payload.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    payload["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(payload)


def make_init_data(user, auth_date=None):
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user, separators=(",", ":")),
    }
    return sign_init_data(fields)


def _make_db_conn(db_path):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_module, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(app_module, "get_db", lambda: _make_db_conn(db_path))
    monkeypatch.setattr(app_module, "send_telegram_message", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "fetch_chat_info", lambda chat_id: (None, None))
    app_module.init_db()
    with TestClient(app_module.app) as c:
        yield c


def seed_member(chat_id=CHAT_ID, user_name="Kir", tg_user_id=123):
    conn = app_module.get_db()
    conn.execute(
        "INSERT INTO members (chat_id, user_name, tg_user_id, username, photo_url, first_seen, left_at) "
        "VALUES (?, ?, ?, NULL, NULL, strftime('%s','now'), NULL)",
        (chat_id, user_name, tg_user_id),
    )
    conn.commit()
    conn.close()


def headers_for(user):
    return {"X-Telegram-Init-Data": make_init_data(user)}


# ---- 1. Missing initData -> 401 on every write endpoint ----
def test_missing_init_data_rejected_on_all_write_endpoints(client):
    assert client.post("/api/add", json={"chat_id": CHAT_ID, "user": "Kir", "amount": 10, "splits": {"Kir": 10}}).status_code == 401
    assert client.post("/api/delete", json={"chat_id": CHAT_ID, "tx_id": "x"}).status_code == 401
    assert client.post("/api/settle", json={"chat_id": CHAT_ID, "from_user": "Kir", "to_user": "Anna", "amount": 5}).status_code == 401
    assert client.post("/api/leave_group", json={"chat_id": CHAT_ID}).status_code == 401
    assert client.get("/api/balances", params={"chat_id": CHAT_ID}).status_code == 401


# ---- 2. Tampered initData -> 401 ----
def test_tampered_init_data_rejected(client):
    seed_member()
    init = make_init_data({"id": 123, "first_name": "Kir"})
    tampered = init + "&extra=1"
    assert client.post(
        "/api/delete",
        headers={"X-Telegram-Init-Data": tampered},
        json={"chat_id": CHAT_ID, "tx_id": "x"},
    ).status_code == 401

    wrong_token_signed = sign_init_data(
        {"auth_date": str(int(time.time())), "user": json.dumps({"id": 123, "first_name": "Kir"})},
        bot_token="attacker:token",
    )
    assert client.get("/api/balances", params={"chat_id": CHAT_ID}, headers={"X-Telegram-Init-Data": wrong_token_signed}).status_code == 401


# ---- 3. Valid initData resolves to the matching members.user_name ----
def test_valid_init_data_resolves_member_and_allows_writes(client):
    seed_member(user_name="Kir", tg_user_id=123)
    seed_member(user_name="Anna", tg_user_id=456)
    h = headers_for({"id": 123, "first_name": "Kir", "username": "kir"})

    r = client.get("/api/balances", params={"chat_id": CHAT_ID, "chat_title": "Test Group"}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["me"] == "Kir"

    # adding on someone else's behalf is allowed (payer != caller), caller still verified
    r = client.post("/api/add", headers=h, json={
        "chat_id": CHAT_ID, "user": "Anna", "amount": 100, "desc": "Dinner",
        "splits": {"Kir": 50, "Anna": 50}, "currency": "EUR",
    })
    assert r.status_code == 200

    tx = "testtx"
    conn = app_module.get_db()
    conn.execute(
        "INSERT INTO expenses (chat_id, tx_id, user_name, amount, description, currency) VALUES (?, ?, ?, ?, ?, ?)",
        (CHAT_ID, tx, "Anna", 100, "Test", "EUR"),
    )
    conn.commit()
    conn.close()
    assert client.post("/api/delete", headers=h, json={"chat_id": CHAT_ID, "tx_id": tx}).status_code == 200

    # settle where caller is one of the parties
    assert client.post("/api/settle", headers=h, json={
        "chat_id": CHAT_ID, "from_user": "Kir", "to_user": "Anna", "amount": 25, "currency": "EUR",
    }).status_code == 200

    # settle between two other people -> 403
    assert client.post("/api/settle", headers=h, json={
        "chat_id": CHAT_ID, "from_user": "Anna", "to_user": "Someone Else", "amount": 25, "currency": "EUR",
    }).status_code == 403

    # leaving only affects the verified caller
    assert client.post("/api/leave_group", headers=h, json={"chat_id": CHAT_ID}).status_code == 200
    conn = app_module.get_db()
    kir = conn.execute("SELECT left_at FROM members WHERE chat_id=? AND user_name=?", (CHAT_ID, "Kir")).fetchone()
    anna = conn.execute("SELECT left_at FROM members WHERE chat_id=? AND user_name=?", (CHAT_ID, "Anna")).fetchone()
    conn.close()
    assert kir["left_at"] is not None
    assert anna["left_at"] is None


def test_non_member_write_rejected_403(client):
    h = headers_for({"id": 999, "first_name": "Stranger"})
    assert client.post("/api/delete", headers=h, json={"chat_id": CHAT_ID, "tx_id": "x"}).status_code == 403
    assert client.post("/api/add", headers=h, json={
        "chat_id": CHAT_ID, "user": "Stranger", "amount": 10, "splits": {"Stranger": 10},
    }).status_code == 403
    assert client.post("/api/leave_group", headers=h, json={"chat_id": CHAT_ID}).status_code == 403


def test_leave_group_cannot_remove_someone_else(client):
    seed_member(user_name="Kir", tg_user_id=123)
    seed_member(user_name="Anna", tg_user_id=456)
    h = headers_for({"id": 456, "first_name": "Anna"})
    assert client.post("/api/leave_group", headers=h, json={"chat_id": CHAT_ID, "user_name": "Kir"}).status_code == 403


def test_balances_registration_ignores_client_supplied_tg_user_id(client):
    # client tries to register as existing user "Kir" with a different verified id
    seed_member(user_name="Kir", tg_user_id=123)
    h = headers_for({"id": 456, "first_name": "Anna"})
    r = client.get("/api/balances", params={"chat_id": CHAT_ID, "tg_user_id": "123", "current_user": "Kir"}, headers=h)
    assert r.status_code == 200
    assert r.json()["me"] == "Anna"
    conn = app_module.get_db()
    kir = conn.execute("SELECT tg_user_id FROM members WHERE chat_id=? AND user_name=?", (CHAT_ID, "Kir")).fetchone()
    conn.close()
    assert kir["tg_user_id"] == 123  # untouched


# ---- 4. Replay protection ----
def test_expired_init_data_rejected(client, monkeypatch):
    monkeypatch.setattr(app_module, "INIT_DATA_MAX_AGE_SECONDS", 60)
    seed_member()
    stale = make_init_data({"id": 123, "first_name": "Kir"}, auth_date=int(time.time()) - 120)
    assert client.post(
        "/api/delete",
        headers={"X-Telegram-Init-Data": stale},
        json={"chat_id": CHAT_ID, "tx_id": "x"},
    ).status_code == 401


def test_verify_init_data_unit():
    init = make_init_data({"id": 123, "first_name": "Kir", "username": "kir"})
    user = app_module.verify_init_data(init, BOT_TOKEN)
    assert user["id"] == 123
    assert user["username"] == "kir"

    assert app_module.verify_init_data("", BOT_TOKEN) is None
    assert app_module.verify_init_data(init, "wrong:token") is None
    assert app_module.verify_init_data(init + "&x=y", BOT_TOKEN) is None

    stale = make_init_data({"id": 123}, auth_date=int(time.time()) - 1000)
    assert app_module.verify_init_data(stale, BOT_TOKEN, max_age_seconds=60) is None
    assert app_module.verify_init_data(stale, BOT_TOKEN, max_age_seconds=0) is not None
