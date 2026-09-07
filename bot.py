#!/usr/bin/env python3
"""
Telegram Marketplace Bot - Single File Implementation
=====================================================
Features:
- User: Balance, Deposit, Marketplace (buy), Withdrawal, Support, Orders
- Admin: Sales stats, Users & Balance (add/deduct), Orders management,
         Broadcast, Marketplace (add/edit products, categories), Payment Methods
- Atomic financial transactions + ledger
- Back/Cancel on multi-step flows
- Input validation, stock & balance checks
- Unique order IDs
- Admin authorization by ADMIN_USER_ID

Deploy on Render (Free Web Service) - Webhook mode:
  Build:  pip install -r requirements.txt
  Start:  python bot.py
  Env:    BOT_TOKEN, ADMIN_USER_ID

  The bot automatically uses webhook when running on Render
  (detects RENDER_EXTERNAL_URL or PORT).

SQLite is used (ephemeral on free Render - data resets on restart).
For production use PostgreSQL + persistent disk or external DB.
"""

import os
import sqlite3
import logging
import re
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, List, Tuple, Dict, Any

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

# ==================== CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_USER_ID", "0").split(",") if x.strip().isdigit()]

DB_PATH = os.getenv("DB_PATH", "marketplace.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== STATES ====================
(
    # Deposit
    DEP_METHOD, DEP_AMOUNT, DEP_TXID, DEP_CONFIRM,
    # Withdrawal
    WD_AMOUNT, WD_METHOD, WD_DETAILS, WD_CONFIRM,
    # Purchase
    BUY_CAT, BUY_SUB, BUY_PRODUCT, BUY_QTY, BUY_CONFIRM,
    # Admin: Add Product
    ADM_P_NAME, ADM_P_DESC, ADM_P_PRICE, ADM_P_STOCK, ADM_P_CAT, ADM_P_SUB, ADM_P_OWNER, ADM_P_CONFIRM,
    # Admin: Edit / other
    ADM_BAL_USER, ADM_BAL_AMOUNT, ADM_BAL_REASON, ADM_BAL_CONFIRM,
    ADM_BROADCAST_MSG, ADM_BROADCAST_CONFIRM,
    ADM_PAY_METHOD, ADM_PAY_DETAILS,
    # Support
    SUPPORT_MSG,
) = range(30)

# ==================== DATABASE ====================
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

@contextmanager
def db_transaction():
    """Atomic transaction context manager."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        c = conn.cursor()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL NOT NULL DEFAULT 0.0,
            created_at TEXT NOT NULL,
            is_banned INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS subcategories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (category_id) REFERENCES categories(id),
            UNIQUE(category_id, name)
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            category_id INTEGER,
            subcategory_id INTEGER,
            owner_id INTEGER NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY (category_id) REFERENCES categories(id),
            FOREIGN KEY (subcategory_id) REFERENCES subcategories(id),
            FOREIGN KEY (owner_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS payment_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            details TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            txid TEXT,
            status TEXT NOT NULL DEFAULT 'pending',  -- pending, approved, rejected
            admin_note TEXT,
            created_at TEXT NOT NULL,
            processed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            details TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',  -- pending, approved, rejected
            admin_note TEXT,
            created_at TEXT NOT NULL,
            processed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT NOT NULL UNIQUE,
            buyer_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            total_price REAL NOT NULL,
            owner_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            created_at TEXT NOT NULL,
            FOREIGN KEY (buyer_id) REFERENCES users(user_id),
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (owner_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,           -- positive = credit, negative = debit
            balance_after REAL NOT NULL,
            type TEXT NOT NULL,             -- deposit, purchase, sale, withdrawal, admin_add, admin_deduct, refund
            reference_id INTEGER,           -- deposit_id / order_id / withdrawal_id etc.
            reference_type TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger(user_id);
        CREATE INDEX IF NOT EXISTS idx_orders_buyer ON orders(buyer_id);
        CREATE INDEX IF NOT EXISTS idx_deposits_status ON deposits(status);
        CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals(status);
        """)
        # Seed default payment methods if empty
        cur = c.execute("SELECT COUNT(*) FROM payment_methods")
        if cur.fetchone()[0] == 0:
            c.executemany(
                "INSERT INTO payment_methods (name, details) VALUES (?, ?)",
                [
                    ("bKash", "Send money to: 01XXXXXXXXX\nAccount Type: Personal\nName: Marketplace"),
                    ("Nagad", "Send money to: 01XXXXXXXXX\nAccount Type: Personal\nName: Marketplace"),
                    ("Binance", "USDT TRC20 Address: TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX\nNetwork: TRC20"),
                ]
            )
        # Seed default categories
        cur = c.execute("SELECT COUNT(*) FROM categories")
        if cur.fetchone()[0] == 0:
            c.execute("INSERT INTO categories (name) VALUES ('Digital Accounts')")
            c.execute("INSERT INTO categories (name) VALUES ('Software / Keys')")
            c.execute("INSERT INTO categories (name) VALUES ('Others')")
            c.execute("INSERT INTO subcategories (category_id, name) VALUES (1, 'Social Media')")
            c.execute("INSERT INTO subcategories (category_id, name) VALUES (1, 'Email')")
            c.execute("INSERT INTO subcategories (category_id, name) VALUES (2, 'License Keys')")
        conn.commit()
    logger.info("Database initialized.")

# ==================== HELPERS ====================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def ensure_user(user_id: int, username: str = None, first_name: str = None):
    with get_conn() as conn:
        cur = conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not cur.fetchone():
            conn.execute(
                "INSERT INTO users (user_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
                (user_id, username, first_name, datetime.utcnow().isoformat())
            )
            conn.commit()

def get_balance(user_id: int) -> float:
    with get_conn() as conn:
        cur = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return float(row["balance"]) if row else 0.0

def get_user(user_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return cur.fetchone()

def format_money(amount: float) -> str:
    return f"{amount:,.2f}"

def next_order_number(conn) -> str:
    cur = conn.execute("SELECT COUNT(*) FROM orders")
    count = cur.fetchone()[0] + 1
    return f"ORD-{1000 + count}"

def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("💰 Balance", callback_data="menu_balance"),
         InlineKeyboardButton("💳 Deposit", callback_data="menu_deposit")],
        [InlineKeyboardButton("🛒 Marketplace", callback_data="menu_marketplace"),
         InlineKeyboardButton("💸 Withdraw", callback_data="menu_withdraw")],
        [InlineKeyboardButton("📦 My Orders", callback_data="menu_orders"),
         InlineKeyboardButton("🆘 Support", callback_data="menu_support")],
    ]
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)

