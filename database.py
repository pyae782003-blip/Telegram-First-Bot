import sqlite3
import os
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def _resolve_db_file() -> str:
    """Resolves SQLite database path from environment variables (e.g. Render/Railway persistent disks) or local path."""
    env_path = os.getenv("DATABASE_PATH") or os.getenv("SQLITE_DB_PATH")
    if env_path:
        db_path = env_path
    else:
        db_url = os.getenv("DATABASE_URL")
        if db_url and db_url.startswith("sqlite:///"):
            db_path = db_url.replace("sqlite:///", "")
        elif db_url and db_url.startswith("sqlite://"):
            db_path = db_url.replace("sqlite://", "")
        else:
            db_path = os.path.join(os.path.dirname(__file__), "store.db")

    parent_dir = os.path.dirname(os.path.abspath(db_path))
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)
    return db_path

DB_FILE = _resolve_db_file()


def get_connection() -> sqlite3.Connection:
    """Returns a SQLite connection with WAL journal mode and busy timeout for high-concurrency cloud environments."""
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initializes tables and seeds initial products and stock if empty."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Users table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 0,
                language TEXT DEFAULT 'my',
                referred_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Migrate existing users table if needed
        cursor.execute("PRAGMA table_info(users)")
        existing_cols = [c["name"] for c in cursor.fetchall()]
        if "language" not in existing_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'my'")
        if "referred_by" not in existing_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        if "referral_earnings" not in existing_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN referral_earnings INTEGER DEFAULT 0")
        if "referral_reward_paid" not in existing_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN referral_reward_paid INTEGER DEFAULT 0")

        # Admins table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Products table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price INTEGER NOT NULL,
                category TEXT DEFAULT 'Digital'
            )
            """
        )

        # Stock table (credentials, accounts, keys, activation links)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                item_data TEXT NOT NULL,
                is_sold INTEGER DEFAULT 0,
                sold_to_user_id INTEGER,
                sold_at TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
            """
        )

        # Orders / Purchase history table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                product_name TEXT NOT NULL,
                price INTEGER NOT NULL,
                delivered_data TEXT NOT NULL,
                purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            """
        )

        # Settings table (for currency exchange rate, payment details, store configs)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        # Multi-tenant Stores Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL UNIQUE,
                store_name TEXT NOT NULL,
                bot_token TEXT DEFAULT '',
                bot_username TEXT DEFAULT '',
                license_status TEXT DEFAULT 'active',
                license_expiry TEXT NOT NULL,
                plan TEXT DEFAULT 'Pro Business',
                thb_exchange_rate INTEGER DEFAULT 135,
                kpay_phone TEXT DEFAULT '09758511925',
                kpay_name TEXT DEFAULT 'Pyae Phyo Kyaw',
                wpay_phone TEXT DEFAULT '09779737113',
                wpay_name TEXT DEFAULT 'Sandar Kyaw',
                thai_bank_info TEXT DEFAULT 'Kasikorn Bank (KBank) - 123-4-56789-0 (Pyae Phyo Kyaw)',
                promptpay_phone TEXT DEFAULT '09758511925',
                promptpay_name TEXT DEFAULT 'Pyae Phyo Kyaw',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Store Rental / Purchase Applications Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS store_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                store_name TEXT NOT NULL,
                plan TEXT NOT NULL,
                slip_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Store Users Association Table (for strict multi-tenant customer isolation)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS store_users (
                store_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (store_id, user_id)
            )
            """
        )

        # Migration: Add store_id column to products, stock, orders if not present
        for tbl in ['products', 'stock', 'orders']:
            cursor.execute(f"PRAGMA table_info({tbl})")
            cols = [c["name"] for c in cursor.fetchall()]
            if "store_id" not in cols:
                cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN store_id INTEGER DEFAULT 1")

        # Migration: Add topup_instructions and qr_file_id columns to stores if not present
        cursor.execute("PRAGMA table_info(stores)")
        store_cols = [c["name"] for c in cursor.fetchall()]
        if "topup_instructions" not in store_cols:
            cursor.execute("ALTER TABLE stores ADD COLUMN topup_instructions TEXT DEFAULT ''")
        if "qr_file_id" not in store_cols:
            cursor.execute("ALTER TABLE stores ADD COLUMN qr_file_id TEXT DEFAULT ''")

        # Seed Store #1 (Master Super Admin Store) if none exists
        cursor.execute("SELECT COUNT(*) as count FROM stores WHERE id = 1")
        if cursor.fetchone()["count"] == 0:
            env_token = os.getenv("TELEGRAM_BOT_TOKEN", "8640565830:AAEpb_PCt75QqG6-jUNbtLZFc8yBAMWupkU").strip()
            env_super_id_str = os.getenv("SAAS_SUPER_ADMIN_IDS", "1920334929").split(",")[0].strip()
            env_super_id = int(env_super_id_str) if env_super_id_str.isdigit() else 1920334929
            env_store_name = os.getenv("STORE_NAME", "Latt Swal Store")
            env_bot_username = os.getenv("BOT_USERNAME", "@pyae_test_bot")
            env_kpay_phone = os.getenv("KPAY_PHONE", "09758511925")
            env_kpay_name = os.getenv("KPAY_NAME", "Pyae Phyo Kyaw")
            env_wpay_phone = os.getenv("WPAY_PHONE", "09779737113")
            env_wpay_name = os.getenv("WPAY_NAME", "Sandar Kyaw")
            env_thai_bank = os.getenv("THAI_BANK_INFO", "Kasikorn Bank (KBank) - 123-4-56789-0 (Pyae Phyo Kyaw)")
            env_promptpay_phone = os.getenv("PROMPTPAY_PHONE", "09758511925")
            env_promptpay_name = os.getenv("PROMPTPAY_NAME", "Pyae Phyo Kyaw")

            cursor.execute(
                """
                INSERT INTO stores (
                    id, owner_id, store_name, bot_token, bot_username,
                    license_status, license_expiry, plan, thb_exchange_rate,
                    kpay_phone, kpay_name, wpay_phone, wpay_name,
                    thai_bank_info, promptpay_phone, promptpay_name
                ) VALUES (
                    1, ?, ?, ?, ?,
                    'active', '2099-12-31 23:59:59', 'Master Enterprise', 135,
                    ?, ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    env_super_id, env_store_name, env_token, env_bot_username,
                    env_kpay_phone, env_kpay_name, env_wpay_phone, env_wpay_name,
                    env_thai_bank, env_promptpay_phone, env_promptpay_name
                )
            )

        default_settings = {
            "store_name": os.getenv("STORE_NAME", "Latt Swal Store"),
            "admin_username": os.getenv("ADMIN_USERNAME", "latt_swal2026"),
            "thb_exchange_rate": os.getenv("THB_EXCHANGE_RATE", "135"),
            "kpay_phone": os.getenv("KPAY_PHONE", "09758511925"),
            "kpay_name": os.getenv("KPAY_NAME", "Pyae Phyo Kyaw"),
            "wpay_phone": os.getenv("WPAY_PHONE", "09779737113"),
            "wpay_name": os.getenv("WPAY_NAME", "Sandar Kyaw"),
            "thai_bank_info": os.getenv("THAI_BANK_INFO", "Kasikorn Bank (KBank) - 123-4-56789-0 (Pyae Phyo Kyaw)"),
            "promptpay_phone": os.getenv("PROMPTPAY_PHONE", "09758511925"),
            "promptpay_name": os.getenv("PROMPTPAY_NAME", "Pyae Phyo Kyaw"),
            "license_status": "active",
            "license_expiry": "2026-12-31 23:59:59",
            "license_plan": "SaaS Monthly Pro",
            "license_key": "SAAS-LATT-SWAL-PRO-2026",
            "license_client_name": "Latt Swal Digital Shop",
        }
        for k, v in default_settings.items():
            cursor.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
            )

        # Seed initial products if none exist
        cursor.execute("SELECT COUNT(*) as count FROM products")
        if cursor.fetchone()["count"] == 0:
            sample_products = [
                (
                    "🎬 Netflix Premium (1 Month)",
                    "4K Ultra HD • Private Profile • 1 Month Warranty\nEmail & Password login format.",
                    8500,
                    "Streaming",
                ),
                (
                    "🎵 Spotify Premium (1 Month)",
                    "Ad-free • High Quality Audio • Offline Downloads\nIndividual Account Invite Link.",
                    5000,
                    "Music",
                ),
                (
                    "🎨 Canva Pro (Lifetime Invite)",
                    "Brand Kits • Unlimited Cloud Storage • Premium Assets\nTeam invite link.",
                    4000,
                    "Design",
                ),
                (
                    "⚡ Telegram Premium (3 Months)",
                    "4GB Uploads • Faster Download • Animated Emojis\nGift code redeem link.",
                    28000,
                    "Telegram",
                ),
                (
                    "🛡️ ExpressVPN Key (1 Month)",
                    "Fast & Secure • Multi-device support\nActivation Code.",
                    6000,
                    "VPN",
                ),
            ]

            cursor.executemany(
                "INSERT INTO products (name, description, price, category) VALUES (?, ?, ?, ?)",
                sample_products,
            )

            # Seed sample stock for each product
            stock_items = [
                # Netflix
                (1, "Email: user101@streammail.com\nPass: Netfl!x#2026\nProfile: PIN 1234"),
                (1, "Email: user102@streammail.com\nPass: Netfl!x#2026\nProfile: PIN 5678"),
                (1, "Email: user103@streammail.com\nPass: Netfl!x#2026\nProfile: PIN 9988"),
                # Spotify
                (2, "Invite Link: https://www.spotify.com/invite/sample-token-abc123"),
                (2, "Invite Link: https://www.spotify.com/invite/sample-token-def456"),
                # Canva
                (3, "Invite Link: https://www.canva.com/brand/join?token=canva-pro-sample-789"),
                (3, "Invite Link: https://www.canva.com/brand/join?token=canva-pro-sample-999"),
                # Telegram Premium
                (4, "Gift Link: https://t.me/giftcode/SAMPLE-TG-PREM-CODE-XYZ"),
                # ExpressVPN
                (5, "Activation Key: EVPN-9982-1143-6672-ABCD"),
                (5, "Activation Key: EVPN-7721-3312-9901-WXYZ"),
            ]

            cursor.executemany(
                "INSERT INTO stock (product_id, item_data) VALUES (?, ?)",
                stock_items,
            )

        conn.commit()


# ==============================================================================
# Admin Functions
# ==============================================================================

def add_admin(user_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return True


def remove_admin(user_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        conn.commit()
        return True


def get_all_admin_ids() -> List[int]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM admins")
        return [row["user_id"] for row in cursor.fetchall()]


def is_admin(user_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None


# ==============================================================================
# User Functions
# ==============================================================================

def get_or_create_user(
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    referrer_id: Optional[int] = None,
    store_id: int = 1,
) -> Dict[str, Any]:
    """Retrieves an existing user (preserving their current balance) or registers a new user with initial 0 MMK balance, and links them to the store."""
    with get_connection() as conn:
        cursor = conn.cursor()
        # Record user-to-store relationship
        cursor.execute(
            "INSERT OR IGNORE INTO store_users (store_id, user_id) VALUES (?, ?)",
            (store_id, user_id),
        )

        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            cursor.execute(
                "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                (username or "", first_name or "", user_id),
            )
            conn.commit()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            return dict(cursor.fetchone())

        initial_balance = 0
        ref_id = referrer_id if (referrer_id and referrer_id != user_id) else None
        cursor.execute(
            "INSERT INTO users (user_id, username, first_name, balance, language, referred_by, referral_earnings, referral_reward_paid) VALUES (?, ?, ?, ?, 'my', ?, 0, 0)",
            (user_id, username or "", first_name or "", initial_balance, ref_id),
        )
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return dict(cursor.fetchone())


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_users(store_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Returns all users, or filtered strictly by store_id for store admins."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if store_id:
            cursor.execute(
                """
                SELECT DISTINCT u.* FROM users u
                WHERE u.user_id IN (
                    SELECT user_id FROM store_users WHERE store_id = ?
                    UNION
                    SELECT user_id FROM orders WHERE store_id = ?
                )
                ORDER BY u.created_at DESC
                """,
                (store_id, store_id),
            )
            rows = cursor.fetchall()
            if rows:
                return [dict(row) for row in rows]
            # Fallback for store 1 if store_users hasn't backfilled
            if store_id == 1:
                cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
                return [dict(row) for row in cursor.fetchall()]
            return []
        else:
            cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]


def add_user_balance(user_id: int, amount: int) -> int:
    """Adds balance to user and returns the updated balance."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id),
        )
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row["balance"] if row else 0


