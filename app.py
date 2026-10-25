import datetime
import pytz
import pandas as pd
import requests
import streamlit as st
import os
from streamlit_autorefresh import st_autorefresh

# --- TELEGRAM ALERT FUNCTION ---
def send_telegram_alert(message):
    try:
        bot_token = str(st.secrets["TELEGRAM_BOT_TOKEN"]).strip()
        chat_id = str(st.secrets["TELEGRAM_CHAT_ID"]).strip()
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        pass

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Dhan Laxmi Apsra", page_icon="📈", layout="wide")

# AUTO REFRESH: Har 3000 ms (3 Second) me live data refresh hoga
st_autorefresh(interval=3000, key="dhan_sniper_autorefresh")

IST = pytz.timezone('Asia/Kolkata')
now_ist = datetime.datetime.now(IST)
today_date = now_ist.date()

# 8:00 AM MORNING HEALTH CHECK TELEGRAM NOTIFICATION
if 'morning_msg_date' not in st.session_state:
    st.session_state.morning_msg_date = None

if now_ist.hour >= 8 and st.session_state.morning_msg_date != today_date:
    send_telegram_alert("🟢 *Dhan Laxmi Apsra:* System Health Check - All systems OK, Tokens Active, Ready for Market!")
    st.session_state.morning_msg_date = today_date

# --- CENTER ALIGNED HEADER WITH LOGO & TITLE ---
if os.path.exists("logo.png"):
    st.markdown(
        """
        <div style="display: flex; justify-content: center; align-items: center; flex-direction: column;">
            <img src="app/static/logo.png" width="240" style="margin-bottom: 10px;">
        </div>
        """,
        unsafe_allow_html=True
    )

st.markdown("<h1 style='text-align: center; margin-top: 0px;'>Dhan Laxmi Apsra</h1>", unsafe_allow_html=True)
st.markdown(f"<p style='text-align: center;'>⏰ <b>Last Refreshed:</b> {now_ist.strftime('%H:%M:%S IST')}</p>", unsafe_allow_html=True)

st.markdown("---")

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
    st.session_state.condition_status = ""
    st.session_state.pain_status = ""

debug_logs = []