def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Sales / Stats", callback_data="adm_stats"),
         InlineKeyboardButton("👥 Users & Balance", callback_data="adm_users")],
        [InlineKeyboardButton("📋 Orders", callback_data="adm_orders"),
         InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
        [InlineKeyboardButton("🏪 Marketplace Handle", callback_data="adm_marketplace"),
         InlineKeyboardButton("💳 Payment Methods", callback_data="adm_payments")],
        [InlineKeyboardButton("📥 Pending Deposits", callback_data="adm_deposits"),
         InlineKeyboardButton("📤 Pending Withdrawals", callback_data="adm_withdrawals")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="back_main")],
    ])

def back_cancel_keyboard(back_data: str = "back_main", cancel_data: str = "cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data=back_data),
         InlineKeyboardButton("❌ Cancel", callback_data=cancel_data)]
    ])

# ==================== LEDGER & BALANCE ====================
def add_ledger(conn, user_id: int, amount: float, type_: str, reference_id: int = None,
               reference_type: str = None, note: str = None) -> float:
    """Update balance + insert ledger. Returns new balance. Must be called inside transaction."""
    cur = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError("User not found")
    old_bal = float(row["balance"])
    new_bal = round(old_bal + amount, 2)
    if new_bal < -0.001:  # floating tolerance
        raise ValueError("Insufficient balance")
    conn.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_bal, user_id))
    conn.execute(
        """INSERT INTO ledger (user_id, amount, balance_after, type, reference_id, reference_type, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, amount, new_bal, type_, reference_id, reference_type, note, datetime.utcnow().isoformat())
    )
    return new_bal

# ==================== START / MENU ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username, user.first_name)
    text = (
        f"👋 Welcome <b>{user.first_name or 'User'}</b>!\n\n"
        "🛒 <b>Telegram Marketplace Bot</b>\n"
        "Buy & sell digital products safely with internal balance.\n\n"
        "Choose an option below:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=main_menu_keyboard(user.id), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=main_menu_keyboard(user.id), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    return await start(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ Action cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id))
    else:
        await update.message.reply_text("❌ Action cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id))
    context.user_data.clear()
    return ConversationHandler.END

# ==================== BALANCE ====================
async def menu_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    bal = get_balance(user_id)
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT * FROM ledger WHERE user_id = ? ORDER BY id DESC LIMIT 8", (user_id,)
        )
        rows = cur.fetchall()
    lines = [f"💰 <b>Your Balance:</b> <code>{format_money(bal)}</code>\n"]
    if rows:
        lines.append("<b>Recent Transactions:</b>")
        for r in rows:
            sign = "+" if r["amount"] >= 0 else ""
            lines.append(f"• {r['type']}: {sign}{format_money(r['amount'])} → {format_money(r['balance_after'])}")
    else:
        lines.append("No transactions yet.")
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Deposit", callback_data="menu_deposit"),
             InlineKeyboardButton("💸 Withdraw", callback_data="menu_withdraw")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="back_main")]
        ]),
        parse_mode=ParseMode.HTML
    )

# ==================== DEPOSIT FLOW ====================
async def menu_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name FROM payment_methods WHERE is_active = 1")
        methods = cur.fetchall()
    if not methods:
        await query.edit_message_text("No payment methods available. Contact admin.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main", callback_data="back_main")]]))
        return ConversationHandler.END
    buttons = [[InlineKeyboardButton(m["name"], callback_data=f"dep_method_{m['id']}")] for m in methods]
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_main")])
    await query.edit_message_text("💳 <b>Deposit</b>\n\nSelect payment method:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
    return DEP_METHOD

async def dep_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    method_id = int(query.data.split("_")[-1])
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM payment_methods WHERE id = ?", (method_id,))
        method = cur.fetchone()
    if not method:
        await query.edit_message_text("Invalid method.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    context.user_data["dep_method"] = method["name"]
    context.user_data["dep_details"] = method["details"]
    await query.edit_message_text(
        f"💳 Method: <b>{method['name']}</b>\n\n"
        f"<pre>{method['details']}</pre>\n\n"
        "Enter the <b>amount</b> you will send (numbers only):",
        reply_markup=back_cancel_keyboard("menu_deposit"),
        parse_mode=ParseMode.HTML
    )
    return DEP_AMOUNT

async def dep_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid positive number.",
                                        reply_markup=back_cancel_keyboard("menu_deposit"))
        return DEP_AMOUNT
    context.user_data["dep_amount"] = amount
    await update.message.reply_text(
        f"Amount: <b>{format_money(amount)}</b>\n\n"
        "Now send the <b>Transaction ID / TXID / Reference</b> of your payment:",
        reply_markup=back_cancel_keyboard("menu_deposit"),
        parse_mode=ParseMode.HTML
    )
    return DEP_TXID

async def dep_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    if len(txid) < 3:
        await update.message.reply_text("❌ TXID too short. Please send a valid reference.",
                                        reply_markup=back_cancel_keyboard("menu_deposit"))
        return DEP_TXID
    context.user_data["dep_txid"] = txid
    amount = context.user_data["dep_amount"]
    method = context.user_data["dep_method"]
    details = context.user_data["dep_details"]
    text = (
        f"📋 <b>Deposit Preview</b>\n\n"
        f"Method: {method}\n"
        f"Amount: <b>{format_money(amount)}</b>\n"
        f"TXID: <code>{txid}</code>\n\n"
        f"Payment Info:\n<pre>{details}</pre>\n\n"
        "Confirm to create a pending deposit request?"
    )
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm", callback_data="dep_confirm")],
            [InlineKeyboardButton("🔙 Back", callback_data="menu_deposit"),
             InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ]),
        parse_mode=ParseMode.HTML
    )
    return DEP_CONFIRM

async def dep_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    amount = context.user_data.get("dep_amount")
    method = context.user_data.get("dep_method")
    txid = context.user_data.get("dep_txid")
    if not all([amount, method, txid]):
        await query.edit_message_text("Session expired. Start again.", reply_markup=main_menu_keyboard(user.id))
      return ConversationHandler.END
    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO deposits (user_id, amount, method, txid, status, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (user.id, amount, method, txid, datetime.utcnow().isoformat())
        )
        dep_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    context.user_data.clear()
    await query.edit_message_text(
        f"✅ <b>Deposit request created!</b>\n\n"
        f"ID: #{dep_id}\nAmount: {format_money(amount)}\nStatus: Pending\n\n"
        "Admin will review and credit your balance soon.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Balance", callback_data="menu_balance"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="back_main")]
        ]),
        parse_mode=ParseMode.HTML
    )
    # Notify admins
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"📥 New Deposit Request #{dep_id}\nUser: {user.id} (@{user.username})\n"
                f"Amount: {format_money(amount)}\nMethod: {method}\nTXID: {txid}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Approve", callback_data=f"adm_dep_approve_{dep_id}"),
                     InlineKeyboardButton("❌ Reject", callback_data=f"adm_dep_reject_{dep_id}")]
                ])
            )
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")
    return ConversationHandler.END

# ==================== WITHDRAWAL FLOW ====================
async def menu_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bal = get_balance(update.effective_user.id)
    if bal <= 0:
        await query.edit_message_text(
            f"💸 Your balance is {format_money(bal)}. Nothing to withdraw.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main", callback_data="back_main")]])
        )
        return ConversationHandler.END
    await query.edit_message_text(
        f"💸 <b>Withdrawal</b>\n\nCurrent Balance: <b>{format_money(bal)}</b>\n\n"
        "Enter the amount you want to withdraw:",
        reply_markup=back_cancel_keyboard(),
        parse_mode=ParseMode.HTML
    )
    return WD_AMOUNT

async def wd_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid positive number.", reply_markup=back_cancel_keyboard())
        return WD_AMOUNT
    bal = get_balance(update.effective_user.id)
    if amount > bal + 0.001:
        await update.message.reply_text(
            f"❌ Insufficient balance.\nRequired: {format_money(amount)}\nAvailable: {format_money(bal)}",
            reply_markup=back_cancel_keyboard()
        )
        return WD_AMOUNT
    context.user_data["wd_amount"] = amount
    with get_conn() as conn:
        cur = conn.execute("SELECT name FROM payment_methods WHERE is_active = 1")
        methods = [r["name"] for r in cur.fetchall()]
    buttons = [[InlineKeyboardButton(m, callback_data=f"wd_method_{m}")] for m in methods]
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="menu_withdraw"),
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    await update.message.reply_text("Select withdrawal method:", reply_markup=InlineKeyboardMarkup(buttons))
    return WD_METHOD

async def wd_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    method = query.data.replace("wd_method_", "")
    context.user_data["wd_method"] = method
    await query.edit_message_text(
        f"Method: <b>{method}</b>\n\n"
        "Enter your payment details (account number / address / etc.):",
        reply_markup=back_cancel_keyboard("menu_withdraw"),
        parse_mode=ParseMode.HTML
    )
    return WD_DETAILS

async def wd_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    details = update.message.text.strip()
    if len(details) < 5:
        await update.message.reply_text("❌ Details too short.", reply_markup=back_cancel_keyboard("menu_withdraw"))
        return WD_DETAILS
    context.user_data["wd_details"] = details
    amount = context.user_data["wd_amount"]
    method = context.user_data["wd_method"]
    text = (
        f"📋 <b>Withdrawal Preview</b>\n\n"
        f"Amount: <b>{format_money(amount)}</b>\n"
        f"Method: {method}\n"
        f"Details: <code>{details}</code>\n\n"
        "⚠️ On confirm, the amount will be reserved from your balance."
    )
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm", callback_data="wd_confirm")],
            [InlineKeyboardButton("🔙 Back", callback_data="menu_withdraw"),
             InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ]),
        parse_mode=ParseMode.HTML
    )
    return WD_CONFIRM

async def wd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    amount = context.user_data.get("wd_amount")
    method = context.user_data.get("wd_method")
    details = context.user_data.get("wd_details")
    if not all([amount, method, details]):
        await query.edit_message_text("Session expired.", reply_markup=main_menu_keyboard(user.id))
        return ConversationHandler.END
    try:
        with db_transaction() as conn:
            # Reserve balance immediately
            new_bal = add_ledger(conn, user.id, -amount, "withdrawal_reserve", note=f"Withdrawal pending {method}")
            conn.execute(
                """INSERT INTO withdrawals (user_id, amount, method, details, status, created_at)
                   VALUES (?, ?, ?, ?, 'pending', ?)""",
                (user.id, amount, method, details, datetime.utcnow().isoformat())
            )
            wd_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            # Update ledger reference
            conn.execute("UPDATE ledger SET reference_id = ?, reference_type = 'withdrawal' WHERE id = (SELECT MAX(id) FROM ledger WHERE user_id = ?)",
                         (wd_id, user.id))
          except ValueError as e:
        await query.edit_message_text(f"❌ {e}", reply_markup=main_menu_keyboard(user.id))
        return ConversationHandler.END
    context.user_data.clear()
    await query.edit_message_text(
        f"✅ <b>Withdrawal request created!</b>\n\n"
        f"ID: #{wd_id}\nAmount: {format_money(amount)}\nNew Balance: {format_money(new_bal)}\n"
        "Status: Pending (admin will process manually)",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Balance", callback_data="menu_balance"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="back_main")]
        ]),
        parse_mode=ParseMode.HTML
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"📤 New Withdrawal #{wd_id}\nUser: {user.id} (@{user.username})\n"
                f"Amount: {format_money(amount)}\nMethod: {method}\nDetails: {details}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Mark Paid", callback_data=f"adm_wd_approve_{wd_id}"),
                     InlineKeyboardButton("❌ Reject (refund)", callback_data=f"adm_wd_reject_{wd_id}")]
                ])
            )
        except Exception as e:
            logger.warning(f"Notify admin fail: {e}")
    return ConversationHandler.END

# ==================== MARKETPLACE / PURCHASE ====================
async def menu_marketplace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name FROM categories WHERE is_active = 1 ORDER BY name")
        cats = cur.fetchall()
    if not cats:
        await query.edit_message_text("📦 No categories available yet.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main", callback_data="back_main")]]))
        return ConversationHandler.END
    buttons = [[InlineKeyboardButton(c["name"], callback_data=f"buy_cat_{c['id']}")] for c in cats]
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="back_main")])
    await query.edit_message_text("🛒 <b>Marketplace</b>\n\nSelect Category:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
    return BUY_CAT

async def buy_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[-1])
    context.user_data["buy_cat_id"] = cat_id
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name FROM subcategories WHERE category_id = ? AND is_active = 1", (cat_id,))
        subs = cur.fetchall()
        cur = conn.execute("SELECT name FROM categories WHERE id = ?", (cat_id,))
        cat_name = cur.fetchone()["name"]
    if not subs:
        # Direct products under category
        return await show_products(update, context, cat_id, None)
    buttons = [[InlineKeyboardButton(s["name"], callback_data=f"buy_sub_{s['id']}")] for s in subs]
    buttons.append([InlineKeyboardButton("🔙 Categories", callback_data="menu_marketplace")])
    await query.edit_message_text(f"📁 {cat_name}\n\nSelect Sub-category:", reply_markup=InlineKeyboardMarkup(buttons))
    return BUY_SUB

async def buy_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sub_id = int(query.data.split("_")[-1])
    context.user_data["buy_sub_id"] = sub_id
    cat_id = context.user_data.get("buy_cat_id")
    return await show_products(update, context, cat_id, sub_id)

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE, cat_id: int, sub_id: Optional[int]):
    query = update.callback_query
    with get_conn() as conn:
        if sub_id:
            cur = conn.execute(
                "SELECT id, name, price, stock FROM products WHERE subcategory_id = ? AND is_active = 1 AND stock > 0 ORDER BY name",
                (sub_id,)
            )
        else:
            cur = conn.execute(
                "SELECT id, name, price, stock FROM products WHERE category_id = ? AND is_active = 1 AND stock > 0 ORDER BY name",
                (cat_id,)
            )
        products = cur.fetchall()
    if not products:
        await query.edit_message_text(
            "📦 Stock: 0 — currently unavailable.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_marketplace")]])
          )
      return BUY_CAT
    buttons = []
    for p in products:
        buttons.append([InlineKeyboardButton(
            f"{p['name']} — {format_money(p['price'])} (Stock: {p['stock']})",
            callback_data=f"buy_prod_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="menu_marketplace")])
    await query.edit_message_text("🛒 Select Product:", reply_markup=InlineKeyboardMarkup(buttons))
    return BUY_PRODUCT

async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    prod_id = int(query.data.split("_")[-1])
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM products WHERE id = ? AND is_active = 1", (prod_id,))
        prod = cur.fetchone()
    if not prod or prod["stock"] <= 0:
        await query.edit_message_text("Product unavailable.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="menu_marketplace")]]))
        return BUY_CAT
    context.user_data["buy_prod"] = dict(prod)
    await query.edit_message_text(
        f"🛍 <b>{prod['name']}</b>\n\n"
        f"{prod['description'] or ''}\n\n"
        f"Price: <b>{format_money(prod['price'])}</b>\n"
        f"Available Stock: <b>{prod['stock']}</b>\n\n"
        "Enter quantity:",
        reply_markup=back_cancel_keyboard("menu_marketplace"),
        parse_mode=ParseMode.HTML
    )
    return BUY_QTY

async def buy_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        qty = int(text)
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid positive integer.", reply_markup=back_cancel_keyboard("menu_marketplace"))
        return BUY_QTY
    prod = context.user_data.get("buy_prod")
    if not prod:
        await update.message.reply_text("Session expired.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    if qty > prod["stock"]:
        await update.message.reply_text(
            f"❌ Not enough stock. Available: {prod['stock']}",
            reply_markup=back_cancel_keyboard("menu_marketplace")
        )
        return BUY_QTY
    total = round(prod["price"] * qty, 2)
    bal = get_balance(update.effective_user.id)
    context.user_data["buy_qty"] = qty
    context.user_data["buy_total"] = total
    text = (
        f"📋 <b>Order Preview</b>\n\n"
        f"Product: {prod['name']}\n"
        f"Qty: {qty}\n"
        f"Unit Price: {format_money(prod['price'])}\n"
        f"Total: <b>{format_money(total)}</b>\n"
        f"Your Balance: {format_money(bal)}\n\n"
    )
    if total > bal + 0.001:
        text += "❌ Insufficient balance. Please deposit first."
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Deposit", callback_data="menu_deposit")],
            [InlineKeyboardButton("🔙 Back", callback_data="menu_marketplace")]
        ]), parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    text += "Confirm purchase?"
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Purchase", callback_data="buy_confirm")],
            [InlineKeyboardButton("🔙 Back", callback_data="menu_marketplace"),
             InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ]),
        parse_mode=ParseMode.HTML
    )
    return BUY_CONFIRM

async def buy_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    prod = context.user_data.get("buy_prod")
    qty = context.user_data.get("buy_qty")
    total = context.user_data.get("buy_total")
    if not all([prod, qty, total]):
        await query.edit_message_text("Session expired.", reply_markup=main_menu_keyboard(user.id))
        return ConversationHandler.END
    try:
        with db_transaction() as conn:
            # Re-check stock & balance inside transaction
            cur = conn.execute("SELECT stock, price, owner_id, name FROM products WHERE id = ? AND is_active = 1", (prod["id"],))
            p = cur.fetchone()
            if not p or p["stock"] < qty:
                raise ValueError(f"Stock changed. Available: {p['stock'] if p else 0}")
            cur = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user.id,))
            bal = float(cur.fetchone()["balance"])
            if bal < total - 0.001:
                raise ValueError("Insufficient balance")
            # Atomic: deduct buyer, reduce stock, credit owner, create order
            order_num = next_order_number(conn)
            conn.execute(
          """INSERT INTO orders (order_number, buyer_id, product_id, quantity, unit_price, total_price, owner_id, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?)""",
                (order_num, user.id, prod["id"], qty, p["price"], total, p["owner_id"], datetime.utcnow().isoformat())
            )
            order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            add_ledger(conn, user.id, -total, "purchase", order_id, "order", f"Bought {qty}x {p['name']}")
            conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (qty, prod["id"]))
            if p["owner_id"] != user.id:
                add_ledger(conn, p["owner_id"], total, "sale", order_id, "order", f"Sold {qty}x {p['name']}")
            new_bal = get_balance(user.id)  # after commit it will be updated, but we have it from ledger
            # Actually get from the add_ledger return, but simplified
    except ValueError as e:
        await query.edit_message_text(f"❌ {e}", reply_markup=main_menu_keyboard(user.id))
        context.user_data.clear()
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Purchase failed")
        await query.edit_message_text("❌ Transaction failed. Please try again.", reply_markup=main_menu_keyboard(user.id))
        context.user_data.clear()
        return ConversationHandler.END
    context.user_data.clear()
    new_bal = get_balance(user.id)
    await query.edit_message_text(
        f"✅ <b>Purchase Successful!</b>\n\n"
        f"Order: <code>{order_num}</code>\n"
        f"Product: {prod['name']}\n"
        f"Qty: {qty}\n"
        f"Total Paid: {format_money(total)}\n"
        f"New Balance: {format_money(new_bal)}\n\n"
        "Thank you!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Marketplace", callback_data="menu_marketplace"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="back_main")]
        ]),
        parse_mode=ParseMode.HTML
    )
    # Notify owner if different
    if p["owner_id"] != user.id:
        try:
            await context.bot.send_message(
                p["owner_id"],
                f"💰 You sold {qty}x {p['name']}!\nOrder: {order_num}\nEarned: {format_money(total)}"
            )
        except Exception:
            pass
    return ConversationHandler.END

# ==================== MY ORDERS ====================
async def menu_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    with get_conn() as conn:
        cur = conn.execute(
            """SELECT o.order_number, o.quantity, o.total_price, o.created_at, p.name
               FROM orders o JOIN products p ON o.product_id = p.id
               WHERE o.buyer_id = ? ORDER BY o.id DESC LIMIT 15""",
            (user_id,)
        )
        rows = cur.fetchall()
    if not rows:
        text = "📦 You have no orders yet."
    else:
        lines = ["📦 <b>Your Recent Orders</b>\n"]
        for r in rows:
            lines.append(f"• <code>{r['order_number']}</code> — {r['name']} x{r['quantity']} = {format_money(r['total_price'])}")
        text = "\n".join(lines)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main", callback_data="back_main")]]), parse_mode=ParseMode.HTML)

# ==================== SUPPORT ====================
async def menu_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🆘 <b>Support</b>\n\nWrite your message. It will be forwarded to admins.",
        reply_markup=back_cancel_keyboard(),
        parse_mode=ParseMode.HTML
    )
    return SUPPORT_MSG

async def support_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message.text
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"🆘 Support from {user.id} (@{user.username}):\n\n{msg}"
            )
        except Exception:
            pass
    await update.message.reply_text(
        "✅ Message sent to support. We will reply soon.",
        reply_markup=main_menu_keyboard(user.id)
    )
    return ConversationHandler.END

# ==================== ADMIN PANEL ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.edit_message_text("⛔ Access denied.")
        return ConversationHandler.END
    await query.edit_message_text("🛠 <b>Admin Panel</b>", reply_markup=admin_menu_keyboard(), parse_mode=ParseMode.HTML)

async def adm_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        total_sales = conn.execute("SELECT COALESCE(SUM(total_price),0) FROM orders").fetchone()[0]
        pending_dep = conn.execute("SELECT COUNT(*) FROM deposits WHERE status='pending'").fetchone()[0]
        pending_wd = conn.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'").fetchone()[0]
        total_products = conn.execute("SELECT COUNT(*) FROM products WHERE is_active=1").fetchone()[0]
    text = (
        f"📊 <b>Stats</b>\n\n"
        f"Users: {total_users}\n"
        f"Orders: {total_orders}\n"
        f"Total Sales Volume: {format_money(total_sales)}\n"
        f"Active Products: {total_products}\n"
        f"Pending Deposits: {pending_dep}\n"
        f"Pending Withdrawals: {pending_wd}"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin", callback_data="admin_panel")]]), parse_mode=ParseMode.HTML)

async def adm_deposits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT d.*, u.username FROM deposits d LEFT JOIN users u ON d.user_id = u.user_id WHERE d.status='pending' ORDER BY d.id DESC LIMIT 20"
        )
        rows = cur.fetchall()
    if not rows:
        await query.edit_message_text("No pending deposits.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="admin_panel")]]))
        return
    lines = ["📥 <b>Pending Deposits</b>\n"]
    buttons = []
    for r in rows:
        lines.append(f"#{r['id']} | User {r['user_id']} (@{r['username']}) | {format_money(r['amount'])} | {r['method']} | TX: {r['txid']}")
        buttons.append([
            InlineKeyboardButton(f"✅ #{r['id']}", callback_data=f"adm_dep_approve_{r['id']}"),
            InlineKeyboardButton(f"❌ #{r['id']}", callback_data=f"adm_dep_reject_{r['id']}")
        ])
    buttons.append([InlineKeyboardButton("🔙 Admin", callback_data="admin_panel")])
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)

async def adm_dep_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    dep_id = int(query.data.split("_")[-1])
    try:
        with db_transaction() as conn:
            cur = conn.execute("SELECT * FROM deposits WHERE id = ? AND status = 'pending'", (dep_id,))
            dep = cur.fetchone()
            if not dep:
                await query.answer("Already processed or not found.", show_alert=True)
                return
            add_ledger(conn, dep["user_id"], dep["amount"], "deposit", dep_id, "deposit", f"Approved deposit #{dep_id}")
            conn.execute(
                "UPDATE deposits SET status='approved', processed_at=? WHERE id=?",
                (datetime.utcnow().isoformat(), dep_id)
            )
        await query.edit_message_text(f"✅ Deposit #{dep_id} approved. Balance credited.")
        try:
            await context.bot.send_message(dep["user_id"], f"✅ Your deposit #{dep_id} of {format_money(dep['amount'])} has been approved!")
        except Exception:
            pass
    except Exception as e:
        logger.exception(e)
        await query.answer("Error processing.", show_alert=True)

async def adm_dep_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    dep_id = int(query.data.split("_")[-1])
    with db_transaction() as conn:
        cur = conn.execute("SELECT * FROM deposits WHERE id = ? AND status = 'pending'", (dep_id,))
        dep = cur.fetchone()
        if not dep:
            await query.answer("Already processed.", show_alert=True)
            return
        conn.execute(
            "UPDATE deposits SET status='rejected', processed_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), dep_id)
        )
    await query.edit_message_text(f"❌ Deposit #{dep_id} rejected.")
    try:
        await context.bot.send_message(dep["user_id"], f"❌ Your deposit #{dep_id} was rejected. Contact support if needed.")
    except Exception:
        pass

async def adm_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT w.*, u.username FROM withdrawals w LEFT JOIN users u ON w.user_id = u.user_id WHERE w.status='pending' ORDER BY w.id DESC LIMIT 20"
        )
        rows = cur.fetchall()
    if not rows:
        await query.edit_message_text("No pending withdrawals.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="admin_panel")]]))
        return
    lines = ["📤 <b>Pending Withdrawals</b>\n"]
    buttons = []
    for r in rows:
        lines.append(f"#{r['id']} | User {r['user_id']} | {format_money(r['amount'])} | {r['method']} | {r['details'][:30]}")
        buttons.append([
            InlineKeyboardButton(f"✅ Paid #{r['id']}", callback_data=f"adm_wd_approve_{r['id']}"),
            InlineKeyboardButton(f"❌ Reject #{r['id']}", callback_data=f"adm_wd_reject_{r['id']}")
        ])
    buttons.append([InlineKeyboardButton("🔙 Admin", callback_data="admin_panel")])
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)

async def adm_wd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    wd_id = int(query.data.split("_")[-1])
    with db_transaction() as conn:
        cur = conn.execute("SELECT * FROM withdrawals WHERE id = ? AND status = 'pending'", (wd_id,))
        wd = cur.fetchone()
        if not wd:
            await query.answer("Already processed.", show_alert=True)
            return
        # Balance already reserved. Just mark complete. (no extra ledger needed, or add note)
        conn.execute(
            "UPDATE withdrawals SET status='approved', processed_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), wd_id)
        )
        # Optional: update ledger note
    await query.edit_message_text(f"✅ Withdrawal #{wd_id} marked as paid.")
    try:
        await context.bot.send_message(wd["user_id"], f"✅ Your withdrawal #{wd_id} of {format_money(wd['amount'])} has been processed!")
    except Exception:
        pass

async def adm_wd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    wd_id = int(query.data.split("_")[-1])
    try:
        with db_transaction() as conn:
            cur = conn.execute("SELECT * FROM withdrawals WHERE id = ? AND status = 'pending'", (wd_id,))
            wd = cur.fetchone()
            if not wd:
                await query.answer("Already processed.", show_alert=True)
                return
            # Refund the reserved amount
            add_ledger(conn, wd["user_id"], wd["amount"], "withdrawal_refund", wd_id, "withdrawal", f"Rejected withdrawal #{wd_id}")
            conn.execute(
          "UPDATE withdrawals SET status='rejected', processed_at=? WHERE id=?",
                (datetime.utcnow().isoformat(), wd_id)
            )
        await query.edit_message_text(f"❌ Withdrawal #{wd_id} rejected & refunded.")
        try:
            await context.bot.send_message(wd["user_id"], f"❌ Your withdrawal #{wd_id} was rejected. Amount refunded to balance.")
        except Exception:
            pass
    except Exception as e:
        logger.exception(e)
        await query.answer("Error.", show_alert=True)

# Admin balance adjust
async def adm_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    await query.edit_message_text(
        "👥 <b>Users & Balance</b>\n\nSend the User ID to adjust balance:",
        reply_markup=back_cancel_keyboard("admin_panel"),
        parse_mode=ParseMode.HTML
    )
    return ADM_BAL_USER

async def adm_bal_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        uid = int(text)
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID (numbers only).", reply_markup=back_cancel_keyboard("admin_panel"))
        return ADM_BAL_USER
    user = get_user(uid)
    if not user:
        await update.message.reply_text("❌ User not found in database.", reply_markup=back_cancel_keyboard("admin_panel"))
        return ADM_BAL_USER
    context.user_data["adm_bal_uid"] = uid
    await update.message.reply_text(
        f"User: {uid} (@{user['username']})\nCurrent Balance: {format_money(user['balance'])}\n\n"
        "Enter amount to add (positive) or deduct (negative):",
        reply_markup=back_cancel_keyboard("admin_panel")
    )
    return ADM_BAL_AMOUNT

async def adm_bal_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        amount = float(text)
        if amount == 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a non-zero number.", reply_markup=back_cancel_keyboard("admin_panel"))
        return ADM_BAL_AMOUNT
    context.user_data["adm_bal_amount"] = amount
    await update.message.reply_text(
        "Optional note / reason (or type - to skip):",
        reply_markup=back_cancel_keyboard("admin_panel")
    )
    return ADM_BAL_REASON

async def adm_bal_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    if reason == "-":
        reason = "Admin adjustment"
    context.user_data["adm_bal_reason"] = reason
    uid = context.user_data["adm_bal_uid"]
    amount = context.user_data["adm_bal_amount"]
    sign = "+" if amount > 0 else ""
    await update.message.reply_text(
        f"Confirm: {sign}{format_money(amount)} to user {uid}\nReason: {reason}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm", callback_data="adm_bal_confirm")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ])
    )
    return ADM_BAL_CONFIRM

async def adm_bal_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    uid = context.user_data.get("adm_bal_uid")
    amount = context.user_data.get("adm_bal_amount")
    reason = context.user_data.get("adm_bal_reason", "Admin")
    if uid is None or amount is None:
        await query.edit_message_text("Session expired.", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END
    type_ = "admin_add" if amount > 0 else "admin_deduct"
    try:
        with db_transaction() as conn:
            new_bal = add_ledger(conn, uid, amount, type_, note=reason)
        await query.edit_message_text(
            f"✅ Balance updated.\nUser {uid} new balance: {format_money(new_bal)}",
            reply_markup=admin_menu_keyboard()
        )
        try:
            await context.bot.send_message(uid, f"ℹ️ Admin adjusted your balance by {format_money(amount)}.\nNew balance: {format_money(new_bal)}\nNote: {reason}")
        except Exception:
            pass
    except ValueError as e:
        await query.edit_message_text(f"❌ {e}", reply_markup=admin_menu_keyboard())
    context.user_data.clear()
    return ConversationHandler.END

# Broadcast
async def adm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    await query.edit_message_text(
        "📢 Send the message to broadcast to all users:\n(or /cancel)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Broadcast", callback_data="cancel")]])
    )
    return ADM_BROADCAST_MSG

async def adm_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["broadcast_text"] = update.message.text
    await update.message.reply_text(
        f"Preview:\n\n{update.message.text}\n\nConfirm broadcast?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Send to all", callback_data="adm_broadcast_confirm")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ])
    )
    return ADM_BROADCAST_CONFIRM

async def adm_broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    text = context.user_data.get("broadcast_text")
    if not text:
        await query.edit_message_text("No message.", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END
    with get_conn() as conn:
        cur = conn.execute("SELECT user_id FROM users")
        users = [r["user_id"] for r in cur.fetchall()]
    success = 0
    for uid in users:
        try:
            await context.bot.send_message(uid, f"📢 <b>Announcement</b>\n\n{text}", parse_mode=ParseMode.HTML)
            success += 1
        except Exception:
            pass
    await query.edit_message_text(f"✅ Broadcast sent to {success}/{len(users)} users.", reply_markup=admin_menu_keyboard())
    context.user_data.clear()
    return ConversationHandler.END

# Payment methods admin (simple list + add)
async def adm_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM payment_methods ORDER BY id")
        rows = cur.fetchall()
    lines = ["💳 <b>Payment Methods</b>\n"]
    for r in rows:
        status = "✅" if r["is_active"] else "❌"
        lines.append(f"{status} {r['name']}\n{r['details'][:80]}...\n")
    buttons = [
        [InlineKeyboardButton("➕ Add / Update Method", callback_data="adm_pay_add")],
        [InlineKeyboardButton("🔙 Admin", callback_data="admin_panel")]
    ]
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)

async def adm_pay_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Send in format:\nMethodName | Details text here\n\nExample:\nbKash | Number: 01xxx Name: Shop",
        reply_markup=back_cancel_keyboard("adm_payments")
    )
    return ADM_PAY_METHOD

async def adm_pay_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "|" not in text:
        await update.message.reply_text("Format: Name | Details", reply_markup=back_cancel_keyboard("adm_payments"))
        return ADM_PAY_METHOD
    name, details = [x.strip() for x in text.split("|", 1)]
    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO payment_methods (name, details, is_active) VALUES (?, ?, 1)
               ON CONFLICT(name) DO UPDATE SET details=excluded.details, is_active=1""",
            (name, details)
        )
    await update.message.reply_text(f"✅ Payment method '{name}' saved.", reply_markup=admin_menu_keyboard())
    return ConversationHandler.END

