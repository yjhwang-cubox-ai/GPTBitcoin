import os
import json
import time
from dotenv import load_dotenv
import pyupbit
from openai import OpenAI
load_dotenv()

from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD

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

df_daily = pyupbit.get_ohlcv("KRW-BTC", count=30, interval="day")
# 일봉 데이터에 지표 추가
df_daily = add_indicators(df_daily)

print(df_daily)