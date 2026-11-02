"""Every customer-facing bot message that the admin (main bot) or a reseller (own bot) can reword from the panel.

A message is a template with {placeholders}. The built-in text below is used until it is edited; an edited text is stored in
/bot_texts/<tenant or 'main'>/<key>. Unknown {words} are left as they are, a broken edit can never crash the bot (the built-in text
is used instead), and HTML is checked before it is saved so Telegram can always display it.

Use:  texts.t("shop_title", line=LINE)           -> the text for the current bot
      texts.use(tenant)                          -> tell this thread which bot it is answering for (set once per update / order)
"""
import re
import threading

from core import db, utils
from core.cache import cache
from core.errors import ApiError

LINE = "━━━━━━━━━━━━━━━━━━"
_tl = threading.local()

# key -> (group, label, placeholders, default)
_R = {}


def _reg(group, key, label, placeholders, default):
    _R[key] = (group, label, placeholders, default)


# ------------------------------------------------------------------ Shop
G = "🛒 Shop & apps"
_reg(G, "shop_title", "Shop (categories list)", "{line}", "🛒 <b>Shop</b>\n{line}\n\nPick a category 👇")
_reg(G, "shop_empty", "Shop is empty", "", "📭 <b>Nothing to buy yet</b>\n\nNew apps are coming soon — check back later!")
_reg(G, "category_title", "Category screen (apps list)", "{icon} {name} {line}",
     "{icon} <b>{name}</b>\n{line}\n\n🔥 Featured and best sellers first — pick an app 👇")
_reg(G, "app_title", "App screen: first line", "{name}", "🔥 <b>{name}</b>")
_reg(G, "app_provider", "App / order: 'provided by' line", "{provider}", "🏪 <i>This service is provided by</i> <b>{provider}</b>")
_reg(G, "app_users", "App screen: people using it now", "{count}", "👥 <b>{count}</b> people are using it right now")
_reg(G, "app_best", "App screen: best value line", "{validity} {per_day}", "⭐ <b>Best value:</b> {validity} — only {per_day}/day")
_reg(G, "app_trial", "App screen: free trial line", "", "🎁 <b>Try it free</b> before you buy!")
_reg(G, "app_choose", "App screen: last line", "", "\n👇 <b>Choose your plan</b>")
_reg(G, "plan_gone", "Plan no longer available", "", "That plan is no longer available.")
G = "🏷 Plan buttons"
_reg(G, "btn_plan_time", "Button of a time plan (e.g. 30 days)", "{star} {label} {price} {inr} {usd} {save}", "{star}{label} — {price}{save}")
_reg(G, "btn_plan_limit", "Button of a limit plan (X credits at Y)", "{credits} {requests} {expiry} {label} {price} {inr} {usd} {star}",
     "{star}{credits} credits{expiry} — {price}")
_reg(G, "btn_plan_trial", "Button of a free trial", "{label}", "🎁 FREE TRIAL · {label}")
_reg(G, "btn_plan_short", "Button of a free short-link plan", "{label}", "🆓 {label} — FREE")

# ------------------------------------------------------------------ Order
G = "🧾 Order & payment"
_reg(G, "confirm_order", "Order summary (top part)", "{line} {app} {plan} {trial_tag} {body}",
     "🧾 <b>Confirm your order</b>\n{line}\n📱 App: <b>{app}</b>\n📦 Plan: <b>{plan}</b>{trial_tag}\n{body}")
_reg(G, "confirm_paid", "Order summary: paid plan", "{price} {provider_line}", "💰 Price: <b>{price}</b>\n{provider_line}\nDo you want to purchase it?")
_reg(G, "confirm_trial", "Order summary: free trial", "",
     "💰 Price: <b>FREE</b>\n\nYou'll instantly get your <b>license key</b>, the <b>APK</b> and the <b>setup video</b>.\n\nStart your free trial?")
_reg(G, "confirm_short", "Order summary: free via short link", "{pages} {s}",
     "💰 Price: <b>FREE 🆓</b>\n\nUnlock it by completing <b>{pages} page{s}</b> on our website. I'll message you after every page and send "
     "your <b>license key</b>, the <b>APK</b> and the <b>setup video</b> as soon as you finish.\n\nDo you want to continue?")
_reg(G, "methods", "Choose how to pay", "{line} {app} {plan} {price} {provider_line} {methods}",
     "💳 <b>Choose how to pay</b>\n{line}\n📱 {app} — {plan}\n💰 Price: <b>{price}</b>\n{provider_line}\n{methods}\n\nNot sure how? Tap <b>How to pay</b> 👇")
_reg(G, "method_upi", "Choose how to pay: UPI line", "", "🏦 <b>UPI</b> — verified by our team (usually 2-3 hours).")
_reg(G, "method_crypto", "Choose how to pay: Crypto line", "",
     "🪙 <b>Crypto (Monero · XMR)</b> — ✅ <b>auto-verified</b>, your key arrives in ~2 minutes after you pay.\n"
     "<i>Wallet, bonus and refer money can't be used with crypto.</i>")
_reg(G, "how_upi", "Guide: how to pay with UPI", "{line}",
     "🏦 <b>How to pay with UPI</b>\n{line}\n\n1️⃣ Choose <b>UPI</b> and confirm your plan.\n"
     "2️⃣ I send you a <b>QR code</b> and a <b>UPI ID</b> with the exact amount.\n"
     "3️⃣ Open any UPI app (GPay, PhonePe, Paytm, BHIM…) → scan the QR <i>or</i> pay to the UPI ID. Pay the <b>exact amount</b> within the time shown.\n"
     "4️⃣ Tap <b>✅ I have Paid</b>, send the <b>payment screenshot</b> and then the <b>12-digit UTR / reference number</b>.\n"
     "5️⃣ Our team checks it (usually 2-3 hours). Your <b>license key</b>, <b>APK</b> and <b>setup video</b> arrive here.\n\n"
     "⚠️ Never reuse an old screenshot or UTR — duplicates are rejected.")
