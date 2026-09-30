import datetime
import pandas as pd
import requests
import streamlit as st

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Nifty Multi-Confirmation Sniper Dashboard (Dhan)", page_icon="⚡", layout="wide")

hide_streamlit_style = """
<style>
#MainMenu {visibility: hidden;}
header {visibility: hidden;}
footer {visibility: hidden;}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

st.title("⚡ Nifty Multi-Confirmation Sniper Dashboard (Dhan Engine)")

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

# --- 2. DHAN API HELPERS ---
def get_dhan_history(security_id, exchange_segment, instrument_type, interval="3"):
    """Fetch intraday candles for VWAP & PDVWAP calculation"""
    try:
        today = datetime.date.today()
        from_date = (today - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")

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
            return 0, 0, 0, 0, 0, 0, 0

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

        today_df = df[df["date"] == today].reset_index(drop=True)
        
        if not today_df.empty:
            tp = (today_df["high"] + today_df["low"] + today_df["close"]) / 3
            cum_vol = today_df["volume"].cumsum()
            cum_tp_vol = (tp * today_df["volume"]).cumsum()
            
            # If volume is 0 (like index spot), fallback to simple price average
            intraday_vwap = (cum_tp_vol / cum_vol).iloc[-1] if cum_vol.iloc[-1] > 0 else today_df["close"].mean()
            current_price = today_df["close"].iloc[-1]
            last_candle = today_df.iloc[-1]
            c_open, c_high, c_low, c_close = last_candle["open"], last_candle["high"], last_candle["low"], last_candle["close"]
        else:
            current_price = df["close"].iloc[-1] if not df.empty else 0
            intraday_vwap, c_open, c_high, c_low, c_close = current_price, current_price, current_price, current_price, current_price

        # Calculate Previous Day VWAP
        past_days = df[df["date"] < today]
        if not past_days.empty:
            last_date = past_days["date"].max()
            last_day_df = past_days[past_days["date"] == last_date]
            tp_prev = (last_day_df["high"] + last_day_df["low"] + last_day_df["close"]) / 3
            cum_vol_prev = last_day_df["volume"].sum()
            cum_tp_vol_prev = (tp_prev * last_day_df["volume"]).sum()
            pdvwap = cum_tp_vol_prev / cum_vol_prev if cum_vol_prev > 0 else last_day_df["close"].mean()
        else:
            pdvwap = current_price

        return current_price, intraday_vwap, pdvwap, c_open, c_high, c_low, c_close

    except Exception as e:
        st.error(f"🚨 Data Fetch Error for Security {security_id}: {e}")
        return 0, 0, 0, 0, 0, 0, 0


def get_atm_and_option_keys():
    """Fetch Nifty 50 Index and detect ATM CE/PE Security IDs via Dhan Option Chain"""
    try:
        # 1. Fetch Option Chain for Nifty 50 (Security ID 13)
        url = "https://api.dhan.co/v2/optionchain"
        payload = {"UnderlyingScrip": 13, "UnderlyingSeg": "NSE_IDX"}
        res = requests.post(url, json=payload, headers=HEADERS, timeout=10).json()

        if res.get("status") == "failure" or "data" not in res:
            st.error(f"🚨 Dhan Option Chain Error: {res.get('remarks', res)}")
            st.stop()

        oc_data = res["data"]
        spot_price = oc_data.get("last_price", 24700)
        atm_strike = int(round(spot_price / 50) * 50)

        oc_list = oc_data.get("oc", {})
        ce_sec_id, pe_sec_id = None, None

        if str(float(atm_strike)) in oc_list:
            strike_info = oc_list[str(float(atm_strike))]
            ce_sec_id = strike_info.get("ce", {}).get("security_id")
            pe_sec_id = strike_info.get("pe", {}).get("security_id")

        return spot_price, atm_strike, ce_sec_id, pe_sec_id

    except Exception as e:
        st.error(f"🚨 Option Chain Fetch Exception: {e}")
        st.stop()

# --- 3. RUN DASHBOARD LOGIC ---
with st.spinner("⚡ Fetching Live Market Data from Dhan API..."):
    spot_ltp, atm_strike, ce_sec_id, pe_sec_id = get_atm_and_option_keys()

st.sidebar.subheader("🎯 Dhan Setup Info")
st.sidebar.write(f"**Nifty Spot Price:** {spot_ltp}")
st.sidebar.write(f"**ATM Strike:** {atm_strike}")
st.sidebar.write(f"**CE Security ID:** {ce_sec_id}")
st.sidebar.write(f"**PE Security ID:** {pe_sec_id}")

if st.sidebar.button("🔄 Refresh Data Now"):
    st.rerun()

# Fetch VWAP and Candle metrics for Spot, CE, PE
spot_price, spot_intra_vwap, spot_pdvwap, _, _, _, _ = get_dhan_history("13", "NSE_IDX", "INDEX")

if ce_sec_id:
    ce_price, ce_intra_vwap, ce_pdvwap, ce_open, ce_high, ce_low, ce_close = get_dhan_history(ce_sec_id, "NSE_FNO", "OPTIDX")
else:
    ce_price = ce_intra_vwap = ce_pdvwap = ce_open = ce_high = ce_low = ce_close = 0

if pe_sec_id:
    pe_price, pe_intra_vwap, pe_pdvwap, pe_open, pe_high, pe_low, pe_close = get_dhan_history(pe_sec_id, "NSE_FNO", "OPTIDX")
else:
    pe_price = pe_intra_vwap = pe_pdvwap = pe_open = pe_high = pe_low = pe_close = 0

if spot_price == 0:
    spot_price = spot_ltp

# --- 4. TRADING CONDITIONS ---
bull_bias = (spot_price > spot_pdvwap) and (spot_price > spot_intra_vwap)
bear_bias = (spot_price < spot_pdvwap) and (spot_price < spot_intra_vwap)

ce_strong_buyer = (ce_price > ce_pdvwap) and (ce_price > ce_intra_vwap)
ce_strong_seller = (ce_price < ce_pdvwap) and (ce_price < ce_intra_vwap)

pe_strong_buyer = (pe_price > pe_pdvwap) and (pe_price > pe_intra_vwap)
pe_strong_seller = (pe_price < pe_pdvwap) and (pe_price < pe_intra_vwap)

# --- 5. UI DISPLAY ---
col1, col2, col3 = st.columns(3)
col1.metric("Nifty Spot Price", f"₹{spot_price}")
col2.metric(f"ATM CE ({atm_strike})", f"₹{ce_price}")
col3.metric(f"ATM PE ({atm_strike})", f"₹{pe_price}")

st.markdown("---")
matrix_data = [
    {"Component": "Nifty Spot", "Price": spot_price, "PDVWAP": round(spot_pdvwap, 2), "VWAP": round(spot_intra_vwap, 2), "Bullish Status": "🟢 Bull Bias" if bull_bias else "⚪", "Bearish Status": "🔴 Bear Bias" if bear_bias else "⚪"},
    {"Component": "CE Option", "Price": ce_price, "PDVWAP": round(ce_pdvwap, 2), "VWAP": round(ce_intra_vwap, 2), "Bullish Status": "🟢 Strong Buyer" if ce_strong_buyer else "⚪", "Bearish Status": "🔴 Strong Seller" if ce_strong_seller else "⚪"},
    {"Component": "PE Option", "Price": pe_price, "PDVWAP": round(pe_pdvwap, 2), "VWAP": round(pe_intra_vwap, 2), "Bullish Status": "🟢 Strong Seller" if pe_strong_seller else "⚪", "Bearish Status": "🔴 Strong Buyer" if pe_strong_buyer else "⚪"},
]
st.dataframe(pd.DataFrame(matrix_data), use_container_width=True)

st.markdown("---")
st.subheader("🚨 Final Trade Signals")

ce_buy_trade = bull_bias and pe_strong_seller and ce_strong_buyer
pe_buy_trade = bear_bias and ce_strong_seller and pe_strong_buyer

col_sig1, col_sig2 = st.columns(2)
with col_sig1:
    if ce_buy_trade:
        ce_risk = ce_close - (ce_low - 1)
        st.success(f"🚀 **CE BUY TRIGGERED!**\n\nEntry: ₹{ce_close}\nSL: ₹{round(ce_low - 1, 2)}\nTarget: ₹{round(ce_close + (2 * ce_risk), 2)}")
    else:
        st.info("⚪ CE Buy Trade: Waiting...")

with col_sig2:
    if pe_buy_trade:
        pe_risk = pe_close - (pe_low - 1)
        st.error(f"📉 **PE BUY TRIGGERED!**\n\nEntry: ₹{pe_close}\nSL: ₹{round(pe_low - 1, 2)}\nTarget: ₹{round(pe_close + (2 * pe_risk), 2)}")
    else:
        st.info("⚪ PE Buy Trade: Waiting...")

st.caption(f"🔄 Last Rendered At: {datetime.datetime.now().strftime('%H:%M:%S')}")
