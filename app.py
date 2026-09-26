import datetime
import hashlib
import json
import pandas as pd
import requests
import streamlit as st
from fyers_apiv3 import fyersModel
from streamlit_autorefresh import st_autorefresh

# Streamlit Page Config
st.set_page_config(
    page_title="Nifty A+ Sniper Dashboard", page_icon="⚡", layout="wide"
)

# Auto-refresh every 3 minutes (180,000 milliseconds) for hands-free automation
st_autorefresh(interval=180000, key="nifty_sniper_refresh")

st.title("⚡ Nifty A+ Multi-Confirmation Sniper Dashboard")

# 1. Fetch Secrets from Streamlit
try:
  CLIENT_ID = st.secrets["FYERS_CLIENT_ID"]
  SECRET_KEY = st.secrets["FYERS_SECRET_KEY"]
  REDIRECT_URI = st.secrets["FYERS_REDIRECT_URI"]
  FY_ID = st.secrets["FYERS_FY_ID"]
  PIN = st.secrets["FYERS_PIN"]
  TOTP_KEY = st.secrets["FYERS_TOTP_KEY"]
except Exception as e:
  st.error(
      "⚠️ Secrets config missing! Please configure Streamlit Secrets in Settings."
  )
  st.stop()

# 2. Authentication & Token Management
token = None
login_message = ""

st.sidebar.header("🔑 Authentication Setup")
manual_token_input = st.sidebar.text_input(
    "Paste Access Token (Cloud Bypass)",
    type="password",
    help="Fyers access token yahan paste karein.",
)

if manual_token_input:
  token = manual_token_input
  login_message = "Connected via Manual Token"
else:
  try:
    import pyotp

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://trade.fyers.in",
        "Referer": "https://trade.fyers.in/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
    }
    totp = pyotp.TOTP(TOTP_KEY).now()

    res1 = requests.post(
        "https://api-t2.fyers.in/vagator/v2/send_login_otp_v2",
        json={"fy_id": FY_ID, "app_id": "2"},
        headers=headers,
    )
    data1 = res1.json()
    if data1.get("s") == "ok" or data1.get("code") == 200:
      request_key = data1["request_key"]
      res2 = requests.post(
          "https://api-t2.fyers.in/vagator/v2/verify_otp",
          json={"request_key": request_key, "otp": totp},
          headers=headers,
      )
      data2 = res2.json()
      if data2.get("s") == "ok" or data2.get("code") == 200:
        request_key = data2["request_key"]
        res3 = requests.post(
            "https://api-t2.fyers.in/vagator/v2/verify_pin_v2",
            json={
                "request_key": request_key,
                "identity_type": "pin",
                "identifier": str(PIN),
            },
            headers=headers,
        )
        data3 = res3.json()
        if data3.get("s") == "ok" or data3.get("code") == 200:
          access_token_auth = data3["data"]["access_token"]
          app_id_type = (
              CLIENT_ID.split("-")[0] if "-" in CLIENT_ID else CLIENT_ID
          )
          app_id_hash = hashlib.sha256(
              f"{CLIENT_ID}:{SECRET_KEY}".encode()
          ).hexdigest()
          headers_step4 = headers.copy()
          headers_step4["Authorization"] = f"Bearer {access_token_auth}"

          res4 = requests.post(
              "https://api-t1.fyers.in/api/v3/generate-authcode",
              json={
                  "fyers_id": FY_ID,
                  "app_id": app_id_type,
                  "redirect_uri": REDIRECT_URI,
                  "app_id_hash": app_id_hash,
                  "code_challenge": "",
                  "state": "sample_state",
                  "scope": "",
                  "nonce": "",
                  "response_type": "code",
                  "create_cookie": True,
              },
              headers=headers_step4,
          )
          data4 = res4.json()
          if "auth_code" in data4:
            session = fyersModel.SessionModel(
                client_id=CLIENT_ID,
                secret_key=SECRET_KEY,
                redirect_uri=REDIRECT_URI,
                response_type="code",
                grant_type="authorization_code",
            )
            session.set_token(data4["auth_code"])
            response = session.generate_token()
            if "access_token" in response:
              token = response["access_token"]
              login_message = "Automated Cloud Login Successful!"
  except Exception as e:
    pass

if not token:
  st.warning(
      "⚠️ **Cloudflare Block:** Fyers ne automated login rok diya hai. Sidebar"
      " me apna Access Token paste karein."
  )
  st.stop()

st.success(f"✅ {login_message}")
fyers = fyersModel.FyersModel(
    client_id=CLIENT_ID, is_async=False, token=token, log_path=""
)


