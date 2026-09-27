import datetime
import hashlib
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from fyers_apiv3 import fyersModel

# --- PAGE SETUP ---
st.set_page_config(page_title="Nifty Multi-Confirmation Sniper Dashboard", page_icon="⚡", layout="wide")

# Hide Streamlit UI Elements (GitHub Icon, Header, Footer)
hide_streamlit_style = """
<style>
#MainMenu {visibility: hidden;}
header {visibility: hidden;}
footer {visibility: hidden;}
.viewerBadge_container__1QSob {display: none !important;}
.styles_viewerBadge__1yB5_ {display: none !important;}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# Native JS Auto-refresh every 3 minutes (180,000 ms)
refresh_interval = 180000
components.html(
    f"""
    <script>
        setTimeout(function() {{ window.location.reload(); }}, {refresh_interval});
    </script>
    """,
    height=0,
    width=0,
)

st.title("⚡ Nifty Multi-Confirmation Sniper Dashboard")

# 1. FETCH SECRETS FROM STREAMLIT
try:
    CLIENT_ID = st.secrets["FYERS_CLIENT_ID"]
    SECRET_KEY = st.secrets["FYERS_SECRET_KEY"]
    REDIRECT_URI = st.secrets["FYERS_REDIRECT_URI"]
except Exception as e:
    st.error("⚠️ Secrets config missing! Please configure CLIENT_ID, SECRET_KEY, and REDIRECT_URI in Streamlit Settings.")
    st.stop()

# 2. SMART OAUTH LOGIN (FIXED REDIRECT LOOP)
if "fyers_access_token" not in st.session_state:
    st.session_state.fyers_access_token = None

# Get auth_code from URL
auth_code = st.query_params.get("auth_code")

if auth_code and not st.session_state.fyers_access_token:
    session = fyersModel.SessionModel(
        client_id=CLIENT_ID,
        secret_key=SECRET_KEY,
        redirect_uri=REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code",
    )
    session.set_token(auth_code)
    try:
        response = session.generate_token()
        if "access_token" in response:
            st.session_state.fyers_access_token = response["access_token"]
            st.success("✅ Logged in successfully!")
            # Remove auth_code from URL without forcing a hard reload loop
            st.query_params.clear()
    except Exception as e:
        st.error(f"Login Failed: {e}")

# If no active token, show 1-Click Login Button
if not st.session_state.fyers_access_token:
    st.warning("🔒 Session inactive. Click the button below to authenticate with Fyers.")
    login_url = f"https://api-t1.fyers.in/api/v3/generate-authcode?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&state=dashboard"
    
    st.markdown(
        f'<a href="{login_url}" target="_self"><button style="background-color:#FF5722; color:white; padding:12px 24px; border:none; border-radius:6px; font-size:16px; font-weight:bold; cursor:pointer;">Login with Fyers</button></a>',
        unsafe_allow_html=True
    )
    st.stop()

token = st.session_state.fyers_access_token
fyers = fyersModel.FyersModel(client_id=CLIENT_ID, is_async=False, token=token, log_path="")

# 3. EXACT VWAP & PDVWAP CALCULATION ENGINE
def get_vwap_baselines(symbol, resolution="3"):
    try:
        today = datetime.date.today()
        from_date = (today - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")

        data = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": from_date,
            "range_to": to_date,
            "cont_flag": "1",
        }
        res = fyers.history(data=data)
        if res.get("s") == "success" and "candles" in res:
            df = pd.DataFrame(res["candles"], columns=["epoch", "open", "high", "low", "close", "volume"])
            df["datetime"] = pd.to_datetime(df["epoch"], unit="s")
            df["date"] = df["datetime"].dt.date
            
            # --- Intraday Live VWAP (Current Day) ---
            today_df = df[df["date"] == today]
            if not today_df.empty:
                tp = (today_df["high"] + today_df["low"] + today_df["close"]) / 3
                cum_vol = today_df["volume"].cumsum()
                cum_tp_vol = (tp * today_df["volume"]).cumsum()
                
                intraday_vwap = (cum_tp_vol / cum_vol).iloc[-1] if cum_vol.iloc[-1] > 0 else today_df["close"].iloc[-1]
                current_price = today_df["close"].iloc[-1]
            else:
                intraday_vwap, current_price = 0, 0

            # --- Previous Day VWAP (PDVWAP) ---
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

            return current_price, intraday_vwap, pdvwap
    except Exception as e:
        pass
    return 0, 0, 0

# 4. DYNAMIC OPEN PRICE & ATM STRIKE SELECTION
@st.cache_data(ttl=300)
def get_dynamic_symbols():
    q_res = fyers.quotes(data={"symbols": "NSE:NIFTY50-INDEX"})
    open_price, ltp_spot = 24700, 24700 
    if q_res.get("s") == "ok" and "d" in q_res and len(q_res["d"]) > 0:
        v = q_res["d"][0]["v"]
        open_price = v.get("open_price", v.get("lp", 24700))
        ltp_spot = v.get("lp", 24700)

    atm_strike = int(round(open_price / 50) * 50)
    
    today = datetime.date.today()
    days_to_thu = (3 - today.weekday()) % 7
    expiry_date = today + datetime.timedelta(days=days_to_thu)
    expiry_str = expiry_date.strftime("%y%b").upper()

    ce_sym = f"NSE:NIFTY{expiry_str}{atm_strike}CE"
    pe_sym = f"NSE:NIFTY{expiry_str}{atm_strike}PE"
    return atm_strike, ce_sym, pe_sym, ltp_spot, open_price

atm_strike, ce_symbol, pe_symbol, ltp_spot, open_price = get_dynamic_symbols()

st.sidebar.subheader("🎯 Market Setup")
st.sidebar.write(f"**Nifty Open Price:** {open_price}")
st.sidebar.write(f"**ATM Strike:** {atm_strike}")
st.sidebar.write(f"**CE Symbol:** {ce_symbol.replace('NSE:', '')}")
st.sidebar.write(f"**PE Symbol:** {pe_symbol.replace('NSE:', '')}")

# 5. FETCH LIVE PRICES & BASELINES
spot_price, spot_intra_vwap, spot_pdvwap = get_vwap_baselines("NSE:NIFTY50-INDEX")
ce_price, ce_intra_vwap, ce_pdvwap = get_vwap_baselines(ce_symbol)
pe_price, pe_intra_vwap, pe_pdvwap = get_vwap_baselines(pe_symbol)

if spot_price == 0:
    spot_price = ltp_spot

# 6. CONDITION EVALUATION ENGINE
bull_bias = (spot_price > spot_pdvwap) and (spot_price > spot_intra_vwap)
bear_bias = (spot_price < spot_pdvwap) and (spot_price < spot_intra_vwap)

ce_strong_buyer = (ce_price > ce_pdvwap) and (ce_price > ce_intra_vwap)
ce_strong_seller = (ce_price < ce_pdvwap) and (ce_price < ce_intra_vwap)

pe_strong_buyer = (pe_price > pe_pdvwap) and (pe_price > pe_intra_vwap)
pe_strong_seller = (pe_price < pe_pdvwap) and (pe_price < pe_intra_vwap)

# --- DASHBOARD UI ---
col1, col2, col3 = st.columns(3)
col1.metric("Nifty Spot Price", f"₹{spot_price}", f"Open: {open_price}")
col2.metric(f"ATM CE ({atm_strike})", f"₹{ce_price}")
col3.metric(f"ATM PE ({atm_strike})", f"₹{pe_price}")

st.markdown("---")
st.subheader("📊 Multi-Confirmation Status (Auto 3-Min Refresh)")

matrix_data = [
    {
        "Component": "Nifty Spot",
        "Price": spot_price,
        "Session Baseline (PDVWAP)": round(spot_pdvwap, 2),
        "Intraday Baseline (VWAP)": round(spot_intra_vwap, 2),
        "Bullish Status": "🟢 Above Both (Bull Bias)" if bull_bias else "⚪ Waiting",
        "Bearish Status": "🔴 Below Both (Bear Bias)" if bear_bias else "⚪ Waiting",
    },
    {
        "Component": "CE Option",
        "Price": ce_price,
        "Session Baseline (PDVWAP)": round(ce_pdvwap, 2),
        "Intraday Baseline (VWAP)": round(ce_intra_vwap, 2),
        "Bullish Status": "🟢 CE Strong Buyer" if ce_strong_buyer else ("🟡 Top 5 Met / Waiting 6th" if ce_price > ce_pdvwap else "⚪ Not Aligned"),
        "Bearish Status": "🔴 CE Strong Seller" if ce_strong_seller else "⚪ Not Aligned",
    },
    {
        "Component": "PE Option",
        "Price": pe_price,
        "Session Baseline (PDVWAP)": round(pe_pdvwap, 2),
        "Intraday Baseline (VWAP)": round(pe_intra_vwap, 2),
        "Bullish Status": "🟢 PE Strong Seller" if pe_strong_seller else ("🟡 Top 5 Met / Waiting 6th" if pe_price < pe_pdvwap else "⚪ Not Aligned"),
        "Bearish Status": "🔴 PE Strong Buyer" if pe_strong_buyer else "⚪ Not Aligned",
    },
]
st.dataframe(pd.DataFrame(matrix_data), use_container_width=True)

st.markdown("---")
st.subheader("🚨 Final Trade Signals")

ce_buy_trade = bull_bias and pe_strong_seller and ce_strong_buyer
pe_buy_trade = bear_bias and ce_strong_seller and pe_strong_buyer

col_sig1, col_sig2 = st.columns(2)
with col_sig1:
    if ce_buy_trade:
        st.success("🚀 **CE BUY TRADE TRIGGERED!**\n\n- Spot > Both Baselines\n- PE < Both Baselines (Strong Seller)\n- CE > Both Baselines (Strong Buyer)")
    elif bull_bias and pe_strong_seller and (ce_price > ce_pdvwap):
        st.warning("⏳ **CE BUY: Top 5 Conditions Locked!** Waiting for 6th condition (CE > Intraday VWAP)...")
    else:
        st.info("⚪ CE Buy Trade: Waiting for market alignment...")

with col_sig2:
    if pe_buy_trade:
        st.error("📉 **PE BUY TRADE TRIGGERED!**\n\n- Spot < Both Baselines\n- CE < Both Baselines (Strong Seller)\n- PE > Both Baselines (Strong Buyer)")
    elif bear_bias and ce_strong_seller and (pe_price > pe_pdvwap):
        st.warning("⏳ **PE BUY: Top 5 Conditions Locked!** Waiting for 6th condition (PE > Intraday VWAP)...")
    else:
        st.info("⚪ PE Buy Trade: Waiting for market alignment...")
