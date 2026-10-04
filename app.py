import datetime
import pytz
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Nifty Advanced Sniper Terminal", page_icon="⚡", layout="wide")

# AUTO REFRESH: Har 3000 ms (3 Second) me live data refresh hoga
st_autorefresh(interval=3000, key="dhan_sniper_autorefresh")

# --- DARK MODE STRICK UI CSS ---
dark_mode_style = """
<style>
#MainMenu {visibility: hidden;}
header {visibility: hidden;}
footer {visibility: hidden;}
/* Force Dark Background and White Text globally */
.stApp {
    background-color: #0E1117 !important;
    color: #FAFAFA !important;
}
/* Table text color adjust */
.stDataFrame {
    color: #FAFAFA !important;
}
</style>
"""
st.markdown(dark_mode_style, unsafe_allow_html=True)

st.title("⚡ Nifty Advanced Sniper Terminal")

# --- 1. FETCH SECRETS ---
try:
    CLIENT_ID = str(st.secrets["DHAN_CLIENT_ID"]).strip()
    ACCESS_TOKEN = str(st.secrets["DHAN_ACCESS_TOKEN"]).strip()
except Exception as e:
    st.error("⚠️ Secrets Missing! Streamlit Cloud Secrets me DHAN_CLIENT_ID aur DHAN_ACCESS_TOKEN config karein.")
    st.stop()

HEADERS = {
    "access-token": ACCESS_TOKEN,
    "client-id": CLIENT_ID,
    "Content-Type": "application/json"
}

# TIMEZONE SETUP: Strictly Indian Standard Time (IST) lock
IST = pytz.timezone('Asia/Kolkata')