def set_user_balance(user_id: int, new_balance: int) -> int:
    """Sets a user's wallet balance directly to an exact amount (can be 0 or any integer)."""
    clean_balance = max(0, int(new_balance))
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (clean_balance, user_id),
        )
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row["balance"] if row else clean_balance


def deduct_user_balance(user_id: int, amount: int) -> int:
    """Deducts an amount from user's wallet balance without dropping below 0."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            return 0
        curr_bal = row["balance"] or 0
        new_bal = max(0, curr_bal - int(amount))
        cursor.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (new_bal, user_id),
        )
        conn.commit()
        return new_bal


def is_user_in_store(user_id: int, store_id: int) -> bool:
    """Checks whether a user is linked to a specific store (registered or purchased)."""
    if store_id == 1:
        return True
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 1 FROM store_users WHERE store_id = ? AND user_id = ?
            UNION
            SELECT 1 FROM orders WHERE store_id = ? AND user_id = ?
            LIMIT 1
            """,
            (store_id, user_id, store_id, user_id),
        )
        return cursor.fetchone() is not None


# ==============================================================================
# Products & Stock Functions
# ==============================================================================

def get_products_with_stock(store_id: int = 1) -> List[Dict[str, Any]]:
    """Returns all products for a specific store with their current available stock count."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT p.id, p.name, p.description, p.price, p.category, p.store_id,
                   COUNT(CASE WHEN s.is_sold = 0 THEN 1 END) as stock_count
            FROM products p
            LEFT JOIN stock s ON p.id = s.product_id
            WHERE p.store_id = ?
            GROUP BY p.id
            ORDER BY p.id ASC
            """,
            (store_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_product(product_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT p.id, p.name, p.description, p.price, p.category, p.store_id,
                   COUNT(CASE WHEN s.is_sold = 0 THEN 1 END) as stock_count
            FROM products p
            LEFT JOIN stock s ON p.id = s.product_id
            WHERE p.id = ?
            GROUP BY p.id
            """,
            (product_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def add_stock(product_id: int, item_data: str, store_id: int = 1) -> bool:
    """Adds a new stock credential or key for a product."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO stock (product_id, item_data, store_id) VALUES (?, ?, ?)",
            (product_id, item_data.strip(), store_id),
        )
        conn.commit()
        return True


def get_product_stock_items(product_id: int) -> List[Dict[str, Any]]:
    """Returns all stock credentials for a product (unsold first, then sold)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, product_id, item_data, is_sold, sold_to_user_id, sold_at
            FROM stock
            WHERE product_id = ?
            ORDER BY is_sold ASC, id ASC
            """,
            (product_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_all_available_stocks(store_id: int = 1) -> List[Dict[str, Any]]:
    """Returns all available unsold stock items with product names for a specific store."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.id, s.product_id, p.name as product_name, s.item_data
            FROM stock s
            JOIN products p ON s.product_id = p.id
            WHERE s.is_sold = 0 AND p.store_id = ?
            ORDER BY s.product_id ASC, s.id ASC
            """,
            (store_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_stock_by_id(stock_id: int) -> Optional[Dict[str, Any]]:
    """Fetches a specific stock item with product name by ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.id, s.product_id, s.item_data, s.is_sold, s.sold_to_user_id, s.sold_at, p.name as product_name, p.store_id
            FROM stock s
            JOIN products p ON s.product_id = p.id
            WHERE s.id = ?
            """,
            (stock_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def update_stock_data(stock_id: int, new_item_data: str) -> bool:
    """Updates the credentials (mail/password/key) of an existing stock item."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE stock SET item_data = ? WHERE id = ?",
            (new_item_data.strip(), stock_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_stock(stock_id: int) -> bool:
    """Deletes a stock item if unsold."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM stock WHERE id = ? AND is_sold = 0", (stock_id,))
        conn.commit()
        return cursor.rowcount > 0


def add_product(name: str, description: str, price: int, category: str = "Digital", store_id: int = 1) -> int:
    """Adds a new product to the catalog scoped by store_id."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO products (name, description, price, category, store_id) VALUES (?, ?, ?, ?, ?)",
            (name, description, price, category, store_id),
        )
        conn.commit()
        return cursor.lastrowid


def delete_product(product_id: int) -> bool:
    """Deletes a product and its unsold stock from the database."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM stock WHERE product_id = ? AND is_sold = 0", (product_id,))
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        return cursor.rowcount > 0


def update_product_price(product_id: int, new_price: int) -> bool:
    """Updates product price."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE products SET price = ? WHERE id = ?", (new_price, product_id))
        conn.commit()
        return cursor.rowcount > 0


def update_product_name(product_id: int, new_name: str) -> bool:
    """Updates product name."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE products SET name = ? WHERE id = ?", (new_name.strip(), product_id))
        conn.commit()
        return cursor.rowcount > 0


def purchase_product(
    user_id: int, product_id: int
) -> Tuple[bool, str, Optional[str], int, str, int]:
    """
    Handles auto-delivery transaction atomically:
    1. Checks user balance.
    2. Checks product stock.
    3. Deducts balance.
    4. Marks 1 stock item as sold.
    5. Saves order record.
    Returns: (success: bool, message: str, delivered_data: Optional[str], remaining_stock: int, product_name: str, price: int)
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        # Fetch user
        cursor.execute("SELECT user_id, balance, referred_by, referral_reward_paid FROM users WHERE user_id = ?", (user_id,))
        user_row = cursor.fetchone()
        if not user_row:
            return False, "⚠️ အကောင့်ရှာမတွေ့ပါ /start ဖြင့် ပြန်လည်စတင်ပါ။", None, 0, "", 0

        user_balance = user_row["balance"]

        # Fetch product
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product_row = cursor.fetchone()
        if not product_row:
            return False, "⚠️ ပစ္စည်းရှာမတွေ့ပါ။", None, 0, "", 0

        price = product_row["price"]
        product_name = product_row["name"]

        # Check balance
        if user_balance < price:
            needed = price - user_balance
            return (
                False,
                f"⚠️ *လက်ကျန်ငွေ မလုံလောက်ပါ!*\n\n"
                f"📦 *ပစ္စည်း -* {product_name}\n"
                f"💰 *ကျသင့်ငွေ -* `{price:,} MMK`\n"
                f"💳 *လက်ကျန်ငွေ -* `{user_balance:,} MMK`\n"
                f"❗ *လိုအပ်သောငွေ -* `{needed:,} MMK`\n\n"
                f"ကျေးဇူးပြု၍ အောက်ပါ '💳 ငွေဖြည့်ရန်' ခလုတ်ကို နှိပ်ပြီး ငွေလွှဲပြေစာ ပေးပို့ဖြည့်သွင်းပါ။",
                None,
                0,
                product_name,
                price,
            )

        # Fetch one unsold stock item
        cursor.execute(
            """
            SELECT id, item_data FROM stock
            WHERE product_id = ? AND is_sold = 0
            LIMIT 1
            """,
            (product_id,),
        )
        stock_row = cursor.fetchone()
        if not stock_row:
            return (
                False,
                f"⚠️ ဝမ်းနည်းပါသည်၊ *{product_name}* ပစ္စည်းလက်ကျန် ကုန်သွားပါပြီ။ Admin မှ မကြာမီ ထပ်ဖြည့်ပေးပါမည်။",
                None,
                0,
                product_name,
                price,
            )

        stock_id = stock_row["id"]
        item_data = stock_row["item_data"]
        now = datetime.now()

        # Deduct balance
        cursor.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (price, user_id),
        )

        # Mark stock as sold
        cursor.execute(
            """
            UPDATE stock
            SET is_sold = 1, sold_to_user_id = ?, sold_at = ?
            WHERE id = ?
            """,
            (user_id, now, stock_id),
        )

        # Record order with product's store_id
        prod_store_id = product_row["store_id"] if (product_row and "store_id" in product_row.keys()) else 1
        cursor.execute(
            """
            INSERT INTO orders (user_id, product_id, product_name, price, delivered_data, purchased_at, store_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, product_id, product_name, price, item_data, now, prod_store_id),
        )

        # Check remaining stock count
        cursor.execute(
            "SELECT COUNT(*) as count FROM stock WHERE product_id = ? AND is_sold = 0",
            (product_id,),
        )
        remaining_stock = cursor.fetchone()["count"]

        # Award 1,000 MMK referral bonus if user was referred and hasn't triggered bonus yet
        referrer_id_to_notify = None
        referred_by = user_row["referred_by"]
        reward_paid = user_row["referral_reward_paid"] if "referral_reward_paid" in user_row.keys() else 0

        if referred_by and not reward_paid:
            ref_id = referred_by
            cursor.execute(
                "UPDATE users SET balance = balance + 1000, referral_earnings = referral_earnings + 1000 WHERE user_id = ?",
                (ref_id,),
            )
            cursor.execute(
                "UPDATE users SET referral_reward_paid = 1 WHERE user_id = ?",
                (user_id,),
            )
            referrer_id_to_notify = ref_id

        conn.commit()
        return True, "ဝယ်ယူမှုအောင်မြင်ပါသည်!", item_data, remaining_stock, product_name, price, referrer_id_to_notify


