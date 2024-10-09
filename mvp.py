import os
import json
from dotenv import load_dotenv
import pyupbit
from openai import OpenAI
load_dotenv()

# 1. 업비트 차트 데이터 가져오기 (30일 일봉)
df = pyupbit.get_ohlcv("KRW-BTC", count=30, interval="day")
# print(df.to_json())

# 2. AI에게 데이터 제공하고 판단 받기
client = OpenAI()

response = client.chat.completions.create(
  model="gpt-4o",
  messages=[
    {
      "role": "system",
      "content": [
        {
          "type": "text",
          "text": "You're a Bitcoin investing expert.\nBased on the provided chart data, tell me whether to buy, sell, or hold at the moment.\nResponse in json format\n\nResponse Example:\n{\"decision\":\"buy\",\"reason\":\"some technical reason\"}\n{\"decision\":\"sell\",\"reason\":\"some technical reason\"}\n{\"decision\":\"hold\",\"reason\":\"some technical reason\"}"
        }
      ]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": df.to_json()
        }
      ]
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "text",
          "text": "{\"decision\":\"hold\",\"reason\":\"The recent trend shows fluctuations with no clear upward or downward trajectory. Despite some prior upward movements, the volume and value data suggest variable trading interest and a lack of strong momentum. It would be prudent to wait for a more definitive trend before making a buy or sell decision.\"}"
        }
      ]
    },
    {
      "role": "assistant",
      "content": [
        {
          "type": "text",
          "text": "{\"decision\":\"hold\",\"reason\":\"The recent trend shows fluctuations with no clear upward or downward trajectory. Despite some prior upward movements, the volume and value data suggest variable trading interest and a lack of strong momentum. It would be prudent to wait for a more definitive trend before making a buy or sell decision.\"}"
        }
      ]
    }
  ],
#   temperature=1,
#   max_tokens=256,
#   top_p=1,
#   frequency_penalty=0,
#   presence_penalty=0,
  response_format={
    "type": "json_object"
  }
)

result = response.choices[0].message.content

# 3. AI의 판단에 따라 실제로 자동매매 진행하기
access = os.getenv('UPBIT_ACCESS_KEY')
secret = os.getenv('UPBIT_SECRET_KEY')
upbit = pyupbit.Upbit(access, secret)

rerult = json.loads(result)

if result['decision'] == 'buy':
    print(upbit.buy_market_order("KRW-BTC", upbit.get_balance("KRW") * 0.9))
    print(result['reason'])
elif result['decision'] == 'sell':
    print(upbit.sell_market_order("KRW-BTC", upbit.get_balance("KRW-XRP")))
    print(result['reason'])
elif result['decision'] == 'hold':
    print(result['reason'])