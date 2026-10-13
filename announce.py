"""
One-off announcement tool: PM every Nova user a message.

Recipients are the distinct tg_user_id values from the members table
(active members only — left_at IS NULL). Telegram only allows a bot to
message users who have already started a private chat with the bot;
users who never did will fail with HTTP 403 and are reported as
unreachable at the end.

Usage:
  python announce.py --dry-run "Your message here"      # list recipients, send nothing
  python announce.py --test 123456789 "Your message"    # preview to one user id only
  python announce.py "Your message here"                # send to everyone
  python announce.py --file message.txt                 # read message from a file
  python announce.py --plain "No <b>HTML</b> here"      # send as plain text

HTML (<b>, <i>, <a href=...>) is enabled by default — escape & < > when not wanted,
or pass --plain.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request

from config import BOT_TOKEN


def get_db():
    conn = sqlite3.connect("nova.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def load_user_ids():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT tg_user_id FROM members "
        "WHERE tg_user_id IS NOT NULL AND tg_user_id > 0 AND left_at IS NULL "
        "ORDER BY tg_user_id"
    ).fetchall()
    conn.close()
    ids = []
    for r in rows:
        try:
            ids.append(int(r["tg_user_id"]))
        except (TypeError, ValueError):
            pass
    return ids


def send_message(user_id: int, text: str, parse_mode: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = json.dumps({
        "chat_id": user_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def describe_403(detail: str) -> str:
    if "blocked by the user" in detail:
        return "user blocked the bot"
    if "can't initiate conversation" in detail:
        return "user never started a private chat with the bot"
    return "forbidden"


def main():
    ap = argparse.ArgumentParser(description="PM an announcement to all Nova users")
    ap.add_argument("message", nargs="?", help="message text (HTML allowed)")
    ap.add_argument("--file", help="read message text from a file")
    ap.add_argument("--plain", action="store_true", help="send as plain text (no HTML parsing)")
    ap.add_argument("--dry-run", action="store_true", help="list recipients without sending")
    ap.add_argument("--test", type=int, metavar="TG_USER_ID", help="send only to this one user id (preview)")
    ap.add_argument("--sleep", type=float, default=0.1, help="delay between sends in seconds (default: 0.1)")
    args = ap.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read().strip()
    else:
        text = (args.message or "").strip()
    if not text:
        ap.error("provide a message or use --file")

    if args.test:
        user_ids = [args.test]
    else:
        user_ids = load_user_ids()
        if args.dry_run:
            print(f"Dry run — {len(user_ids)} recipient(s), nothing sent:")
            for uid in user_ids:
                print(f"  {uid}")
            return

    if not BOT_TOKEN:
        sys.exit("BOT_TOKEN is not set (add it to .env)")
    print(f"Sending to {len(user_ids)} user(s)...")

    parse_mode = None if args.plain else "HTML"
    sent, failed = 0, []
    for i, uid in enumerate(user_ids, 1):
        try:
            send_message(uid, text, parse_mode)
            sent += 1
            print(f"[{i}/{len(user_ids)}] sent to {uid}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            reason = describe_403(detail) if e.code == 403 else f"HTTP {e.code}"
            failed.append((uid, reason))
            print(f"[{i}/{len(user_ids)}] FAILED {uid}: {reason} — {detail[:120]}")
            if e.code == 429:  # flood control — respect retry_after
                try:
                    wait = json.loads(detail).get("parameters", {}).get("retry_after", 3)
                except Exception:
                    wait = 3
                print(f"  rate limited — waiting {wait}s")
                time.sleep(float(wait))
        except Exception as e:
            failed.append((uid, str(e)))
            print(f"[{i}/{len(user_ids)}] FAILED {uid}: {e}")
        time.sleep(args.sleep)

    print()
    print(f"Done. Sent: {sent}, failed: {len(failed)}")
    for uid, reason in failed:
        print(f"  {uid}: {reason}")


if __name__ == "__main__":
    main()
