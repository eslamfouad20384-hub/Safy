import streamlit as st
import requests
import pandas as pd
import numpy as np
import time

st.set_page_config(layout="wide")

# ==============================
# إعدادات عامة
# ==============================

MIN_MARKET_CAP = 50_000_000
MIN_VOLUME = 5_000_000
TOP_LIMIT = 300

# ==============================
# أدوات مساعدة
# ==============================

def fetch_market_list():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 250,
        "page": 1,
        "sparkline": False
    }
    data = requests.get(url, params=params).json()
    df = pd.DataFrame(data)
    return df

def fetch_ohlc(symbol):
    try:
        url = f"https://min-api.cryptocompare.com/data/v2/histohour"
        params = {
            "fsym": symbol.upper(),
            "tsym": "USDT",
            "limit": 200
        }
        r = requests.get(url, params=params).json()
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

def calculate_score(df, smart_mode=False):
    latest = df.iloc[-1]
    score = 0

    # دعم تقريبي
    support = df["close"].rolling(50).min().iloc[-1]
    if abs(latest["close"] - support) / latest["close"] < 0.05:
        score += 20

    # RSI
    if latest["rsi"] < 35:
        score += 15

    # MACD
    if latest["macd"] > latest["signal"]:
        score += 15

    # EMA Trend
    if latest["ema50"] > latest["ema200"]:
        score += 15

    # حجم تداول
    avg_vol = df["volumeto"].rolling(20).mean().iloc[-1]
    if latest["volumeto"] > avg_vol:
        score += 10

    if smart_mode:
        score += 10  # زيادة وزن السيولة

    return score

# ==============================
# دالة الدعم والأهداف (فيبوناتشي)
# ==============================

def find_targets(df):
    latest_price = df.iloc[-1]["close"]

    # دعم فعلي من Swing Lows
    df["swing_low"] = df["low"][
        (df["low"].shift(1) > df["low"]) &
        (df["low"].shift(-1) > df["low"])
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
        support1 = df["low"].rolling(50).min().iloc[-1]
        support2 = support1 * 0.97

    # القمة والقاع لأهداف فيبوناتشي
    period_high = df["high"].rolling(50).max().iloc[-1]
    period_low = df["low"].rolling(50).min().iloc[-1]

    target1 = period_low + (period_high - period_low) * 0.382
    target2 = period_low + (period_high - period_low) * 0.618

    return target1, target2, support1, support2

# ==============================
# الواجهة
# ==============================

st.title("AI Spot Market Scanner")

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
    total = len(market_df)

    for i, row in market_df.iterrows():

        symbol = row["symbol"].upper()

        ohlc = fetch_ohlc(symbol)
        if ohlc is None or len(ohlc) < 100:
            continue

        ohlc = add_indicators(ohlc)

        score = calculate_score(ohlc, smart_mode)

        if score >= 65:

            target1, target2, support1, support2 = find_targets(ohlc)

            results.append({
                "symbol": symbol,
                "price": ohlc.iloc[-1]["close"],
                "score": score,
                "target1": target1,
                "target2": target2,
                "support1": support1,
                "support2": support2
            })

        progress.progress((len(results)+1)/total)

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

            st.write("💰 سعر الدخول الحالي:", round(latest["close"], 4))
            st.write("🟢 شراء إضافي 1:", round(results_df[results_df["symbol"]==selected]["support1"].values[0],4))
            st.write("🟢 شراء إضافي 2:", round(results_df[results_df["symbol"]==selected]["support2"].values[0],4))
            st.write("🎯 الهدف الأول:", round(results_df[results_df["symbol"]==selected]["target1"].values[0],4))
            st.write("🎯 الهدف الثاني:", round(results_df[results_df["symbol"]==selected]["target2"].values[0],4))
            st.write("RSI:", round(latest["rsi"], 2))
            st.write("MACD:", round(latest["macd"], 4))
            st.write("Signal:", round(latest["signal"], 4))
