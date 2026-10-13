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
or pass --plain. Use --markdown to write with **bold** MarkdownV2 syntax; all other
special characters are escaped automatically, so plain text never breaks the message.
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

# Characters Telegram's MarkdownV2 treats specially — escaped automatically.
_MD_SPECIAL = set(r"_*[]()~`>#+-=|{}.!\\")


def to_markdown_v2(text: str) -> str:
    """Escape MarkdownV2 specials, keeping **bold** markers intact."""
    out = []
    for i, part in enumerate(text.split("**")):
        if i % 2 == 1:  # inside a bold span — escape content, keep markers
            out.append("**" + _escape_md(part) + "**")
        else:
            out.append(_escape_md(part))
    return "".join(out)


def _escape_md(text: str) -> str:
    return "".join("\\" + ch if ch in _MD_SPECIAL else ch for ch in text)


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
    ap.add_argument("--plain", action="store_true", help="send as plain text (no formatting)")
    ap.add_argument("--markdown", action="store_true", help="send as MarkdownV2 (**bold** works, other specials auto-escaped)")
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

    if args.plain and args.markdown:
        ap.error("--plain and --markdown are mutually exclusive")
    if args.markdown:
        parse_mode, text = "MarkdownV2", to_markdown_v2(text)
    elif args.plain:
        parse_mode = None
    else:
        parse_mode = "HTML"

    if args.test:
        user_ids = [args.test]
    else:
        user_ids = load_user_ids()
        if args.dry_run:
            print(f"Dry run — {len(user_ids)} recipient(s), nothing sent:")
            for uid in user_ids:
                print(f"  {uid}")
            print()
            print(f"Message as it will be sent ({parse_mode or 'plain text'}):")
            print(text)
            return

    if not BOT_TOKEN:
        sys.exit("BOT_TOKEN is not set (add it to .env)")
    print(f"Sending to {len(user_ids)} user(s)...")
    print(f"Message ({parse_mode or 'plain text'}):")
    print(text)
    print()
    sent, failed = 0, []
    for i, uid in enumerate(user_ids, 1):
        try:
            resp = send_message(uid, text, parse_mode)
            sent += 1
            print(f"[{i}/{len(user_ids)}] sent to {uid}")
            result = resp.get("result") or {}
            entities = result.get("entities") or []
            if entities:
                print("  Telegram stored formatting entities: " + ", ".join(
                    f"{e.get('type')}@{e.get('offset')}:{e.get('length')}" for e in entities
                ))
            else:
                print("  (no formatting entities — message is plain text)")
            stored = result.get("text")
            if stored is not None:
                print("  Stored text (exactly as Telegram has it):")
                print("  " + str(stored).replace("\n", "\n  "))
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
