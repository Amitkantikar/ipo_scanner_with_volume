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
THRESHOLD = 0.03           # 3% near ATH
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
    try:
        url = "https://archives.nseindia.com/content/equities/bulk.csv"
        r = requests.get(url, timeout=30)
        df = pd.read_csv(BytesIO(r.content))
        df.columns = df.columns.str.strip()
        return symbol.upper() in df["SYMBOL"].astype(str).str.upper().values
    except:
        return False


# --------------------------
# POSITIVE NEWS CHECK
# --------------------------
def check_positive_news(symbol):
    try:
        tk = yf.Ticker(symbol + ".NS")
        news_list = tk.news
        if not news_list:
            return False

        pos_words = [
            "surge", "jumps", "rallies", "strong", "record",
            "expands", "beats", "profit", "growth", "upgrade",
            "bullish", "wins", "approval"
        ]

        for item in news_list:
            title = item.get("title", "").lower()
            if any(w in title for w in pos_words):
                return True

        return False
    except:
        return False


# --------------------------
# INSIDER BUYING CHECK
# --------------------------
def check_insider_buying(symbol):
    try:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        url = "https://api.bseindia.com/BseIndiaAPI/api/InsiderTrading/w"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Referer": "https://www.bseindia.com",
        }
        payload = {
            "pageno": 1,
            "strPrevDate": start,
            "strToDate": end,
            "strScrip": symbol
        }

        r = requests.post(url, json=payload, headers=headers, timeout=15)

        if not r.text.strip() or r.text.strip().startswith("<"):
            return False

        try:
            data = r.json()
        except:
            return False

        if "Table" not in data:
            return False

        for row in data["Table"]:
            mode = str(row.get("Mode", "")).lower()
            if any(x in mode for x in ["acquisition", "buy", "purchase"]):
                return True

        return False

    except Exception as e:
        print("Insider check failed:", e)
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
# SAFE DATE PARSER
# --------------------------
def safe_parse_date(x):
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(str(x), fmt)
        except:
            continue
    return pd.NaT


# --------------------------
# Filter IPOs listed within X days
# --------------------------
def get_recent_ipos(days: int):
    df = fetch_equity_list()

    # parse dates safely
    df["DATE OF LISTING"] = df["DATE OF LISTING"].apply(safe_parse_date)

    # remove rows with invalid date
    df = df.dropna(subset=["DATE OF LISTING"])

    # ensure we keep only the EARLIEST listing date per stock
    df = df.sort_values("DATE OF LISTING")
    df = df.groupby("SYMBOL", as_index=False).first()

    cutoff = datetime.now() - timedelta(days=days)

    # FILTER: only stocks truly listed in last X days
    recent = df[df["DATE OF LISTING"] >= cutoff]

    # only EQ series
    recent = recent[recent["SERIES"] == "EQ"]

    return recent["SYMBOL"].tolist()


# --------------------------
# Fetch full history
# --------------------------
def fetch_history(symbol):
    try:
        hist = yf.Ticker(symbol + ".NS").history(period="max")
    except:
        return None
    return hist if not hist.empty else None


# --------------------------
# Compute ATH
# --------------------------
def compute_ath(hist):
    try:
        ath = hist["High"].max()
        ath_idx = hist["High"].idxmax()
        ath_pos = hist.index.get_loc(ath_idx)
        return ath, ath_idx, ath_pos, len(hist)
    except:
        return None


# ======================================================================
# MAIN WORKFLOW
# ======================================================================
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

        # ATH
        ath_info = compute_ath(hist)
        if not ath_info:
            print("ATH unavailable:", sym)
            continue

        ath, ath_idx, ath_pos, total = ath_info

        # must have 3 candles since ATH
        if ath_pos > total - 4:
            print("ATH too recent, skipping.")
            continue

        current = hist["Close"].iloc[-1]

        # SIGNALS
        has_bulk = check_bulk_deal(sym)
        has_news = check_positive_news(sym)
        has_insider = check_insider_buying(sym)

        bulk_msg = "Yes" if has_bulk else "No"
        news_msg = "Yes" if has_news else "No"
        insider_msg = "Yes" if has_insider else "No"

        # MAIN CONDITION
        if current >= ath * (1 - THRESHOLD):
            diff = round((ath - current) / ath * 100, 2)

            msg = (
                f"🚨 *IPO Near All-Time High!*\n"
                f"*Symbol:* {sym}\n"
                f"*ATH:* {ath:.2f}\n"
                f"*CMP:* {current:.2f}\n"
                f"*Distance:* {diff}%\n"
                f"*Bulk Deal:* {bulk_msg}\n"
                f"*Positive News:* {news_msg}\n"
                f"*Insider Buying:* {insider_msg}"
            )

            print("ALERT:", sym, diff)
            send_telegram(msg)

    print("\n✔ Scan Complete")