_reg(G, "how_crypto", "Guide: how to pay with Crypto", "{line}",
     "🪙 <b>How to pay with Crypto (Monero · XMR)</b>\n{line}\n\n1️⃣ Choose <b>Crypto</b> and confirm your plan.\n"
     "2️⃣ I create a <b>personal Monero address</b> just for this order and show the <b>exact XMR amount</b>, a QR code and a <b>60-minute</b> timer.\n"
     "3️⃣ In your Monero wallet (Cake Wallet, Feather, Monerujo, MyMonero…) or an exchange withdrawal, <b>scan the QR</b> or paste the address and send <b>the exact amount</b>.\n"
     "4️⃣ That's it! After <b>1 confirmation</b> (about 2 minutes) the payment is detected <b>automatically</b> and your <b>license key</b>, "
     "<b>APK</b> and <b>setup video</b> are sent here. No screenshot needed.\n\n"
     "💡 <b>Tips</b>\n• Send it in <b>one transfer</b>, before the timer ends.\n"
     "• Exchanges add a withdrawal fee — choose the option where the <i>recipient gets</i> the full amount.\n"
     "• Each order has its own address: never pay an old address.\n"
     "• Wallet, bonus and refer money can't be used with crypto.")
_reg(G, "card_upi", "UPI payment card", "{line} {app} {plan} {wallet_lines} {provider_line} {amount} {upi} {txn} {left} {till}",
     "💳 <b>Payment Request</b>\n{line}\n📱 App: <b>{app}</b>\n📦 Plan: <b>{plan}</b>\n{wallet_lines}{provider_line}💵 Pay now: <b>{amount}</b>\n\n"
     "🏦 UPI ID: <code>{upi}</code>\n🧾 Txn ID: <code>{txn}</code>\n⏳ Time left: <b>{left}</b> (till {till})\n\n"
     "Scan the QR or pay exactly <b>{amount}</b> to the UPI ID, then tap <b>I have Paid</b>.")
_reg(G, "card_upi_deposit", "UPI wallet-deposit card", "{line} {amount} {upi} {txn} {left} {till}",
     "💳 <b>Wallet Deposit</b>\n{line}\n💵 Pay now: <b>{amount}</b>\n\n🏦 UPI ID: <code>{upi}</code>\n🧾 Txn ID: <code>{txn}</code>\n"
     "⏳ Time left: <b>{left}</b> (till {till})\n\nScan the QR or pay exactly <b>{amount}</b> to the UPI ID, then tap <b>I have Paid</b>.")
_reg(G, "card_crypto", "Crypto payment card", "{line} {app} {plan} {price} {inr} {provider_line} {xmr} {address} {txn} {left} {till} {confirmations}",
     "🪙 <b>Pay with Monero (XMR)</b>\n{line}\n📱 App: <b>{app}</b>\n📦 Plan: <b>{plan}</b>\n💵 Price: <b>{price}</b> (≈ {inr})\n{provider_line}\n"
     "🪙 Send exactly: <code>{xmr}</code> XMR\n📬 To this address:\n<code>{address}</code>\n\n🧾 Order: <code>{txn}</code>\n"
     "⏳ Time left: <b>{left}</b> (till {till})\n✅ <b>Auto-verified</b> after {confirmations} confirmation — no screenshot needed.")
_reg(G, "card_crypto_deposit", "Crypto wallet-deposit card", "{line} {amount} {rate} {xmr} {address} {txn} {left} {till} {confirmations}",
     "🪙 <b>Wallet deposit with Monero (XMR)</b>\n{line}\n👛 You will receive: <b>{amount}</b> in your wallet\n"
     "📌 Fixed rate: <b>1 XMR = {rate}</b> <i>(does not change with the market)</i>\n\n🪙 Send exactly: <code>{xmr}</code> XMR\n"
     "📬 To this address:\n<code>{address}</code>\n\n🧾 Order: <code>{txn}</code>\n⏳ Time left: <b>{left}</b> (till {till})\n"
     "✅ <b>Auto-credited</b> after {confirmations} confirmation — no screenshot needed.\n"
     "💡 If you send a little more or less, you get exactly what arrives × the fixed rate.")
_reg(G, "crypto_detected", "Crypto payment detected", "{line} {need} {s}",
     "👀 <b>Payment detected!</b>\n{line}\n\nYour transfer is on the network. Waiting for <b>{need}</b> confirmation{s} (about 2 minutes per block). "
     "Your key arrives here automatically — nothing else to do.")
_reg(G, "ask_screenshot", "Ask for the payment screenshot", "{line}",
     "📸 <b>Upload your payment screenshot</b>\n{line}\n\nSend the screenshot of the successful payment as a photo.\nSend /cancel to go back.")
_reg(G, "shot_not_photo", "Screenshot: not a photo", "", "Please send the payment <b>screenshot as a photo</b> (JPG / PNG) or /cancel.")
_reg(G, "shot_too_large", "Screenshot: too large", "", "That image is too large (max 8 MB).")
_reg(G, "shot_unreadable", "Screenshot: cannot read", "", "Couldn't read that image. Please try again.")
_reg(G, "shot_invalid", "Screenshot: not a real image", "",
     "🚫 <b>That is not a valid screenshot.</b>\nPlease send a real photo / screenshot of the payment (JPG or PNG). Renamed or edited files are not accepted.")
_reg(G, "shot_used", "Screenshot: already used", "", "🚫 This screenshot was already used for another payment. Please send the correct one.")
_reg(G, "shot_received", "Screenshot received, ask for UTR", "", "✅ Screenshot received.\n\nNow send the <b>12-digit UTR / reference number</b> of the payment.")
_reg(G, "utr_bad", "UTR: wrong format", "", "❌ The UTR must be exactly <b>12 digits</b>. Please send it again (or /cancel).")
_reg(G, "utr_used", "UTR: already used", "", "🚫 This UTR was already used for another payment. Check it and send the correct one.")
_reg(G, "proof_expired", "Payment request expired", "", "⌛ This payment request expired. Please start a new purchase.")
_reg(G, "proof_error", "Transaction problem", "", "Something went wrong with this transaction. Please start again.")
_reg(G, "proof_pending", "Proof submitted: waiting for verification", "{line} {txn} {utr}",
     "🕒 <b>Payment verification is pending</b>\n{line}\n\n🧾 Txn: <code>{txn}</code>\n🔢 UTR: <code>{utr}</code>\n\n"
     "Please wait <b>2-3 hours</b> for manual verification. You'll receive your key here.")
