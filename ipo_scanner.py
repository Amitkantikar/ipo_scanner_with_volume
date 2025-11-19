import os
import requests
import pandas as pd
import yfinance as yf
from io import BytesIO
from datetime import datetime, timedelta
import warnings


warnings.filterwarnings("ignore", category=FutureWarning)

# --------------------------
# CONFIG
# --------------------------
MIN_LISTING_DAYS = 120      # number of days since listing
THRESHOLD = 0.03            # 3% near ATH
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")


# --------------------------
# Telegram
# --------------------------
def send_telegram(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram credentials missing.")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}

    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print("Telegram error:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram send failed:", e)


# --------------------------
# BULK DEAL CHECK
# --------------------------
def check_bulk_deal(symbol):
    """
    Returns True if symbol appears in today's NSE bulk deals.
    """
    try:
        url = "https://archives.nseindia.com/content/equities/bulk.csv"
        r = requests.get(url, timeout=30)
        df = pd.read_csv(BytesIO(r.content))
        df.columns = df.columns.str.strip()

        # Check if SYMBOL is found
        if "SYMBOL" in df.columns:
            return symbol in df["SYMBOL"].astype(str).str.upper().values
        return False
    except:
        return False


# --------------------------
# POSITIVE NEWS CHECK (Yahoo Finance)
# --------------------------
def check_positive_news(symbol):
    """
    Returns True if Yahoo Finance shows any recent positive headlines.
    Uses simple keyword filtering.
    """
    try:
        tk = yf.Ticker(symbol + ".NS")
        news_list = tk.news

        if not news_list:
            return False

        positive_keywords = [
            "surge", "jumps", "rallies", "strong", "record", "expands",
            "beats", "profit", "growth", "upgrade", "bullish", "wins", "approval"
        ]

        for item in news_list:
            title = item.get("title", "").lower()
            if any(word in title for word in positive_keywords):
                return True
        return False
    except:
        return False


# --------------------------
# Load NSE Equity CSV
# --------------------------
def fetch_equity_list():
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    r = requests.get(url, timeout=40)
    df = pd.read_csv(BytesIO(r.content))
    df.columns = df.columns.str.strip()
    return df


# --------------------------
# Filter IPOs listed within X days
# --------------------------
def get_recent_ipos(days: int):
    df = fetch_equity_list()

    df["DATE OF LISTING"] = pd.to_datetime(df["DATE OF LISTING"], errors="coerce")
    df = df.dropna(subset=["DATE OF LISTING"])

    cutoff = datetime.now() - timedelta(days=days)

    recent = df[df["DATE OF LISTING"] >= cutoff]
    recent = recent[recent["SERIES"] == "EQ"]

    return recent["SYMBOL"].tolist()


# --------------------------
# Fetch full history (single-ticker)
# --------------------------
def fetch_history(symbol):
    try:
        hist = yf.Ticker(symbol + ".NS").history(period="max")
        if hist.empty:
            return None
        return hist
    except:
        return None


# --------------------------
# Compute ATH
# --------------------------
def compute_ath(hist):
    if hist is None or hist.empty:
        return None

    if "High" not in hist.columns:
        return None

    try:
        ath = hist["High"].max()
        ath_idx = hist["High"].idxmax()
        ath_pos = hist.index.get_loc(ath_idx)
        total = len(hist)
        return ath, ath_idx, ath_pos, total
    except:
        return None


# --------------------------
# MAIN WORKFLOW
# --------------------------
if __name__ == "__main__":
    print("Fetching IPOs from NSE...")

    ipo_symbols = get_recent_ipos(MIN_LISTING_DAYS)
    print(f"Found {len(ipo_symbols)} IPOs:", ipo_symbols)

    for sym in ipo_symbols:
        print("\nChecking:", sym)

        hist = fetch_history(sym)
        if hist is None:
            print("No YF history:", sym)
            continue

        # ------------------------------------
        # 🔥 VOLUME FILTER — CURRENT VOLUME > AVG OF LAST 2 CANDLES
        # ------------------------------------
        if len(hist) < 3:
            print("Not enough candles for volume filter:", sym)
            continue

        v0 = hist["Volume"].iloc[-1]
        v1 = hist["Volume"].iloc[-2]
        v2 = hist["Volume"].iloc[-3]

        avg_last_2 = (v1 + v2) / 2

        if v0 <= avg_last_2:
            print(f"Skipping {sym} — No volume spike (vol {v0}, avg2 {avg_last_2:.0f})")
            continue

        # ------------------------------------

        ath_info = compute_ath(hist)
        if not ath_info:
            print("ATH unavailable:", sym)
            continue

        ath, ath_idx, ath_pos, total = ath_info

        # Must have minimum 3 candles since ATH (your rule)
        if ath_pos > total - 4:
            print("ATH too recent, skipping.")
            continue

        # Current CMP
        current = hist["Close"].iloc[-1]

        # ------------------------------------
        # ⭐ NEW FILTERS — BULK DEAL + POSITIVE NEWS
        # ------------------------------------
        has_bulk = check_bulk_deal(sym)
        has_news = check_positive_news(sym)

        bulk_msg = "Yes" if has_bulk else "No"
        news_msg = "Yes" if has_news else "No"

        # ------------------------------------

        # Threshold check
        if current >= ath * (1 - THRESHOLD):
            diff = round((ath - current) / ath * 100, 2)

            msg = (
                f"🚨 *IPO Near All-Time High!*\n"
                f"*Symbol:* {sym}\n"
                f"*Listing Date:* {hist.index[0].date()}\n"
                f"*ATH:* {ath:.2f}\n"
                f"*CMP:* {current:.2f}\n"
                f"*Distance from ATH:* {diff}%\n"
                f"*Volume Spike:* {v0} (avg2 = {int(avg_last_2)})\n"
                f"*Bulk Deal:* {bulk_msg}\n"
                f"*Positive News:* {news_msg}"
            )

            print("ALERT:", sym, diff)
            send_telegram(msg)

    print("\n✔ Scan Complete")
