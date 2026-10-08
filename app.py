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

IST = pytz.timezone('Asia/Kolkata')
now_ist = datetime.datetime.now(IST)
today_date = now_ist.date()

# HEADER WITH LIVE AUTO-REFRESH TIMESTAMP
col_h1, col_h2 = st.columns([3, 1])
with col_h1:
    st.title("⚡ Nifty Advanced Sniper Terminal")
with col_h2:
    st.caption(f"⏰ **Last Refreshed:** {now_ist.strftime('%H:%M:%S IST')}")

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

# --- INITIALIZE STATE ENGINE ---
if 'trades' not in st.session_state:
    st.session_state.trades = {}

if 'alert_triggered' not in st.session_state:
    st.session_state.alert_triggered = False
    st.session_state.snapshot_table = None
    st.session_state.snapshot_time = None
    st.session_state.snapshot_type = None
    st.session_state.yesterday_status_text = ""

# --- 2. DHAN API HELPERS & DEBUGGER ---
debug_logs = []

def get_dhan_history(security_id, exchange_segment, instrument_type, interval="1"):
    try:
        from_date = (today_date - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        to_date = today_date.strftime("%Y-%m-%d")

        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument_type,
            "interval": str(interval),
            "fromDate": from_date,
            "toDate": to_date
        }
        
        url = "https://api.dhan.co/v2/charts/intraday"
        res = requests.post(url, json=payload, headers=HEADERS, timeout=10).json()

        # Flexible Time Key Lookup for Dhan API variations
        time_key = next((k for k in ["start_Time", "start_time", "startTime", "timestamp", "epoch"] if k in res and res[k]), None)

        if not time_key or "close" not in res or not res["close"]:
            err_msg = res.get("remarks") or res.get("errorMessage") or str(res)[:200]
            return 0, 0, 0, 0, 0, 0, 0, 0, 0, f"Charts API Error (SecID {security_id}): {err_msg}"

        df = pd.DataFrame({
            "epoch": res[time_key],
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

        # Kal (Yesterday) Ka Data
        past_days = df[df["date"] < today_date]
        if not past_days.empty:
            last_date = past_days["date"].max()
            last_day_df = past_days[past_days["date"] == last_date]
            tp_prev = (last_day_df["high"] + last_day_df["low"] + last_day_df["close"]) / 3
            cum_vol_prev = last_day_df["volume"].sum()
            cum_tp_vol_prev = (tp_prev * last_day_df["volume"]).sum()
            
            pdvwap = cum_tp_vol_prev / cum_vol_prev if cum_vol_prev > 0 else last_day_df["close"].mean()
            prev_close = last_day_df["close"].iloc[-1]
        else:
            pdvwap = current_price
            prev_close = current_price

        return current_price, intraday_vwap, pdvwap, c_open, c_high, c_low, c_close, day_open_price, prev_close, "OK"

    except Exception as e:
        return 0, 0, 0, 0, 0, 0, 0, 0, 0, f"Exception (SecID {security_id}): {str(e)}"

def get_atm_option_keys(atm_strike):
    try:
        url = "https://api.dhan.co/v2/optionchain"
        payload = {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"}
        res = requests.post(url, json=payload, headers=HEADERS, timeout=10).json()

        if res.get("status") == "failure" or "data" not in res:
            return None, None, f"Option Chain API Error: {res}"
        
        oc_data = res.get("data", {})
        oc_list = oc_data.get("oc", {})
        
        target_strike = float(atm_strike)
        for strike_str, strike_info in oc_list.items():
            try:
                if abs(float(strike_str) - target_strike) < 1.0:
                    ce_id = strike_info.get("ce", {}).get("security_id")
                    pe_id = strike_info.get("pe", {}).get("security_id")
                    return ce_id, pe_id, "OK"
            except (ValueError, TypeError):
                continue
        return None, None, f"Strike {atm_strike} Option Chain keys me nahi mila."
    except Exception as e:
        return None, None, f"Option Chain Exception: {str(e)}"

# --- 3. FETCH LIVE MARKET DATA ---
spot_price, spot_intra_vwap, spot_pdvwap, _, _, _, _, spot_open_price, spot_prev_close, status_spot = get_dhan_history("13", "IDX_I", "INDEX", interval="1")

if status_spot != "OK":
    debug_logs.append(f"Spot Data Error: {status_spot}")

atm_strike = int(round(spot_open_price / 50) * 50) if spot_open_price > 0 else (int(round(spot_price / 50) * 50) if spot_price > 0 else 24700)
ce_sec_id, pe_sec_id, status_oc = get_atm_option_keys(atm_strike)

if status_oc != "OK":
    debug_logs.append(f"Option Chain Error: {status_oc}")

if ce_sec_id:
    ce_price, ce_intra_vwap, ce_pdvwap, ce_open, ce_high, ce_low, ce_close, _, ce_prev_close, status_ce = get_dhan_history(ce_sec_id, "NSE_FNO", "OPTIDX", interval="1")
    if status_ce != "OK":
        debug_logs.append(f"CE Option Error: {status_ce}")
else:
    ce_price = ce_intra_vwap = ce_pdvwap = ce_open = ce_high = ce_low = ce_close = ce_prev_close = 0

if pe_sec_id:
    pe_price, pe_intra_vwap, pe_pdvwap, pe_open, pe_high, pe_low, pe_close, _, pe_prev_close, status_pe = get_dhan_history(pe_sec_id, "NSE_FNO", "OPTIDX", interval="1")
    if status_pe != "OK":
        debug_logs.append(f"PE Option Error: {status_pe}")
else:
    pe_price = pe_intra_vwap = pe_pdvwap = pe_open = pe_high = pe_low = pe_close = pe_prev_close = 0

# --- 4. CORE STRATEGY CONDITIONS ---
bull_bias = (spot_price > spot_pdvwap) and (spot_price > spot_intra_vwap)
bear_bias = (spot_price < spot_pdvwap) and (spot_price < spot_intra_vwap)

ce_strong_buyer = (ce_price > ce_pdvwap) and (ce_price > ce_intra_vwap)
ce_strong_seller = (ce_price < ce_pdvwap) and (ce_price < ce_intra_vwap)

pe_strong_buyer = (pe_price > pe_pdvwap) and (pe_price > pe_intra_vwap)
pe_strong_seller = (pe_price < pe_pdvwap) and (pe_price < pe_intra_vwap)

ce_buy_condition = bull_bias and pe_strong_seller and ce_strong_buyer
pe_buy_condition = bear_bias and ce_strong_seller and pe_strong_buyer

# --- 5. BUILD LIVE CONTINUOUS TABLE (Updates every 3 secs) ---
live_table_data = [
    {
        "Component": "Nifty Spot",
        "Intraday Bias": "Bullish" if spot_price > spot_intra_vwap else "Bearish",
        "Old Bias": "Bullish" if spot_price > spot_pdvwap else "Bearish"
    },
    {
        "Component": "CE",
        "Intraday Bias": "Intraday Call Buyer" if ce_price > ce_intra_vwap else "Intraday Call Seller",
        "Old Bias": "Old Call Buyer" if ce_price > ce_pdvwap else "Old Call Seller"
    },
    {
        "Component": "PE",
        "Intraday Bias": "Intraday Put Buyer" if pe_price > pe_intra_vwap else "Intraday Put Seller",
        "Old Bias": "Old Put Buyer" if pe_price > pe_pdvwap else "Old Put Seller"
    }
]
df_live = pd.DataFrame(live_table_data)

# Yesterday closing vs Yesterday VWAP Logic (Live)
ce_close_status = "buyer" if ce_prev_close > ce_pdvwap else "seller"
pe_close_status = "buyer" if pe_prev_close > pe_pdvwap else "seller"
live_yesterday_text = f"ce close = {ce_close_status}  |  pe close = {pe_close_status}"

# --- 6. ALERT TRIGGER & SNAPSHOT ENGINE (FREEZE LOGIC) ---
if not st.session_state.alert_triggered and spot_price > 0:
    if ce_buy_condition or pe_buy_condition:
        st.session_state.alert_triggered = True
        st.session_state.snapshot_time = datetime.datetime.now(IST).strftime("%H:%M:%S")
        st.session_state.snapshot_type = "🚀 CE BUY ALERT" if ce_buy_condition else "📉 PE BUY ALERT"
        st.session_state.snapshot_table = df_live.copy()
        st.session_state.yesterday_status_text = live_yesterday_text

# --- 7. UI RENDER ---
# 7A. TOP METRICS
col1, col2, col3 = st.columns(3)
col1.metric("Nifty Spot Price", f"₹{spot_price}", f"Day Open: {spot_open_price}")
col2.metric(f"Strike CE ({atm_strike})", f"₹{ce_price}")
col3.metric(f"Strike PE ({atm_strike})", f"₹{pe_price}")

st.markdown("---")

# 7B. LIVE MARKET STATUS TABLE (Continuous Update)
st.subheader("📡 LIVE MARKET STATUS (Auto-Refreshing)")
st.table(df_live)
st.markdown(f"**{live_yesterday_text}**")

st.markdown("---")

# 7C. ALERT DASHBOARD (Freeze Table)
st.subheader("🚨 LIVE ALERT DASHBOARD")
if st.session_state.alert_triggered:
    st.success(f"**{st.session_state.snapshot_type} TRIGGERED AT {st.session_state.snapshot_time}**")
    st.caption("🔒 Ye data usi time ka freeze kiya hua snapshot hai (Pura din change nahi hoga)")
    st.table(st.session_state.snapshot_table)
    st.markdown(f"**{st.session_state.yesterday_status_text}**")
else:
    st.info("⏳ Waiting for Market Alert... Jab condition match hogi tab snapshot yahan freeze ho jayega.")

# --- 8. AUTO-TRADE LOGGER & EXPORT ---
BUFFER = 2.0
current_time_str = datetime.datetime.now(IST).strftime("%H:%M:%S")

active_ce_id = None
active_pe_id = None

for tid, t in st.session_state.trades.items():
    if t["Type"] == "CE BUY" and t["Status"] == "Active 🟢":
        active_ce_id = tid
    if t["Type"] == "PE BUY" and t["Status"] == "Active 🔴":
        active_pe_id = tid

if active_ce_id:
    trade = st.session_state.trades[active_ce_id]
    if ce_price >= trade["Target"]:
        trade["Status"] = "Target Hit 🎯"
    elif ce_price <= trade["SL"]:
        trade["Status"] = "SL Hit ❌"
elif ce_buy_condition and spot_price > 0:
    st.session_state.trades[f"CE_{current_time_str}"] = {
        "Date": today_date.strftime("%Y-%m-%d"), "Time": current_time_str, "Type": "CE BUY",
        "Nifty_Open": spot_open_price, "Strike": atm_strike, "Entry": ce_close,
        "SL": round(ce_low - BUFFER, 2), "Target": round(ce_close + (2 * (ce_close - (ce_low - BUFFER))), 2),
        "Status": "Active 🟢"
    }

if active_pe_id:
    trade = st.session_state.trades[active_pe_id]
    if pe_price >= trade["Target"]:
        trade["Status"] = "Target Hit 🎯"
    elif pe_price <= trade["SL"]:
        trade["Status"] = "SL Hit ❌"
elif pe_buy_condition and spot_price > 0:
    st.session_state.trades[f"PE_{current_time_str}"] = {
        "Date": today_date.strftime("%Y-%m-%d"), "Time": current_time_str, "Type": "PE BUY",
        "Nifty_Open": spot_open_price, "Strike": atm_strike, "Entry": pe_close,
        "SL": round(pe_low - BUFFER, 2), "Target": round(pe_close + (2 * (pe_close - (pe_low - BUFFER))), 2),
        "Status": "Active 🔴"
    }

st.markdown("---")
st.subheader("📊 Auto-Updating Trade Log")

if st.session_state.trades:
    df_trades = pd.DataFrame(list(st.session_state.trades.values()))[::-1]
    st.dataframe(df_trades, use_container_width=True)
    
    # EXPORT CSV / EXCEL
    csv_data = df_trades.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download Trade Log (CSV / Excel)",
        data=csv_data,
        file_name=f"trade_log_{today_date}.csv",
        mime="text/csv"
    )
else:
    st.write("⏳ Abhi tak koi trade lagaya nahi gaya hai...")

# --- 9. DEBUG & ERROR LOG PANEL ---
st.markdown("---")
with st.expander("🛠️ API Debug & Error Logs"):
    if debug_logs:
        for log in debug_logs:
            st.error(log)
    else:
        st.success("✅ Sabhi API Calls perfectly work kar rahe hain! Live Chart & Options Connected.")