_reg(G, "in_verification", "Already one payment in verification", "{line} {txn}",
     "⏳ <b>Your payment is in verification</b>\n{line}\n\n🧾 Txn: <code>{txn}</code>\nPlease wait 2-3 hours. You'll get your key here once approved.")
_reg(G, "daily_limit", "Daily payment request limit reached", "{limit}",
     "🚫 You reached today's limit of <b>{limit}</b> payment requests. Try again tomorrow.")
_reg(G, "crypto_down", "Crypto temporarily unavailable", "",
     "⚠️ Crypto payments are temporarily unavailable. Please pay with UPI or try again in a few minutes.")
_reg(G, "payments_down", "Payments temporarily unavailable", "", "⚠️ Payments are temporarily unavailable. Please try again later.")
_reg(G, "min_upi", "Minimum UPI amount", "{min}",
     "ℹ️ UPI payments start at <b>{min}</b>. Your wallet balance just changed — please pick the plan again.")
_reg(G, "trial_used", "Free trial already used", "", "🎁 You've already used the free trial for this app.\nLoved it? Grab a full plan 👇")
_reg(G, "order_done", "Order done (wallet paid everything)", "", "✅ Done! Use the menu below to continue.")
_reg(G, "open_request", "Open payment request exists", "", "ℹ️ You already have an open payment request — here it is 👇")
_reg(G, "pay_cancelled", "Payment cancelled / expired", "{label} {refunded}", "⛔ <b>Payment {label}.</b>{refunded}")
_reg(G, "pay_failed", "Payment rejected by the admin", "{line} {txn} {reason} {refunded}",
     "❌ <b>Payment verification failed</b>\n{line}\n\n🧾 Txn: <code>{txn}</code>\n📝 Reason: {reason}{refunded}\n\nYou can try again from <b>Shop</b> in the menu.")

# ------------------------------------------------------------------ After purchase
G = "🔑 After purchase (key, video, APK)"
_reg(G, "license_head_paid", "Key message: title (paid)", "", "🎉 <b>Payment verified!</b>")
_reg(G, "license_head_wallet", "Key message: title (paid by wallet)", "", "✅ <b>Order complete!</b>")
_reg(G, "license_head_trial", "Key message: title (free trial)", "", "🎁 <b>Your free trial is ready!</b>")
_reg(G, "license_head_short", "Key message: title (free via short link)", "", "🎉 <b>All steps verified — your free key is ready!</b>")
_reg(G, "license_head_crypto", "Key message: title (paid in crypto)", "", "🪙 <b>Crypto payment confirmed!</b> ✅")
_reg(G, "license_done", "Key message (sent after every purchase)", "{head} {line} {app} {key} {plan} {validity_line} {wallet_line} {provider_line}",
     "{head}\n{line}\n📱 <b>{app}</b>\n🔑 Your license key:\n<code>{key}</code>\n\n📦 Plan: {plan}\n{validity_line}{wallet_line}\n"
     "Tap the key to copy it. Your APK and setup video are coming right below 👇")
_reg(G, "validity_time", "Key message: validity line (time plan)", "{expires}", "⏳ Valid until: {expires}")
_reg(G, "validity_limit", "Key message: validity line (limit plan)", "{credits} {requests} {expiry}",
     "🔢 Credits: <b>{credits}</b> (you can see the balance anytime in My Keys){expiry}")
_reg(G, "delivery_video", "Setup video caption", "", "🎬 <b>How to set up</b>\nWatch this first, then install the app.")
_reg(G, "delivery_apk", "APK caption (below the key)", "", "📲 Install the APK and paste your key.")
_reg(G, "delivery_key_time", "Key line sent with the APK / message (time plan)", "{app} {key} {plan} {expires}",
     "✅ <b>{app}</b>\n🔑 <code>{key}</code>\n⏳ {plan} · valid until {expires}")
_reg(G, "delivery_key_limit", "Key line sent with the APK / message (limit plan)", "{app} {key} {credits} {requests} {expiry}",
     "✅ <b>{app}</b>\n🔑 <code>{key}</code>\n🔢 {credits} credits{expiry}")
_reg(G, "deposit_done_upi", "Wallet deposit done (UPI)", "{line} {amount} {total}",
     "✅ <b>Deposit successful!</b>\n{line}\n💳 Added: <b>{amount}</b>\n👛 Wallet total: <b>{total}</b>\n\nUse it on your next purchase 🛒")
_reg(G, "deposit_done_crypto", "Wallet deposit done (crypto)", "{line} {xmr} {rate} {amount} {total}",
     "✅ <b>Crypto deposit successful!</b>\n{line}\n🪙 Received: <b>{xmr} XMR</b>\n📌 Fixed rate: 1 XMR = {rate}\n💳 Added: <b>{amount}</b>\n"
     "👛 Wallet total: <b>{total}</b>\n\nUse it on your next purchase 🛒")

# ------------------------------------------------------------------ Wallet
G = "💰 Wallet, deposit & withdraw"
_reg(G, "deposit_choose", "Deposit: choose UPI or Crypto", "{line} {rate}",
     "➕ <b>Deposit Money</b>\n{line}\n\nHow do you want to add money?\n\n🏦 <b>UPI</b> — pay by UPI, an admin verifies your screenshot.\n"
     "🪙 <b>Crypto (Monero)</b> — automatic. Credited at a <b>fixed rate: 1 XMR = {rate}</b>, whatever the market does.")
_reg(G, "deposit_ask", "Deposit: ask for the amount", "{line} {min} {extra}",
     "➕ <b>Deposit Money</b>\n{line}\n\nSend the amount you want to add (minimum <b>{min}</b>).{extra}\nSend /cancel to go back.")
_reg(G, "deposit_invalid", "Deposit: invalid amount", "", "Please send a valid amount, e.g. <code>100</code> (or /cancel).")
_reg(G, "deposit_min", "Deposit: below minimum", "{min}", "The minimum deposit is <b>{min}</b>. Send a higher amount.")
_reg(G, "deposit_big", "Deposit: too large", "", "That amount is too large. Please send a smaller amount.")
_reg(G, "deposit_crypto_down", "Deposit: crypto unavailable", "",
     "⚠️ Crypto deposits are temporarily unavailable (or the amount is below the crypto minimum). Try UPI or a different amount.")