# Helper: Calculate Baselines (Intraday Baseline & Session Baseline) from History
def get_baselines(symbol, resolution="3"):
  try:
    # Get today's date range
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
      df = pd.DataFrame(
          res["candles"], columns=["epoch", "open", "high", "low", "close", "volume"]
      )
      df["datetime"] = pd.to_datetime(df["epoch"], unit="s")
      df["date"] = df["datetime"].dt.date

      # Intraday Baseline (VWAP of Current Day)
      today_df = df[df["date"] == today]
      if not today_df.empty:
        # Typical price * volume cumulative sum / cumulative volume
        tp = (
            (today_df["high"] + today_df["low"] + today_df["close"])
            / 3
            * today_df["volume"]
        )
        cum_tp = tp.cumsum()
        cum_vol = today_df["volume"].cumsum()
        intraday_baseline = (
            (cum_tp / cum_vol).iloc[-1]
            if cum_vol.iloc[-1] > 0
            else today_df["close"].iloc[-1]
        )
        current_price = today_df["close"].iloc[-1]
      else:
        intraday_baseline = 0
        current_price = 0

      # Session Baseline (Previous Day VWAP / Close reference)
      past_days = df[df["date"] < today]
      if not past_days.empty:
        last_date = past_days["date"].max()
        last_day_df = past_days[past_days["date"] == last_date]
        tp_prev = (
            (last_day_df["high"] + last_day_df["low"] + last_day_df["close"])
            / 3
            * last_day_df["volume"]
        )
        session_baseline = (
            tp_prev.sum() / last_day_df["volume"].sum()
            if last_day_df["volume"].sum() > 0
            else last_day_df["close"].iloc[-1]
        )
      else:
        session_baseline = current_price

      return current_price, intraday_baseline, session_baseline
  except Exception as e:
    pass
  return 0, 0, 0


# 3. Automatic Expiry & Strike Detection based on Nifty Open Price
@st.cache_data(ttl=300)
def get_dynamic_symbols():
  # Fetch Nifty Spot Quote to get Open Price
  q_res = fyers.quotes(data={"symbols": "NSE:NIFTY50-INDEX"})
  open_price = 24700  # Fallback
  ltp_spot = 24700
  if q_res.get("s") == "ok" and "d" in q_res and len(q_res["d"]) > 0:
    v = q_res["d"][0]["v"]
    open_price = v.get("open_price", v.get("lp", 24700))
    ltp_spot = v.get("lp", 24700)

  atm_strike = int(round(open_price / 50) * 50)

  # Determine current weekly expiry format (e.g., Nifty weekly expiry formatting)
  # Fyers symbol format for current weekly expiry can be computed or queried.
  # Using standard current month/week approximation or dynamic symbol builder:
  today = datetime.date.today()
  # Find coming Thursday
  days_to_thu = (3 - today.weekday()) % 7
  expiry_date = today + datetime.timedelta(days=days_to_thu)
  expiry_str = expiry_date.strftime("%y%b").upper()  # e.g., 26OCT or similar

  ce_sym = f"NSE:NIFTY{expiry_str}{atm_strike}CE"
  pe_sym = f"NSE:NIFTY{expiry_str}{atm_strike}PE"

  return atm_strike, ce_sym, pe_sym, ltp_spot, open_price


atm_strike, ce_symbol, pe_symbol, ltp_spot, open_price = get_dynamic_symbols()

st.sidebar.subheader("🎯 Auto Detected Setup")
st.sidebar.write(f"**ATM Strike:** {atm_strike}")
st.sidebar.write(f"**CE Symbol:** {ce_symbol}")
st.sidebar.write(f"**PE Symbol:** {pe_symbol}")

# 4. Fetch Prices & Baselines for Spot, CE, PE
spot_price, spot_intra, spot_sess = get_baselines("NSE:NIFTY50-INDEX")
ce_price, ce_intra, ce_sess = get_baselines(ce_symbol)
pe_price, pe_intra, pe_sess = get_baselines(pe_symbol)

# Fallback if live price is 0
if spot_price == 0:
  spot_price = ltp_spot

# 5. Condition Engine Evaluation
# Spot Conditions
spot_old_bullish = spot_price > spot_sess
spot_intra_bullish = spot_price > spot_intra
spot_old_bearish = spot_price < spot_sess
spot_intra_bearish = spot_price < spot_intra

a_plus_bull_bias = spot_old_bullish and spot_intra_bullish
a_plus_bear_bias = spot_old_bearish and spot_intra_bearish

# CE Conditions
ce_old_buyer = ce_price > ce_sess
ce_new_buyer = ce_price > ce_intra
ce_old_seller = ce_price < ce_sess
ce_new_seller = ce_price < ce_intra

a_plus_ce_buyer = ce_old_buyer and ce_new_buyer
a_plus_ce_seller = ce_old_seller and ce_new_seller

