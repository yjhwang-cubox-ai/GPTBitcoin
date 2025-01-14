import os
from dotenv import load_dotenv
import pyupbit
import pandas as pd
import json
from openai import OpenAI
import ta
from ta.utils import dropna
import time
import requests
import base64
from PIL import Image
import io
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    WebDriverException,
    NoSuchElementException,
)
import logging
from datetime import datetime
from youtube_transcript_api import YouTubeTranscriptApi
from pydantic import BaseModel
from openai import OpenAI
import sqlite3
from datetime import datetime


class TradingDecision(BaseModel):
    decision: str
    percentage: int
    reason: str


# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()


def init_database():
    """데이터베이스 초기화 및 테이블 생성"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "trading_history.db"
    )
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 거래 기록 테이블
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            decision TEXT NOT NULL,
            percentage INTEGER NOT NULL,
            reason TEXT,
            btc_balance REAL NOT NULL,
            krw_balance REAL NOT NULL,
            btc_avg_buy_price REAL NOT NULL,
            btc_krw_price REAL NOT NULL
        )
    """
    )

    conn.commit()
    conn.close()


def save_trade(
    decision,
    percentage,
    reason,
    btc_balance,
    krw_balance,
    btc_avg_buy_price,
    btc_krw_price,
):
    """거래 기록 저장"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "trading_history.db"
    )
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    timestamp = datetime.now().isoformat()

    cursor.execute(
        """
        INSERT INTO trades (
            timestamp, decision, percentage, reason,
            btc_balance, krw_balance, btc_avg_buy_price, btc_krw_price
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            timestamp,
            decision,
            percentage,
            reason,
            btc_balance,
            krw_balance,
            btc_avg_buy_price,
            btc_krw_price,
        ),
    )

    conn.commit()
    conn.close()


