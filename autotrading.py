import os
import json
import requests
import logging
import time
from datetime import datetime, timedelta
import pandas as pd
import ta
from ta.utils import dropna
from pydantic import BaseModel
from openai import OpenAI
import pyupbit
import sqlite3
import schedule

import tiktoken


def count_tokens(messages):
    """
    Count tokens for the messages to be sent to the GPT-4 API.
    """
    encoding = tiktoken.encoding_for_model("gpt-4")

    num_tokens = 0

    for message in messages:
        # Add tokens for message role
        num_tokens += len(encoding.encode(message["role"]))

        # Handle content based on type
        if isinstance(message["content"], str):
            num_tokens += len(encoding.encode(message["content"]))
        elif isinstance(message["content"], list):
            for content_item in message["content"]:
                if content_item["type"] == "text":
                    num_tokens += len(encoding.encode(content_item["text"]))
                # Add handling for other content types if needed

    # Add tokens for message formatting
    # Each message follows format: {"role": role, "content": content}
    num_tokens += 4  # Add 4 tokens for message format per message

    return num_tokens


class TradingDecision(BaseModel):
    decision: str
    percentage: int
    reason: str


def validate_environment():
    """필요한 환경 변수 검증"""
    required_vars = [
        "UPBIT_ACCESS_KEY",
        "UPBIT_SECRET_KEY",
        "OPENAI_API_KEY",
        "SERPAPI_API_KEY",
    ]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing_vars)}"
        )


def init_db():
    conn = sqlite3.connect("trading_history.db")
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS trades
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT,
                  decision TEXT,
                  percentage INTEGER,
                  reason TEXT,
                  btc_balance REAL,
                  krw_balance REAL,
                  btc_avg_buy_price REAL,
                  btc_krw_price REAL,
                  reflection TEXT)"""
    )
    conn.commit()
    return conn


def get_db_connection():
    return sqlite3.connect("trading_history.db")


def log_trade(
    conn,
    decision,
    percentage,
    reason,
    btc_balance,
    krw_balance,
    btc_avg_buy_price,
    btc_krw_price,
    reflection="",
):
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    c.execute(
        """INSERT INTO trades 
                 (timestamp, decision, percentage, reason, btc_balance, krw_balance, btc_avg_buy_price, btc_krw_price, reflection) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            timestamp,
            decision,
            percentage,
            reason,
            btc_balance,
            krw_balance,
            btc_avg_buy_price,
            btc_krw_price,
            reflection,
        ),
    )
    conn.commit()


def get_recent_trades(conn, days=7):
    c = conn.cursor()
    seven_days_ago = (datetime.now() - timedelta(days=days)).isoformat()
    c.execute(
        "SELECT * FROM trades WHERE timestamp > ? ORDER BY timestamp DESC",
        (seven_days_ago,),
    )
    columns = [column[0] for column in c.description]

    return pd.DataFrame.from_records(data=c.fetchall(), columns=columns)


def get_current_status(upbit, ticker):
    """현재 계좌 상태 조회"""
    try:
        krw_balance = float(upbit.get_balance("KRW"))
        btc_balance = float(upbit.get_balance(ticker))
        avg_buy_price = float(upbit.get_avg_buy_price(ticker))
        current_price = pyupbit.get_current_price(ticker)

        return {
            "krw_balance": krw_balance,
            "btc_balance": btc_balance,
            "avg_buy_price": avg_buy_price,
            "current_price": current_price,
        }
    except Exception as e:
        logger.error(f"계좌 상태 조회 실패: {e}")
        return None


def get_fear_and_greed_index():
    url = "https://api.alternative.me/fng/"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data["data"][0]
    else:
        logger.error(
            f"Failed to fetch Fear and Greed Index. Status code: {response.status_code}"
        )
        return None


def get_bitcoin_news():
    serpapi_key = os.getenv("SERPAPI_API_KEY")
    url = "https://serpapi.com/search.json"
    params = {"engine": "google_news", "q": "btc", "api_key": serpapi_key}

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        news_results = data.get("news_results", [])
        headlines = []
        for item in news_results:
            headlines.append(
                {"title": item.get("title", ""), "date": item.get("date", "")}
            )

        return headlines[:5]
    except requests.RequestException as e:
        logger.error(f"Error fetching news: {e}")
        return []


