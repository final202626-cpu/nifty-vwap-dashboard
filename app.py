import base64
import datetime
import time
import pandas as pd
import pyotp
import requests
import streamlit as st
from fyers_apiv3 import fyersModel

# --- PAGE SETUP ---
st.set_page_config(page_title="Nifty Multi-Confirmation Sniper Dashboard", page_icon="⚡", layout="wide")

hide_streamlit_style = """
<style>
#MainMenu {visibility: hidden;}
header {visibility: hidden;}
footer {visibility: hidden;}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

st.title("⚡ Nifty Multi-Confirmation Sniper Dashboard")

# 1. FETCH SECRETS
try:
    CLIENT_ID = st.secrets["FYERS_CLIENT_ID"]
    SECRET_KEY = st.secrets["FYERS_SECRET_KEY"]
    REDIRECT_URI = st.secrets.get("FYERS_REDIRECT_URI", "")
    FYERS_ID = st.secrets["FYERS_ID"]
    FYERS_PIN = st.secrets["FYERS_PIN"]
    FYERS_TOTP_KEY = st.secrets["FYERS_TOTP_KEY"]
except Exception as e:
    st.error(f"⚠️ Secrets Missing Key Error: {e}")
    st.stop()

# --- AUTOMATED FYERS LOGIN FUNCTION ---
def get_auto_fyers_token():
    try:
        # 1. TOTP Generation
        totp = pyotp.TOTP(FYERS_TOTP_KEY).now()

        # 2. Validate FYERS ID
        headers = {"Content-Type": "application/json"}
        payload_id = {"fy_id": base64.b64encode(FYERS_ID.encode()).decode(), "app_id": "2"}
        res1 = requests.post("https://api-t1.fyers.in/api/v3/validate-id", json=payload_id, headers=headers).json()
        if res1.get("s") != "ok":
            st.error(f"❌ FYERS ID Validation Failed: {res1}")
            return None
        request_key = res1["request_key"]

        # 3. Validate TOTP
        payload_totp = {"request_key": request_key, "totp": totp}
        res2 = requests.post("https://api-t1.fyers.in/api/v3/validate-totp", json=payload_totp, headers=headers).json()
        if res2.get("s") != "ok":
            st.error(f"❌ TOTP Validation Failed: {res2}")
            return None
        request_key = res2["request_key"]

        # 4. Validate PIN
        payload_pin = {
            "request_key": request_key,
            "pin": base64.b64encode(FYERS_PIN.encode()).decode(),
            "identity_type": "pin"
        }
        res3 = requests.post("https://api-t1.fyers.in/api/v3/validate-pin", json=payload_pin, headers=headers).json()
        if res3.get("s") != "ok":
            st.error(f"❌ PIN Validation Failed: {res3}")
            return None
        token_internal = res3["data"]["access_token"]

        # 5. Get Auth Code
        headers_auth = {"Authorization": f"{FYERS_ID}:{token_internal}", "Content-Type": "application/json"}
        payload_code = {
            "fyers_id": FYERS_ID,
            "app_id": CLIENT_ID.split("-")[0] if "-" in CLIENT_ID else CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "grant_type": "authorization_code"
        }
        res4 = requests.post("https://api-t1.fyers.in/api/v3/token", json=payload_code, headers=headers_auth).json()
        auth_code = res4.get("auth_code")
        if not auth_code:
            st.error(f"❌ Auth Code Generation Failed: {res4}")
            return None

        # 6. Convert Auth Code to Access Token via SDK
        session = fyersModel.SessionModel(
            client_id=CLIENT_ID,
            secret_key=SECRET_KEY,
            redirect_uri=REDIRECT_URI,
            response_type="code",
            grant_type="authorization_code"
        )
        session.set_token(auth_code)
        response = session.generate_token()

        if response.get("s") == "ok":
            return response["access_token"]
        else:
            st.error(f"❌ Token Conversion Failed: {response}")
            return None
    except Exception as e:
        st.error(f"🚨 Auto Login Exception: {e}")
        return None

# --- TOKEN SESSION MANAGEMENT ---
if "fyers_token" not in st.session_state or st.session_state.fyers_token is None:
    with st.spinner("🤖 Auto-Logging in to Fyers API... Please wait..."):
        token = get_auto_fyers_token()
        if token:
            st.session_state.fyers_token = token
            st.success("✅ Auto Login Successful! Dashboard Going LIVE...")
            time.sleep(1)
            st.rerun()
        else:
            st.error("❌ Auto Login Failed! Please check your Secrets configuration.")
            st.stop()

# 2. INITIALIZE FYERS API
fyers = fyersModel.FyersModel(client_id=CLIENT_ID, is_async=False, token=st.session_state.fyers_token, log_path="")

# 3. VWAP ENGINE
def get_vwap_baselines_and_candle(symbol, resolution="3"):
    try:
        today = datetime.date.today()
        from_date = (today - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")

        data = {"symbol": symbol, "resolution": resolution, "date_format": "1", "range_from": from_date, "range_to": to_date, "cont_flag": "1"}
        res = fyers.history(data=data)
        
        if res.get("s") == "error":
            st.error(f"🚨 API Error for {symbol}: {res.get('message')}")
            return 0, 0, 0, 0, 0, 0, 0
            
        if res.get("s") == "success" and "candles" in res:
            df = pd.DataFrame(res["candles"], columns=["epoch", "open", "high", "low", "close", "volume"])
            df["datetime"] = pd.to_datetime(df["epoch"], unit="s")
            df["date"] = df["datetime"].dt.date
            
            today_df = df[df["date"] == today].reset_index(drop=True)
            if not today_df.empty:
                tp = (today_df["high"] + today_df["low"] + today_df["close"]) / 3
                cum_vol = today_df["volume"].cumsum()
                cum_tp_vol = (tp * today_df["volume"]).cumsum()
                
                intraday_vwap = (cum_tp_vol / cum_vol).iloc[-1] if cum_vol.iloc[-1] > 0 else today_df["close"].iloc[-1]
                current_price = today_df["close"].iloc[-1]
                last_candle = today_df.iloc[-1]
                c_open, c_high, c_low, c_close = last_candle["open"], last_candle["high"], last_candle["low"], last_candle["close"]
            else:
                intraday_vwap, current_price, c_open, c_high, c_low, c_close = 0, 0, 0, 0, 0, 0

            past_days = df[df["date"] < today]
            if not past_days.empty:
                last_date = past_days["date"].max()
                last_day_df = past_days[past_days["date"] == last_date]
                tp_prev = (last_day_df["high"] + last_day_df["low"] + last_day_df["close"]) / 3
                cum_vol_prev = last_day_df["volume"].sum()
                cum_tp_vol_prev = (tp_prev * last_day_df["volume"]).sum()
                pdvwap = cum_tp_vol_prev / cum_vol_prev if cum_vol_prev > 0 else last_day_df["close"].iloc[-1]
            else:
                pdvwap = current_price

            return current_price, intraday_vwap, pdvwap, c_open, c_high, c_low, c_close
    except Exception as e:
        pass
    return 0, 0, 0, 0, 0, 0, 0

# 4. DYNAMIC OPEN PRICE
def get_dynamic_symbols():
    q_res = fyers.quotes(data={"symbols": "NSE:NIFTY50-INDEX"})
    if q_res.get("s") != "ok":
        st.error(f"🚨 Fyers Quote API Error: {q_res.get('message', q_res)}")
        if q_res.get("code") == -17:
            st.session_state.fyers_token = None
            st.rerun()
        st.stop()
        
    open_price, ltp_spot = 24700, 24700 
    if "d" in q_res and len(q_res["d"]) > 0:
        v = q_res["d"][0]["v"]
        open_price = v.get("open_price", v.get("lp", 24700))
        ltp_spot = v.get("lp", 24700)

    atm_strike = int(round(open_price / 50) * 50)
    today = datetime.date.today()
    
    days_to_expiry = (1 - today.weekday()) % 7
    expiry_date = today + datetime.timedelta(days=days_to_expiry)
    expiry_str = expiry_date.strftime("%y%b").upper()

    ce_sym = f"NSE:NIFTY{expiry_str}{atm_strike}CE"
    pe_sym = f"NSE:NIFTS{expiry_str}{atm_strike}PE".replace("NIFTS", "NIFTY")
    return atm_strike, ce_sym, pe_sym, ltp_spot, open_price

atm_strike, ce_symbol, pe_symbol, ltp_spot, open_price = get_dynamic_symbols()

st.sidebar.subheader("🎯 Market Setup")
st.sidebar.write(f"**Nifty Open Price:** {open_price}")
st.sidebar.write(f"**ATM Strike:** {atm_strike}")
st.sidebar.write(f"**CE Symbol:** {ce_symbol.replace('NSE:', '')}")
st.sidebar.write(f"**PE Symbol:** {pe_symbol.replace('NSE:', '')}")

# 5. FETCH LIVE/HISTORICAL PRICES
spot_price, spot_intra_vwap, spot_pdvwap, _, _, _, _ = get_vwap_baselines_and_candle("NSE:NIFTY50-INDEX")
ce_price, ce_intra_vwap, ce_pdvwap, ce_open, ce_high, ce_low, ce_close = get_vwap_baselines_and_candle(ce_symbol)
pe_price, pe_intra_vwap, pe_pdvwap, pe_open, pe_high, pe_low, pe_close = get_vwap_baselines_and_candle(pe_symbol)

if spot_price == 0:
    spot_price = ltp_spot

# 6. CONDITIONS
bull_bias = (spot_price > spot_pdvwap) and (spot_price > spot_intra_vwap)
bear_bias = (spot_price < spot_pdvwap) and (spot_price < spot_intra_vwap)

ce_strong_buyer = (ce_price > ce_pdvwap) and (ce_price > ce_intra_vwap)
ce_strong_seller = (ce_price < ce_pdvwap) and (ce_price < ce_intra_vwap)

pe_strong_buyer = (pe_price > pe_pdvwap) and (pe_price > pe_intra_vwap)
pe_strong_seller = (pe_price < pe_pdvwap) and (pe_price < pe_intra_vwap)

# UI MATRIX
col1, col2, col3 = st.columns(3)
col1.metric("Nifty Spot Price", f"₹{spot_price}", f"Open: {open_price}")
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

st.write(f"🔄 Last Updated: {datetime.datetime.now().strftime('%H:%M:%S')}")
time.sleep(180)
st.rerun()
