# Nova Bot — Telegram Expense-Splitting Mini App

Track shared expenses in Telegram groups with a Wallet-style interface. Add expenses, settle debts, and see who owes whom — all inside the Telegram Mini App.

## Features

- **Group expense tracking**  — add expenses with custom splits (equal or per-person amounts)
- **Multi-currency**  — USD, EUR, GBP, GEL, RUB with approximate conversions
- **Wallet view** — swipe through all your groups, see your net balance at a glance
- **Balances deck** — card-style overview of who owes whom
- **Settle up** — suggested payments to zero out debts
- **Activity log** — recent transactions with delete support
- **Telegram Native** — back button, haptic feedback, inline keyboard deep links

## Architecture

```
bot.py     — Aiogram bot, handles /start and /split commands, sends deep links
app.py     — FastAPI server, serves the Mini App + REST API (port 8000)
index.html — Single-page frontend (Telegram WebApp SDK)
config.py  — Environment config, loads BOT_TOKEN from .env
```

## Local Setup

```bash
git clone https://github.com/YOUR_USER/nova-bot
cd nova-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env — add your BOT_TOKEN from @BotFather
python app.py &    # FastAPI on port 8000
python bot.py      # Telegram bot (long polling)
```


## Deep links

- **Group chat** — `/split` opens the Mini App with `startapp=g<chat_id>`, showing that group's expenses
- **Private chat** — `/start` opens the wallet with all your groups
- **Add to group** — the "Add Nova to a group" button uses `startgroup=true`

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/balances` | GET | Per-currency balances, transfers, group info |
| `/api/groups` | GET | List of groups the user belongs to |
| `/api/add` | POST | Add a new expense |
| `/api/delete` | POST | Delete an expense by tx_id |
| `/api/settle` | POST | Record a settlement payment |
| `/api/group_photo` | GET | Serve cached group profile photo |
| `/api/leave_group` | POST | Remove user from a group |

## License

MIT