# ==============================================================================
# Orders & Stats Functions
# ==============================================================================

def get_user_orders(user_id: int, limit: int = 5, store_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Fetches recent purchase history for a user, optionally filtered by store_id."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if store_id is not None:
            cursor.execute(
                """
                SELECT id, product_name, price, delivered_data, purchased_at
                FROM orders
                WHERE user_id = ? AND store_id = ?
                ORDER BY purchased_at DESC
                LIMIT ?
                """,
                (user_id, store_id, limit),
            )
        else:
            cursor.execute(
                """
                SELECT id, product_name, price, delivered_data, purchased_at
                FROM orders
                WHERE user_id = ?
                ORDER BY purchased_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
        return [dict(row) for row in cursor.fetchall()]


def get_recent_orders(limit: int = 10, store_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Fetches global recent orders with user details, optionally filtered by store_id."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if store_id is not None:
            cursor.execute(
                """
                SELECT o.id, o.user_id, u.username, u.first_name, o.product_name, o.price, o.purchased_at
                FROM orders o
                LEFT JOIN users u ON o.user_id = u.user_id
                WHERE o.store_id = ?
                ORDER BY o.purchased_at DESC
                LIMIT ?
                """,
                (store_id, limit),
            )
        else:
            cursor.execute(
                """
                SELECT o.id, o.user_id, u.username, u.first_name, o.product_name, o.price, o.purchased_at
                FROM orders o
                LEFT JOIN users u ON o.user_id = u.user_id
                ORDER BY o.purchased_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [dict(row) for row in cursor.fetchall()]


def get_store_stats(store_id: int = 1) -> Dict[str, Any]:
    """Returns store dashboard statistics for the admin panel strictly scoped by store_id."""
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT COUNT(DISTINCT user_id) as count FROM (
                SELECT user_id FROM store_users WHERE store_id = ?
                UNION
                SELECT user_id FROM orders WHERE store_id = ?
            )
            """,
            (store_id, store_id),
        )
        total_users = cursor.fetchone()["count"]
        if store_id == 1 and total_users == 0:
            cursor.execute("SELECT COUNT(*) as count FROM users")
            total_users = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) as count, COALESCE(SUM(price), 0) as revenue FROM orders WHERE store_id = ?", (store_id,))
        orders_row = cursor.fetchone()
        total_orders = orders_row["count"]
        total_revenue = orders_row["revenue"]

        cursor.execute("SELECT COUNT(*) as count FROM products WHERE store_id = ?", (store_id,))
        total_products = cursor.fetchone()["count"]

        cursor.execute("""
            SELECT COUNT(*) as count FROM stock s
            JOIN products p ON s.product_id = p.id
            WHERE s.is_sold = 0 AND p.store_id = ?
        """, (store_id,))
        total_stock = cursor.fetchone()["count"]

        return {
            "total_users": total_users,
            "total_orders": total_orders,
            "total_revenue": total_revenue,
            "total_products": total_products,
            "total_stock": total_stock,
        }


def get_monthly_sales_stats(store_id: int = 1) -> List[Dict[str, Any]]:
    """Returns monthly sales breakdown: month, order count, total revenue scoped by store_id."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                COALESCE(strftime('%Y-%m', purchased_at), substr(purchased_at, 1, 7)) as month_str,
                COUNT(*) as order_count,
                COALESCE(SUM(price), 0) as total_sales
            FROM orders
            WHERE store_id = ?
            GROUP BY month_str
            ORDER BY month_str DESC
            """,
            (store_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


# ==============================================================================
# Settings & Exchange Rate Functions
# ==============================================================================

def get_setting(key: str, default: str = "") -> str:
    """Gets a setting value by key."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Sets or updates a setting value."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()


def get_store_setting(store_id: int, key: str, default: str = "") -> str:
    """Gets setting for a specific store, falling back to global setting or default."""
    val = get_setting(f"store_{store_id}_{key}", "")
    if val:
        return val
    return get_setting(key, default)


def set_store_setting(store_id: int, key: str, value: str) -> None:
    """Sets a store-specific setting."""
    set_setting(f"store_{store_id}_{key}", value)


def get_exchange_rate(store_id: int = 1) -> int:
    """Returns the current THB to MMK exchange rate for a store (Default: 135 MMK per 1 THB)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT thb_exchange_rate FROM stores WHERE id = ?", (store_id,))
        row = cursor.fetchone()
        if row and row["thb_exchange_rate"]:
            return int(row["thb_exchange_rate"])
    val = get_setting("thb_exchange_rate", "135")
    try:
        return int(val)
    except ValueError:
        return 135


def set_exchange_rate(rate: int, store_id: int = 1) -> int:
    """Sets a new THB to MMK exchange rate for a store."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE stores SET thb_exchange_rate = ? WHERE id = ?", (rate, store_id))
        conn.commit()
    if store_id == 1:
        set_setting("thb_exchange_rate", str(rate))
    return rate


# ==============================================================================
# Language & Referral Functions
# ==============================================================================

def get_user_language(user_id: int) -> str:
    """Gets user language ('my' or 'en', defaults to 'my')."""
    user = get_user(user_id)
    if user and user.get("language"):
        return user["language"]
    return "my"


def set_user_language(user_id: int, lang: str) -> None:
    """Updates user language preference."""
    if lang not in ("my", "en"):
        lang = "my"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET language = ? WHERE user_id = ?", (lang, user_id))
        conn.commit()


def get_referral_stats(user_id: int) -> Dict[str, Any]:
    """Returns referral stats: total invited count, total earnings, and list of referred users."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM users WHERE referred_by = ?", (user_id,))
        count = cursor.fetchone()["count"]

        cursor.execute("SELECT referral_earnings FROM users WHERE user_id = ?", (user_id,))
        user_row = cursor.fetchone()
        earnings = user_row["referral_earnings"] if (user_row and "referral_earnings" in user_row.keys()) else 0

        cursor.execute(
            "SELECT user_id, username, first_name, created_at FROM users WHERE referred_by = ? ORDER BY created_at DESC LIMIT 10",
            (user_id,),
        )
        invited_users = [dict(r) for r in cursor.fetchall()]

        return {
            "total_referred": count,
            "referral_earnings": earnings or 0,
            "invited_users": invited_users,
        }


# ==============================================================================
# SaaS Store Customization & Multi-tenant Licensing Functions
# ==============================================================================

def get_store_name(store_id: int = 1) -> str:
    """Gets the dynamically configured store name."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT store_name FROM stores WHERE id = ?", (store_id,))
        row = cursor.fetchone()
        if row and row["store_name"]:
            return row["store_name"]
    if store_id == 1:
        return get_setting("store_name", "Digital Store")
    return f"Store #{store_id}"


