import hashlib
import json
import pandas as pd
import pyotp
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
  CLIENT_ID = st.secrets["7VPVG6SDK8-100"]  # Example: "XX12345-100"
  SECRET_KEY = st.secrets["FWPRTCV2S2"]  # App Secret
  REDIRECT_URI = st.secrets["https://127.0.0.1"]  # "https://127.0.0.1"
  FY_ID = st.secrets["XS39623"]  # Your Fyers User ID (e.g. "XX12345")
  PIN = st.secrets["2112"]  # Your 4 Digit User PIN
  TOTP_KEY = st.secrets["NKFQBHN5K4RSNOM5LZP4NW7AJ23KSBAN"]  # 32 Character Secret TOTP Key
except Exception as e:
  st.error(
      "⚠️ Secrets config missing! Please configure Streamlit Secrets in Settings."
  )
  st.stop()


# 2. Automated Headless Login Function (Cloud Compatible)
@st.cache_resource(ttl=14400)  # Cache Token for 4 Hours
def get_fyers_access_token():
  try:
    totp = pyotp.TOTP(TOTP_KEY).now()

    # Step 1: Send Login OTP
    url_send_otp = "https://api-t1.fyers.in/api/v3/send-login-otp"
    res1 = requests.post(
        url_send_otp, json={"fy_id": FY_ID, "app_id": "2"}
    ).json()
    if res1.get("s") != "ok":
      return None, f"Step 1 Failed: {res1.get('message', 'OTP Send Error')}"

    request_key = res1["request_key"]

    # Step 2: Verify OTP
    url_verify_otp = "https://api-t1.fyers.in/api/v3/verify-otp"
    res2 = requests.post(
        url_verify_otp, json={"request_key": request_key, "otp": totp}
    ).json()
    if res2.get("s") != "ok":
      return None, f"Step 2 Failed: {res2.get('message', 'OTP Verification Error')}"

    request_key = res2["request_key"]

    # Step 3: Verify PIN
    url_verify_pin = "https://api-t1.fyers.in/api/v3/verify-pin"
    res3 = requests.post(
        url_verify_pin,
        json={
            "request_key": request_key,
            "pin": str(PIN),
            "identity_type": "pin",
        },
    ).json()
    if res3.get("s") != "ok":
      return None, f"Step 3 Failed: {res3.get('message', 'PIN Verification Error')}"

    access_token_auth = res3["data"]["access_token"]

    # Step 4: Generate Auth Code
    app_id_type = CLIENT_ID.split("-")[0] if "-" in CLIENT_ID else CLIENT_ID
    app_id_hash = hashlib.sha256(
        f"{CLIENT_ID}:{SECRET_KEY}".encode()
    ).hexdigest()

    url_token = "https://api-t1.fyers.in/api/v3/generate-authcode"
    payload_token = {
        "fyers_id": FY_ID,
        "app_id": app_id_type,
        "redirect_uri": REDIRECT_URI,
        "app_id_hash": app_id_hash,
        "code_challenge": "",
        "state": "sample_state",
    }
    headers = {"Authorization": f"{FY_ID}:{access_token_auth}"}

    res4 = requests.post(
        url_token, json=payload_token, headers=headers
    ).json()
    if "auth_code" not in res4:
      return None, (
          f"Step 4 Failed: {res4.get('message', 'Auth Code Generation Error')}"
      )

    auth_code = res4["auth_code"]

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
      return response["access_token"], "Success"
    else:
      return None, f"Token Error: {response}"

  except Exception as e:
    return None, f"Exception Error: {str(e)}"


# Login Execution
token, status = get_fyers_access_token()

if not token:
  st.error(f"❌ Login Failed: {status}")
  st.info("💡 Secrets check karein: Client ID, Secret, PIN aur TOTP Key.")
  st.stop()

st.success("✅ Fyers API Connected Successfully!")

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
st.sidebar.header("⚙️ Option Settings")
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