# PE Conditions
pe_old_buyer = pe_price > pe_sess
pe_new_buyer = pe_price > pe_intra
pe_old_seller = pe_price < pe_sess
pe_new_seller = pe_price < pe_intra

a_plus_pe_buyer = pe_old_buyer and pe_new_buyer
a_plus_pe_seller = pe_old_seller and pe_new_seller


# --- DASHBOARD UI ---
col1, col2, col3 = st.columns(3)
col1.metric("Nifty Spot Price", f"₹{spot_price}", f"Open: {open_price}")
col2.metric("ATM CE Strike", ce_symbol.split("NIFTY")[1], f"₹{ce_price}")
col3.metric("ATM PE Strike", pe_symbol.split("NIFTY")[1], f"₹{pe_price}")

st.markdown("---")
st.subheader("📊 Multi-Confirmation Condition Status (3-Min Interval)")

# Display Condition Matrix Table
matrix_data = [
    {
        "Component": "Nifty Spot",
        "Price": spot_price,
        "Session Baseline": round(spot_sess, 2),
        "Intraday Baseline": round(spot_intra, 2),
        "Bullish Status": (
            "🟢 Above Both (A+ Bull Bias)"
            if a_plus_bull_bias
            else "⚪ Waiting"
        ),
        "Bearish Status": (
            "🔴 Below Both (A+ Bear Bias)"
            if a_plus_bear_bias
            else "⚪ Waiting"
        ),
    },
    {
        "Component": "CE Option",
        "Price": ce_price,
        "Session Baseline": round(ce_sess, 2),
        "Intraday Baseline": round(ce_intra, 2),
        "Bullish Status": (
            "🟢 A+ CE Buyer (Above Both)"
            if a_plus_ce_buyer
            else (
                "🟡 Top 5 Met / Waiting 6th"
                if ce_old_buyer
                else "⚪ Not Aligned"
            )
        ),
        "Bearish Status": (
            "🔴 A+ CE Seller (Below Both)"
            if a_plus_ce_seller
            else "⚪ Not Aligned"
        ),
    },
    {
        "Component": "PE Option",
        "Price": pe_price,
        "Session Baseline": round(pe_sess, 2),
        "Intraday Baseline": round(pe_intra, 2),
        "Bullish Status": (
            "🟢 A+ PE Seller (Below Both)"
            if a_plus_pe_seller
            else (
                "🟡 Top 5 Met / Waiting 6th"
                if pe_old_seller
                else "⚪ Not Aligned"
            )
        ),
        "Bearish Status": (
            "🔴 A+ PE Buyer (Above Both)"
            if a_plus_pe_buyer
            else "⚪ Not Aligned"
        ),
    },
]

st.dataframe(pd.DataFrame(matrix_data), use_container_width=True)

st.markdown("---")
st.subheader("🚨 Final Trade Signals")

# Final Trade Trigger Logic with Smart Wait (Top 5 lock + 6th wait handling)
ce_buy_trade = a_plus_bull_bias and a_plus_pe_seller and a_plus_ce_buyer
pe_buy_trade = a_plus_bear_bias and a_plus_ce_seller and a_plus_pe_buyer

col_sig1, col_sig2 = st.columns(2)

with col_sig1:
  if ce_buy_trade:
    st.success(
        "🚀 **A+ CE BUY TRADE TRIGGERED!**\n\n- Spot is Above Both Baselines\n-"
        " PE is Below Both Baselines (Strong Seller)\n- CE is Above Both"
        " Baselines (Strong Buyer)"
    )
  else:
    # Check partial condition for smart waiting alert
    if a_plus_bull_bias and a_plus_pe_seller and ce_old_buyer:
      st.warning(
          "⏳ **CE BUY: Top 5 Conditions Locked!** Waiting for 6th condition"
          " (CE price above Intraday Baseline) on upcoming candles..."
      )
    else:
      st.info("⚪ CE Buy Trade: Waiting for market alignment...")

with col_sig2:
  if pe_buy_trade:
    st.error(
        "📉 **A+ PE BUY TRADE TRIGGERED!**\n\n- Spot is Below Both Baselines\n-"
        " CE is Below Both Baselines (Strong Seller)\n- PE is Above Both"
        " Baselines (Strong Buyer)"
    )
  else:
    if a_plus_bear_bias and a_plus_ce_seller and pe_old_buyer:
      st.warning(
          "⏳ **PE BUY: Top 5 Conditions Locked!** Waiting for 6th condition"
          " (PE price above Intraday Baseline) on upcoming candles..."
      )
    else:
      st.info("⚪ PE Buy Trade: Waiting for market alignment...")