# Marketplace admin - simplified add product
async def adm_marketplace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
return
    buttons = [
        [InlineKeyboardButton("➕ Add Product", callback_data="adm_add_product")],
        [InlineKeyboardButton("📋 List Products", callback_data="adm_list_products")],
        [InlineKeyboardButton("🔙 Admin", callback_data="admin_panel")]
    ]
    await query.edit_message_text("🏪 Marketplace Management", reply_markup=InlineKeyboardMarkup(buttons))

async def adm_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, price, stock, is_active FROM products ORDER BY id DESC LIMIT 20")
        rows = cur.fetchall()
    lines = ["📋 Products (latest 20):\n"]
    for r in rows:
        lines.append(f"#{r['id']} {r['name']} | {format_money(r['price'])} | Stock {r['stock']} | {'✅' if r['is_active'] else '❌'}")
    await query.edit_message_text("\n".join(lines) or "No products.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="adm_marketplace")]]))

async def adm_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Product Name:", reply_markup=back_cancel_keyboard("adm_marketplace"))
    return ADM_P_NAME

async def adm_p_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["p_name"] = update.message.text.strip()
    await update.message.reply_text("Description (or - for empty):", reply_markup=back_cancel_keyboard("adm_marketplace"))
    return ADM_P_DESC

async def adm_p_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    context.user_data["p_desc"] = "" if desc == "-" else desc
    await update.message.reply_text("Price (number):", reply_markup=back_cancel_keyboard("adm_marketplace"))
    return ADM_P_PRICE

async def adm_p_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text.strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Valid positive number.", reply_markup=back_cancel_keyboard("adm_marketplace"))
        return ADM_P_PRICE
    context.user_data["p_price"] = price
    await update.message.reply_text("Stock (integer):", reply_markup=back_cancel_keyboard("adm_marketplace"))
    return ADM_P_STOCK

async def adm_p_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stock = int(update.message.text.strip())
        if stock < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Valid non-negative integer.", reply_markup=back_cancel_keyboard("adm_marketplace"))
        return ADM_P_STOCK
    context.user_data["p_stock"] = stock
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name FROM categories WHERE is_active=1")
        cats = cur.fetchall()
    buttons = [[InlineKeyboardButton(c["name"], callback_data=f"adm_p_cat_{c['id']}")] for c in cats]
    buttons.append([InlineKeyboardButton("🔙 Cancel", callback_data="cancel")])
    await update.message.reply_text("Select Category:", reply_markup=InlineKeyboardMarkup(buttons))
    return ADM_P_CAT

async def adm_p_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[-1])
    context.user_data["p_cat"] = cat_id
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name FROM subcategories WHERE category_id=? AND is_active=1", (cat_id,))
        subs = cur.fetchall()
    if not subs:
        context.user_data["p_sub"] = None
        await query.edit_message_text(
            f"Owner User ID (who receives the money on sale).\nDefault = your ID ({update.effective_user.id})\nSend number or 0 for self:",
            reply_markup=back_cancel_keyboard("adm_marketplace")
        )
        return ADM_P_OWNER
    buttons = [[InlineKeyboardButton(s["name"], callback_data=f"adm_p_sub_{s['id']}")] for s in subs]
    buttons.append([InlineKeyboardButton("No Sub", callback_data="adm_p_sub_0")])
    await query.edit_message_text("Select Sub-category:", reply_markup=InlineKeyboardMarkup(buttons))
    return ADM_P_SUB