def get_last_trade():
    """마지막 거래 기록 조회"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "trading_history.db"
    )
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM trades ORDER BY timestamp DESC LIMIT 1")
    last_trade = cursor.fetchone()

    conn.close()
    return last_trade


def get_trade_history(days=7):
    """최근 거래 기록 조회"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "trading_history.db"
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # 컬럼명으로 접근 가능하도록 설정
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT * FROM trades 
        WHERE timestamp >= datetime('now', '-' || ? || ' days')
        ORDER BY timestamp DESC
    """,
        (days,),
    )

    trades = cursor.fetchall()
    conn.close()

    return [dict(trade) for trade in trades]


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


def setup_chrome_options():
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--headless")  # 디버깅을 위해 헤드리스 모드 비활성화
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
    return chrome_options


def create_driver():
    logger.info("ChromeDriver 설정 중...")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=setup_chrome_options())
    return driver


def click_element_by_xpath(driver, xpath, element_name, wait_time=10):
    try:
        element = WebDriverWait(driver, wait_time).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
        # 요소가 뷰포트에 보일 때까지 스크롤
        driver.execute_script("arguments[0].scrollIntoView(true);", element)
        # 요소가 클릭 가능할 때까지 대기
        element = WebDriverWait(driver, wait_time).until(
            EC.element_to_be_clickable((By.XPATH, xpath))
        )
        element.click()
        logger.info(f"{element_name} 클릭 완료")
        time.sleep(2)  # 클릭 후 잠시 대기
    except TimeoutException:
        logger.error(f"{element_name} 요소를 찾는 데 시간이 초과되었습니다.")
    except ElementClickInterceptedException:
        logger.error(
            f"{element_name} 요소를 클릭할 수 없습니다. 다른 요소에 가려져 있을 수 있습니다."
        )
    except NoSuchElementException:
        logger.error(f"{element_name} 요소를 찾을 수 없습니다.")
    except Exception as e:
        logger.error(f"{element_name} 클릭 중 오류 발생: {e}")


def perform_chart_actions(driver):
    # 시간 메뉴 클릭
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]",
        "시간 메뉴",
    )

    # 1시간 옵션 선택
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]/cq-menu-dropdown/cq-item[8]",
        "1시간 옵션",
    )

    # 지표 메뉴 클릭
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴",
    )

    # 볼린저 밴드 옵션 선택
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[15]",
        "볼린저 밴드 옵션",
    )


def capture_and_encode_screenshot(driver):
    try:
        # 스크린샷 캡처
        png = driver.get_screenshot_as_png()

        # PIL Image로 변환
        img = Image.open(io.BytesIO(png))

        # 이미지 리사이즈 (OpenAI API 제한에 맞춤)
        img.thumbnail((2000, 2000))

        # 현재 시간을 파일명에 포함
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"upbit_chart_{current_time}.png"

        # 현재 스크립트의 경로를 가져옴
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # 파일 저장 경로 설정
        file_path = os.path.join(script_dir, filename)

        # 이미지 파일로 저장
        img.save(file_path)
        logger.info(f"스크린샷이 저장되었습니다: {file_path}")

        # 이미지를 바이트로 변환
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")

        # base64로 인코딩
        base64_image = base64.b64encode(buffered.getvalue()).decode("utf-8")

        return base64_image, file_path
    except Exception as e:
        logger.error(f"스크린샷 캡처 및 인코딩 중 오류 발생: {e}")
        return None, None


def get_combined_transcript(video_id):
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["ko"])
        combined_text = " ".join(entry["text"] for entry in transcript)
        return combined_text
    except Exception as e:
        logger.error(f"Error fetching YouTube transcript: {e}")
        return ""


def execute_buy(upbit, ticker, amount):
    """매수 주문 실행 및 에러 처리"""
    try:
        return upbit.buy_market_order(ticker, amount)
    except Exception as e:
        logger.error(f"매수 주문 실패: {e}")
        return None


def execute_sell(upbit, ticker, volume):
    """매도 주문 실행 및 에러 처리"""
    try:
        return upbit.sell_market_order(ticker, volume)
    except Exception as e:
        logger.error(f"매도 주문 실패: {e}")
        return None


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


def init_reflection_database():
    """리플렉션(반성) 데이터베이스 테이블 생성"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "trading_history.db"
    )
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 거래 리플렉션 테이블
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_reflections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            total_trades INTEGER NOT NULL,
            profit_loss_percentage REAL NOT NULL,
            market_condition TEXT NOT NULL,
            strategy_effectiveness TEXT NOT NULL,
            key_learnings TEXT NOT NULL,
            improvement_suggestions TEXT NOT NULL
        )
    """
    )

    # actual_profit_loss 컬럼이 존재하는지 확인
    cursor.execute("PRAGMA table_info(trades)")
    columns = cursor.fetchall()
    column_names = [column[1] for column in columns]

    # actual_profit_loss 컬럼이 없을 때만 추가
    if "actual_profit_loss" not in column_names:
        cursor.execute(
            """
            ALTER TABLE trades 
            ADD COLUMN actual_profit_loss REAL DEFAULT 0.0
            """
        )

    conn.commit()
    conn.close()


def calculate_trade_performance(trade_history):
    """거래 실적 계산"""
    if not trade_history:
        return 0, 0

    total_profit_loss = 0
    for trade in trade_history:
        initial_value = (
            trade["krw_balance"] + trade["btc_balance"] * trade["btc_krw_price"]
        )
        final_value = (
            trade["krw_balance"] + trade["btc_balance"] * trade["btc_krw_price"]
        )
        profit_loss = ((final_value - initial_value) / initial_value) * 100
        total_profit_loss += profit_loss

    avg_profit_loss = total_profit_loss / len(trade_history)
    return avg_profit_loss, len(trade_history)


def analyze_market_condition(df_daily):
    """시장 상황 분석"""
    latest_data = df_daily.iloc[-1]

    # 트렌드 분석
    sma_20 = latest_data["sma_20"]
    current_price = latest_data["close"]
    rsi = latest_data["rsi"]

    if current_price > sma_20 and rsi > 50:
        trend = "상승"
    elif current_price < sma_20 and rsi < 50:
        trend = "하락"
    else:
        trend = "횡보"

    # 변동성 분석
    volatility = df_daily["close"].pct_change().std() * 100

    return {"trend": trend, "volatility": volatility, "rsi": rsi}


def generate_reflection(client, trade_history, market_condition):
    """AI를 활용한 거래 반성 및 개선점 도출"""
    avg_profit_loss, total_trades = calculate_trade_performance(trade_history)

    prompt = {
        "trading_metrics": {
            "total_trades": total_trades,
            "avg_profit_loss": round(avg_profit_loss, 2),
            "market_trend": market_condition["trend"],
            "volatility": round(market_condition["volatility"], 2),
            "rsi": round(market_condition["rsi"], 2),
            "trade_history": trade_history,
        }
    }

    response = client.chat.completions.create(
        model="gpt-4o-2024-08-06",
        messages=[
            {
                "role": "system",
                "content": "당신은 전문적인 트레이딩 코치입니다. 거래 기록을 분석하여 객관적인 평가와 구체적인 개선방안을 제시해야 합니다.",
            },
            {
                "role": "user",
                "content": json.dumps(prompt, ensure_ascii=False, indent=2),
            },
        ],
        response_format={"type": "json_object"},
        max_tokens=1000,
    )

    reflection = response.choices[0].message.content
    return reflection


def save_reflection(trade_history, reflection, df_daily):
    """리플렉션 결과 저장"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "trading_history.db"
    )
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 리플렉션 기간 설정
    period_start = trade_history[0]["timestamp"]
    period_end = trade_history[-1]["timestamp"]

    # 리플렉션 내용 파싱
    sections = reflection.split("\n\n")
    strategy_effectiveness = sections[0] if len(sections) > 0 else ""
    key_learnings = sections[1] if len(sections) > 1 else ""
    improvement_suggestions = sections[2] if len(sections) > 2 else ""

    # 시장 상황 분석
    market_condition = analyze_market_condition(df_daily)

    cursor.execute(
        """
        INSERT INTO trade_reflections (
            timestamp,
            period_start,
            period_end,
            total_trades,
            profit_loss_percentage,
            market_condition,
            strategy_effectiveness,
            key_learnings,
            improvement_suggestions
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            datetime.now().isoformat(),
            period_start,
            period_end,
            len(trade_history),
            calculate_trade_performance(trade_history)[0],
            json.dumps(market_condition),
            strategy_effectiveness,
            key_learnings,
            improvement_suggestions,
        ),
    )

    conn.commit()
    conn.close()


def get_latest_reflections(days=30):
    """최근 리플렉션 조회"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "trading_history.db"
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT * FROM trade_reflections
        WHERE timestamp >= datetime('now', '-' || ? || ' days')
        ORDER BY timestamp DESC
    """,
        (days,),
    )

    reflections = cursor.fetchall()
    conn.close()

    return [dict(reflection) for reflection in reflections]


def incorporate_reflection_into_decision(client, reflection_history, current_analysis):
    """과거 리플렉션을 고려한 거래 결정"""

    format_example = {"decision": "buy/sell/hold", "percentage": 0, "reason": "string"}

    response = client.chat.completions.create(
        model="gpt-4o-2024-08-06",
        messages=[
            {
                "role": "system",
                "content": """당신은 과거의 실수로부터 학습하는 AI 트레이딩 시스템입니다.
                과거 리플렉션과 현재 시장 상황을 종합적으로 고려하여 더 나은 거래 결정을 내려야 합니다.
                응답은 반드시 JSON 형식이어야 합니다.""",
            },
            {
                "role": "user",
                "content": f"""
                과거 리플렉션 기록:
                {json.dumps(reflection_history, indent=2)}

                현재 분석:
                {json.dumps(current_analysis, indent=2)}

                과거의 실수와 학습을 고려하여, 현재 거래 결정을 조정해주세요.
                결정은 다음 형식이어야 합니다:
                {json.dumps(format_example, indent=2)}
                """,
            },
        ],
        response_format={"type": "json_object"},
    )

    return json.loads(response.choices[0].message.content)


def perform_periodic_reflection(client, df_daily):
    """주기적인 거래 반성 및 시스템 개선"""
    # 최근 거래 기록 조회
    trade_history = get_trade_history(days=7)
    if not trade_history:
        logger.info("리플렉션을 위한 충분한 거래 기록이 없습니다.")
        return

    # 시장 상황 분석
    market_condition = analyze_market_condition(df_daily)

    # 리플렉션 생성
    reflection = generate_reflection(client, trade_history, market_condition)

    # 리플렉션 저장
    save_reflection(trade_history, reflection, df_daily=df_daily)

    logger.info("리플렉션이 완료되었습니다.")
    logger.info(f"리플렉션 내용: {reflection}")


def ai_trading():
    TICKER = "KRW-BTC"

    # 데이터베이스 초기화
    init_database()
    init_reflection_database()

    try:
        # Upbit 객체 생성
        access = os.getenv("UPBIT_ACCESS_KEY")
        secret = os.getenv("UPBIT_SECRET_KEY")
        upbit = pyupbit.Upbit(access, secret)

        # 현재 계좌 상태 조회
        status = get_current_status(upbit, TICKER)

        # 1. 현재 투자 상태 조회
        all_balances = upbit.get_balances()
        filtered_balances = [
            balance for balance in all_balances if balance["currency"] in ["BTC", "KRW"]
        ]

        # 2. 차트 데이터 조회
        df_daily = pyupbit.get_ohlcv(TICKER, interval="day", count=30)
        if df_daily is None or len(df_daily) == 0:
            raise Exception("일봉 데이터 조회 실패")

        df_daily = dropna(df_daily)
        df_daily = add_indicators(df_daily)

        df_hourly = pyupbit.get_ohlcv(TICKER, interval="minute60", count=24)
        if df_hourly is None or len(df_hourly) == 0:
            raise Exception("시간봉 데이터 조회 실패")

        df_hourly = dropna(df_hourly)
        df_hourly = add_indicators(df_hourly)

        # 3. 주문 데이터 조회
        orderbook = pyupbit.get_orderbook(TICKER)
        if orderbook is None:
            raise Exception("호가 데이터 조회 실패")

        # 4. 외부 데이터 조회
        fear_greed_index = get_fear_and_greed_index()
        news_headlines = get_bitcoin_news()
        # youtube_transcript = get_combined_transcript("3XbtEX3jUv4")

        with open("strategy.txt", "r", encoding="utf-8") as f:
            youtube_transcript = f.read()

        # 차트 캡처
        chart_image = None
        saved_file_path = None
        driver = None
        try:
            driver = create_driver()
            driver.get("https://upbit.com/full_chart?code=CRIX.UPBIT.KRW-BTC")
            logger.info("차트 페이지 로딩 완료")
            time.sleep(30)
            perform_chart_actions(driver)
            chart_image, saved_file_path = capture_and_encode_screenshot(driver)
        except Exception as e:
            logger.error(f"차트 캡처 실패: {e}")
        finally:
            if driver:
                driver.quit()

        # 최근 리플렉션 기록 조회
        reflection_history = get_latest_reflections(days=30)

        # AI 분석
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-2024-08-06",
            messages=[
                {
                    "role": "system",
                    "content": f"""You are a Bitcoin investment expert. Analyze the current market situation based on the following legendary Korean investor's trading strategy as your foundational approach:

        [Core Trading Principles]
        {youtube_transcript}

        Using the above trading methodology as your foundation, analyze the following data comprehensively to make a buy/sell/hold decision:
        - Technical indicators and market data
        - Recent news headlines and their potential impact on Bitcoin price
        - The Fear and Greed Index and its implications
        - Overall market sentiment
        - Patterns and trends visible in the chart image

        Your response should include:
        1. A decision (buy, sell, or hold)
        2. For buy decisions: percentage (1-100) of available KRW to use
        For sell decisions: percentage (1-100) of held BTC to sell
        For hold decisions: exactly 0
        3. Reasoning for your decision (explain in relation to the legendary investor's trading principles)

        The percentage must be an integer between 1-100 for buy/sell decisions, and exactly 0 for hold decisions.
        Your percentage should reflect the strength of your conviction based on the analyzed data and alignment with the core trading principles.""",
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
        - Fear and Greed Index: {json.dumps(fear_greed_index)}""",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{chart_image}"
                                },
                            },
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
        initial_analysis = json.loads(response.choices[0].message.content)

        # 리플렉션을 고려한 최종 결정
        final_decision = incorporate_reflection_into_decision(
            client, reflection_history, initial_analysis
        )

        # 거래 실행 (final_decision 기반)
        order_result = None
        if final_decision["decision"] == "buy":
            buy_amount = (
                status["krw_balance"] * (final_decision["percentage"] / 100) * 0.9995
            )
            if buy_amount > 5000:
                order_result = execute_buy(upbit, TICKER, buy_amount)

        elif final_decision["decision"] == "sell":
            sell_amount = status["btc_balance"] * (final_decision["percentage"] / 100)
            if sell_amount * status["current_price"] > 5000:
                order_result = execute_sell(upbit, TICKER, sell_amount)

        # 거래 후 최종 상태 저장
        final_status = get_current_status(upbit, TICKER)
        if final_status:
            save_trade(
                decision=final_decision["decision"],
                percentage=final_decision["percentage"],
                reason=final_decision["reason"],
                btc_balance=final_status["btc_balance"],
                krw_balance=final_status["krw_balance"],
                btc_avg_buy_price=final_status["avg_buy_price"],
                btc_krw_price=final_status["current_price"],
            )

        # 주기적인 리플렉션 수행 (매 10번째 거래마다)
        last_trade = get_last_trade()
        if last_trade and last_trade[0] % 10 == 0:
            perform_periodic_reflection(client, df_daily=df_daily)

    except Exception as e:
        logger.error(f"트레이딩 실행 중 오류 발생: {e}")


if __name__ == "__main__":
    ai_trading()
