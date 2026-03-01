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
FIB_PERIOD = 50
MIN_SCORE = 60

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
    if "market_cap" not in df.columns:
        df["market_cap"] = df.get("market_cap_usd", 0)
    if "total_volume" not in df.columns:
        df["total_volume"] = df.get("total_volume_usd", 0)
    return df

def fetch_ohlc(symbol):
    try:
        url = f"https://min-api.cryptocompare.com/data/v2/histohour"
        params = {"fsym": symbol.upper(), "tsym": "USDT", "limit": 200}
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
    if latest["rsi"] < 50:
        score += 20
    if latest["macd"] > latest["signal"]:
        score += 20
    if latest["ema50"] > latest["ema200"]:
        score += 20
    # حجم التداول
    if "volumeto" not in df.columns:
        df["volumeto"] = df.get("volumefrom", 0) * df["close"]
    avg_vol = df["volumeto"].rolling(20).mean().iloc[-1]
    if latest["volumeto"] > avg_vol:
        score += 10
    if smart_mode:
        score += 10
    return score

# ==============================
# الأهداف القديمة
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

    period_high = df["high"].rolling(FIB_PERIOD).max().iloc[-1]
    period_low  = df["low"].rolling(FIB_PERIOD).min().iloc[-1]

    target1 = max(latest_price, period_low + (period_high - period_low) * 0.382)
    target2 = max(latest_price, period_low + (period_high - period_low) * 0.618)
    target3 = max(latest_price, period_low + (period_high - period_low) * 1.0)

    return target1, target2, target3, support1, support2

# ==============================
# دعم ومقاومة 30 و 50
# ==============================
def calculate_support_resistance(df):
    if len(df) < 50:
        return None

    current_price = df["close"].iloc[-1]

    support_30 = df["low"].rolling(30).min().iloc[-1]
    resistance_30 = df["high"].rolling(30).max().iloc[-1]

    support_50 = df["low"].rolling(50).min().iloc[-1]
    resistance_50 = df["high"].rolling(50).max().iloc[-1]

    def proximity(price, level):
        return abs(price - level) / level

    location = "منطقة محايدة"

    if proximity(current_price, support_50) < 0.03:
        location = "قريب من دعم قوي (50 يوم)"
    elif proximity(current_price, support_30) < 0.03:
        location = "قريب من دعم (30 يوم)"
    elif proximity(current_price, resistance_50) < 0.03:
        location = "قريب من مقاومة قوية (50 يوم)"
    elif proximity(current_price, resistance_30) < 0.03:
        location = "قريب من مقاومة (30 يوم)"

    return support_30, support_50, resistance_30, resistance_50, location

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
    status_text = st.empty()
    total = len(market_df)

    for idx, row in enumerate(market_df.itertuples(), start=1):
        symbol = row.symbol.upper()
        status_text.text(f"جارٍ تحميل العملة {idx} من {total} - {round(idx/total*100,1)}%")
        st.write(f"⏳ جاري فحص: {symbol}")

        ohlc = fetch_ohlc(symbol)
        if ohlc is None or len(ohlc) < 100:
            st.write(f"❌ {symbol} → فشل جلب بيانات OHLC")
            continue
        else:
            st.write(f"✅ {symbol} → بيانات OHLC جاهزة ({len(ohlc)} شمعة)")

        # تحقق من حجم التداول
        if "volumeto" not in ohlc.columns:
            ohlc["volumeto"] = ohlc.get("volumefrom", 0) * ohlc["close"]
            st.write(f"❌ {symbol} → حجم التداول مش موجود، تم حسابه من volumefrom")
        else:
            st.write(f"✅ {symbol} → حجم التداول موجود")

        ohlc = add_indicators(ohlc)
        score = calculate_score(ohlc, smart_mode)

        if score < MIN_SCORE:
            st.write(f"⚠ {symbol} → رفض بسبب Score منخفض ({score})")
            continue

        target1, target2, target3, support1, support2 = find_targets(ohlc)
        st.write(f"🎯 {symbol} → ناجح، Score = {score}")

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
            support_30, support_50, resistance_30, resistance_50, location = calculate_support_resistance(ohlc)

            st.write(f"💰 سعر الدخول الحالي: {round(latest['close'],4)}")
            st.write(f"🟢 دعم 30 يوم: {round(support_30,4)}")
            st.write(f"🟢 دعم 50 يوم: {round(support_50,4)}")
            st.write(f"🔴 مقاومة 30 يوم: {round(resistance_30,4)}")
            st.write(f"🔴 مقاومة 50 يوم: {round(resistance_50,4)}")
            st.write(f"📍 مكان السعر: {location}")