def set_store_name(name: str, store_id: int = 1) -> str:
    """Updates the store name dynamically in the database for the given store."""
    name = name.strip()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE stores SET store_name = ? WHERE id = ?", (name, store_id))
        conn.commit()
    if store_id == 1:
        set_setting("store_name", name)
    return name


def get_store_topup_instructions(store_id: int = 1) -> str:
    """Gets custom top-up instructions text for a store."""
    return get_store_setting(store_id, "topup_instructions", "")


def set_store_topup_instructions(store_id: int, text: str) -> None:
    """Sets custom top-up instructions text for a store."""
    set_store_setting(store_id, "topup_instructions", text.strip())


def get_store_qr_file(store_id: int = 1) -> str:
    """Gets the payment QR file_id or status ('none', 'removed', telegram file_id, or empty). Scoped strictly per store."""
    if store_id == 1:
        return get_setting("qr_file_id", "")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT qr_file_id FROM stores WHERE id = ?", (store_id,))
        row = cursor.fetchone()
        if row and row["qr_file_id"]:
            return row["qr_file_id"]
    return get_setting(f"store_{store_id}_qr_file_id", "")


def set_store_qr_file(store_id: int, file_id: str) -> None:
    """Sets payment QR file_id or marks as 'none'/'removed' strictly scoped per store."""
    clean_val = file_id.strip()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE stores SET qr_file_id = ? WHERE id = ?", (clean_val, store_id))
        conn.commit()
    set_setting(f"store_{store_id}_qr_file_id", clean_val)
    if store_id == 1:
        set_setting("qr_file_id", clean_val)


