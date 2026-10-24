"""
Comprehensive Localization (Myanmar & English) for Latt Swal Store Telegram Bot.
Provides seamless language switching between Myanmar (မြန်မာစာ) and English.
"""

from typing import Dict, Any

STRINGS: Dict[str, Dict[str, str]] = {
    # =========================================================================
    # Navigation Buttons
    # =========================================================================
    "btn_products": {
        "my": "🛒 ထုတ်ကုန်များ",
        "en": "🛒 Products",
    },
    "btn_topup": {
        "my": "💳 ငွေဖြည့်ရန်",
        "en": "💳 Top-up",
    },
    "btn_referral": {
        "my": "👥 မိတ်ဆက်",
        "en": "👥 Referral",
    },
    "btn_history": {
        "my": "📜 မှတ်တမ်း",
        "en": "📜 History",
    },
    "btn_status": {
        "my": "👤 Member Status",
        "en": "👤 Member Status",
    },
    "btn_guide": {
        "my": "📖 အသုံးပြုနည်း",
        "en": "📖 User Guide",
    },
    "btn_language": {
        "my": "🌐 ဘာသာစကား",
        "en": "🌐 Language",
    },
    "btn_support": {
        "my": "❓ အကူအညီ",
        "en": "❓ Support",
    },
    "btn_admin_panel": {
        "my": "👑 Admin Panel",
        "en": "👑 Admin Panel",
    },
    "btn_back_home": {
        "my": "🔙 ပင်မစာမျက်နှာသို့",
        "en": "🔙 Back to Home",
    },
    "btn_refresh": {
        "my": "🔄 Refresh",
        "en": "🔄 Refresh",
    },
    "btn_buy_now": {
        "my": "🛒 ဝယ်ယူမည် (Buy Now)",
        "en": "🛒 Buy Now",
    },
    "btn_contact_admin": {
        "my": "💬 Admin ထံ စာပို့ရန်",
        "en": "💬 Contact Admin",
    },
    "btn_thai_qr": {
        "my": "🇹🇭 Thai QR Code (PromptPay)",
        "en": "🇹🇭 Thai QR Code (PromptPay)",
    },

    # =========================================================================
    # Welcome & Main Menu
    # =========================================================================
    "welcome_message": {
        "my": (
            "🇲🇲 *{store_name} မှ ကြိုဆိုပါသည်!* 👋\n\n"
            "👤 *အကောင့်ပရိုဖိုင် (Profile)*\n"
            "• အမည် - *{name}*\n"
            "• User ID - `{user_id}`\n"
            "• လက်ကျန် Balance - `{balance:,} MMK`\n"
            "• မိတ်ဆက်ရငွေ - `{referral_earnings:,} MMK`\n"
            "• THB ငွေလဲနှုန်း - `1 THB = {exchange_rate:,} MMK`\n\n"
            "Digital Accounts , Premium Subscriptions များကို "
            "*အလိုအလျောက် (Auto-Delivery)* စနစ်ဖြင့် ၂၄ နာရီ ဝယ်ယူနိုင်သော Official Store ဖြစ်ပါသည်။\n"
            "{admin_tag}\n"
            "👇 အောက်ပါခလုတ်များကို နှိပ်၍ အသုံးပြုနိုင်ပါသည် -"
        ),
        "en": (
            "🌟 *Welcome to {store_name}!* 👋\n\n"
            "👤 *User Profile*\n"
            "• Name - *{name}*\n"
            "• User ID - `{user_id}`\n"
            "• Current Balance - `{balance:,} MMK`\n"
            "• Referral Earnings - `{referral_earnings:,} MMK`\n"
            "• THB Exchange Rate - `1 THB = {exchange_rate:,} MMK`\n\n"
            "Your 24/7 automated store for Digital Accounts and Premium Subscriptions.\n"
            "{admin_tag}\n"
            "👇 Tap the buttons below to explore our services -"
        ),
    },

    # =========================================================================
    # Products Catalog
    # =========================================================================
    "catalog_title": {
        "my": "🛒 *{store_name} - ရရှိနိုင်သော ပစ္စည်းများ*\n\nဝယ်ယူလိုသော ပစ္စည်းကို ရွေးချယ်ပြီး အလိုအလျောက် ချက်ချင်း ဝယ်ယူနိုင်ပါသည် -\n──────────────────────",
        "en": "🛒 *{store_name} - Available Products*\n\nSelect a product to view details and purchase with instant auto-delivery -\n──────────────────────",
    },
    "product_detail": {
        "my": (
            "*{name}*\n"
            "🏷️ *အုပ်စု -* {category}\n\n"
            "📝 *အသေးစိတ် အချက်အလက် -*\n{description}\n\n"
            "💰 *စျေးနှုန်း -* `{price:,} MMK` (~{thb_price:,} THB)\n"
            "{stock_status}\n"
            "──────────────────────"
        ),
        "en": (
            "*{name}*\n"
            "🏷️ *Category -* {category}\n\n"
            "📝 *Product Details -*\n{description}\n\n"
            "💰 *Price -* `{price:,} MMK` (~{thb_price:,} THB)\n"
            "{stock_status}\n"
            "──────────────────────"
        ),
    },
    "stock_available": {
        "my": "✅ ပစ္စည်းလက်ကျန် - `{count} ခု`",
        "en": "✅ In Stock - `{count} items`",
    },
    "stock_out": {
        "my": "❌ ပစ္စည်းလက်ကျန် - ကုန်သွားပါပြီ (Out of Stock)",
        "en": "❌ Out of Stock",
    },

    # =========================================================================
    # Top-up (MMK & THB Support & Exchange Rate)
    # =========================================================================
    "topup_info": {
        "my": (
            "💳 *{store_name} - ငွေဖြည့်သွင်းရန် (Top-up)*\n\n"
            "💵 *သင်၏ လက်ရှိ Balance -* `{balance:,} MMK`\n\n"
            "💱 *လက်ရှိ ထိုင်းဘတ် (THB) ငွေလဲနှုန်း -*\n"
            "👉 *`1 THB (ဘတ်) = {exchange_rate:,} MMK (ကျပ်)`*\n"
            "• 100 THB = `{calc_100:,} MMK`\n"
            "• 300 THB = `{calc_300:,} MMK`\n"
            "• 500 THB = `{calc_500:,} MMK`\n"
            "• 1,000 THB = `{calc_1000:,} MMK`\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🇲🇲 *မြန်မာကျပ်ငွေဖြင့် လွှဲရန် (MMK Payments) -*\n"
            "📱 *KBZPay (KPay) -* `{kpay_phone}`\n"
            "👤 *Name -* `{kpay_name}`\n\n"
            "📱 *WavePay (WPay) -* `{wpay_phone}`\n"
            "👤 *Name -* `{wpay_name}`\n\n"
            "🇹🇭 *ထိုင်းဘတ်ငွေဖြင့် လွှဲရန် (THB Payments) -*\n"
            "🏦 *Bank -* SCB (Siam Commercial Bank)\n"
            "📱 *PromptPay -* `XXX-XXX-9883` / `{promptpay_phone}`\n"
            "👤 *Name -* `{promptpay_name}`\n"
            "📸 *Thai QR Code -* အထက်ပါ/အောက်ပါ QR Code ကို Scan ဖတ်၍ တိုက်ရိုက် လွှဲနိုင်ပါသည်\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📸 *ငွေဖြည့်သွင်းနည်း အဆင့်ဆင့် -*\n"
            "1. အထက်ပါ အကောင့် သို့မဟုတ် Thai QR Code သို့ မိမိဖြည့်လိုသော ငွေပမာဏကို လွှဲပေးပါ။\n"
            "2. ငွေလွှဲပြီးပါက ရရှိသော *ငွေလွှဲပြေစာ (Screenshot / Slip)* ကို ဤ Bot ထဲသို့ ဓာတ်ပုံ ပို့ပေးပါ။\n"
            "3. Admin မှ ပြေစာကို စစ်ဆေးအတည်ပြုပြီးပါက သင့်အကောင့်ထဲသို့ Balance ချက်ချင်း ထည့်သွင်းပေးပါမည်။\n\n"
            "📞 အကူအညီလိုအပ်ပါက {admin_contact} သို့ ဆက်သွယ်မေးမြန်းနိုင်ပါသည်။"
        ),
        "en": (
            "💳 *{store_name} - Account Top-up*\n\n"
            "💵 *Your Current Balance -* `{balance:,} MMK`\n\n"
            "💱 *Current Thai Baht (THB) Exchange Rate -*\n"
            "👉 *`1 THB = {exchange_rate:,} MMK`*\n"
            "• 100 THB = `{calc_100:,} MMK`\n"
            "• 300 THB = `{calc_300:,} MMK`\n"
            "• 500 THB = `{calc_500:,} MMK`\n"
            "• 1,000 THB = `{calc_1000:,} MMK`\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🇲🇲 *Myanmar Kyat (MMK Accounts) -*\n"
            "📱 *KBZPay -* `{kpay_phone}`\n"
            "👤 *Name -* `{kpay_name}`\n\n"
            "📱 *WavePay -* `{wpay_phone}`\n"
            "👤 *Name -* `{wpay_name}`\n\n"
            "🇹🇭 *Thai Baht (THB Accounts) -*\n"
            "🏦 *Bank -* SCB (Siam Commercial Bank)\n"
            "📱 *PromptPay -* `XXX-XXX-9883` / `{promptpay_phone}`\n"
            "👤 *Name -* `{promptpay_name}`\n"
            "📸 *Thai QR Code -* Scan the Thai QR Code above/below to pay directly\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📸 *How to Top-up -*\n"
            "1. Transfer your desired amount to any account or scan the Thai QR Code.\n"
            "2. Send the *transaction screenshot (slip)* directly to this bot chat.\n"
            "3. Admin will review and credit the balance to your account immediately.\n\n"
            "📞 Need assistance? Contact {admin_contact} directly."
        ),
    },
    "thai_qr_caption": {
        "my": (
            "🇹🇭 *[Thai QR Payment - PromptPay]*\n\n"
            "🏦 *Bank -* SCB (Siam Commercial Bank)\n"
            "📱 *PromptPay ID -* `XXX-XXX-9883`\n"
            "💱 *လက်ရှိ ငွေလဲနှုန်း -* `1 THB = {exchange_rate:,} MMK`\n\n"
            "• 100 THB = `{calc_100:,} MMK`\n"
            "• 300 THB = `{calc_300:,} MMK`\n"
            "• 500 THB = `{calc_500:,} MMK`\n"
            "• 1,000 THB = `{calc_1000:,} MMK`\n\n"
            "📸 အထက်ပါ QR Code ကို Scan ဖတ်၍ လိုချင်သော ဘတ်ငွေပမာဏ လွှဲပေးပါ။\n"
            "ငွေလွှဲပြီးပါက ရရှိသော *ငွေလွှဲပြေစာ (Slip / Screenshot)* ကို ဤ Bot ထဲသို့ ဓာတ်ပုံ ပို့ပေးပါ။ Admin မှ စစ်ဆေးအတည်ပြုပြီးပါက သင့်အကောင့်ထဲသို့ Balance ချက်ချင်း ထည့်သွင်းပေးပါမည်။"
        ),
        "en": (
            "🇹🇭 *[Thai QR Payment - PromptPay]*\n\n"
            "🏦 *Bank -* SCB (Siam Commercial Bank)\n"
            "📱 *PromptPay ID -* `XXX-XXX-9883`\n"
            "💱 *Exchange Rate -* `1 THB = {exchange_rate:,} MMK`\n\n"
            "• 100 THB = `{calc_100:,} MMK`\n"
            "• 300 THB = `{calc_300:,} MMK`\n"
            "• 500 THB = `{calc_500:,} MMK`\n"
            "• 1,000 THB = `{calc_1000:,} MMK`\n\n"
            "📸 Scan the QR Code above with any Thai Banking App to transfer.\n"
            "After transferring, send the *payment screenshot (slip)* to this chat. Admin will review and credit your balance immediately."
        ),
    },
    "slip_received": {
        "my": (
            "✅ *သင်၏ ငွေလွှဲပြေစာကို Admin ထံသို့ ပေးပို့ထားပြီး ဖြစ်ပါသည်!*\n\n"
            "Admin မှ စစ်ဆေးအတည်ပြုပြီးပါက သင့်အကောင့်ထဲသို့ Balance အလိုအလျောက် ရောက်ရှိလာပါမည်။\n"
            "ကျေးဇူးပြု၍ ခေတ္တစောင့်ဆိုင်းပေးပါ။ 🙏"
        ),
        "en": (
            "✅ *Your payment slip has been submitted to Admin!*\n\n"
            "Once confirmed, the balance will be automatically credited to your account.\n"
            "Please wait a moment. Thank you! 🙏"
        ),
    },

    # =========================================================================
    # Referral System
    # =========================================================================
    "referral_info": {
        "my": (
            "👥 *{store_name} - မိတ်ဆက် (Referral Program)*\n\n"
            "မိတ်ဆွေများကို သင့် Referral Link ဖြင့် ဖိတ်ခေါ်ပြီး ဆုလာဘ်များ ရယူပါ!\n\n"
            "🎁 *မိတ်ဆက်ဆုကြေး အထူးအစီအစဉ် -*\n"
            "သင့် Link ဖြင့် ဝင်ရောက်လာသော မိတ်ဆွေတစ်ဦးသည် စတိုးမှ ပစ္စည်းစတင်ဝယ်ယူတိုင်း "
            "သင့်အကောင့်ထဲသို့ *`1,000 MMK` Bonus* အလိုအလျောက် ရရှိပါမည်!\n\n"
            "🔗 *သင့် မိတ်ဆက် Link -*\n"
            "`{ref_link}`\n\n"
            "📊 *မိတ်ဆက်စာရင်း အကျဉ်းချုပ် -*\n"
            "• ဖိတ်ခေါ်ခဲ့သော မိတ်ဆွေ - `{total_referred} ယောက်`\n"
            "• မိတ်ဆက်ရငွေ စုစုပေါင်း - `{referral_earnings:,} MMK`\n\n"
            "💡 အထက်ပါ Link ကို မိတ်ဆွေများထံ Share ပေး၍ စတင်ဖိတ်ခေါ်နိုင်ပါသည်!"
        ),
        "en": (
            "👥 *{store_name} - Referral Program*\n\n"
            "Invite friends using your unique referral link and earn rewards!\n\n"
            "🎁 *Referral Bonus Reward -*\n"
            "When your referred friend makes their first purchase, "
            "you will receive a *`1,000 MMK` Bonus* credited directly to your balance!\n\n"
            "🔗 *Your Referral Link -*\n"
            "`{ref_link}`\n\n"
            "📊 *Referral Summary -*\n"
            "• Friends Invited - `{total_referred} users`\n"
            "• Total Referral Earnings - `{referral_earnings:,} MMK`\n\n"
            "💡 Share your link with friends to get started!"
        ),
    },

    # =========================================================================
    # History (Orders)
    # =========================================================================
    "history_title": {
        "my": "📜 *{store_name} - ဝယ်ယူမှုမှတ်တမ်း (Order History)*\n\n",
        "en": "📜 *{store_name} - Purchase History*\n\n",
    },
    "history_empty": {
        "my": "ℹ️ ဝယ်ယူထားသော မှတ်တမ်း မရှိသေးပါ။",
        "en": "ℹ️ No purchase history found yet.",
    },

    # =========================================================================
    # Member Status
    # =========================================================================
    "member_status": {
        "my": (
            "👤 *{store_name} - Member Status*\n\n"
            "🆔 *User ID -* `{user_id}`\n"
            "👤 *အမည် -* {name}\n"
            "🏷️ *Username -* @{username}\n"
            "💰 *လက်ကျန်ငွေ -* `{balance:,} MMK`\n"
            "🌐 *ဘာသာစကား -* {lang_name}\n\n"
            "{orders_section}"
        ),
        "en": (
            "👤 *{store_name} - Member Profile*\n\n"
            "🆔 *User ID -* `{user_id}`\n"
            "👤 *Name -* {name}\n"
            "🏷️ *Username -* @{username}\n"
            "💰 *Balance -* `{balance:,} MMK`\n"
            "🌐 *Language -* {lang_name}\n\n"
            "{orders_section}"
        ),
    },

    # =========================================================================
    # User Guide (အသုံးပြုနည်း)
    # =========================================================================
    "guide_info": {
        "my": (
            "📖 *{store_name} - အသုံးပြုနည်း လမ်းညွှန်*\n\n"
            "🔹 *၁။ ငွေဖြည့်သွင်းနည်း (Top-up)*\n"
            "• `💳 ငွေဖြည့်ရန်` ခလုတ်ကို နှိပ်ပါ။\n"
            "• KBZPay, WavePay သို့မဟုတ် Thai Bank / PromptPay ဖြင့် လွှဲပေးပါ။\n"
            "• ငွေလွှဲပြေစာ Screenshot ကို Bot ထဲသို့ ဓာတ်ပုံ ပို့ပေးပါ။ Admin မှ စစ်ဆေးအတည်ပြုပေးပါမည်။\n\n"
            "🔹 *၂။ ပစ္စည်းဝယ်ယူနည်း (Buy Product)*\n"
            "• `🛒 ထုတ်ကုန်များ` ကို နှိပ်ပြီး လိုချင်သော ပစ္စည်းကို ရွေးပါ။\n"
            "• `ဝယ်ယူမည်` နှိပ်လိုက်သည်နှင့် Bot မှ Account, Code သို့မဟုတ် Link ကို ချက်ချင်း အလိုအလျောက် ပို့ပေးပါမည်။\n\n"
            "🔹 *၃။ ဝယ်ယူပြီး ပစ္စည်းများ ပြန်ကြည့်နည်း*\n"
            "• `📜 မှတ်တမ်း` သို့မဟုတ် `👤 Member Status` တွင် အချိန်မရွေး ပြန်လည်ကြည့်ရှုနိုင်ပါသည်။"
        ),
        "en": (
            "📖 *{store_name} - User Guide*\n\n"
            "🔹 *1. How to Top-up Balance*\n"
            "• Tap `💳 Top-up` in the menu.\n"
            "• Transfer via KBZPay, WavePay, or Thai Bank / PromptPay.\n"
            "• Send the transfer screenshot to the bot chat for Admin approval.\n\n"
            "🔹 *2. How to Purchase Products*\n"
            "• Tap `🛒 Products` and choose your desired item.\n"
            "• Tap `Buy Now`, and the bot will instantly deliver the login/code/link.\n\n"
            "🔹 *3. Viewing Past Purchases*\n"
            "• Check `📜 History` or `👤 Member Status` at any time to access your delivered credentials."
        ),
    },

    # =========================================================================
    # Support / Help
    # =========================================================================
    "support_info": {
        "my": (
            "❓ *{store_name} - အကူအညီနှင့် ဝန်ဆောင်မှု*\n\n"
            "ကျွန်ုပ်တို့ စတိုးသည် ၂၄ နာရီ အလိုအလျောက် ဝန်ဆောင်မှုပေးနေပါသည်။\n\n"
            "🔹 အကောင့်အချက်အလက်၊ ငွေဖြည့်သွင်းမှု သို့မဟုတ် အာမခံ (Warranty) နှင့် ပတ်သက်၍ "
            "အကူအညီ လိုအပ်ပါက Admin ထံ တိုက်ရိုက် ဆက်သွယ် မေးမြန်းနိုင်ပါသည် -\n\n"
            "📞 *Admin -* {admin_contact}\n"
            "⏰ *ဝန်ဆောင်မှုချိန် -* ၂၄ နာရီ အလိုအလျောက်စနစ်"
        ),
        "en": (
            "❓ *{store_name} - Help & Support*\n\n"
            "Our automated store is available 24/7.\n\n"
            "🔹 If you have questions regarding accounts, top-up balance, or product warranty, "
            "feel free to contact our Admin directly -\n\n"
            "📞 *Admin -* {admin_contact}\n"
            "⏰ *Support Hours -* 24/7 Automated Delivery"
        ),
    },

    # =========================================================================
    # Language Selection Menu
    # =========================================================================
    "language_menu": {
        "my": (
            "🌐 *ဘာသာစကား ရွေးချယ်ရန် (Select Language)*\n\n"
            "သင်အသုံးပြုလိုသော ဘာသာစကားကို ရွေးချယ်ပေးပါ -\n"
            "Please select your preferred language below -"
        ),
        "en": (
            "🌐 *Language Settings (Select Language)*\n\n"
            "Please select your preferred language below -\n"
            "သင်အသုံးပြုလိုသော ဘာသာစကားကို ရွေးချယ်ပေးပါ -"
        ),
    },
    "language_changed_my": {
        "my": "🇲🇲 ဘာသာစကားကို *မြန်မာစာ* သို့ အောင်မြင်စွာ ပြောင်းလဲပြီးပါပြီ!",
        "en": "🇲🇲 Language has been changed to *Myanmar*!",
    },
    "language_changed_en": {
        "my": "🇬🇧 Language has been changed to *English* successfully!",
        "en": "🇬🇧 Language has been changed to *English* successfully!",
    },

    # =========================================================================
    # Purchase Success & Failure
    # =========================================================================
    "purchase_success": {
        "my": (
            "🎉 *ဝယ်ယူမှု အောင်မြင်ပါသည်! (Payment Successful)*\n\n"
            "📦 *ပစ္စည်း -* {product_name}\n"
            "💰 *ကျသင့်ငွေ -* `{price:,} MMK`\n"
            "💳 *လက်ကျန်ငွေ -* `{balance:,} MMK`\n\n"
            "🔑 *သင်၏ ပစ္စည်းအချက်အလက် (Auto-Delivered) -*\n"
            "```\n{delivered_data}\n```\n\n"
            "⚠️ _လုံခြုံရေးအတွက် သင်၏ အချက်အလက်များကို သိမ်းဆည်းထားပါ။ "
            "Member Status တွင်လည်း ပြန်လည်ကြည့်ရှုနိုင်ပါသည်။_"
        ),
        "en": (
            "🎉 *Purchase Successful!*\n\n"
            "📦 *Product -* {product_name}\n"
            "💰 *Price -* `{price:,} MMK`\n"
            "💳 *Remaining Balance -* `{balance:,} MMK`\n\n"
            "🔑 *Your Credentials (Auto-Delivered) -*\n"
            "```\n{delivered_data}\n```\n\n"
            "⚠️ _Please save your credentials safely. You can also review them in Member Status anytime._"
        ),
    },
    "insufficient_balance": {
        "my": "⚠️ *လက်ကျန်ငွေ မလုံလောက်ပါ!*\n\nကျေးဇူးပြု၍ ငွေဖြည့်သွင်းပြီးမှ ပြန်လည် ဝယ်ယူပေးပါ။",
        "en": "⚠️ *Insufficient Balance!*\n\nPlease top-up your balance before purchasing this item.",
    },
}


def t(key: str, lang: str = "my", **kwargs: Any) -> str:
    """
    Returns localized string for the given key and language.
    Falls back to Burmese ('my') if language or key is not found in English.
    """
    if lang not in ("my", "en"):
        lang = "my"

    entry = STRINGS.get(key)
    if not entry:
        return key

    template = entry.get(lang) or entry.get("my", key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template
