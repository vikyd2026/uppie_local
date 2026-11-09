import pymongo
import requests
import os
from datetime import date, datetime, timedelta
from urllib.parse import quote
import Constants
import certifi



# Current date and time labels
now = datetime.now()



BASE_URL = "https://api.upstox.com"

UNDERLYING = "NSE_INDEX|Nifty 50"

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {Constants.ACCESS_TOKEN}"
}

session = requests.Session()
session.headers.update(HEADERS)

client = pymongo.MongoClient(Constants.mongo_conn, tlsCAFile=certifi.where())
db = client.get_database('uppie')
collection_dashboard_data = db["raw_data"]
collection_nifty_data = db["nifty_data"]


def normalize_ohlc(ohlc_payload):
    if not isinstance(ohlc_payload, dict):
        return {}

    return {
        "open": ohlc_payload.get("open"),
        "high": ohlc_payload.get("high"),
        "low": ohlc_payload.get("low"),
        "close": ohlc_payload.get("close"),
    }


def normalize_candle_ohlc(raw_candle):
    if isinstance(raw_candle, dict):
        return {
            "open": raw_candle.get("open"),
            "high": raw_candle.get("high"),
            "low": raw_candle.get("low"),
            "close": raw_candle.get("close"),
        }

    if isinstance(raw_candle, (list, tuple)) and len(raw_candle) >= 5:
        return {
            "open": raw_candle[1],
            "high": raw_candle[2],
            "low": raw_candle[3],
            "close": raw_candle[4],
        }

    return {}


def fetch_ohlc_for_instrument(instrument_key):
    instrument_key_key = instrument_key.replace("|", ":")

    try:
        response = api_get(
            "/v3/market-quote/ohlc",
            {
                "instrument_key": instrument_key
            }
        )
        if isinstance(response, dict):
            payload = response.get(instrument_key_key) or response.get("data") or response
            if isinstance(payload, dict):
                ohlc = payload.get("ohlc")
                if isinstance(ohlc, dict):
                    return normalize_ohlc(ohlc)
            return normalize_ohlc(payload)
    except Exception as exc:
        if not getattr(getattr(exc, "response", None), "status_code", None) == 400:
            raise

    try:
        candle_window_from = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
        candle_window_to = datetime.now().strftime("%Y-%m-%dT%H:%M")
        response = api_get(
            f"/v3/historical-candle/{quote(instrument_key, safe='')}/1minute",
            {
                "from": candle_window_from,
                "to": candle_window_to,
            }
        )
        payload = response.get("data", response)
        candles = payload.get("candles", []) if isinstance(payload, dict) else payload
        if not candles:
            return {}
        latest_candle = candles[-1]
        return normalize_candle_ohlc(latest_candle)
    except Exception:
        return {}


def api_get(endpoint, params=None):
    response = session.get(
        f"{BASE_URL}{endpoint}",
        params=params,
        timeout=15
    )

    response.raise_for_status()

    result = response.json()

    if result.get("status") != "success":
        raise Exception(f"Upstox API error: {result}")

    return result["data"]

# --------------------------------
# 1. NIFTY AND INDIA VIX
# --------------------------------

print("\n========== MARKET DATA (NEW RECORD FETCH ACTIVITY) ==========")

data_label = now.strftime("%H:%M")
date_label = now.strftime("%d-%m-%Y")

print("Data Label:", data_label)
print("Date Label:", date_label)

market_data = api_get(
    "/v3/market-quote/ltp",
    {
        "instrument_key":
            "NSE_INDEX|Nifty 50,NSE_INDEX|India VIX"
    }
)

nifty = market_data["NSE_INDEX:Nifty 50"]
vix = market_data["NSE_INDEX:India VIX"]

print(f"NIFTY LTP     :: {nifty['last_price']}")
print(f"NIFTY CHANGE  : {nifty['last_price'] - nifty['cp']:.2f}")

print(f"INDIA VIX     : {vix['last_price']}")
print(f"VIX CHANGE    : {vix['last_price'] - vix['cp']:.2f}")

# --------------------------------
# 2. GET OHLC FOR NIFTY AND VIX
# --------------------------------

ohlc_market_data = {}
for instrument_key in ["NSE_INDEX|Nifty 50", "NSE_INDEX|India VIX"]:
    ohlc_market_data[instrument_key.replace("|", ":")] = fetch_ohlc_for_instrument(instrument_key)