def add_indicators(df):
    # 볼린저 밴드
    indicator_bb = ta.volatility.BollingerBands(
        close=df["close"], window=20, window_dev=2
    )
    df["bb_bbm"] = indicator_bb.bollinger_mavg()
    df["bb_bbh"] = indicator_bb.bollinger_hband()
    df["bb_bbl"] = indicator_bb.bollinger_lband()

    # RSI
    df["rsi"] = ta.momentum.RSIIndicator(close=df["close"], window=14).rsi()

    # MACD
    macd = ta.trend.MACD(close=df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_diff"] = macd.macd_diff()

    # 이동평균선
    df["sma_20"] = ta.trend.SMAIndicator(close=df["close"], window=20).sma_indicator()
    df["ema_12"] = ta.trend.EMAIndicator(close=df["close"], window=12).ema_indicator()

    return df


def calculate_performance(trades_df):
    if trades_df.empty:
        return 0

    initial_balance = (
        trades_df.iloc[-1]["krw_balance"]
        + trades_df.iloc[-1]["btc_balance"] * trades_df.iloc[-1]["btc_krw_price"]
    )
    final_balance = (
        trades_df.iloc[0]["krw_balance"]
        + trades_df.iloc[0]["btc_balance"] * trades_df.iloc[0]["btc_krw_price"]
    )

    return (final_balance - initial_balance) / initial_balance * 100


def generate_reflection(trades_df, current_market_data):
    performance = calculate_performance(trades_df)

    client = OpenAI()

    messages = [
        {
            "role": "developer",
            "content": "You are an AI trading assistant tasked with analyzing recent trading performance and current market conditions to generate insights and improvements for future trading decisions.",
        },
        {
            "role": "user",
            "content": f"""
                Recent trading data:
                {trades_df.to_json(orient='records')}
                
                Current market data:
                {current_market_data}
                
                Overall performance in the last 7 days: {performance:.2f}%
                
                Please analyze this data and provide:
                1. A brief reflection on the recent trading decisions
                2. Insights on what worked well and what didn't
                3. Suggestions for improvement in future trading decisions
                4. Any patterns or trends you notice in the market data
                
                Limit your response to 250 words or less.
                """,
        },
    ]
    token_count = count_tokens(messages)
    logger.info(f"Estimated token count for reflection: {token_count}")

    response = client.chat.completions.create(
        model="gpt-4o-2024-11-20",
        messages=[
            {
                "role": "developer",
                "content": "You are an AI trading assistant tasked with analyzing recent trading performance and current market conditions to generate insights and improvements for future trading decisions.",
            },
            {
                "role": "user",
                "content": f"""
                Recent trading data:
                {trades_df.to_json(orient='records')}
                
                Current market data:
                {current_market_data}
                
                Overall performance in the last 7 days: {performance:.2f}%
                
                Please analyze this data and provide:
                1. A brief reflection on the recent trading decisions
                2. Insights on what worked well and what didn't
                3. Suggestions for improvement in future trading decisions
                4. Any patterns or trends you notice in the market data
                
                Limit your response to 250 words or less.
                """,
            },
        ],
    )

    return response.choices[0].message.content


def run_scheduled_trading():
    """스케줄된 거래 실행 함수"""
    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"Starting hourly trading at {current_time}")

        # 실제 거래 로직 실행
        ai_trading()

        logger.info(f"Completed trading execution at {current_time}")
    except Exception as e:
        logger.error(f"Error during trading execution: {str(e)}")
        logger.exception("상세 에러:")


