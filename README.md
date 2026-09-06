# Telegram Marketplace Bot

Single-file Telegram marketplace bot with internal wallet, deposits, withdrawals, product marketplace, admin panel, atomic transactions & ledger.

## Features

**User**
- Balance + transaction history
- Deposit (bKash / Nagad / Binance etc.) → pending → admin approve
- Marketplace (category → subcategory → product → quantity → confirm)
- Atomic purchase (balance check + stock check + transfer to owner)
- Withdrawal (amount reserved on request)
- My Orders
- Support message to admins

**Admin** (set via `ADMIN_USER_ID`)
- Stats
- Pending Deposits / Withdrawals (approve / reject with refund)
- Users & Balance (add / deduct with ledger)
- Broadcast
- Payment Method setup
- Add products (with owner)
- List products

## Important Rules Implemented

- All balance changes go through ledger
- Purchase is atomic (single DB transaction)
- Stock & balance re-checked at confirm time
- No negative balance
- Back / Cancel on multi-step flows
- Unique order numbers
- Admin actions protected by user_id check

---

## Deploy on Render (Free Web Service) – Recommended

### 1. GitHub-এ আপলোড করুন
- `bot.py`
- `requirements.txt`
- `README.md`

### 2. Render.com → New → **Web Service**
- GitHub রিপোজিটরি কানেক্ট করুন
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python bot.py`
- Instance Type: **Free**

### 3. Environment Variables যোগ করুন

| Key             | Value                                      |
|-----------------|--------------------------------------------|
| `BOT_TOKEN`     | আপনার BotFather টোকেন                      |
| `ADMIN_USER_ID` | আপনার Telegram User ID (উদাহরণ: 123456789) |

একাধিক অ্যাডমিন হলে:
```
ADMIN_USER_ID=123456789,987654321
```

### 4. Deploy করুন

বট অটোমেটিকভাবে **Webhook** মোডে চালু হবে (কারণ Render `PORT` এবং `RENDER_EXTERNAL_URL` দেয়)।

---

## Local Run (Polling)

```bash
export BOT_TOKEN="123456:ABC..."
export ADMIN_USER_ID="123456789"
pip install -r requirements.txt
python bot.py
```

---

## Notes / Limitations (Free Tier)

- Free Web Service **১৫ মিনিট** কোনো ট্রাফিক না পেলে sleep করে।
- Sleep থেকে উঠতে ১০-৩০ সেকেন্ড সময় লাগতে পারে (cold start)।
- SQLite ডেটা রিস্টার্ট/রিডিপ্লয় হলে মুছে যায় (ephemeral disk)।
- প্রোডাকশনে চাইলে PostgreSQL + paid plan ব্যবহার করুন।

---

## Database

SQLite file `marketplace.db` is created automatically.

Default payment methods (bKash, Nagad, Binance) এবং ক্যাটাগরি seed করা আছে।
