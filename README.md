# Telegram Digital Store Bot (Latt Swal Store)

A full-featured Telegram Digital Store Bot built with `python-telegram-bot` and SQLite database with instant auto-delivery and Admin Management Panel.

## 💳 Payment Accounts
- **KBZPay (KPay):** `09758511925` (Pyae Phyo Kyaw)
- **WavePay (WPay):** `09779737113` (Sandar Kyaw)

## 📞 Support
- **Admin Support:** [@latt_swal2026](https://t.me/latt_swal2026)

## 🌟 Features
- **Store Menu**:
  - `🛒 ပစ္စည်းဝယ်ရန်` - Interactive catalog with real-time stock counters.
  - `💳 ငွေဖြည့်ရန်` - KPay & WavePay info with demo top-up buttons.
  - `👤 Member Status` - Balance and purchase history.
  - `❓ အကူအညီ` - Customer support guide with Admin @latt_swal2026.
- **Auto-Delivery**: Instant atomic balance deduction and delivery of credentials/links.
- **Admin Panel (`/admin`)**:
  - 🔑 **Stock Mail & Password Management**: View, edit (`/editstock <id> <data>`), and delete (`/deletestock <id>`) stock items.
  - ⚙️ **Product Management**: View, edit price (`/editprice`), edit name (`/editname`), delete product (`/deleteproduct`), add product (`/addproduct`).
  - 📊 **Store Statistics**: Live user count, completed orders, total revenue, and available stock.
  - 📜 **Recent Orders**: Recent purchase history with buyer username and timestamps.
  - 👥 **User Management**: Inspect users, balances, and `/addbalance <user_id> <amount>`.
  - 📢 **Broadcast**: `/broadcast <message>` to send announcements to all users.

## 🚀 How to Run

### Option 1: Double-click launcher
Run `run_bot.bat` or in terminal:
```powershell
.\run_bot.bat
```

### Option 2: Python Command
```powershell
.\.venv\Scripts\python.exe bot.py
```

## 📱 Telegram Bot
- Username: [@pyae_test_bot](https://t.me/pyae_test_bot)