async def adm_p_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sub_id = int(query.data.split("_")[-1])
    context.user_data["p_sub"] = None if sub_id == 0 else sub_id
    await query.edit_message_text(
        f"Owner User ID (receives sale money).\nSend 0 for yourself ({update.effective_user.id}):",
        reply_markup=back_cancel_keyboard("adm_marketplace")
    )
    return ADM_P_OWNER

async def adm_p_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        owner = int(text)
        if owner == 0:
            owner = update.effective_user.id
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.", reply_markup=back_cancel_keyboard("adm_marketplace"))
        return ADM_P_OWNER
    ensure_user(owner)
    context.user_data["p_owner"] = owner
    p = context.user_data
    text = (
        f"Confirm Product:\n"
        f"Name: {p['p_name']}\n"
        f"Price: {format_money(p['p_price'])}\n"
        f"Stock: {p['p_stock']}\n"
        f"Owner: {owner}"
    )
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Create", callback_data="adm_p_confirm")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ])
    )
    return ADM_P_CONFIRM

async def adm_p_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    p = context.user_data
    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO products (name, description, price, stock, category_id, subcategory_id, owner_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (p["p_name"], p.get("p_desc", ""), p["p_price"], p["p_stock"], p.get("p_cat"), p.get("p_sub"), p["p_owner"], datetime.utcnow().isoformat())
        )
    await query.edit_message_text("✅ Product created!", reply_markup=admin_menu_keyboard())
    context.user_data.clear()
    return ConversationHandler.END