# ------------------------------------------------------------------ Misc
G = "💬 Other"
_reg(G, "faq_empty", "FAQs: no questions", "{line}", "❓ <b>FAQs</b>\n{line}\n\nNo questions yet — check back soon!")
_reg(G, "faq_title", "FAQs: question list", "{line}", "❓ <b>FAQs</b>\n{line}\n\nTap a question to see the answer 👇")
_reg(G, "maintenance", "Store under maintenance", "{line} {reason}", "🛠 <b>Store under maintenance</b>\n{line}\n\n{reason}")
_reg(G, "menu_hint", "Menu hint after /start", "", "👇 Use the menu below anytime")
_reg(G, "use_menu", "Unknown message: use the menu", "", "Use the menu below 👇")
_reg(G, "menu_shown", "Menu keyboard switched on", "", "⌨️ <b>Menu is on</b> — the buttons are below your message box.\nTap <b>🙈 Hide menu</b> to close them any time.")
_reg(G, "menu_hidden", "Menu keyboard switched off", "", "🙈 <b>Menu hidden.</b> Tap <b>⌨️ Show menu</b> on the Home screen (or send /menu) to bring it back.")

# ------------------------------------------------------------------ Wallet screen, withdraw, history
G = "💰 Wallet, deposit & withdraw"
_reg(G, "wallet_screen", "Wallet screen", "{line} {deposit} {refer} {bonus_line} {total} {min_deposit} {crypto_line} {min_withdraw}",
     "💰 <b>My Wallet</b>\n{line}\n💳 Deposit Money: <b>{deposit}</b>\n🎁 Refer Money: <b>{refer}</b>{bonus_line}\n{line}\n"
     "✨ Usable on purchases: <b>{total}</b>\n\n➕ Minimum deposit: {min_deposit}{crypto_line}\n💸 Minimum withdrawal: {min_withdraw} <i>(Refer Money only)</i>")
_reg(G, "wallet_bonus_line", "Wallet screen: bonus line (only when the user has bonus)", "{bonus} {left}",
     "\n⚡ Bonus: <b>{bonus}</b> · ⏰ expires in {left}")
_reg(G, "wallet_crypto_line", "Wallet screen: crypto rate line (only when crypto deposits are on)", "{rate}",
     "\n🪙 Crypto deposit rate: <b>1 XMR = {rate}</b> <i>(fixed)</i>")
_reg(G, "wd_low", "Withdraw: balance below the minimum", "{line} {min} {refer}",
     "💸 <b>Withdraw</b>\n{line}\n\nYou can withdraw your <b>Refer Money</b> once it reaches <b>{min}</b>.\nYour Refer Money: <b>{refer}</b>\n\nInvite friends to earn more 🎁")
_reg(G, "wd_ask_upi", "Withdraw: ask for the UPI ID", "{line} {refer}",
     "💸 <b>Withdraw Refer Money</b>\n{line}\n\n💰 Available: <b>{refer}</b>\n\nSend your <b>UPI ID</b> (like <code>name@bank</code>).\nSend /cancel to go back.")
_reg(G, "wd_bad_upi", "Withdraw: invalid UPI ID", "", "❌ That doesn't look like a UPI ID. Send it like <code>name@bank</code> (or /cancel).")
_reg(G, "wd_ask_amount", "Withdraw: ask for the amount", "{upi} {min} {max}",
     "🏦 UPI: <code>{upi}</code>\n\nNow send the <b>amount</b> to withdraw (min {min}, max {max}).")
_reg(G, "wd_error", "Withdraw: amount problem", "{error}", "⚠️ {error}\nSend another amount (or /cancel).")
_reg(G, "wd_done", "Withdraw: request sent", "{line} {amount} {upi} {id}",
     "✅ <b>Withdrawal requested</b>\n{line}\n\n💰 Amount: <b>{amount}</b>\n🏦 UPI: <code>{upi}</code>\n🧾 ID: <code>{id}</code>\n\nWe'll notify you here as soon as it's approved.")
_reg(G, "history_deposit", "Deposit history: title", "", "🧾 <b>Deposit history</b>")
_reg(G, "history_withdraw", "Withdraw history: title", "", "📤 <b>Withdraw history</b>")
_reg(G, "history_screen", "History screen (the list is filled in)", "{title} {line} {body} {pager}", "{title}\n{line}\n\n{body}{pager}")
_reg(G, "history_empty", "History: nothing yet", "", "Nothing here yet.")
_reg(G, "history_page", "History: page counter", "{page} {pages}", "Page {page}/{pages}")

# ------------------------------------------------------------------ Daily check-in & spin
G = "📅 Check-in & spin"
_reg(G, "reward_off", "Check-in / spin switched off", "{label}", "😴 <b>{label}</b> is not available right now.")
_reg(G, "reward_claimed", "Already claimed today", "{line} {label} {wait}",
     "✅ <b>{label}</b>\n{line}\n\nYou already claimed today's reward.\n⏳ Come back in <b>{wait}</b> — it resets at <b>12:00 AM</b>.\n\n💡 Don't forget to use your bonus on a purchase!")
_reg(G, "checkin_screen", "Daily Check-in screen", "{line} {min} {max} {minutes}",
     "📅 <b>Daily Check-in</b>\n{line}\n\nCheck in every day and win a random bonus between <b>{min}</b> and <b>{max}</b>!\n"
     "⏳ Bonus is valid for <b>{minutes} minutes</b> — use it on any purchase.")
_reg(G, "spin_screen", "Spin the Wheel screen", "{line} {min} {max} {minutes}",
     "🎡 <b>Spin the Wheel</b>\n{line}\n\nOne free spin every day! Win between <b>{min}</b> and <b>{max}</b>.\n⏳ Bonus is valid for <b>{minutes} minutes</b>.")
