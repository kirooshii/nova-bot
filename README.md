# Nova Bot — Telegram Splitwise Mini App

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
bot.py   — Aiogram bot, handles /start and /split commands, sends deep links
app.py   — FastAPI server, serves the Mini App and REST API
index.html — Single-page frontend (Telegram WebApp SDK)
config.py — Environment config (BOT_TOKEN)
```

## Setup

```bash
# Clone
git clone https://github.com/YOUR_USER/nova-bot
cd nova-bot

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set your bot token
cp .env.example .env
# Edit .env and add your BOT_TOKEN from @BotFather

# Run both servers
python app.py &    # FastAPI on port 8000
python bot.py      # Telegram bot (long polling)
```

Expose `app.py` (port 8000) via ngrok or a public server, then set the Mini App URL in @BotFather to `https://your-domain.com`.

## Deep links

- **Group chat**  — `/split` opens the Mini App with `startapp=g<chat_id>`, showing that group's expenses
- **Private chat**  — `/start` opens the wallet with all your groups
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