def ai_trading():
    # Upbit 초기화 및 DB 연결
    access = os.getenv("UPBIT_ACCESS_KEY")
    secret = os.getenv("UPBIT_SECRET_KEY")
    upbit = pyupbit.Upbit(access=access, secret=secret)
    conn = get_db_connection()

    # 1. 현재 계좌 상태 조회
    status = get_current_status(upbit=upbit, ticker="KRW-BTC")

    # 2. 시장 데이터 수집
    df_daily = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=30)
    df_hourly = pyupbit.get_ohlcv("KRW-BTC", interval="minute60", count=24)

    if df_daily is None or df_hourly is None:
        raise Exception("차트 데이터 조회 실패")

    df_daily = dropna(df_daily)
    df_hourly = dropna(df_hourly)

    df_daily = add_indicators(df_daily)
    df_hourly = add_indicators(df_hourly)

    # 3. 추가 데이터 수집
    orderbook = pyupbit.get_orderbook("KRW-BTC")
    if orderbook is None:
        raise Exception("호가 데이터 조회 실패")
    fear_greed_index = get_fear_and_greed_index()
    news_headlines = get_bitcoin_news()

    # 6. YOUTUBE 워뇨띠 매매기법 가져오기
    with open("strategy.txt", "r", encoding="utf-8") as f:
        youtube_transcript = f.read()

    # 최근 거래 내역 가져오기
    recent_trades = get_recent_trades(conn)

    # 현재 시장 데이터 수집
    current_market_data = {
        "fear_greed_index": fear_greed_index,
        "news_headlines": news_headlines,
        "orderbook": orderbook,
        "daily_ohlcv": df_daily.to_dict(),
        "hourly_ohlcv": df_hourly.to_dict(),
    }

    # AI 분석 시작
    client = OpenAI()

    # 반성 및 개선 내용 생성
    reflection = generate_reflection(recent_trades, current_market_data)

    ############
    messages = [
        {
            "role": "developer",
            "content": f"""You are an expert in Bitcoin investing. Analyze the provided data and determine whether to buy, sell, or hold at the current moment. Consider the following in your analysis:

                - Technical indicators and market data
                - Recent news headlines and their potential impact on Bitcoin price
                - The Fear and Greed Index and its implications
                - Overall market sentiment
                - Patterns and trends visible in the chart image
                - Recent trading performance and reflection

                Recent trading reflection:
                {reflection}

                Particularly important is to always refer to the trading method of 'Wonyyotti', a legendary Korean investor, to assess the current situation and make trading decisions. Wonyyotti's trading method is as follows:

                {youtube_transcript}

                Based on this trading method, analyze the current market situation and make a judgment by synthesizing it with the provided data and recent performance reflection.

                Response format:
                1. Decision (buy, sell, or hold)
                2. If the decision is 'buy', provide a percentage (1-100) of available KRW to use for buying.
                If the decision is 'sell', provide a percentage (1-100) of held BTC to sell.
                If the decision is 'hold', set the percentage to 0.
                3. Reason for your decision

                Ensure that the percentage is an integer between 1 and 100 for buy/sell decisions, and exactly 0 for hold decisions.
                Your percentage should reflect the strength of your conviction in the decision based on the analyzed data.""",
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""Current Balance Status:
                                    - KRW Balance: {status['krw_balance']}
                                    - BTC Balance: {status['btc_balance']}
                                    - Average Buy Price: {status['avg_buy_price']}
                                    - Current Price: {status['current_price']}

                                    Technical Analysis Data:
                                    - Daily Chart: {df_daily.to_json()}
                                    - Hourly Chart: {df_hourly.to_json()}
                                    - Order Book: {json.dumps(orderbook)}
                                    - News Headlines: {json.dumps(news_headlines)}
                                    - Fear and Greed Index: {json.dumps(fear_greed_index)}
                        """,
                }
            ],
        },
    ]
    token_count = count_tokens(messages)
    logger.info(f"Estimated token count for trading: {token_count}")

    response = client.chat.completions.create(
        model="gpt-4o-2024-11-20",
        messages=[
            {
                "role": "developer",
                "content": f"""You are an expert in Bitcoin investing. Analyze the provided data and determine whether to buy, sell, or hold at the current moment. Consider the following in your analysis:

                - Technical indicators and market data
                - Recent news headlines and their potential impact on Bitcoin price
                - The Fear and Greed Index and its implications
                - Overall market sentiment
                - Patterns and trends visible in the chart image
                - Recent trading performance and reflection

                Recent trading reflection:
                {reflection}

                Particularly important is to always refer to the trading method of 'Wonyyotti', a legendary Korean investor, to assess the current situation and make trading decisions. Wonyyotti's trading method is as follows:

                {youtube_transcript}

                Based on this trading method, analyze the current market situation and make a judgment by synthesizing it with the provided data and recent performance reflection.

                Response format:
                1. Decision (buy, sell, or hold)
                2. If the decision is 'buy', provide a percentage (1-100) of available KRW to use for buying.
                If the decision is 'sell', provide a percentage (1-100) of held BTC to sell.
                If the decision is 'hold', set the percentage to 0.
                3. Reason for your decision

                Ensure that the percentage is an integer between 1 and 100 for buy/sell decisions, and exactly 0 for hold decisions.
                Your percentage should reflect the strength of your conviction in the decision based on the analyzed data.""",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"""Current Balance Status:
                                    - KRW Balance: {status['krw_balance']}
                                    - BTC Balance: {status['btc_balance']}
                                    - Average Buy Price: {status['avg_buy_price']}
                                    - Current Price: {status['current_price']}

                                    Technical Analysis Data:
                                    - Daily Chart: {df_daily.to_json()}
                                    - Hourly Chart: {df_hourly.to_json()}
                                    - Order Book: {json.dumps(orderbook)}
                                    - News Headlines: {json.dumps(news_headlines)}
                                    - Fear and Greed Index: {json.dumps(fear_greed_index)}
                        """,
                    }
                ],
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "trading_decision",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "decision": {
                            "type": "string",
                            "enum": ["buy", "sell", "hold"],
                        },
                        "percentage": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                    "required": ["decision", "percentage", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        max_tokens=4095,
    )
    # initial_analysis = json.loads(response.choices[0].message.content)
    result = TradingDecision.model_validate_json(response.choices[0].message.content)

    print(f"### AI Decision: {result.decision.upper()} ###")
    print(f"### Reason: {result.reason} ###")

    order_executed = False

    if result.decision == "buy":
        my_krw = upbit.get_balance("KRW")
        buy_amount = my_krw * (result.percentage / 100) * 0.9995  # 수수료 고려
        if buy_amount > 5000:
            print(f"### Buy Order Executed: {result.percentage}% of available KRW ###")
            order = upbit.buy_market_order("KRW-BTC", buy_amount)
            if order:
                order_executed = True
            print(order)
        else:
            print("### Buy Order Failed: Insufficient KRW (less than 5000 KRW) ###")
    elif result.decision == "sell":
        my_btc = upbit.get_balance("KRW-BTC")
        sell_amount = my_btc * (result.percentage / 100)
        current_price = pyupbit.get_current_price("KRW-BTC")
        if sell_amount * current_price > 5000:
            print(f"### Sell Order Executed: {result.percentage}% of held BTC ###")
            order = upbit.sell_market_order("KRW-BTC", sell_amount)
            if order:
                order_executed = True
            print(order)
        else:
            print(
                "### Sell Order Failed: Insufficient BTC (less than 5000 KRW worth) ###"
            )

    # 거래 실행 여부와 관계없이 현재 잔고 조회
    time.sleep(1)  # API 호출 제한을 고려하여 잠시 대기
    balances = upbit.get_balances()
    btc_balance = next(
        (
            float(balance["balance"])
            for balance in balances
            if balance["currency"] == "BTC"
        ),
        0,
    )
    krw_balance = next(
        (
            float(balance["balance"])
            for balance in balances
            if balance["currency"] == "KRW"
        ),
        0,
    )
    btc_avg_buy_price = next(
        (
            float(balance["avg_buy_price"])
            for balance in balances
            if balance["currency"] == "BTC"
        ),
        0,
    )
    current_btc_price = pyupbit.get_current_price("KRW-BTC")

    # 거래 정보 및 반성 내용 로깅
    log_trade(
        conn,
        result.decision,
        result.percentage if order_executed else 0,
        result.reason,
        btc_balance,
        krw_balance,
        btc_avg_buy_price,
        current_btc_price,
        reflection,
    )

    # 데이터베이스 연결 종료
    conn.close()


def main():
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler("trading_bot.log"), logging.StreamHandler()],
    )
    global logger
    logger = logging.getLogger(__name__)

    try:
        # 데이터베이스 초기화
        init_db()

        # 환경 변수 검증
        validate_environment()

        # 1시간마다 실행되도록 스케줄 설정
        logger.info("Setting up schedule to run every hour...")
        schedule.every().hour.at(":00").do(run_scheduled_trading)

        # 시작 메시지 출력
        logger.info("Trading bot started. Will execute every hour at :00")
        logger.info("Press Ctrl+C to stop the bot.")

        # 현재 분이 0분이면 즉시 실행
        current_minute = datetime.now().minute
        if current_minute == 0:
            logger.info("Current time is on the hour, executing initial trade...")
            run_scheduled_trading()
        else:
            next_run = schedule.next_run()
            logger.info(f"Next trade will execute at {next_run}")

        # 스케줄러 실행
        while True:
            schedule.run_pending()
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Trading bot stopped by user")
    except Exception as e:
        logger.error(f"Critical error in main loop: {str(e)}")
        logger.exception("상세 에러:")
    finally:
        logger.info("Trading bot shutdown complete")


if __name__ == "__main__":
    main()