nifty_ohlc = ohlc_market_data.get("NSE_INDEX:Nifty 50", {})
vix_ohlc = ohlc_market_data.get("NSE_INDEX:India VIX", {})

nifty_snapshot = {
    "date_label": date_label,
    "time_label": data_label,
    "timestamp": now.isoformat(timespec="seconds"),
    "instrument": "NSE_INDEX|Nifty 50",
    "nifty_ltp": nifty.get("last_price"),
    "nifty_value": nifty.get("last_price"),
    "nifty_ohlc": nifty_ohlc,
    "nifty_change": nifty.get("last_price") - nifty.get("cp", 0),
    "vix_ltp": vix.get("last_price"),
    "vix_value": vix.get("last_price"),
    "vix_ohlc": vix_ohlc,
    "vix_change": vix.get("last_price") - vix.get("cp", 0),
    "source": "upstox"
}

inserted_snapshot = collection_nifty_data.insert_one(nifty_snapshot)
print(f"Inserted nifty snapshot into nifty_data with id: {inserted_snapshot.inserted_id}")

# --------------------------------
# 3. GET AVAILABLE OPTION CONTRACTS
# --------------------------------

print("\n========== OPTION EXPIRY ==========")

contracts = api_get(
    "/v2/option/contract",
    {
        "instrument_key": UNDERLYING
    }
)

today = date.today()

future_expiries = sorted({
    contract["expiry"]
    for contract in contracts
    if date.fromisoformat(contract["expiry"]) >= today
})

if not future_expiries:
    raise Exception("No upcoming NIFTY expiry found")

expiry_date = future_expiries[0]

print("Selected Expiry:", expiry_date)


# --------------------------------
# 3. GET NIFTY OPTION CHAIN
# --------------------------------

print("\n========== NIFTY OPTION CHAIN ==========")

option_chain = api_get(
    "/v2/option/chain",
    {
        "instrument_key": UNDERLYING,
        "expiry_date": expiry_date
    }
)

print("Underlying Spot:", option_chain[0]["underlying_spot_price"]
      if option_chain else "N/A")

print("\n")

nifty_ltp = nifty["last_price"]