# ==================== FALLBACK / ERROR ====================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ An error occurred. Please try /start again.")
        except Exception:
            pass

# ==================== MAIN ====================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    if not ADMIN_IDS or ADMIN_IDS == [0]:
        logger.warning("ADMIN_USER_ID not set properly. Admin features disabled.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).concurrent_updates(False).build()

    # Main conversation for multi-step flows    
  conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(menu_deposit, pattern="^menu_deposit$"),
            CallbackQueryHandler(menu_withdraw, pattern="^menu_withdraw$"),
            CallbackQueryHandler(menu_marketplace, pattern="^menu_marketplace$"),
            CallbackQueryHandler(menu_support, pattern="^menu_support$"),
            CallbackQueryHandler(adm_users, pattern="^adm_users$"),
            CallbackQueryHandler(adm_broadcast, pattern="^adm_broadcast$"),
            CallbackQueryHandler(adm_pay_add, pattern="^adm_pay_add$"),
            CallbackQueryHandler(adm_add_product, pattern="^adm_add_product$"),
        ],
        states={
            DEP_METHOD: [
                CallbackQueryHandler(dep_method, pattern=r"^dep_method_\d+$"),
                CallbackQueryHandler(back_main, pattern="^back_main$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            DEP_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, dep_amount),
                CallbackQueryHandler(menu_deposit, pattern="^menu_deposit$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            DEP_TXID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, dep_txid),
                CallbackQueryHandler(menu_deposit, pattern="^menu_deposit$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            DEP_CONFIRM: [
                CallbackQueryHandler(dep_confirm, pattern="^dep_confirm$"),
                CallbackQueryHandler(menu_deposit, pattern="^menu_deposit$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            WD_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wd_amount),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            WD_METHOD: [
                CallbackQueryHandler(wd_method, pattern=r"^wd_method_"),
                CallbackQueryHandler(menu_withdraw, pattern="^menu_withdraw$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            WD_DETAILS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wd_details),
                CallbackQueryHandler(menu_withdraw, pattern="^menu_withdraw$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            WD_CONFIRM: [
                CallbackQueryHandler(wd_confirm, pattern="^wd_confirm$"),
                CallbackQueryHandler(menu_withdraw, pattern="^menu_withdraw$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            BUY_CAT: [
                CallbackQueryHandler(buy_cat, pattern=r"^buy_cat_\d+$"),
                CallbackQueryHandler(back_main, pattern="^back_main$"),
            ],
            BUY_SUB: [
                CallbackQueryHandler(buy_sub, pattern=r"^buy_sub_\d+$"),
                CallbackQueryHandler(menu_marketplace, pattern="^menu_marketplace$"),
            ],
            BUY_PRODUCT: [
                CallbackQueryHandler(buy_product, pattern=r"^buy_prod_\d+$"),
                CallbackQueryHandler(menu_marketplace, pattern="^menu_marketplace$"),
            ],
            BUY_QTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, buy_qty),
                CallbackQueryHandler(menu_marketplace, pattern="^menu_marketplace$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            BUY_CONFIRM: [
                CallbackQueryHandler(buy_confirm, pattern="^buy_confirm$"),
                CallbackQueryHandler(menu_marketplace, pattern="^menu_marketplace$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            SUPPORT_MSG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, support_msg),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            ADM_BAL_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, adm_bal_user),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
                CallbackQueryHandler(admin_panel, pattern="^admin_panel$"),
            ],
            ADM_BAL_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, adm_bal_amount),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            ADM_BAL_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, adm_bal_reason),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            ADM_BAL_CONFIRM: [
                CallbackQueryHandler(adm_bal_confirm, pattern="^adm_bal_confirm$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            ADM_BROADCAST_MSG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, adm_broadcast_msg),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            ADM_BROADCAST_CONFIRM: [
                CallbackQueryHandler(adm_broadcast_confirm, pattern="^adm_broadcast_confirm$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            ADM_PAY_METHOD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, adm_pay_save),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            ADM_P_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_p_name), CallbackQueryHandler(cancel, pattern="^cancel$")],
            ADM_P_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_p_desc), CallbackQueryHandler(cancel, pattern="^cancel$")],
            ADM_P_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_p_price), CallbackQueryHandler(cancel, pattern="^cancel$")],
            ADM_P_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_p_stock), CallbackQueryHandler(cancel, pattern="^cancel$")],
            ADM_P_CAT: [
                CallbackQueryHandler(adm_p_cat, pattern=r"^adm_p_cat_\d+$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            ADM_P_SUB: [
                CallbackQueryHandler(adm_p_sub, pattern=r"^adm_p_sub_\d+$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            ADM_P_OWNER: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_p_owner), CallbackQueryHandler(cancel, pattern="^cancel$")],
            ADM_P_CONFIRM: [
                CallbackQueryHandler(adm_p_confirm, pattern="^adm_p_confirm$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern="^cancel$"),
            CallbackQueryHandler(back_main, pattern="^back_main$"),
        ],
        allow_reentry=True,
        name="main_conv",
        persistent=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    # Non-conversation callbacks
    app.add_handler(CallbackQueryHandler(menu_balance, pattern="^menu_balance$"))
    app.add_handler(CallbackQueryHandler(menu_orders, pattern="^menu_orders$"))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(adm_stats, pattern="^adm_stats$"))
    app.add_handler(CallbackQueryHandler(adm_deposits, pattern="^adm_deposits$"))
    app.add_handler(CallbackQueryHandler(adm_withdrawals, pattern="^adm_withdrawals$"))
    app.add_handler(CallbackQueryHandler(adm_marketplace, pattern="^adm_marketplace$"))
    app.add_handler(CallbackQueryHandler(adm_list_products, pattern="^adm_list_products$"))
    app.add_handler(CallbackQueryHandler(adm_payments, pattern="^adm_payments$"))
    app.add_handler(CallbackQueryHandler(back_main, pattern="^back_main$"))

    # Admin approve/reject (can be outside conv)
    app.add_handler(CallbackQueryHandler(adm_dep_approve, pattern=r"^adm_dep_approve_\d+$"))
    app.add_handler(CallbackQueryHandler(adm_dep_reject, pattern=r"^adm_dep_reject_\d+$"))
    app.add_handler(CallbackQueryHandler(adm_wd_approve, pattern=r"^adm_wd_approve_\d+$"))
    app.add_handler(CallbackQueryHandler(adm_wd_reject, pattern=r"^adm_wd_reject_\d+$"))

    app.add_error_handler(error_handler)

    # ---------- Start mode ----------
    # On Render Web Service → use Webhook (free tier)
    # Locally or Background Worker → use Polling

    port = int(os.environ.get("PORT", "0"))
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")

    if render_url or port:
        # Webhook mode for Render Free Web Service
        webhook_path = "/webhook"
        if render_url:
            webhook_url = f"{render_url}{webhook_path}"
        else:
            # Fallback (should not happen on Render)
            webhook_url = f"https://your-service.onrender.com{webhook_path}"

        listen_port = port if port else 10000

        logger.info(f"Starting in WEBHOOK mode on port {listen_port}")
        logger.info(f"Webhook URL: {webhook_url}")

        app.run_webhook(
            listen="0.0.0.0",
            port=listen_port,
            url_path=webhook_path,
            webhook_url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    else:
        # Local development or paid Background Worker
        logger.info("Starting in POLLING mode")
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
