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
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException, WebDriverException, NoSuchElementException
import logging
from datetime import datetime
from youtube_transcript_api import YouTubeTranscriptApi
from pydantic import BaseModel
from openai import OpenAI

class TradingDecision(BaseModel):
    decision: str
    percentage: int
    reason: str

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

def add_indicators(df):
    # 볼린저 밴드
    indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
    df['bb_bbm'] = indicator_bb.bollinger_mavg()
    df['bb_bbh'] = indicator_bb.bollinger_hband()
    df['bb_bbl'] = indicator_bb.bollinger_lband()
    
    # RSI
    df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
    
    # MACD
    macd = ta.trend.MACD(close=df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_diff'] = macd.macd_diff()
    
    # 이동평균선
    df['sma_20'] = ta.trend.SMAIndicator(close=df['close'], window=20).sma_indicator()
    df['ema_12'] = ta.trend.EMAIndicator(close=df['close'], window=12).ema_indicator()
    
    return df

def get_fear_and_greed_index():
    url = "https://api.alternative.me/fng/"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data['data'][0]
    else:
        logger.error(f"Failed to fetch Fear and Greed Index. Status code: {response.status_code}")
        return None

def get_bitcoin_news():
    serpapi_key = os.getenv("SERPAPI_API_KEY")
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_news",
        "q": "btc",
        "api_key": serpapi_key
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        news_results = data.get("news_results", [])
        headlines = []
        for item in news_results:
            headlines.append({
                "title": item.get("title", ""),
                "date": item.get("date", "")
            })
        
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
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
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
        logger.error(f"{element_name} 요소를 클릭할 수 없습니다. 다른 요소에 가려져 있을 수 있습니다.")
    except NoSuchElementException:
        logger.error(f"{element_name} 요소를 찾을 수 없습니다.")
    except Exception as e:
        logger.error(f"{element_name} 클릭 중 오류 발생: {e}")

def perform_chart_actions(driver):
    # 시간 메뉴 클릭
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]",
        "시간 메뉴"
    )
    
    # 1시간 옵션 선택
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]/cq-menu-dropdown/cq-item[8]",
        "1시간 옵션"
    )
    
    # 지표 메뉴 클릭
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]",
        "지표 메뉴"
    )
    
    # 볼린저 밴드 옵션 선택
    click_element_by_xpath(
        driver,
        "/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[15]",
        "볼린저 밴드 옵션"
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
        base64_image = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        return base64_image, file_path
    except Exception as e:
        logger.error(f"스크린샷 캡처 및 인코딩 중 오류 발생: {e}")
        return None, None

def get_combined_transcript(video_id):
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        combined_text = ' '.join(entry['text'] for entry in transcript)
        return combined_text
    except Exception as e:
        logger.error(f"Error fetching YouTube transcript: {e}")
        return ""

def ai_trading():
    # Upbit 객체 생성
    access = os.getenv("UPBIT_ACCESS_KEY")
    secret = os.getenv("UPBIT_SECRET_KEY")
    upbit = pyupbit.Upbit(access, secret)

    # 1. 현재 투자 상태 조회
    all_balances = upbit.get_balances()
    filtered_balances = [balance for balance in all_balances if balance['currency'] in ['BTC', 'KRW']]
    
    # 2. 오더북(호가 데이터) 조회
    orderbook = pyupbit.get_orderbook("KRW-BTC")
    
    # 3. 차트 데이터 조회 및 보조지표 추가
    df_daily = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=30)
    df_daily = dropna(df_daily)
    df_daily = add_indicators(df_daily)
    
    df_hourly = pyupbit.get_ohlcv("KRW-BTC", interval="minute60", count=24)
    df_hourly = dropna(df_hourly)
    df_hourly = add_indicators(df_hourly)

    # 4. 공포 탐욕 지수 가져오기
    fear_greed_index = get_fear_and_greed_index()

    # 5. 뉴스 헤드라인 가져오기
    news_headlines = get_bitcoin_news()

    # 6. YouTube 자막 데이터 가져오기
    youtube_transcript = get_combined_transcript("TWINrTppUl4")  # 여기에 실제 비트코인 관련 YouTube 영상 ID를 넣으세요

    # Selenium으로 차트 캡처
    driver = None
    try:
        driver = create_driver()
        driver.get("https://upbit.com/full_chart?code=CRIX.UPBIT.KRW-BTC")
        logger.info("페이지 로드 완료")
        time.sleep(30)  # 페이지 로딩 대기 시간 증가
        logger.info("차트 작업 시작")
        perform_chart_actions(driver)
        logger.info("차트 작업 완료")
        chart_image, saved_file_path = capture_and_encode_screenshot(driver)
        logger.info(f"스크린샷 캡처 완료. 저장된 파일 경로: {saved_file_path}")
    except WebDriverException as e:
        logger.error(f"WebDriver 오류 발생: {e}")
        chart_image, saved_file_path = None, None
    except Exception as e:
        logger.error(f"차트 캡처 중 오류 발생: {e}")
        chart_image, saved_file_path = None, None
    finally:
        if driver:
            driver.quit()

    # AI에게 데이터 제공하고 판단 받기
    client = OpenAI()

    response = client.chat.completions.create(
        model="gpt-4o-2024-08-06",
        messages=[
            {
                "role": "system",
                "content": """You are an expert in Bitcoin investing. Analyze the provided data including technical indicators, market data, recent news headlines, the Fear and Greed Index, YouTube video transcript, and the chart image. Tell me whether to buy, sell, or hold at the moment. Consider the following in your analysis:
                - Technical indicators and market data
                - Recent news headlines and their potential impact on Bitcoin price
                - The Fear and Greed Index and its implications
                - Overall market sentiment
                - The patterns and trends visible in the chart image
                - Insights from the YouTube video transcript
                
                Respond with:
                1. A decision (buy, sell, or hold)
                2. If the decision is 'buy', provide a percentage (1-100) of available KRW to use for buying.
                If the decision is 'sell', provide a percentage (1-100) of held BTC to sell.
                If the decision is 'hold', set the percentage to 0.
                3. A reason for your decision
                
                Ensure that the percentage is an integer between 1 and 100 for buy/sell decisions, and exactly 0 for hold decisions.
                Your percentage should reflect the strength of your conviction in the decision based on the analyzed data."""
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"""Current investment status: {json.dumps(filtered_balances)}
        Orderbook: {json.dumps(orderbook)}
        Daily OHLCV with indicators (30 days): {df_daily.to_json()}
        Hourly OHLCV with indicators (24 hours): {df_hourly.to_json()}
        Recent news headlines: {json.dumps(news_headlines)}
        Fear and Greed Index: {json.dumps(fear_greed_index)}
        YouTube Video Transcript: {youtube_transcript}"""
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{chart_image}"
                        }
                    }
                ]
            }
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "trading_decision",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "decision": {"type": "string", "enum": ["buy", "sell", "hold"]},
                        "percentage": {"type": "integer"},
                        "reason": {"type": "string"}
                    },
                    "required": ["decision", "percentage", "reason"],
                    "additionalProperties": False
                }
            }
        },
        max_tokens=4095
    )

    # 최신 pydantic 메서드 사용
    result = TradingDecision.model_validate_json(response.choices[0].message.content)

    print(f"### AI Decision: {result.decision.upper()} ###")
    print(f"### Reason: {result.reason} ###")

    if result.decision == "buy":
        my_krw = upbit.get_balance("KRW")
        buy_amount = my_krw * (result.percentage / 100) * 0.9995  # 수수료 고려
        if buy_amount > 5000:
            print(f"### Buy Order Executed: {result.percentage}% of available KRW ###")
            print(upbit.buy_market_order("KRW-BTC", buy_amount))
        else:
            print("### Buy Order Failed: Insufficient KRW (less than 5000 KRW) ###")
    elif result.decision == "sell":
        my_btc = upbit.get_balance("KRW-BTC")
        sell_amount = my_btc * (result.percentage / 100)
        current_price = pyupbit.get_orderbook(ticker="KRW-BTC")['orderbook_units'][0]["ask_price"]
        if sell_amount * current_price > 5000:
            print(f"### Sell Order Executed: {result.percentage}% of held BTC ###")
            print(upbit.sell_market_order("KRW-BTC", sell_amount))
        else:
            print("### Sell Order Failed: Insufficient BTC (less than 5000 KRW worth) ###")
    elif result.decision == "hold":
        print("### Hold Position ###")

ai_trading()

# # Main loop
# while True:
#     try:
#         ai_trading()
#         time.sleep(3600 * 4)  # 4시간마다 실행
#     except Exception as e:
#         logger.error(f"An error occurred: {e}")
#         time.sleep(300)  # 오류 발생 시 5분 후 재시도