_reg(G, "reward_won", "You won (shown under the check-in / spin result)", "{line} {amount} {minutes} {until}",
     "🎉 <b>You won {amount}!</b>\n{line}\n\n💰 Added to your wallet as <b>bonus</b>.\n⏳ Valid for <b>{minutes} minutes</b> (until {until}).\n\n🛒 Use it on any purchase before it expires!")
_reg(G, "checkin_anim_start", "Check-in animation: first frame", "{line}", "📅 <b>Checking you in…</b>\n{line}\n\n▱▱▱▱▱▱\n\n🎁")
_reg(G, "checkin_anim", "Check-in animation: every next frame", "{line} {bar} {frame}", "📅 <b>Opening your daily reward…</b>\n{line}\n\n{bar}\n\n{frame}")
_reg(G, "checkin_done", "Check-in finished", "{won}", "📅 <b>Daily Check-in done!</b>\n\n{won}")
_reg(G, "spin_anim_caption", "Spin animation caption", "", "🎡 <b>Spinning…</b> good luck! 🍀")
_reg(G, "spin_done", "Spin finished", "{won}", "🎡 <b>Spin complete!</b>\n\n{won}")
_reg(G, "toast_checkin", "Pop-up when the check-in starts", "", "🎉 Claimed!")
_reg(G, "toast_spin", "Pop-up when the spin starts", "", "🎡 Good luck!")
_reg(G, "bonus_expiring", "Warning: bonus is about to expire", "{line} {amount} {minutes}",
     "⏰ <b>Your bonus is about to expire!</b>\n{line}\n\n💰 <b>{amount}</b> expires in about <b>{minutes} min</b>.\nUse it now on a purchase before it's gone 👇")

# ------------------------------------------------------------------ Refer & earn
G = "🎁 Refer & earn"
_reg(G, "refer_off", "Refer & Earn switched off", "", "🎁 <b>Refer &amp; Earn</b> is not available right now.")
_reg(G, "refer_confirm_hint", "Refer & Earn intro: line under your intro message", "", "👇 <b>Tap Confirm to join Refer &amp; Earn</b> — your personal link appears right after.")
_reg(G, "toast_registered", "Pop-up: registered for Refer & Earn", "", "✅ You are registered!")
_reg(G, "refer_registered", "Refer & Earn: registered", "{line}",
     "✅ <b>You are registered!</b>\n{line}\n\n🎉 Welcome to <b>Refer &amp; Earn</b>.\n⏳ Creating your personal link…")
_reg(G, "refer_dashboard", "Refer & Earn dashboard", "{line} {link} {count} {converted} {earned} {balance} {percent}",
     "🎁 <b>Your Refer &amp; Earn</b>\n{line}\n\n🔗 <b>Your link</b>\n<code>{link}</code>\n\n👥 Total referrals: <b>{count}</b>  (✅ {converted} bought)\n"
     "💰 Total earned: <b>{earned}</b>\n👛 Refer Money balance: <b>{balance}</b>\n\n♾️ You earn <b>{percent}%</b> on <b>every</b> purchase your friends make — for life.")
_reg(G, "referrals_list", "My referrals list", "{line} {total}",
     "👥 <b>My referrals</b>\n{line}\n\nTotal: <b>{total}</b> · tap a name for details\n✅ bought a plan   ⏳ joined, not bought yet")
_reg(G, "referrals_empty", "My referrals: nobody yet", "{line}",
     "👥 <b>My referrals</b>\n{line}\n\nNobody joined with your link yet.\nShare it and start earning 🚀")
_reg(G, "referral_detail", "One referral's details", "{line} {name} {status} {earned} {purchases}",
     "🙋 <b>{name}</b>\n{line}\nStatus: {status}\n💰 Earned from this friend: <b>{earned}</b>\n\n{purchases}")
_reg(G, "referral_bought", "Referral status: bought", "", "✅ <b>Bought a plan</b>")
_reg(G, "referral_not_bought", "Referral status: not bought yet", "", "⏳ <b>Not bought yet</b>")
_reg(G, "referral_purchase", "Referral: one purchase line", "{app} {plan} {commission} {percent}",
     "📱 <b>{app}</b> — {plan}\n    💸 Commission: <b>{commission}</b> ({percent}%)")
_reg(G, "referral_no_purchases", "Referral: no purchases yet", "", "No purchases yet — you'll earn when they buy.")
_reg(G, "referred_by", "Welcome: who referred you", "{name}", "🤝 You were referred by <b>{name}</b>. Welcome aboard!")

# ------------------------------------------------------------------ My keys
G = "🔑 My keys"
_reg(G, "keys_empty", "My Keys: no license yet", "{line}", "🔑 <b>My License Keys</b>\n{line}\n\nYou don't have a license yet.")
_reg(G, "keys_screen", "My Keys screen (the key cards are filled in)", "{line} {count} {keys} {pager}",
     "🔑 <b>My License Keys</b> ({count})\n{line}\n\n{keys}\n\nTap a key to copy it.{pager}")
_reg(G, "keys_page", "My Keys: page counter", "{page} {pages}", "Page {page}/{pages}")
_reg(G, "key_card_time", "Key card: time plan", "{app} {validity} {key} {status} {until} {device}",
     "📱 <b>{app}</b> — {validity}\n<code>{key}</code>\n{status} · until {until}\n📲 Device: {device}")
_reg(G, "key_card_limit", "Key card: credit (pay-as-you-go) plan", "{app} {validity} {key} {status} {until_part} {left}",
     "📱 <b>{app}</b> — {validity}\n<code>{key}</code>\n{status}{until_part}\n{left}")
_reg(G, "key_left", "Key card: credits left", "{left} {total}", "🔢 Left: <b>{left}</b> of {total} credits")
_reg(G, "key_limit_done", "Key card: limit finished", "", "⚠️ <b>Limit finished</b> — buy more in Shop")
_reg(G, "key_status_active", "Key status: active", "", "🟢 Active")
_reg(G, "key_status_expired", "Key status: expired", "", "🔴 Expired")
_reg(G, "key_status_revoked", "Key status: revoked", "", "⛔ Revoked")
_reg(G, "key_device_linked", "Key card: device linked", "", "linked")
_reg(G, "key_device_free", "Key card: device not linked", "", "not linked yet")
_reg(G, "key_inactive", "Download: key no longer active (pop-up)", "", "This key is not active any more.")
_reg(G, "apk_unavailable", "Download: APK missing (pop-up)", "", "The APK is not available right now. Contact support.")
_reg(G, "toast_sending", "Download: sending (pop-up)", "", "📥 Sending…")

