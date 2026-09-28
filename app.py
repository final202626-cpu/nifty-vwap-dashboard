import datetime
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
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
    REDIRECT_URI = st.secrets.get("FYERS_REDIRECT_URI", "") # Add this in your streamlit secrets
except Exception as e:
    st.error("⚠️ Secrets config missing! Please configure CLIENT_ID, SECRET_KEY, and FYERS_REDIRECT_URI.")
    st.stop()

# 2. MAIN SCREEN TOKEN INPUT & AUTO-CONVERTER
st.markdown("### 🔐 Fyers Authentication")
access_token_input = st.text_input("Enter Auth Code (from URL) OR Access Token", type="password", placeholder="Paste code here...")

if not access_token_input:
    st.warning("🔒 Please enter your Fyers Code above to load the live trading dashboard.")
    st.stop()

# Auto-Convert Auth Code to Access Token
if len(access_token_input) < 100:  
    st.info("🔄 Short Auth Code detected! Converting to Access Token...")
    if not REDIRECT_URI:
        st.error("❌ FYERS_REDIRECT_URI is not set in Streamlit Secrets. Cannot convert Auth Code!")
        st.stop()
    
    session = fyersModel.SessionModel(
        client_id=CLIENT_ID,
        secret_key=SECRET_KEY,
        redirect_uri=REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code"
    )
    session.set_token(access_token_input)
    response = session.generate_token()
    
    if response.get("s") == "ok":
        token = response["access_token"]
        st.success("✅ Token Generated Successfully! Dashboard is going LIVE...")
    else:
        st.error(f"❌ Token Conversion Failed! Fyers Error: {response}")
        st.stop()
else:
    token = access_token_input

fyers = fyersModel.FyersModel(client_id=CLIENT_ID, is_async=False, token=token, log_path="")

# Auto-refresh
refresh_interval = 180000
components.html(f"<script>setTimeout(function() {{ window.location.reload(); }}, {refresh_interval});</script>", height=0, width=0)

# 3. EXACT VWAP ENGINE
def get_vwap_baselines_and_candle(symbol, resolution="3"):
    try:
        today = datetime.date.today()
        from_date = (today - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")

        data = {"symbol": symbol, "resolution": resolution, "date_format": "1", "range_from": from_date, "range_to": to_date, "cont_flag": "1"}
        res = fyers.history(data=data)
        
        if res.get("s") == "error":
            st.error(f"🚨 API Error for {symbol}: {res.get('message')}")
            
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

# 4. DYNAMIC OPEN PRICE (No Caching to prevent stuck dummy values)
def get_dynamic_symbols():
    q_res = fyers.quotes(data={"symbols": "NSE:NIFTY50-INDEX"})
    if q_res.get("s") != "ok":
        st.error(f"🚨 Fyers Quote API Error: {q_res.get('message', q_res)}")
        st.stop()
        
    open_price, ltp_spot = 24700, 24700 
    if "d" in q_res and len(q_res["d"]) > 0:
        v = q_res["d"][0]["v"]
        open_price = v.get("open_price", v.get("lp", 24700))
        ltp_spot = v.get("lp", 24700)

    atm_strike = int(round(open_price / 50) * 50)
    today = datetime.date.today()
    
    # Expiry calculation logic for Nifty
    days_to_tue = (1 - today.weekday()) % 7
    expiry_date = today + datetime.timedelta(days=days_to_tue)
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

# 5. FETCH LIVE PRICES
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
