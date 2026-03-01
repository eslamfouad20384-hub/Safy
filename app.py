import streamlit as st
import requests
import pandas as pd
import numpy as np
import time
import sqlite3
from sklearn.ensemble import RandomForestClassifier
from datetime import datetime, timedelta

st.set_page_config(layout="wide")

# ==============================
# إعدادات عامة
# ==============================
MIN_MARKET_CAP = 50_000_000
MIN_VOLUME = 5_000_000
TOP_LIMIT = 300
FIB_PERIOD = 50
MIN_SCORE = 60
CACHE_DB = "cache/ohlc_cache.db"

# ==============================
# إعداد قاعدة بيانات SQLite
# ==============================
conn = sqlite3.connect(CACHE_DB, check_same_thread=False)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS ohlc_cache (
    symbol TEXT,
    timestamp TEXT,
    data TEXT,
    PRIMARY KEY(symbol, timestamp)
)
""")
conn.commit()

# ==============================
# أدوات مساعدة
# ==============================
def fetch_market_list():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "page": 1,
            "sparkline": False
        }
        data = requests.get(url, params=params, timeout=10).json()
        df = pd.DataFrame(data)
        # تأكد من الأعمدة
        if "market_cap" not in df.columns:
            df["market_cap"] = df.get("market_cap_usd", 0)
        if "total_volume" not in df.columns:
            df["total_volume"] = df.get("total_volume_usd", 0)
        return df
    except:
        return pd.DataFrame()

def fetch_ohlc(symbol):
    """جلب البيانات من الكاش أو المصادر"""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    # تحقق من الكاش أولاً
    c.execute("SELECT data FROM ohlc_cache WHERE symbol=? AND timestamp=?", (symbol, ts))
    row = c.fetchone()
    if row:
        df = pd.read_json(row[0])
        return df

    # جرب CoinGecko
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{symbol}/ohlc"
        params = {"vs_currency":"usd","days":7}
        r = requests.get(url, params=params, timeout=10).json()
        df = pd.DataFrame(r, columns=["timestamp","open","high","low","close"])
        df["volumeto"] = df["close"]*1000  # تقريب حجم التداول
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        # حفظ الكاش
        c.execute("INSERT OR REPLACE INTO ohlc_cache (symbol, timestamp, data) VALUES (?, ?, ?)",
                  (symbol, ts, df.to_json()))
        conn.commit()
        return df
    except:
        pass

    # جرب CryptoCompare
    try:
        url = f"https://min-api.cryptocompare.com/data/v2/histohour"
        params = {"fsym": symbol.upper(), "tsym": "USDT", "limit": 200}
        r = requests.get(url, params=params, timeout=10).json()
        df = pd.DataFrame(r["Data"]["Data"])
        return df
    except:
        return None

def add_indicators(df):
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))
    df["macd"] = df["close"].ewm(span=12).mean() - df["close"].ewm(span=26).mean()
    df["signal"] = df["macd"].ewm(span=9).mean()
    return df

# ==============================
# دالة الدعم والأهداف الديناميكية
# ==============================
def find_targets(df):
    latest_price = df.iloc[-1]["close"]
    df["swing_low"] = df["low"][
        (df["low"].shift(1) > df["low"]) & (df["low"].shift(-1) > df["low"])
    ]
    swing_lows = df["swing_low"].dropna()
    valid_supports = swing_lows[swing_lows < latest_price].sort_values(ascending=False)
    if len(valid_supports) >= 2:
        support1 = valid_supports.iloc[0]
        support2 = valid_supports.iloc[1]
    elif len(valid_supports) == 1:
        support1 = valid_supports.iloc[0]
        support2 = support1 * 0.97
    else:
        support1 = df["low"].rolling(FIB_PERIOD).min().iloc[-1]
        support2 = support1 * 0.97

    # Pivot Points + فيبوناتشي
    period_high = df["high"].rolling(FIB_PERIOD).max().iloc[-1]
    period_low = df["low"].rolling(FIB_PERIOD).min().iloc[-1]
    target1 = period_low + (period_high - period_low) * 0.382
    target2 = period_low + (period_high - period_low) * 0.618
    target3 = period_low + (period_high - period_low) * 1.0
    return target1, target2, target3, support1, support2

# ==============================
# ML لتقييم الفرص
# ==============================
def ml_score(df):
    X = df[["rsi","macd","signal","ema50","ema200","volumeto"]].fillna(0)
    y = (df["close"].shift(-1) > df["close"]).astype(int)  # حركة صعودية
    model = RandomForestClassifier(n_estimators=50)
    model.fit(X[:-1], y[:-1])
    latest = X.iloc[-1].values.reshape(1,-1)
    pred = model.predict_proba(latest)[0][1]  # احتمال الصعود
    return pred * 100

# ==============================
# واجهة Streamlit
# ==============================
st.title("AI Spot Market Scanner with ML & Dynamic Support")

smart_mode = st.checkbox("Smart Capital Mode")

if st.button("🔍 Scan Market"):
    st.info("جاري تحميل السوق...")
    market_df = fetch_market_list()
    market_df = market_df[
        (market_df["market_cap"] > MIN_MARKET_CAP) &
        (market_df["total_volume"] > MIN_VOLUME)
    ]
    market_df = market_df.head(TOP_LIMIT)

    results = []
    progress = st.progress(0)
    status_text = st.empty()
    total = len(market_df)

    for idx, row in enumerate(market_df.itertuples(), start=1):
        symbol = row.symbol.upper()
        ohlc = fetch_ohlc(symbol)
        if ohlc is None or len(ohlc) < 50:
            continue
        ohlc = add_indicators(ohlc)
        score = ml_score(ohlc)
        if score >= MIN_SCORE:
            target1, target2, target3, support1, support2 = find_targets(ohlc)
            results.append({
                "symbol": symbol,
                "price": ohlc.iloc[-1]["close"],
                "score": score,
                "target1": target1,
                "target2": target2,
                "target3": target3,
                "support1": support1,
                "support2": support2
            })
        progress.progress(idx/total)
        status_text.text(f"جارٍ تحميل العملة {idx} من {total} - {round(idx/total*100,1)}%")

    if not results:
        st.warning("لا توجد فرص حالياً")
    else:
        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values("score", ascending=False).head(10)
        st.success("أفضل 10 فرص حالياً")
        st.dataframe(results_df)

        selected = st.selectbox("اختر عملة للتحليل المفصل", results_df["symbol"])
        if selected:
            ohlc = fetch_ohlc(selected)
            ohlc = add_indicators(ohlc)
            st.subheader(f"تحليل {selected}")
            st.line_chart(ohlc[["close", "ema50", "ema200"]])
            latest = ohlc.iloc[-1]
            st.write("💰 سعر الدخول الحالي:", round(latest["close"],4))
            st.write("🟢 شراء إضافي 1:", round(results_df[results_df["symbol"]==selected]["support1"].values[0],4))
            st.write("🟢 شراء إضافي 2:", round(results_df[results_df["symbol"]==selected]["support2"].values[0],4))
            st.write("🎯 الهدف الأول:", round(results_df[results_df["symbol"]==selected]["target1"].values[0],4))
            st.write("🎯 الهدف الثاني:", round(results_df[results_df["symbol"]==selected]["target2"].values[0],4))
            st.write("🎯 الهدف الثالث:", round(results_df[results_df["symbol"]==selected]["target3"].values[0],4))
            st.write("RSI:", round(latest["rsi"],2))
            st.write("MACD:", round(latest["macd"],4))
            st.write("Signal:", round(latest["signal"],4))
