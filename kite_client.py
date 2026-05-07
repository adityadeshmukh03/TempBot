# kite_client.py

from kiteconnect import KiteConnect
from pathlib import Path
import config

kite = KiteConnect(api_key=config.KITE_API_KEY)

def get_login_url():
    return kite.login_url()

def set_access_token(request_token):
    data = kite.generate_session(request_token, api_secret=config.KITE_API_SECRET)
    kite.set_access_token(data["access_token"])
    return data["access_token"]

def load_access_token():
    token_path = Path("access_token.txt")
    if not token_path.exists():
        raise RuntimeError("access_token.txt not found. Run run_login.py once before starting the bot.")
    token = token_path.read_text().strip()
    if not token:
        raise RuntimeError("access_token.txt is empty. Run run_login.py again to generate today's access token.")
    kite.set_access_token(token)
    return kite

def get_underlying_ltp(name):
    key = name.upper()
    tradingsymbol = config.UNDERLYING_SYMBOL.get(key, name)
    exchange = config.UNDERLYING_EXCHANGE.get(key, "NSE")
    quote_key = f"{exchange}:{tradingsymbol}"
    quote = kite.quote([quote_key])
    data = quote.get(quote_key, {})
    ltp = data.get("last_price")
    if ltp is None:
        raise RuntimeError(f"Could not fetch underlying LTP for {quote_key}")
    return float(ltp)

def get_underlying_instrument_token(name):
    key = name.upper()
    tradingsymbol = config.UNDERLYING_SYMBOL.get(key, name)
    exchange = config.UNDERLYING_EXCHANGE.get(key, "NSE")
    import pandas as pd
    instruments = kite.instruments(exchange)
    df = pd.DataFrame(instruments)
    result = df[df["tradingsymbol"] == tradingsymbol]
    if result.empty:
        result = df[df["name"].astype(str).str.upper() == name.upper()]
    if result.empty:
        raise RuntimeError(f"Could not resolve underlying token for {name} on {exchange}")
    return int(result.iloc[0]["instrument_token"])