# ------------------------------------------------------------------ Support, reseller, seller, collection, join
G = "🎧 Support, seller & more"
_reg(G, "support_screen", "Support screen", "{line} {body}", "🎧 <b>Customer Support</b>\n{line}\n\n{body}")
_reg(G, "support_default", "Support: default text", "", "Need help? Our team is one tap away.")
_reg(G, "support_unset", "Support: not configured", "", "🎧 Support is not configured yet. Please try again later.")
_reg(G, "reseller_closed", "Reseller applications closed", "{line}",
     "🏪 <b>Reseller</b>\n{line}\n\nReseller applications are closed right now. Please check back later.")
_reg(G, "seller_is", "Seller: you already are one", "{line} {uid}",
     "💼 <b>You are a seller</b>\n{line}\n\n👤 Username: <code>{uid}</code>\n"
     "🔑 Your password was shown when you joined. Forgot it? Tap <b>Reset my password</b> — you get a new one instantly.")
_reg(G, "seller_closed", "Seller sign-ups closed", "{line}",
     "💼 <b>Become Seller</b>\n{line}\n\nSeller sign-ups are closed right now. Please check back later.")
_reg(G, "seller_intro", "Become a Seller: explanation", "{line}",
     "💼 <b>Become a Seller</b>\n{line}\n\nBuild your own app and let our shop sell it for you.\n\n"
     "🧩 Your own <b>license API</b> (endpoint + key)\n📤 Publish apps with plans, <b>free trials</b>, videos &amp; APK — just <b>send the files to this bot</b> and paste the id it gives you\n"
     "🧪 <b>Live APK testing</b> before review — device lock, revoke &amp; maintenance\n💰 Earn on <b>every sale</b> and withdraw by UPI\n\n"
     "👇 Tap below — your Seller Panel account is created instantly. Your <b>username is your Telegram ID</b> and a strong password is generated for you.")
_reg(G, "seller_welcome", "Seller: account created", "{line} {panel} {uid} {password}",
     "🎉 <b>Welcome, seller!</b>\n{line}{panel}\n👤 Username: <code>{uid}</code>\n🔑 Password: <code>{password}</code>\n\n"
     "⚠️ <b>Save this password now.</b> It is shown only once. Change it anytime in Seller Panel → Settings; lost it? Tap <b>Become Seller</b> again to get a new one.\n\n"
     "📎 <b>Sending files:</b> send your <b>.apk</b> (as a file) and your <b>.mp4 videos</b> (as a video) to this bot. It replies with "
     "an id that only you can use — paste it in <b>Publish App</b>.")
_reg(G, "seller_new_password", "Seller: new password", "{line} {panel} {uid} {password}",
     "🔑 <b>New password created</b>\n{line}{panel}\n👤 Username: <code>{uid}</code>\n🔑 Password: <code>{password}</code>\n\n"
     "⚠️ <b>Save this password now.</b> It is shown only once. Change it anytime in Seller Panel → Settings; lost it? Tap <b>Become Seller</b> again to get a new one.\n\n"
     "📎 <b>Sending files:</b> send your <b>.apk</b> (as a file) and your <b>.mp4 videos</b> (as a video) to this bot. It replies with "
     "an id that only you can use — paste it in <b>Publish App</b>.")
_reg(G, "toast_seller_ok", "Seller: account ready (pop-up)", "", "✅ Account ready!")
_reg(G, "toast_seller_reset", "Seller: new password (pop-up)", "", "🔑 New password created")
_reg(G, "seller_tg_connected", "Seller: Telegram connected", "{name}",
     "✅ <b>Telegram connected!</b>\nHi <b>{name}</b> — you'll now get app reviews, sales and withdrawal updates here.")
_reg(G, "seller_tg_expired", "Seller: connect link expired", "", "⚠️ This link expired. Open the seller panel and tap <b>Connect Telegram</b> again.")
_reg(G, "seller_file_hint", "Seller sent something that is not an APK / video", "",
     "📎 Send me your <b>.apk</b> as a file, or your preview / setup <b>video</b> (.mp4) as a video, and I will reply with its id.")
_reg(G, "file_id_reply", "Reply with a file id (admin / seller sends a file)", "{line} {label} {fid} {hint_line} {info} {stored}",
     "📎 <b>{label} id</b>\n{line}\n\n<code>{fid}</code>\n\n👆 Tap to copy. Only you can see and use this id.{hint_line}{info}{stored}")
_reg(G, "collection_none", "Payment collection: no UPI linked", "", "💵 No UPI ID is linked to this chat.")
_reg(G, "collection_screen", "My Payment Collection screen", "{line} {today} {today_n} {yesterday} {yesterday_n} {all} {all_n} {pending} {failed} {success}",
     "💵 <b>My Payment Collection</b>\n{line}\n\n📅 Today: <b>{today}</b> ({today_n})\n🕘 Yesterday: <b>{yesterday}</b> ({yesterday_n})\n"
     "🏆 All time: <b>{all}</b> ({all_n})\n\n⏳ Pending: <b>{pending}</b>   ❌ Failed: <b>{failed}</b>   ✅ Success: <b>{success}</b>")
_reg(G, "collection_list", "Payment collection: list of payments", "{label} {count} {line} {body}", "{label} <b>payments</b> ({count})\n{line}\n\n{body}")
_reg(G, "collection_empty", "Payment collection: empty list", "", "Nothing here.")
_reg(G, "join_gate", "Join the channels first", "{line} {which}",
     "🔒 <b>Join to unlock the store</b>\n{line}\n\nPlease join {which} below, then tap <b>Verify</b> 👇")
