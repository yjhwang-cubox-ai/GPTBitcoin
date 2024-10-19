import os
import json
import time
import base64
import requests
from dotenv import load_dotenv
import pyupbit
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator
from ta.trend import MACD
from openai import OpenAI
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

# Load environment variables
load_dotenv()

# Function to encode the image in base64
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# Function to add technical indicators to OHLCV data
def add_indicators(df):
    # Bollinger Bands
    indicator_bb = BollingerBands(close=df["close"], window=20, window_dev=2)
    df['bb_mavg'] = indicator_bb.bollinger_mavg()
    df['bb_hband'] = indicator_bb.bollinger_hband()
    df['bb_lband'] = indicator_bb.bollinger_lband()

    # # RSI (Relative Strength Index)
    # rsi = RSIIndicator(close=df['close'], window=14)
    # df['rsi'] = rsi.rsi()

    # # MACD (Moving Average Convergence Divergence)
    # macd = MACD(close=df['close'], window_slow=26, window_fast=12, window_sign=9)
    # df['macd'] = macd.macd()
    # df['macd_signal'] = macd.macd_signal()
    # df['macd_diff'] = macd.macd_diff()

    return df

# Fetch Fear and Greed Index from API
def get_fear_and_greed_index():
    try:
        response = requests.get("https://api.alternative.me/fng/?limit=1")
        if response.status_code == 200:
            fng_data = response.json()
            return {
                "fng_value": fng_data['data'][0]['value'],
                "fng_classification": fng_data['data'][0]['value_classification']
            }
        else:
            print("Error fetching Fear and Greed Index")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error occurred while fetching Fear and Greed Index: {e}")
        return None

# Fetch and capture the chart using Selenium
def capture_chart():
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")

    driver = webdriver.Chrome(options=chrome_options)
    driver.get("https://upbit.com/full_chart?code=CRIX.UPBIT.KRW-BTC")

    try:
        # Click on the time frame menu
        menu_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]'))
        )
        menu_button.click()

        # Click the '1시간' (1-hour) option
        one_hour_option = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]/cq-menu-dropdown/cq-item[8]'))
        )
        one_hour_option.click()

        # Wait for the chart to update
        time.sleep(3)

        # Click on the 지표메뉴 (Indicator Menu)
        indicator_menu_button = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]'))
        )
        indicator_menu_button.click()

        # Click the 볼린저밴드 (Bollinger Bands) option
        bollinger_band_option = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[15]'))
        )
        bollinger_band_option.click()

        # Wait for the indicator to apply and capture the screenshot
        time.sleep(3)
        full_screenshot = driver.get_screenshot_as_png()

        # Save the screenshot
        screenshot_path = "chart_with_bollinger_bands.png"
        with open(screenshot_path, "wb") as file:
            file.write(full_screenshot)

        print("Screenshot with Bollinger Bands saved successfully!")
        return encode_image(screenshot_path)

    except Exception as e:
        print(f"An error occurred: {e}")
        return None

    finally:
        driver.quit()

# AI trading logic
def ai_trading():
    try:
        # 1. Fetch chart data
        print("Fetching daily and hourly chart data...")
        daily_df = pyupbit.get_ohlcv("KRW-BTC", count=30, interval="day")
        hourly_df = pyupbit.get_ohlcv("KRW-BTC", count=24, interval="minute60")

        if daily_df is None or hourly_df is None:
            print("Failed to fetch chart data.")
            return

        # 2. Add technical indicators
        daily_df = add_indicators(daily_df)
        hourly_df = add_indicators(hourly_df)

        # 3. Capture the chart and get base64-encoded image
        print("Capturing chart image...")
        base64_image = capture_chart()
        if base64_image is None:
            print("Failed to capture chart image.")
            return

        # 4. Fetch Fear and Greed Index
        print("Fetching Fear and Greed Index data...")
        fng_data = get_fear_and_greed_index()
        if fng_data is None:
            print("Failed to fetch Fear and Greed Index data.")
            return

        # 5. Get AI decision on trading, including the chart image
        client = OpenAI()
        print("Requesting AI decision with chart analysis...")

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert Bitcoin investor. Based on the provided data, including daily and hourly chart data, "
                        "Fear and Greed Index, and the attached chart image with Bollinger Bands, determine whether to buy, sell, or hold. "
                        "Provide reasoning in JSON format."
                    )
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "daily_chart": daily_df.to_dict(),
                        "hourly_chart": hourly_df.to_dict(),
                        "fear_and_greed_index": fng_data,
                        "chart_image": f"data:image/png;base64,{base64_image}"
                    })
                }
            ]
        )

        result = response.choices[0]['message']['content']
        decision_data = json.loads(result)

        if 'decision' not in decision_data or 'reason' not in decision_data:
            print("AI response format is incorrect.")
            return

        print(f"### AI Decision: {decision_data['decision'].upper()} ###")
        print(f"### Reason: {decision_data['reason']} ###")

    except Exception as e:
        print(f"An error occurred: {e}")

# Main loop
while True:
    ai_trading()
    print("Waiting for next round...")
    time.sleep(600)