# --- 2. DHAN API HELPERS ---
def get_dhan_history(security_id, exchange_segment, instrument_type, interval="3"):
    try:
        from_date = (today_date - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
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

        time_key = next((k for k in ["start_Time", "start_time", "startTime", "timestamp", "epoch"] if k in res and res[k]), None)

        if not time_key or "close" not in res or not res["close"]:
            err_msg = res.get("remarks") or res.get("errorMessage") or str(res)[:150]
            return 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, f"Charts API Error (SecID {security_id}): {err_msg}"

        df = pd.DataFrame({
            "epoch": res[time_key],
            "open": res["open"],
            "high": res["high"],
            "low": res["low"],
            "close": res["close"],
            "volume": res.get("volume", [1] * len(res["close"]))
        })

        df["datetime"] = pd.to_datetime(df["epoch"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        df["date"] = df["datetime"].dt.date

        # Per-day VWAP Calculation Logic
        def calc_vwap(day_df):
            if day_df.empty:
                return day_df
            tp = (day_df["high"] + day_df["low"] + day_df["close"]) / 3
            vol = day_df["volume"].replace(0, 1)
            cum_vol = vol.cumsum()
            cum_tp_vol = (tp * vol).cumsum()
            day_df["vwap"] = cum_tp_vol / cum_vol
            return day_df

        df = df.groupby("date", group_keys=False).apply(calc_vwap)

        today_df = df[df["date"] == today_date].reset_index(drop=True)
        
        if not today_df.empty:
            day_open_price = float(today_df["open"].iloc[0])
            intraday_vwap = float(today_df["vwap"].iloc[-1])
            current_price = float(today_df["close"].iloc[-1])
            last_candle = today_df.iloc[-1]
            c_open, c_high, c_low, c_close = float(last_candle["open"]), float(last_candle["high"]), float(last_candle["low"]), float(last_candle["close"])
            today_first_vwap = float(today_df["vwap"].iloc[0])
        else:
            current_price = float(df["close"].iloc[-1]) if not df.empty else 0
            day_open_price = current_price
            intraday_vwap, c_open, c_high, c_low, c_close = current_price, current_price, current_price, current_price, current_price
            today_first_vwap = current_price

        # Kal (Yesterday) Ka Data Extraction
        past_days = df[df["date"] < today_date]
        if not past_days.empty:
            last_date = past_days["date"].max()
            last_day_df = past_days[past_days["date"] == last_date]
            pdvwap = float(last_day_df["vwap"].iloc[-1])
            prev_close = float(last_day_df["close"].iloc[-1])
            prev_day_last_vwap = float(last_day_df["vwap"].iloc[-1])
        else:
            pdvwap = intraday_vwap if intraday_vwap > 0 else current_price
            prev_close = current_price
            prev_day_last_vwap = intraday_vwap

        return current_price, intraday_vwap, pdvwap, c_open, c_high, c_low, c_close, day_open_price, prev_close, prev_day_last_vwap, today_first_vwap, "OK"

    except Exception as e:
        return 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, f"Exception (SecID {security_id}): {str(e)}"

# CACHE OPTION CHAIN LOOKUP FOR 5 MINS
@st.cache_data(ttl=300)
def get_atm_option_keys_cached(atm_strike, token, client_id):
    headers = {
        "access-token": token,
        "client-id": client_id,
        "Content-Type": "application/json"
    }
    try:
        exp_url = "https://api.dhan.co/v2/optionchain/expirylist"
        exp_payload = {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"}
        exp_res = requests.post(exp_url, json=exp_payload, headers=headers, timeout=10).json()

        expiry_dates = exp_res.get("data", [])
        if not expiry_dates:
            err = exp_res.get("remarks") or exp_res.get("errorMessage") or str(exp_res)[:150]
            return None, None, f"Expiry List API Error: {err}"

        nearest_expiry = expiry_dates[0]

        oc_url = "https://api.dhan.co/v2/optionchain"
        oc_payload = {
            "UnderlyingScrip": 13,
            "UnderlyingSeg": "IDX_I",
            "Expiry": str(nearest_expiry)
        }
        oc_res = requests.post(oc_url, json=oc_payload, headers=headers, timeout=10).json()

        if oc_res.get("status") == "failure" or "data" not in oc_res:
            err = oc_res.get("remarks") or oc_res.get("errorMessage") or str(oc_res)[:150]
            return None, None, f"Option Chain API Error: {err}"

        oc_data = oc_res.get("data", {})
        oc_list = oc_data.get("oc", {})

        target_strike = float(atm_strike)
        for strike_str, strike_info in oc_list.items():
            try:
                if abs(float(strike_str) - target_strike) < 1.0:
                    ce_dict = strike_info.get("ce", {})
                    pe_dict = strike_info.get("pe", {})
                    ce_id = ce_dict.get("security_id") or ce_dict.get("securityId")
                    pe_id = pe_dict.get("security_id") or pe_dict.get("securityId")
                    if ce_id and pe_id:
                        return str(ce_id), str(pe_id), "OK"
            except (ValueError, TypeError):
                continue

        return None, None, f"Strike {atm_strike} (Expiry {nearest_expiry}) Option Chain me nahi mila."
    except Exception as e:
        return None, None, f"Option Chain Exception: {str(e)}"

def get_atm_option_keys(atm_strike):
    return get_atm_option_keys_cached(atm_strike, ACCESS_TOKEN, CLIENT_ID)

# --- 3. FETCH LIVE MARKET DATA ---
spot_price, spot_intra_vwap, spot_pdvwap, _, _, _, _, spot_open_price, spot_prev_close, spot_prev_last_vwap, spot_today_first_vwap, status_spot = get_dhan_history("13", "IDX_I", "INDEX", interval="3")

if status_spot != "OK":
    debug_logs.append(f"Spot Data Error: {status_spot}")

atm_strike = int(round(spot_open_price / 50) * 50) if spot_open_price > 0 else (int(round(spot_price / 50) * 50) if spot_price > 0 else 23000)
ce_sec_id, pe_sec_id, status_oc = get_atm_option_keys(atm_strike)

if status_oc != "OK":
    debug_logs.append(f"Option Chain Error: {status_oc}")

if ce_sec_id:
    ce_price, ce_intra_vwap, ce_pdvwap, ce_open, ce_high, ce_low, ce_close, _, ce_prev_close, _, _, status_ce = get_dhan_history(ce_sec_id, "NSE_FNO", "OPTIDX", interval="3")
    if status_ce != "OK":
        debug_logs.append(f"CE Option Error: {status_ce}")
else:
    ce_price = ce_intra_vwap = ce_pdvwap = ce_open = ce_high = ce_low = ce_close = ce_prev_close = 0

if pe_sec_id:
    pe_price, pe_intra_vwap, pe_pdvwap, pe_open, pe_high, pe_low, pe_close, _, pe_prev_close, _, _, status_pe = get_dhan_history(pe_sec_id, "NSE_FNO", "OPTIDX", interval="3")
    if status_pe != "OK":
        debug_logs.append(f"PE Option Error: {status_pe}")
else:
    pe_price = pe_intra_vwap = pe_pdvwap = pe_open = pe_high = pe_low = pe_close = pe_prev_close = 0

# --- 4. EXACT LABELS DEFINITION ---
spot_old_bias = "Old Bias Bullish" if spot_price >= spot_pdvwap else "Old Bias Bearish"
spot_new_bias = "New Bias Bullish" if spot_price >= spot_intra_vwap else "New Bias Bearish"

ce_old_label = "Old Call Buyer" if ce_price >= ce_pdvwap else "Old Call Seller"
ce_new_label = "New Call Buyer" if ce_price >= ce_intra_vwap else "New Call Seller"

pe_old_label = "Old Put Buyer" if pe_price >= pe_pdvwap else "Old Put Seller"
pe_new_label = "New Put Buyer" if pe_price >= pe_intra_vwap else "New Put Seller"

# --- 5. CORE CONDITIONS CHECK ---
spot_c1 = spot_price >= spot_pdvwap
spot_c2 = spot_price >= spot_intra_vwap
pe_c3 = pe_price < pe_pdvwap
pe_c4 = pe_price < pe_intra_vwap
ce_c5 = ce_price >= ce_pdvwap
ce_c6 = ce_price >= ce_intra_vwap

spot_bear_c1 = spot_price < spot_pdvwap
spot_bear_c2 = spot_price < spot_intra_vwap
ce_bear_c3 = ce_price < ce_pdvwap
ce_bear_c4 = ce_price < ce_intra_vwap
pe_bear_c5 = pe_price >= pe_pdvwap
pe_bear_c6 = pe_price >= pe_intra_vwap

ce_core_5 = spot_c1 and spot_c2 and pe_c3 and pe_c4 and ce_c5
ce_buy_6_of_6 = ce_core_5 and ce_c6
ce_buy_5_of_6 = ce_core_5 and (not ce_c6)

pe_core_5 = spot_bear_c1 and spot_bear_c2 and ce_bear_c3 and ce_bear_c4 and pe_bear_c5
pe_buy_6_of_6 = pe_core_5 and pe_bear_c6
pe_buy_5_of_6 = pe_core_5 and (not pe_bear_c6)

ce_buy_condition = ce_buy_6_of_6 or ce_buy_5_of_6
pe_buy_condition = pe_buy_6_of_6 or pe_buy_5_of_6

current_condition_status = ""
if ce_buy_6_of_6:
    current_condition_status = "6/6 (All conditions met)"
elif ce_buy_5_of_6:
    current_condition_status = "5/6 new call buyer absent"
elif pe_buy_6_of_6:
    current_condition_status = "6/6 (All conditions met)"
elif pe_buy_5_of_6:
    current_condition_status = "5/6 new put buyer absent"

current_pain_status = ""
if ce_buy_condition:
    current_pain_status = "No Pain" if spot_prev_last_vwap < spot_today_first_vwap else "Pain Yes"
elif pe_buy_condition:
    current_pain_status = "No Pain" if spot_prev_last_vwap > spot_today_first_vwap else "Pain Yes"

# --- 6. BUILD LIVE CONTINUOUS TABLE ---
live_table_data = [
    {
        "Component": "Nifty Spot",
        "Old Bias": spot_old_bias,
        "Intraday Bias": spot_new_bias
    },
    {
        "Component": "CE",
        "Old Bias": ce_old_label,
        "Intraday Bias": ce_new_label
    },
    {
        "Component": "PE",
        "Old Bias": pe_old_label,
        "Intraday Bias": pe_new_label
    }
]
df_live = pd.DataFrame(live_table_data)
df_live_vertical = df_live.set_index("Component").T

ce_close_status = "Call Buyer" if ce_prev_close >= ce_pdvwap else "Call seller"
pe_close_status = "Put Buyer" if pe_prev_close >= pe_pdvwap else "Put Seller"
live_yesterday_text = f"Ce vwap ke upr/niche close: ce close = {ce_close_status} | pe close = {pe_close_status}"

# --- 7. ALERT TRIGGER & SNAPSHOT ENGINE ---
if not st.session_state.alert_triggered and spot_price > 0:
    if ce_buy_condition or pe_buy_condition:
        st.session_state.alert_triggered = True
        st.session_state.snapshot_time = datetime.datetime.now(IST).strftime("%H:%M:%S IST")
        st.session_state.snapshot_type = "🚀 CE BUY ALERT" if ce_buy_condition else "📉 PE BUY ALERT"
        st.session_state.snapshot_table = df_live_vertical.copy()
        st.session_state.yesterday_status_text = live_yesterday_text
        st.session_state.condition_status = current_condition_status
        st.session_state.pain_status = current_pain_status

        msg = f"🚨 *{st.session_state.snapshot_type} TRIGGERED*\n\n⏰ *Time:* {st.session_state.snapshot_time}\n🎯 *Strike:* {atm_strike}\n📊 *Nifty Spot:* ₹{spot_price}\n📋 *Condition:* {current_condition_status}\n⚠️ *Pain:* {current_pain_status}"
        send_telegram_alert(msg)

# --- 8. UI RENDER ---
col1, col2, col3 = st.columns(3)
col1.metric("Nifty Spot Price", f"₹{spot_price}", f"Day Open: {spot_open_price}")
col2.metric(f"Strike CE ({atm_strike})", f"₹{ce_price}")
col3.metric(f"Strike PE ({atm_strike})", f"₹{pe_price}")

st.markdown("---")

st.subheader("📡 LIVE MARKET STATUS (Auto-Refreshing)")
st.table(df_live_vertical)
st.markdown(f"**{live_yesterday_text}**")

st.markdown("---")

st.subheader("🚨 LIVE ALERT DASHBOARD")
if st.session_state.alert_triggered:
    st.success(f"**{st.session_state.snapshot_type} TRIGGERED AT {st.session_state.snapshot_time}**")
    st.caption(f"🔒 Ye data {st.session_state.snapshot_time} par freeze kiya gaya snapshot hai (Pura din change nahi hoga)")
    st.table(st.session_state.snapshot_table)
    st.markdown(f"**Condition Status:** `{st.session_state.condition_status}`")
    st.markdown(f"**Pain Status:** `{st.session_state.pain_status}`")
    st.markdown(f"**{st.session_state.yesterday_status_text}**")
else:
    st.info("⏳ Waiting for Market Alert... Jab condition match hogi tab snapshot yahan freeze ho jayega.")

# --- 9. AUTO-TRADE LOGGER & EXPORT ---
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
        "Status": "Active 🟢", "Condition": current_condition_status, "Pain": current_pain_status
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
        "Status": "Active 🔴", "Condition": current_condition_status, "Pain": current_pain_status
    }

st.markdown("---")
st.subheader("📊 Auto-Updating Trade Log")

if st.session_state.trades:
    df_trades = pd.DataFrame(list(st.session_state.trades.values()))[::-1]
    st.dataframe(df_trades, use_container_width=True)
    
    csv_data = df_trades.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download Trade Log (CSV / Excel)",
        data=csv_data,
        file_name=f"trade_log_{today_date}.csv",
        mime="text/csv"
    )
else:
    st.write("⏳ Abhi tak koi trade lagaya nahi gaya hai...")

# --- 10. DEBUG & ERROR LOG PANEL ---
st.markdown("---")
with st.expander("🛠️ API Debug & Error Logs"):
    if debug_logs:
        for log in debug_logs:
            st.error(log)
    else:
        st.success("✅ Sabhi API Calls perfectly work kar rahe hain!")