_reg(G, "join_which_all", "Join: wording for several channels", "", "all channels")
_reg(G, "join_which_one", "Join: wording for one channel", "", "our channel")
_reg(G, "toast_not_joined", "Verify: not joined yet (pop-up)", "{channels}", "You haven't joined: {channels}")
_reg(G, "toast_verified", "Verify: joined (pop-up)", "", "Verified ✅")
_reg(G, "wallet_options", "Pay with wallet money: choose how to pay", "{line} {app} {plan} {price} {wallet} {use} {upi_part} {note}",
     "💳 <b>Choose how to pay</b>\n{line}\n📱 {app} — {plan}\n💰 Price: <b>{price}</b>\n\n👛 Your wallet:\n{wallet}\n\n💳 With the wallet: <b>-{use}</b>{upi_part}{note}")
_reg(G, "wallet_upi_part", "Wallet payment: the rest is paid by UPI", "{cash}", " + <b>{cash}</b> by UPI")
_reg(G, "wallet_no_upi", "Wallet payment: nothing to pay by UPI", "", " — nothing to pay by UPI")
_reg(G, "wallet_split_note", "Wallet payment: UPI minimum note", "{cash} {use}",
     "\nℹ️ UPI payments start at <b>{cash}</b>, so only {use} is taken from the wallet.")
_reg(G, "slow_down", "Too many messages", "{wait}", "🚫 <b>Slow down!</b> Too many messages. Try again in {wait}s.")
_reg(G, "toast_slow", "Too many taps (pop-up)", "{wait}", "Slow down ✋ ({wait}s)")
_reg(G, "cancelled", "/cancel", "", "Cancelled. Use the menu below to continue.")
_reg(G, "utr_text", "UTR must be text", "", "Please send the 12-digit UTR as text (or /cancel).")
_reg(G, "toast_not_allowed", "Not allowed (pop-up)", "", "Not allowed.")

# ------------------------------------------------------------------ Admin-only (admin chats of the main bot)
G = "🛠 Admin chat commands"
_reg(G, "admin_help", "/help (admin)", "{line}",
     "🛠 <b>Admin commands</b>\n{line}\n\n/help — show this list\n\n"
     "/send <code>chat_id message</code> — send a message to one user\n   e.g. <code>/send 123456789 Your key is ready!</code>\n\n"
     "/broadcast — send a message to <b>all users</b>. Send the command, then send your message (text, or a photo / "
     "video / file with a caption). You'll see a <b>preview</b>, how many users will receive it, and <b>Confirm</b> / <b>Cancel</b> buttons.\n\n"
     "/pending — total pending payments grouped by UPI id\n\n"
     "/block <code>chat_id</code> — block a user. I ask for the <b>reason</b>; a blocked user gets <b>no reply at all</b>\n"
     "/unblock <code>chat_id</code> — let them back in\n/blocked — list blocked users\n\n"
     "/adminlogin — a private sign-in link for the admin panel (valid 30 minutes). Also in the ≡ menu.\n\n/cancel — stop what you are doing\n\n"
     "📎 <b>File IDs:</b> send me any photo, video, APK or file and I reply with its <b>file id</b> — paste it in the "
     "panel (Apps → Video ID / Setup video ID / App (APK) ID, or Seller → Submissions).\n\n"
     "👤 <b>User commands:</b> /start /buy /wallet /refer /checkin /spin /keys /support")
_reg(G, "admin_link_done", "Admin: Telegram connected from the panel", "{role} {name}",
     "✅ <b>Telegram connected</b>\n\nThis chat is now the <b>{role}</b> chat for panel account <b>{name}</b>. You'll get new-user alerts, payment alerts with "
     "✅ Verify / ❌ Fail buttons and the admin commands (/help) here.")
_reg(G, "admin_link_expired", "Admin: connect link expired", "", "⚠️ This link expired or was already used. Open the panel → Settings → Bot and tap <b>Connect my Telegram</b> again.")
_reg(G, "admin_login_open", "/adminlogin: panel has no login gate", "", "The admin panel sign-in page is open (ADMIN_GATE is off on this server).")
_reg(G, "admin_login_link", "/adminlogin: link message", "{line} {minutes}",
     "🔐 <b>Admin login</b>\n{line}\n\nTap the button to open the sign-in page. The link works for <b>{minutes} minutes</b> and is only for you — don't forward it.")
_reg(G, "admin_send_usage", "/send: usage", "", "Usage: <code>/send chat_id message</code>\nExample: <code>/send 123456789 Hello!</code>")
_reg(G, "admin_send_ok", "/send: sent", "{chat}", "✅ Sent to <code>{chat}</code>.")
_reg(G, "admin_send_fail", "/send: failed", "{error}", "❌ Could not send: {error}")
_reg(G, "admin_pending_none", "/pending: nothing pending", "", "✅ <b>No pending payments.</b>")
_reg(G, "admin_pending", "/pending: summary (the UPI rows are filled in)", "{line} {count} {amount} {rows}",
     "⏳ <b>Pending payments</b>\n{line}\nTotal: <b>{count}</b> payments · <b>{amount}</b>\n\n{rows}")
_reg(G, "admin_block_usage", "/block: usage", "", "Usage: <code>/block chat_id</code>\nExample: <code>/block 123456789</code>")
_reg(G, "admin_block_admin", "/block: target is an admin", "", "⚠️ You cannot block an admin.")
_reg(G, "admin_block_already", "/block: already blocked", "{chat}", "ℹ️ <code>{chat}</code> is already blocked.")
_reg(G, "admin_block_ask", "/block: ask for the reason", "{line} {chat} {name_line}",
     "🚫 <b>Block user</b>\n{line}\n\n🆔 <code>{chat}</code>{name_line}\n\n✍️ Send the <b>reason</b> now (or /cancel).")
_reg(G, "admin_block_done", "/block: user blocked", "{chat} {reason}",
     "🚫 <code>{chat}</code> is now <b>blocked</b>. They will get no replies.\nReason: {reason}")
_reg(G, "admin_unblock_usage", "/unblock: usage", "", "Usage: <code>/unblock chat_id</code>")
_reg(G, "admin_unblock_ok", "/unblock: done", "{chat}", "✅ <code>{chat}</code> is unblocked.")
_reg(G, "admin_blocked_none", "/blocked: nobody", "", "✅ Nobody is blocked.")
_reg(G, "admin_blocked_list", "/blocked: list", "{line} {count} {rows}", "🚫 <b>Blocked users</b> ({count})\n{line}\n\n{rows}")
_reg(G, "admin_bc_prompt", "/broadcast: ask for the message", "{line}",
     "📣 <b>New broadcast</b>\n{line}\n\nSend the message now — text, or a photo / video / file with a caption. Formatting (bold, italic, links) is kept.\n\nSend /cancel to abort.")