# --- 2. DHAN API HELPERS ---
def get_dhan_history(security_id, exchange_segment, instrument_type, interval="3"):
    try:
        today_date = datetime.datetime.now(IST).date()
        from_date = (today_date - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        to_date = today_date.strftime("%Y-%m-%d")

        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrumentType": instrument_type,
            "interval": interval,
            "fromDate": from_date,
            "toDate": to_date
        }
        
        url = "https://api.dhan.co/v2/charts/intraday"
        res = requests.post(url, json=payload, headers=HEADERS, timeout=10).json()

        if "start_Time" not in res or not res["start_Time"]:
            return 0, 0, 0, 0, 0, 0, 0, 0

        df = pd.DataFrame({
            "epoch": res["start_Time"],
            "open": res["open"],
            "high": res["high"],
            "low": res["low"],
            "close": res["close"],
            "volume": res.get("volume", [0] * len(res["close"]))
        })

        df["datetime"] = pd.to_datetime(df["epoch"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        df["date"] = df["datetime"].dt.date

        today_df = df[df["date"] == today_date].reset_index(drop=True)
        
        if not today_df.empty:
            day_open_price = today_df["open"].iloc[0]
            
            tp = (today_df["high"] + today_df["low"] + today_df["close"]) / 3
            cum_vol = today_df["volume"].cumsum()
            cum_tp_vol = (tp * today_df["volume"]).cumsum()
            
            intraday_vwap = (cum_tp_vol / cum_vol).iloc[-1] if cum_vol.iloc[-1] > 0 else today_df["close"].mean()
            current_price = today_df["close"].iloc[-1]
            last_candle = today_df.iloc[-1]
            c_open, c_high, c_low, c_close = last_candle["open"], last_candle["high"], last_candle["low"], last_candle["close"]
        else:
            current_price = df["close"].iloc[-1] if not df.empty else 0
            day_open_price = current_price
            intraday_vwap, c_open, c_high, c_low, c_close = current_price, current_price, current_price, current_price, current_price

        past_days = df[df["date"] < today_date]
        if not past_days.empty:
            last_date = past_days["date"].max()
            last_day_df = past_days[past_days["date"] == last_date]
            tp_prev = (last_day_df["high"] + last_day_df["low"] + last_day_df["close"]) / 3
            cum_vol_prev = last_day_df["volume"].sum()
            cum_tp_vol_prev = (tp_prev * last_day_df["volume"]).sum()
            pdvwap = cum_tp_vol_prev / cum_vol_prev if cum_vol_prev > 0 else last_day_df["close"].mean()
        else:
            pdvwap = current_price

        return current_price, intraday_vwap, pdvwap, c_open, c_high, c_low, c_close, day_open_price

    except Exception as e:
        st.error(f"🚨 Data Fetch Error for Security {security_id}: {e}")
        return 0, 0, 0, 0, 0, 0, 0, 0

def get_atm_option_keys(atm_strike):
    try:
        url = "https://api.dhan.co/v2/optionchain"
        payload = {"UnderlyingScrip": 13, "UnderlyingSeg": "NSE_IDX"}
        res = requests.post(url, json=payload, headers=HEADERS, timeout=10).json()

        if res.get("status") == "failure" or "data" not in res:
            st.error(f"🚨 Dhan Option Chain Error: {res.get('remarks', res)}")
            return None, None

        oc_data = res["data"]
        oc_list = oc_data.get("oc", {})
        ce_sec_id, pe_sec_id = None, None

        if str(float(atm_strike)) in oc_list:
            strike_info = oc_list[str(float(atm_strike))]
            ce_sec_id = strike_info.get("ce", {}).get("security_id")
            pe_sec_id = strike_info.get("pe", {}).get("security_id")

        return ce_sec_id, pe_sec_id

    except Exception as e:
        st.error(f"🚨 Option Chain Fetch Exception: {e}")
        return None, None

# --- 3. EXECUTE DASHBOARD ENGINE ---
spot_price, spot_intra_vwap, spot_pdvwap, _, _, _, _, spot_open_price = get_dhan_history("13", "NSE_IDX", "INDEX")

if spot_open_price > 0:
    atm_strike = int(round(spot_open_price / 50) * 50)
else:
    atm_strike = 24700

ce_sec_id, pe_sec_id = get_atm_option_keys(atm_strike)

st.sidebar.subheader("🎯 Auto-Engine Setup")
st.sidebar.write(f"**Nifty Day Open:** ₹{spot_open_price}")
st.sidebar.write(f"**Strike:** {atm_strike}")
st.sidebar.success("⚡ Live Auto-Refreshing Every 3 Sec (IST)")

if ce_sec_id:
    ce_price, ce_intra_vwap, ce_pdvwap, ce_open, ce_high, ce_low, ce_close, _ = get_dhan_history(ce_sec_id, "NSE_FNO", "OPTIDX")
else:
    ce_price = ce_intra_vwap = ce_pdvwap = ce_open = ce_high = ce_low = ce_close = 0

if pe_sec_id:
    pe_price, pe_intra_vwap, pe_pdvwap, pe_open, pe_high, pe_low, pe_close, _ = get_dhan_history(pe_sec_id, "NSE_FNO", "OPTIDX")
else:
    pe_price = pe_intra_vwap = pe_pdvwap = pe_open = pe_high = pe_low = pe_close = 0

# --- 4. TRADING LOGIC MATRIX ---
bull_bias = (spot_price > spot_pdvwap) and (spot_price > spot_intra_vwap)
bear_bias = (spot_price < spot_pdvwap) and (spot_price < spot_intra_vwap)

ce_strong_buyer = (ce_price > ce_pdvwap) and (ce_price > ce_intra_vwap)
ce_strong_seller = (ce_price < ce_pdvwap) and (ce_price < ce_intra_vwap)

pe_strong_buyer = (pe_price > pe_pdvwap) and (pe_price > pe_intra_vwap)
pe_strong_seller = (pe_price < pe_pdvwap) and (pe_price < pe_intra_vwap)

# --- 5. UI DISPLAY METRICS & TABLE ---
col1, col2, col3 = st.columns(3)
col1.metric("Nifty Spot Price", f"₹{spot_price}", f"Day Open: {spot_open_price}")
col2.metric(f"Strike CE ({atm_strike})", f"₹{ce_price}")
col3.metric(f"Strike PE ({atm_strike})", f"₹{pe_price}")

st.markdown("---")

matrix_data = [
    {"Component": "Nifty Spot", "Price": spot_price, "OLD": round(spot_pdvwap, 2), "NEW": round(spot_intra_vwap, 2), "Bullish Status": "🟢 Bull Bias" if bull_bias else "⚪", "Bearish Status": "🔴 Bear Bias" if bear_bias else "⚪"},
    {"Component": "CE Option", "Price": ce_price, "OLD": round(ce_pdvwap, 2), "NEW": round(ce_intra_vwap, 2), "Bullish Status": "🟢 Strong Buyer" if ce_strong_buyer else "⚪", "Bearish Status": "🔴 Strong Seller" if ce_strong_seller else "⚪"},
    {"Component": "PE Option", "Price": pe_price, "OLD": round(pe_pdvwap, 2), "NEW": round(pe_intra_vwap, 2), "Bullish Status": "🟢 Strong Seller" if pe_strong_seller else "⚪", "Bearish Status": "🔴 Strong Buyer" if pe_strong_buyer else "⚪"},
]
st.dataframe(pd.DataFrame(matrix_data), use_container_width=True)

st.markdown("---")
st.subheader("🚨 Live A+ Multi-Confirmation Trade Signals")

ce_buy_trade = bull_bias and pe_strong_seller and ce_strong_buyer
pe_buy_trade = bear_bias and ce_strong_seller and pe_strong_buyer

BUFFER = 2.0

col_sig1, col_sig2 = st.columns(2)
with col_sig1:
    if ce_buy_trade:
        ce_entry = ce_close
        ce_sl = ce_low - BUFFER
        ce_risk = ce_entry - ce_sl
        ce_target = ce_entry + (2 * ce_risk)
        st.success(f"🚀 **CE BUY TRIGGERED!**\n\n- **Entry (Close):** ₹{round(ce_entry, 2)}\n- **SL (Low - 2pts):** ₹{round(ce_sl, 2)}\n- **Target (1:2):** ₹{round(ce_target, 2)}")
    else:
        st.info("⚪ CE Buy Trade: Conditions Not Met")

with col_sig2:
    if pe_buy_trade:
        pe_entry = pe_close
        pe_sl = pe_low - BUFFER
        pe_risk = pe_entry - pe_sl
        pe_target = pe_entry + (2 * pe_risk)
        st.error(f"📉 **PE BUY TRIGGERED!**\n\n- **Entry (Close):** ₹{round(pe_entry, 2)}\n- **SL (Low - 2pts):** ₹{round(pe_sl, 2)}\n- **Target (1:2):** ₹{round(pe_target, 2)}")
    else:
        st.info("⚪ PE Buy Trade: Conditions Not Met")

st.caption(f"🔄 Auto-Refreshed At (IST): {datetime.datetime.now(IST).strftime('%H:%M:%S')}")