def get_admin_username() -> str:
    """Gets the configured admin contact username."""
    return get_setting("admin_username", "latt_swal2026")


def set_admin_username(username: str) -> str:
    """Updates the admin contact username dynamically."""
    username = username.strip().lstrip("@")
    set_setting("admin_username", username)
    return username


def get_license_info() -> Dict[str, Any]:
    """
    Fetches the global / default SaaS license status and subscription validity.
    """
    return get_store_license(1)


def set_license_expiry(expiry_date_str: str) -> Dict[str, Any]:
    """Sets a specific expiry date string for Store #1."""
    expiry_date_str = expiry_date_str.strip()
    if len(expiry_date_str) == 10:
        expiry_date_str += " 23:59:59"
    update_store(1, license_expiry=expiry_date_str, license_status="active")
    set_setting("license_expiry", expiry_date_str)
    set_setting("license_status", "active")
    return get_store_license(1)


def extend_license(days: int = 30) -> Dict[str, Any]:
    """Extends Store #1 license by X days."""
    res = extend_store_license(1, days)
    set_setting("license_expiry", res.get("expiry_full", ""))
    set_setting("license_status", "active")
    return res


def set_license_status(status: str) -> None:
    """Updates license status ('active', 'suspended', 'expired') for Store #1."""
    set_store_license_status(1, status)
    set_setting("license_status", status.strip().lower())