_reg(G, "admin_bc_error", "/broadcast: message problem", "{error}", "⚠️ {error}\nSend another message or /cancel.")
_reg(G, "admin_bc_rejected", "/broadcast: Telegram rejected the preview", "{error}", "⚠️ Telegram rejected this message: {error}\nFix it and send again, or /cancel.")
_reg(G, "admin_bc_preview", "/broadcast: preview question", "{line} {n} {s}",
     "👆 <b>Preview</b>\n{line}\n\n👥 This will be sent to <b>{n}</b> user{s}.\n\nSend it now?")
_reg(G, "admin_bc_stale", "/broadcast: no longer pending (pop-up)", "", "This broadcast is no longer pending.")
_reg(G, "admin_bc_cancelled", "/broadcast: cancelled", "", "❌ Broadcast cancelled.")
_reg(G, "admin_bc_started", "/broadcast: started", "{n} {id}",
     "🚀 <b>Broadcast started</b> to <b>{n}</b> users.\nTrack it in the panel → Broadcast (<code>{id}</code>).")
_reg(G, "admin_fail_ask", "Failing a payment: ask for the reason", "{txn}", "✍️ Enter the <b>reason</b> for failing txn <code>{txn}</code>:")
_reg(G, "admin_fail_done", "Failing a payment: done", "{txn}", "❌ Txn <code>{txn}</code> marked as failed. The user was notified.")
_reg(G, "admin_verified", "Verifying a payment (pop-up)", "", "Verified ✅ — sent to the user.")
_reg(G, "admin_nothing", "Nothing changed", "", "Okay, nothing changed.")
_reg(G, "admin_not_allowed", "You are not allowed (pop-up)", "", "You are not allowed to do this.")

ADMIN_GROUPS = {"🛠 Admin chat commands"}

KEYS = list(_R)
_ALL = list(dict.fromkeys(v[0] for v in _R.values()))
GROUPS = [g for g in _ALL if g != "💬 Other" and g not in ADMIN_GROUPS] + ["💬 Other"] + [g for g in _ALL if g in ADMIN_GROUPS]
_VAR = re.compile(r"\{(\w+)\}")


# ------------------------------------------------------------------ runtime
def use(tenant=""):
    """Remember which bot this thread is answering for (called once per update / order)."""
    _tl.tenant = tenant or ""


def current():
    return getattr(_tl, "tenant", "")


def default(key):
    return _R[key][3]


def _overrides(tenant):
    ck = ("bot_texts", tenant or "main")
    hit = cache.get(ck)
    if hit is not None:
        return hit[0]
    raw = db.get(f"/bot_texts/{tenant or 'main'}") or {}
    out = {k: v.get("text") for k, v in raw.items() if isinstance(v, dict) and v.get("text")}
    cache.set(ck, (out,), 8)
    return out


def template(key, tenant=None):
    tenant = current() if tenant is None else tenant
    return _overrides(tenant).get(key) or _R[key][3]


def fill(tpl, /, **vals):
    return _VAR.sub(lambda mo: str(vals[mo.group(1)]) if mo.group(1) in vals else mo.group(0), tpl)


def t(key, /, **vals):
    """The text for the current bot with {placeholders} filled. Never raises: falls back to the built-in text."""
    vals.setdefault("line", LINE)
    try:
        return fill(template(key), **vals)
    except Exception:  # noqa: BLE001
        return fill(_R[key][3], **vals)


# ------------------------------------------------------------------ editing (panel)
_OK_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a", "tg-spoiler", "blockquote", "tg-emoji", "span"}


def check_html(text):
    """Telegram-safe HTML: only its tags, all closed, no stray '<'. Returns None when fine, else a message."""
    stack = []
    pos = 0
    for mo in re.finditer(r"<(/?)([A-Za-z][\w\-]*)([^<>]*)>", text):
        if "<" in text[pos:mo.start()]:
            return "Use &lt; instead of a bare '<' character."
        pos = mo.end()
        closing, name = mo.group(1) == "/", mo.group(2).lower()
        if name not in _OK_TAGS:
            return f"<{name}> is not allowed. Use b, i, u, s, code, pre, a, blockquote or tg-spoiler."
        if closing:
            if not stack or stack[-1] != name:
                return f"</{name}> does not match an opening tag."
            stack.pop()
        else:
            stack.append(name)
    if "<" in text[pos:]:
        return "Use &lt; instead of a bare '<' character."
    if stack:
        return f"<{stack[-1]}> is not closed."
    return None


def _allowed(tenant, key):
    """Admin-chat commands exist only in the main bot, so a reseller never sees (or edits) them."""
    return not (tenant and _R[key][0] in ADMIN_GROUPS)


def listing(tenant=""):
    ov = _overrides(tenant)
    items = [{"key": k, "group": g, "label": lb, "placeholders": ph, "default": d, "text": ov.get(k, ""), "custom": k in ov}
             for k, (g, lb, ph, d) in _R.items() if _allowed(tenant, k)]
    return {"groups": [g for g in GROUPS if any(i["group"] == g for i in items)], "items": items}


def save(tenant, key, text, admin=""):
    if key not in _R or not _allowed(tenant, key):
        raise ApiError("Unknown message.", 404)
    text = utils.clean_text(text, "Message", 3500, True)
    bad = check_html(text)
    if bad:
        raise ApiError(f"HTML problem: {bad}")
    db.set(f"/bot_texts/{tenant or 'main'}/{key}", {"text": text, "ts": utils.now(), "by": admin})
    cache.clear()


def reset(tenant, key=None):
    if key and (key not in _R or not _allowed(tenant, key)):
        raise ApiError("Unknown message.", 404)
    db.set(f"/bot_texts/{tenant or 'main'}" + (f"/{key}" if key else ""), None)
    cache.clear()
