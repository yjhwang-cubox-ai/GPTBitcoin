import os
from dotenv import load_dotenv
import pyupbit
import pandas as pd
import json
from openai import OpenAI, OpenAIError, RateLimitError, APIError, BadRequestError
import ta
from ta.utils import dropna
import time
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from youtube_transcript_api import YouTubeTranscriptApi
from pydantic import BaseModel
from typing import List

load_dotenv()

class TradingAnalysis(BaseModel):
    decision: str  # "buy", "sell", "hold" 중 하나
    reason: str
    confidence_score: int  # 0-100

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
            max_tokens=4095,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "trading_analysis",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "analysis_steps": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "indicator": {
                                            "type": "string"
                                        },
                                        "analysis": {
                                            "type": "string"
                                        }
                                    },
                                    "required": ["indicator", "analysis"],
                                    "additionalProperties": False
                                }
                            },
                            "market_analysis": {
                                "type": "object",
                                "properties": {
                                    "news_sentiment": {
                                        "type": "string"
                                    },
                                    "fear_greed": {
                                        "type": "string"
                                    },
                                    "youtube_insights": {
                                        "type": "string"
                                    }
                                },
                                "required": ["news_sentiment", "fear_greed", "youtube_insights"],
                                "additionalProperties": False
                            },
                            "technical_analysis": {
                                "type": "object",
                                "properties": {
                                    "trend": {
                                        "type": "string"
                                    },
                                    "support_level": {
                                        "type": "string"
                                    },
                                    "resistance_level": {
                                        "type": "string"
                                    }
                                },
                                "required": ["trend", "support_level", "resistance_level"],
                                "additionalProperties": False
                            },
                            "final_decision": {
                                "type": "object",
                                "properties": {
                                    "action": {
                                        "type": "string"
                                    },
                                    "reasoning": {
                                        "type": "string"
                                    }
                                },
                                "required": ["action", "reasoning"],
                                "additionalProperties": False
                            }
                        },
                        "required": [
                            "analysis_steps",
                            "market_analysis",
                            "technical_analysis",
                            "final_decision"
                        ],
                        "additionalProperties": False
                    }
                }
            }
        )

        result = json.loads(response.choices[0].message.content)
        print(result)


        print("\n=== AI 트레이딩 분석 결과 ===")
        print("\n[분석 단계]")
        for step in result['analysis_steps']:
            print(f"\n{step['indicator']}:")
            print(f"분석: {step['analysis']}")
            
        print("\n[시장 분석]")
        market = result['market_analysis']
        print(f"뉴스 심리: {market['news_sentiment']}")
        print(f"공포탐욕지수: {market['fear_greed']}")
        print(f"유튜브 분석: {market['youtube_insights']}")
        
        print("\n[기술적 분석]")
        tech = result['technical_analysis']
        print(f"트렌드: {tech['trend']}")
        print(f"지지선: {tech['support_level']}")
        print(f"저항선: {tech['resistance_level']}")
        
        print("\n[최종 결정]")
        decision = result['final_decision']
        print(f"행동: {decision['action'].upper()}")
        print(f"근거: {decision['reasoning']}")

    except Exception as e:
        print(f"에러 발생: {e}")
        if isinstance(e, RateLimitError):
            print("API 호출 한도 초과. 잠시 후 다시 시도하세요.")
        elif isinstance(e, APIError):
            print("OpenAI API 오류. 다시 시도하세요.")
        elif isinstance(e, BadRequestError):
            print("잘못된 요청입니다. API 파라미터를 확인하세요.")
        elif isinstance(e, OpenAIError):
            print("OpenAI 관련 기타 오류가 발생했습니다.")
        raise

if __name__ == "__main__":
    ai_trading()
