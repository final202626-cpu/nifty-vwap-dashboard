import hashlib
import json
import pandas as pd
import requests
import streamlit as st
from fyers_apiv3 import fyersModel

# Streamlit Page Config
st.set_page_config(
    page_title="Nifty Live VWAP Dashboard", page_icon="📈", layout="wide"
)

st.title("📈 Nifty Live VWAP & Option Scanner (Fyers Cloud Mode)")

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

# 2. Token Management (Automated Attempt + Manual Fallback for Cloud IPs)
token = None
login_message = ""

# Sidebar for Manual Token Option (Agar Cloudflare block kare toh yahan paste kar sakte hain)
st.sidebar.header("🔑 Authentication Setup")
manual_token_input = st.sidebar.text_input(
    "Or Paste Access Token Directly",
    type="password",
    help=(
        "Agar automated cloud login block ho jaye, toh Fyers ka access token"
        " yahan daal dein."
    ),
)

if manual_token_input:
  token = manual_token_input
  login_message = "Connected via Manual Token (Cloud Bypass)"
else:
  # Try Automated Headless Login
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

    # Step 1: Send OTP
    res1 = requests.post(
        "https://api-t2.fyers.in/vagator/v2/send_login_otp_v2",
        json={"fy_id": FY_ID, "app_id": "2"},
        headers=headers,
    )
    data1 = res1.json()

    if data1.get("s") == "ok" or data1.get("code") == 200:
      request_key = data1["request_key"]

      # Step 2: Verify OTP
      res2 = requests.post(
          "https://api-t2.fyers.in/vagator/v2/verify_otp",
          json={"request_key": request_key, "otp": totp},
          headers=headers,
      )
      data2 = res2.json()

      if data2.get("s") == "ok" or data2.get("code") == 200:
        request_key = data2["request_key"]

        # Step 3: Verify PIN
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

          # Step 4: Generate Auth Code
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
            auth_code = data4["auth_code"]

            # Step 5: SDK Session Token
            session = fyersModel.SessionModel(
                client_id=CLIENT_ID,
                secret_key=SECRET_KEY,
                redirect_uri=REDIRECT_URI,
                response_type="code",
                grant_type="authorization_code",
            )
            session.set_token(auth_code)
            response = session.generate_token()

            if "access_token" in response:
              token = response["access_token"]
              login_message = "Automated Cloud Login Successful!"
  except Exception as e:
    pass  # Fallback will handle it gracefully

# Check Token Status
if not token:
  st.warning(
      "⚠️ **Cloudflare Firewall Restriction:** Fyers ne cloud server (US IP)"
      " se automated OTP login block kar diya hai."
  )
  st.info(
      "💡 **Solution:** Aap apne Fyers app ya web se ek Access Token generate"
      " karke left sidebar me **'Or Paste Access Token Directly'** wale box me"
      " daal dein. Dashboard turant live ho jayega!"
  )
  st.stop()

st.success(f"✅ {login_message}")

fyers = fyersModel.FyersModel(
    client_id=CLIENT_ID, is_async=False, token=token, log_path=""
)


# 3. Fetch Nifty Open & Calculate ATM
@st.cache_data(ttl=60)
def fetch_nifty_data():
  data = {"symbols": "NSE:NIFTY50-INDEX"}
  res = fyers.quotes(data=data)
  if res.get("s") == "ok" and "d" in res and len(res["d"]) > 0:
    q_data = res["d"][0]["v"]
    open_p = q_data.get("open_price", 0)
    ltp_p = q_data.get("lp", 0)
    return open_p, ltp_p
  return 0, 0


open_price, ltp_nifty = fetch_nifty_data()

if open_price == 0:
  st.warning("⚠️ Market Data unavailable or Market Closed.")
  st.stop()

atm_strike = int(round(open_price / 50) * 50)

# Dashboard Top Metrics
col1, col2, col3 = st.columns(3)
col1.metric("Nifty 50 Open Price", f"₹{open_price}")
col2.metric("Nifty 50 Current Price", f"₹{ltp_nifty}")
col3.metric("Calculated ATM Strike", f"{atm_strike}")

st.markdown("---")

# 4. Options Expiry Settings
expiry_string = st.sidebar.text_input(
    "Option Expiry Format",
    value="24OCT",
    help="Example: 24OCT for October 2024 expiry",
)

ce_symbol = f"NSE:NIFTY{expiry_string}{atm_strike}CE"
pe_symbol = f"NSE:NIFTY{expiry_string}{atm_strike}PE"

st.subheader(f"📊 Live Options VWAP Status for ATM: {atm_strike}")


# 5. Fetch Options Quotes and Check VWAP
def fetch_options_vwap(symbols):
  data = {"symbols": ",".join(symbols)}
  res = fyers.quotes(data=data)
  out = []
  if res.get("s") == "ok" and "d" in res:
    for item in res["d"]:
      v = item["v"]
      sym = item["n"]
      lp = v.get("lp", 0)  # LTP
      vwap = v.get(
          "average_price", v.get("prev_close_price", 0)
      )  # Average Price / VWAP
      signal = "🟢 BUY (LTP > VWAP)" if lp > vwap else "🔴 WAIT (LTP <= VWAP)"
      out.append({
          "Symbol": sym,
          "LTP (₹)": lp,
          "VWAP (₹)": vwap,
          "Signal Status": signal,
          "Volume": v.get("volume", 0),
      })
  return pd.DataFrame(out)


df_opts = fetch_options_vwap([ce_symbol, pe_symbol])

if not df_opts.empty:
  st.dataframe(df_opts, use_container_width=True)
else:
  st.info("Option Quotes not found. Check Expiry Format in sidebar.")

if st.button("🔄 Refresh Data Now"):
  st.rerun()