# Round NIFTY to nearest multiple of 50 (ATM strike)
atm_strike = int((nifty_ltp + 25) // 50) * 50

# Generate strikes: ATM ± 400, interval 50
strike_array = list(range(atm_strike - 400, atm_strike + 401, 50))

print("NIFTY LTP:", nifty_ltp)
print("ATM Strike:", atm_strike)
print("Strike Array:", strike_array)

filtered_option_chain = [
    item for item in option_chain
    if item.get("strike_price") in strike_array
]

print("Filtered strikes:", len(filtered_option_chain))


# --------------------------------
# 4. DISPLAY OPTION CHAIN
# --------------------------------

print(
    f"{'STRIKE':>8} | "
    f"{'CE LTP':>9} | "
    f"{'CE OI':>12} | "
    f"{'CE VOL':>9} | "
    f"{'CE IV':>7} | "
    f"{'CE DELTA':>9} | "
    f"{'CE GAMMA':>9} | "
    f"{'CE THETA':>9} | "
    f"{'CE VEGA':>8} | "
    f"{'PE LTP':>9} | "
    f"{'PE OI':>12} | "
    f"{'PE VOL':>9} | "
    f"{'PE IV':>7} | "
    f"{'PE DELTA':>9} | "
    f"{'PE GAMMA':>9} | "
    f"{'PE THETA':>9} | "
    f"{'PE VEGA':>8} | "
    f"{'PCR OI':>8} | "
    f"{'PCR VOL':>8}"
)

print("-" * 210)

all_ce_oi = 0
all_pe_oi = 0
all_ce_vol = 0
all_pe_vol = 0
raw_data_records = []

for item in option_chain:
    call = item.get("call_options") or {}
    put = item.get("put_options") or {}

    ce = call.get("market_data") or {}
    pe = put.get("market_data") or {}

    all_ce_oi += ce.get("oi", 0) or 0
    all_pe_oi += pe.get("oi", 0) or 0
    all_ce_vol += ce.get("volume", 0) or 0
    all_pe_vol += pe.get("volume", 0) or 0

for item in filtered_option_chain:

    call = item.get("call_options") or {}
    put = item.get("put_options") or {}

    ce = call.get("market_data") or {}
    pe = put.get("market_data") or {}

    ce_greeks = call.get("option_greeks") or {}
    pe_greeks = put.get("option_greeks") or {}

    ce_oi = ce.get("oi", 0) or 0
    pe_oi = pe.get("oi", 0) or 0
    ce_vol = ce.get("volume", 0) or 0
    pe_vol = pe.get("volume", 0) or 0

    strike = item.get("strike_price")
    pcr_oi = (ce_oi / pe_oi) if pe_oi else 0
    pcr_vol = (ce_vol / pe_vol) if pe_vol else 0

    raw_record = {
        "date_label": date_label,
        "time_label": data_label,
        "expiry_date": expiry_date,
        "underlying": UNDERLYING,
        "strike_price": strike,
        "call_ltp": ce.get("ltp"),
        "call_oi": ce_oi,
        "call_vol": ce_vol,
        "call_iv": ce_greeks.get("iv", 0),
        "call_delta": ce_greeks.get("delta", 0),
        "call_gamma": ce_greeks.get("gamma", 0),
        "call_theta": ce_greeks.get("theta", 0),
        "call_vega": ce_greeks.get("vega", 0),
        "put_ltp": pe.get("ltp"),
        "put_oi": pe_oi,
        "put_vol": pe_vol,
        "put_iv": pe_greeks.get("iv", 0),
        "put_delta": pe_greeks.get("delta", 0),
        "put_gamma": pe_greeks.get("gamma", 0),
        "put_theta": pe_greeks.get("theta", 0),
        "put_vega": pe_greeks.get("vega", 0),
        "pcr_oi": pcr_oi,
        "pcr_vol": pcr_vol,
        "processed": 0,
    }
    raw_data_records.append(raw_record)

    print(
        f"{strike:>8.0f} | "
        f"{ce.get('ltp', 0):>9.2f} | "
        f"{ce_oi:>12} | "
        f"{ce_vol:>9} | "
        f"{ce_greeks.get('iv', 0):>7.2f} | "
        f"{ce_greeks.get('delta', 0):>9.3f} | "
        f"{ce_greeks.get('gamma', 0):>9.3f} | "
        f"{ce_greeks.get('theta', 0):>9.3f} | "
        f"{ce_greeks.get('vega', 0):>8.3f} | "
        f"{pe.get('ltp', 0):>9.2f} | "
        f"{pe_oi:>12} | "
        f"{pe_vol:>9} | "
        f"{pe_greeks.get('iv', 0):>7.2f} | "
        f"{pe_greeks.get('delta', 0):>9.3f} | "
        f"{pe_greeks.get('gamma', 0):>9.3f} | "
        f"{pe_greeks.get('theta', 0):>9.3f} | "
        f"{pe_greeks.get('vega', 0):>8.3f} | "
        f"{pcr_oi:>8.2f} | "
        f"{pcr_vol:>8.2f}"
    )

overall_pcr_oi = (all_ce_oi / all_pe_oi) if all_pe_oi else 0
overall_pcr_vol = (all_ce_vol / all_pe_vol) if all_pe_vol else 0

print("\nOverall PCR (OI):", round(overall_pcr_oi, 2))
print("Overall PCR (Volume):", round(overall_pcr_vol, 2))
print("\nSelected strike PCR values:")

selected_ce_oi = 0
selected_pe_oi = 0
selected_ce_vol = 0
selected_pe_vol = 0

for item in filtered_option_chain:
    call = item.get("call_options") or {}
    put = item.get("put_options") or {}

    ce = call.get("market_data") or {}
    pe = put.get("market_data") or {}

    selected_ce_oi += ce.get("oi", 0) or 0
    selected_pe_oi += pe.get("oi", 0) or 0
    selected_ce_vol += ce.get("volume", 0) or 0
    selected_pe_vol += pe.get("volume", 0) or 0

selected_pcr_oi = (selected_ce_oi / selected_pe_oi) if selected_pe_oi else 0
selected_pcr_vol = (selected_ce_vol / selected_pe_vol) if selected_pe_vol else 0

for record in raw_data_records:
    record["pcr_oi"] = selected_pcr_oi
    record["pcr_vol"] = selected_pcr_vol

print(f"Selected PCR (OI): {selected_pcr_oi:.2f}")
print(f"Selected PCR (Volume): {selected_pcr_vol:.2f}")

if raw_data_records:
    inserted = collection_dashboard_data.insert_many(raw_data_records)
    print(f"Inserted {len(inserted.inserted_ids)} raw_data records with processed=0")
else:
    print("No raw_data records to insert.")


print("\nOption chain fetched successfully.")