def kill_switch() -> Dict[str, Any]:
    """Instantly suspends Store #1."""
    set_store_license_status(1, "suspended")
    set_setting("license_status", "suspended")
    return get_store_license(1)


def activate_license(days: int = 30) -> Dict[str, Any]:
    """Reactivates or initializes Store #1."""
    return extend_store_license(1, days)


# ==============================================================================
# Multi-tenant Stores & Store Applications Management
# ==============================================================================

def get_store(store_id: int) -> Optional[Dict[str, Any]]:
    """Fetches store by ID with owner details."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.*, u.username as owner_username, u.first_name as owner_first_name
            FROM stores s
            LEFT JOIN users u ON s.owner_id = u.user_id
            WHERE s.id = ?
            """,
            (store_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_store_by_owner(owner_id: int) -> Optional[Dict[str, Any]]:
    """Fetches store by owner_id."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stores WHERE owner_id = ?", (owner_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_store_by_token(token: str) -> Optional[Dict[str, Any]]:
    """Fetches store by bot_token."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stores WHERE bot_token = ?", (token,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_store_by_bot_username(username: str) -> Optional[Dict[str, Any]]:
    """Fetches store by bot_username (@username or username)."""
    uname = username.strip().lstrip("@")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM stores WHERE REPLACE(bot_username, '@', '') = ? COLLATE NOCASE",
            (uname,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_store_id(user_id: int) -> Optional[int]:
    """Fetches the store_id the user is registered under in store_users table."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT store_id FROM store_users WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
            (user_id,),
        )
        row = cursor.fetchone()
        return row["store_id"] if row else None


def get_all_stores() -> List[Dict[str, Any]]:
    """Returns all tenant stores with owner details."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.*, u.username as owner_username, u.first_name as owner_first_name
            FROM stores s
            LEFT JOIN users u ON s.owner_id = u.user_id
            ORDER BY s.id ASC
            """
        )
        return [dict(row) for row in cursor.fetchall()]


def clean_bot_token(token: Optional[str]) -> str:
    """Cleans and sanitizes a Telegram bot token by stripping whitespace, newlines, tabs, and non-printable ASCII characters."""
    if not token:
        return ""
    import re
    s = str(token).strip().strip("'\"`")
    # Remove all whitespace characters (\r, \n, \t, spaces) and non-printable control characters
    s = re.sub(r"[\s\x00-\x1f\x7f-\x9f]", "", s)
    return s


def create_store(
    owner_id: int,
    store_name: str,
    plan: str = "Pro Business",
    days: int = 30,
    bot_token: str = "",
    bot_username: str = "",
) -> int:
    """Creates a new store or renews/updates existing store for the owner."""
    bot_token = clean_bot_token(bot_token)
    bot_username = bot_username.strip() if bot_username else ""
    expiry_dt = datetime.now() + timedelta(days=days)
    expiry_str = expiry_dt.strftime("%Y-%m-%d 23:59:59")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM stores WHERE owner_id = ?", (owner_id,))
        existing = cursor.fetchone()
        if existing:
            store_id = existing["id"]
            cursor.execute(
                """
                UPDATE stores
                SET store_name = ?, plan = ?, license_expiry = ?, license_status = 'active',
                    bot_token = CASE WHEN ? != '' THEN ? ELSE bot_token END,
                    bot_username = CASE WHEN ? != '' THEN ? ELSE bot_username END
                WHERE id = ?
                """,
                (store_name, plan, expiry_str, bot_token, bot_token, bot_username, bot_username, store_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO stores (owner_id, store_name, plan, license_expiry, license_status, bot_token, bot_username)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (owner_id, store_name, plan, expiry_str, bot_token, bot_username),
            )
            store_id = cursor.lastrowid
        # Ensure owner is registered in admins table
        cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (owner_id,))
        conn.commit()
        return store_id


def update_store(store_id: int, **kwargs) -> bool:
    """Updates fields on a store record."""
    if not kwargs:
        return False
    if "bot_token" in kwargs and kwargs["bot_token"] is not None:
        kwargs["bot_token"] = clean_bot_token(kwargs["bot_token"])
    if "bot_username" in kwargs and kwargs["bot_username"] is not None:
        kwargs["bot_username"] = str(kwargs["bot_username"]).strip()
    fields = []
    values = []
    for k, v in kwargs.items():
        fields.append(f"{k} = ?")
        values.append(v)
    values.append(store_id)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE stores SET {', '.join(fields)} WHERE id = ?", tuple(values))
        conn.commit()
        return cursor.rowcount > 0


def get_store_license(store_id: int) -> Dict[str, Any]:
    """Checks license and validity for a specific store."""
    store = get_store(store_id)
    if not store:
        return {
            "is_valid": False,
            "status": "not_found",
            "expiry_str": "N/A",
            "expiry_full": "N/A",
            "days_remaining": 0,
            "plan": "None",
            "store_name": "Unknown",
            "owner_id": 0,
            "bot_token": "",
            "bot_username": "",
        }

    status = (store.get("license_status") or "active").strip().lower()
    expiry_str = (store.get("license_expiry") or "2026-12-31 23:59:59").strip()
    plan = store.get("plan") or "Pro Business"
    store_name = store.get("store_name") or (get_setting("store_name", "Digital Store") if store_id == 1 else f"Store #{store_id}")
    owner_id = store.get("owner_id") or 0

    expiry_dt = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%d %H:%M"):
        try:
            expiry_dt = datetime.strptime(expiry_str, fmt)
            break
        except ValueError:
            pass

    if not expiry_dt:
        expiry_dt = datetime.now() + timedelta(days=30)
        expiry_str = expiry_dt.strftime("%Y-%m-%d 23:59:59")
        update_store(store_id, license_expiry=expiry_str)

    now = datetime.now()
    time_diff = expiry_dt - now
    days_remaining = time_diff.days

    if status == "suspended":
        is_valid = False
        effective_status = "suspended"
    elif now > expiry_dt:
        is_valid = False
        effective_status = "expired"
        if status != "expired":
            update_store(store_id, license_status="expired")
    else:
        is_valid = (status == "active")
        effective_status = status if is_valid else "inactive"

    return {
        "is_valid": is_valid,
        "status": effective_status,
        "expiry_str": expiry_dt.strftime("%Y-%m-%d"),
        "expiry_full": expiry_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "days_remaining": max(0, days_remaining) if is_valid else 0,
        "plan": plan,
        "store_name": store_name,
        "owner_id": owner_id,
        "bot_token": store.get("bot_token") or "",
        "bot_username": store.get("bot_username") or "",
    }


def extend_store_license(store_id: int, days: int = 30) -> Dict[str, Any]:
    """Extends store license by X days from current expiry or now."""
    store = get_store(store_id)
    if not store:
        return {}
    now = datetime.now()
    current_expiry_dt = None
    expiry_val = store.get("license_expiry") or ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            current_expiry_dt = datetime.strptime(expiry_val, fmt)
            break
        except ValueError:
            pass

    if not current_expiry_dt or current_expiry_dt < now:
        base_dt = now
    else:
        base_dt = current_expiry_dt

    new_expiry_dt = base_dt + timedelta(days=days)
    new_expiry_str = new_expiry_dt.strftime("%Y-%m-%d 23:59:59")
    update_store(store_id, license_expiry=new_expiry_str, license_status="active")
    return get_store_license(store_id)


def set_store_license_status(store_id: int, status: str) -> None:
    """Updates license status ('active', 'suspended', 'expired') for a store."""
    update_store(store_id, license_status=status.strip().lower())


def create_store_request(
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    store_name: str,
    plan: str,
    slip_file_id: Optional[str],
) -> int:
    """Records a new store onboarding application."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO store_requests (user_id, username, first_name, store_name, plan, slip_file_id, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """,
            (user_id, username, first_name, store_name, plan, slip_file_id),
        )
        conn.commit()
        return cursor.lastrowid


def get_store_request(req_id: int) -> Optional[Dict[str, Any]]:
    """Gets store request by ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM store_requests WHERE id = ?", (req_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_pending_store_requests() -> List[Dict[str, Any]]:
    """Gets all pending store applications."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM store_requests WHERE status = 'pending' ORDER BY created_at DESC")
        return [dict(row) for row in cursor.fetchall()]


def approve_store_request(req_id: int, days: int = 30) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Approves a store request, sets up/activates their store, adds admin access."""
    req = get_store_request(req_id)
    if not req:
        return False, None
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE store_requests SET status = 'approved' WHERE id = ?", (req_id,))
        conn.commit()

    store_id = create_store(
        owner_id=req["user_id"],
        store_name=req["store_name"],
        plan=req["plan"],
        days=days,
    )
    store = get_store(store_id)
    return True, store


def reject_store_request(req_id: int) -> bool:
    """Rejects a store request."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE store_requests SET status = 'rejected' WHERE id = ?", (req_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_store(store_id: int) -> bool:
    """Deletes a client store and unlinks its products and stocks."""
    if store_id == 1:
        return False  # Protect master store
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM stores WHERE id = ?", (store_id,))
        cursor.execute("DELETE FROM stock WHERE store_id = ?", (store_id,))
        cursor.execute("DELETE FROM products WHERE store_id = ?", (store_id,))
        conn.commit()
        return cursor.rowcount > 0


