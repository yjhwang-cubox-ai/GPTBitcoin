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
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from youtube_transcript_api import YouTubeTranscriptApi

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
        print(f"Failed to fetch Fear and Greed Index. Status code: {response.status_code}")
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
        response.raise_for_status()  # Raises a HTTPError if the status is 4xx, 5xx
        data = response.json()
        
        news_results = data.get("news_results", [])
        headlines = []
        for item in news_results:
            headlines.append({
                "title": item.get("title", ""),
                "date": item.get("date", "")
            })
        
        return headlines[:5]  # 최신 5개의 뉴스 헤드라인만 반환
    except requests.RequestException as e:
        print(f"Error fetching news: {e}")
        return []

def capture_chart():
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--start-maximized')
    chrome_options.add_argument('--window-size=1920,1080')
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    try:
        driver.get('https://upbit.com/full_chart?code=CRIX.UPBIT.KRW-BTC')
        time.sleep(10)
        
        # 1시간 차트 설정
        time_menu = driver.find_element(By.XPATH, '/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]')
        time_menu.click()
        time.sleep(1)
        one_hour_option = driver.find_element(By.XPATH, '/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]/cq-menu-dropdown/cq-item[8]')
        one_hour_option.click()
        time.sleep(2)

        # 지표 추가
        indicators = driver.find_element(By.XPATH, '/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]')
        indicators.click()
        time.sleep(2)
        bollinger_band = driver.find_element(By.XPATH, '/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[15]')
        bollinger_band.click()
        time.sleep(2)

        driver.save_screenshot("upbit_chart.png")
        print("차트가 성공적으로 캡처되었습니다.")
        # 스크린샷 캡처 및 base64 인코딩
        screenshot = driver.get_screenshot_as_base64()
        return screenshot
        
    except Exception as e:
        print(f'차트 캡처 중 에러 발생: {str(e)}')
        return None
    finally:
        driver.quit()

def get_youtube_insights():
    """비트코인 관련 주요 유튜브 채널의 최신 분석 내용을 가져옴"""
    # 주요 비트코인 분석 영상 ID 리스트
    video_ids = [
        "pCmJ8wsAS_w",  # 예시 영상 ID
        # 여기에 더 많은 신뢰할 수 있는 비트코인 분석 채널의 최신 영상 ID 추가
    ]
    
    combined_insights = []
    for video_id in video_ids:
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id)
            text = ' '.join([content['text'] for content in transcript])
            combined_insights.append({
                'video_id': video_id,
                'content': text
            })
        except Exception as e:
            print(f"유튜브 자막 추출 중 에러 발생: {e}")
            continue
    
    return combined_insights

def ai_trading():
    # Upbit 객체 생성
    access = os.getenv("UPBIT_ACCESS_KEY")
    secret = os.getenv("UPBIT_SECRET_KEY")
    upbit = pyupbit.Upbit(access, secret)

    try:
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

        # 차트 이미지 캡처
        chart_image = capture_chart()

        # 유튜브 인사이트 추가
        youtube_insights = get_youtube_insights()

        # OpenAI API 호출
        client = OpenAI()
        
        messages = [
            {
                "role": "system",
                "content": """You are an expert in Bitcoin investing. Analyze all provided data including technical indicators, market data, recent news headlines, YouTube analysis content, the Fear and Greed Index, and the technical chart image. Provide a comprehensive analysis and trading decision. Consider:
                - Technical indicators and market data
                - Chart patterns and visual analysis
                - Recent news headlines and their potential impact
                - YouTube experts' analysis and insights
                - The Fear and Greed Index
                - Overall market sentiment
                
                Response in json format with detailed analysis.
                {"decision": "buy/sell/hold", "reason": "detailed analysis including chart patterns and YouTube insights", "confidence_score": "0-100"}"""
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
                        YouTube Analysis Insights: {json.dumps(youtube_insights)}"""
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{chart_image}"
                        }
                    }
                ]
            }
        ]

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=1000,
            response_format={"type": "json_object"}
        )

        result = json.loads(response.choices[0].message.content)
        
        print("### AI Decision: ", result["decision"].upper(), "###")
        print(f"### Reason: {result['reason']} ###")
        print(f"### Confidence Score: {result['confidence_score']} ###")

        # 신뢰도 점수가 70 이상일 때만 매매 실행
        if float(result['confidence_score']) >= 70:
            if result["decision"] == "buy":
                my_krw = upbit.get_balance("KRW")
                if my_krw*0.9995 > 5000:
                    print("### Buy Order Executed ###")
                    print(upbit.buy_market_order("KRW-BTC", my_krw * 0.9995))
                else:
                    print("### Buy Order Failed: Insufficient KRW ###")
            elif result["decision"] == "sell":
                my_btc = upbit.get_balance("KRW-BTC")
                current_price = pyupbit.get_orderbook(ticker="KRW-BTC")['orderbook_units'][0]["ask_price"]
                if my_btc*current_price > 5000:
                    print("### Sell Order Executed ###")
                    print(upbit.sell_market_order("KRW-BTC", my_btc))
                else:
                    print("### Sell Order Failed: Insufficient BTC ###")
        else:
            print(f"### Trading Skipped: Low Confidence Score ({result['confidence_score']}) ###")

    except Exception as e:
        print(f"An error occurred: {e}")

# Main loop
while True:
    try:
        ai_trading()
        time.sleep(3600)  # 1시간마다 실행 (API 사용량 제한 고려)
    except Exception as e:
        print(f"An error occurred: {e}")
        time.sleep(300)  # 오류 발생 시 5분 후 재시도