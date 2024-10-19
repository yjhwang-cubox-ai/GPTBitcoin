import os
import json
import time
import requests
from dotenv import load_dotenv
import pyupbit
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator
from ta.trend import MACD
from openai import OpenAI

# Load environment variables
load_dotenv()

# Add technical indicators to OHLCV data
def add_indicators(df):
    # Bollinger Bands
    indicator_bb = BollingerBands(close=df["close"], window=20, window_dev=2)
    df['bb_mavg'] = indicator_bb.bollinger_mavg()
    df['bb_hband'] = indicator_bb.bollinger_hband()
    df['bb_lband'] = indicator_bb.bollinger_lband()

    # RSI (Relative Strength Index)
    rsi = RSIIndicator(close=df['close'], window=14)
    df['rsi'] = rsi.rsi()

    # MACD (Moving Average Convergence Divergence)
    macd = MACD(close=df['close'], window_slow=26, window_fast=12, window_sign=9)
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_diff'] = macd.macd_diff()

    return df

# Fetch Fear and Greed Index from API
def get_fear_and_greed_index():
    try:
        response = requests.get("https://api.alternative.me/fng/?limit=1")
        if response.status_code == 200:
            fng_data = response.json()
            # Extract relevant data
            fng_value = fng_data['data'][0]['value']
            fng_classification = fng_data['data'][0]['value_classification']
            return {
                "fng_value": fng_value,
                "fng_classification": fng_classification
            }
        else:
            print("Error fetching Fear and Greed Index")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error occurred while fetching Fear and Greed Index: {e}")
        return None

def ai_trading():
    try:
        # 1. Fetch chart data
        print("Fetching daily and hourly chart data...")
        daily_df = pyupbit.get_ohlcv("KRW-BTC", count=30, interval="day")
        hourly_df = pyupbit.get_ohlcv("KRW-BTC", count=24, interval="minute60")
        
        if daily_df is None or hourly_df is None:
            print("Failed to fetch chart data.")
            return

        # Add technical indicators
        daily_df = add_indicators(daily_df)
        hourly_df = add_indicators(hourly_df)

        # 2. Fetch orderbook data
        print("Fetching orderbook data...")
        orderbook = pyupbit.get_orderbook("KRW-BTC")
        if orderbook is None:
            print("Failed to fetch orderbook data.")
            return

        # 3. Fetch balance and investment status
        access = os.getenv('UPBIT_ACCESS_KEY')
        secret = os.getenv('UPBIT_SECRET_KEY')
        if not access or not secret:
            print("Upbit API keys not found. Check your .env file.")
            return

        upbit = pyupbit.Upbit(access, secret)

        krw_balance = upbit.get_balance("KRW")
        btc_balance = upbit.get_balance("KRW-BTC")

        if krw_balance is None or btc_balance is None:
            print("Failed to fetch balance data.")
            return

        # 4. Fetch Fear and Greed Index
        print("Fetching Fear and Greed Index data...")
        fng_data = get_fear_and_greed_index()
        if fng_data is None:
            print("Failed to fetch Fear and Greed Index data.")
            return

        # 5. Get AI decision on trading
        client = OpenAI()
        print("Requesting AI decision...")

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You're a Bitcoin investing expert. Based on the provided data, including daily and hourly chart data, order book data, "
                        "investment status, and Fear and Greed Index, determine whether to buy, sell, or hold. "
                        "Response in json format.\n\n"
                        "Example response:\n{\"decision\":\"buy\",\"reason\":\"some technical reason\"}\n"
                        "{\"decision\":\"sell\",\"reason\":\"some technical reason\"}\n{\"decision\":\"hold\",\"reason\":\"some technical reason\"}"
                    )
                },
                {"role": "user", "content": json.dumps({
                    "daily_chart": daily_df.to_dict(),
                    "hourly_chart": hourly_df.to_dict(),
                    "orderbook": orderbook,
                    "investment_status": {
                        "krw_balance": krw_balance,
                        "btc_balance": btc_balance
                    },
                    "fear_and_greed_index": fng_data
                })}
            ]
        )

        result = response.choices[0].message['content']
        decision_data = json.loads(result)
        
        if 'decision' not in decision_data or 'reason' not in decision_data:
            print("AI response format is incorrect.")
            return

        print(f"### AI Decision: {decision_data['decision'].upper()} ###")
        print(f"### Reason: {decision_data['reason']} ###")

        # 6. Execute trade based on AI decision
        if decision_data['decision'] == 'buy':
            if krw_balance * 0.9995 > 5000:
                print("### Executing Buy Order ###")
                print(upbit.buy_market_order("KRW-BTC", krw_balance * 0.9995))
            else:
                print("### Buy Order Failed: Insufficient KRW ###")
        elif decision_data['decision'] == 'sell':
            current_price = pyupbit.get_current_price("KRW-BTC")
            if btc_balance * current_price > 5000:
                print("### Executing Sell Order ###")
                print(upbit.sell_market_order("KRW-BTC", btc_balance))
            else:
                print("### Sell Order Failed: Insufficient BTC ###")
        elif decision_data['decision'] == 'hold':
            print("### Hold ###")

    except requests.exceptions.RequestException as e:
        print(f"Network error occurred: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

# Main loop
while True:
    ai_trading()
    print("Waiting for next round...")
    time.sleep(600)
