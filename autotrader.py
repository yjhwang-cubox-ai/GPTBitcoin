import os
import json
import time
from dotenv import load_dotenv
import pyupbit
from openai import OpenAI
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD

load_dotenv()

# 일봉 데이터에 보조 지표 추가
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

def ai_traiding():
    # 1. 업비트 차트 데이터 가져오기 (30일 일봉)
    daily_df = pyupbit.get_ohlcv("KRW-BTC", count=30, interval="day")
    # 일봉 데이터에 지표 추가
    daily_df = add_indicators(daily_df)
    # 24시간 시간 봉 데이터 가져오기
    hourly_df = pyupbit.get_ohlcv("KRW-BTC", count=24, interval="minute60")
    
    # 2. 오더북 데이터 가져오기
    orderbook = pyupbit.get_orderbook("KRW-BTC")
    
    # 3. 투자 상태 조회하기 (잔고 데이터)
    access = os.getenv('UPBIT_ACCESS_KEY')
    secret = os.getenv('UPBIT_SECRET_KEY')
    upbit = pyupbit.Upbit(access, secret)
    
    krw_balance = upbit.get_balance("KRW")
    btc_balance = upbit.get_balance("KRW-BTC")
    
    # AI에 데이터 전달하여 매매 판단 받기
    client = OpenAI()
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You're a Bitcoin investing expert.\nBased on the provided data, including daily and hourly chart data, order book data, "
                    "and investment status, determine whether to buy, sell, or hold.\nResponse in json format\n\n"
                    "Response Example:\n{\"decision\":\"buy\",\"reason\":\"some technical reason\"}\n{\"decision\":\"sell\",\"reason\":\"some technical reason\"}\n{\"decision\":\"hold\",\"reason\":\"some technical reason\"}"
                )
            },
            {"role": "user", "content": json.dumps({
                "daily_chart": daily_df.to_dict(),
                "hourly_chart": hourly_df.to_dict(),
                "orderbook": orderbook,
                "investment_status": {
                    "krw_balance": krw_balance,
                    "btc_balance": btc_balance
                }
            })}
        ]
    )
    
    result = response.choices[0].message.content
    decision_data = json.loads(result)
    
    print(f"### AI Decision: {decision_data['decision'].upper()} ###")
    print(f"### Reason: {decision_data['reason']} ###")
    
    # 매매 판단에 따라 실제 매매 진행
    if decision_data['decision'] == 'buy':
        if krw_balance * 0.9995 > 5000:
            print("### Buy Order Executed ###")
            print(upbit.buy_market_order("KRW-BTC", krw_balance * 0.9995))
        else:
            print("### Buy Order Failed: Insufficient KRW ###")
    elif decision_data['decision'] == 'sell':
        current_price = pyupbit.get_current_price("KRW-BTC")
        if btc_balance * current_price > 5000:
            print("### Sell Order Executed ###")
            print(upbit.sell_market_order("KRW-BTC", btc_balance))
        else:
            print("### Sell Order Failed: Insufficient BTC ###")
    elif decision_data['decision'] == 'hold':
        print("### Hold ###")
    
while True:
    time.sleep(600)
    ai_traiding()

ai_traiding()
