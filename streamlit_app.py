import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import os

# 페이지 기본 설정
st.set_page_config(
    page_title="Trading History Dashboard", page_icon="📈", layout="wide"
)


# 데이터베이스 연결 함수
def get_connection():
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "trading_history.db"
    )
    return sqlite3.connect(db_path)


# 데이터 로드 함수
def load_data():
    conn = get_connection()
    query = "SELECT * FROM trades ORDER BY timestamp DESC"
    df = pd.read_sql_query(query, conn)
    conn.close()

    # timestamp를 datetime으로 변환
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


# 대시보드 제목
st.title("📊 Trading History Dashboard")

# 데이터 로드
df = load_data()

# 기본 통계 지표
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Trades", len(df))
with col2:
    profit_trades = len(df[df["decision"] == "SELL"])
    st.metric("Total Sells", profit_trades)
with col3:
    latest_btc = df.iloc[0]["btc_balance"] if not df.empty else 0
    st.metric("Current BTC Balance", f"{latest_btc:.8f}")
with col4:
    latest_krw = df.iloc[0]["krw_balance"] if not df.empty else 0
    st.metric("Current KRW Balance", f"{latest_krw:,.0f}")

# 차트 섹션
st.subheader("📈 Price and Balance History")
tab1, tab2 = st.tabs(["Price History", "Balance History"])

with tab1:
    # BTC 가격 변화 차트
    fig_price = px.line(
        df, x="timestamp", y="btc_krw_price", title="BTC-KRW Price History"
    )
    st.plotly_chart(fig_price, use_container_width=True)

with tab2:
    # BTC의 KRW 가치 계산
    df['btc_value_in_krw'] = df['btc_balance'] * df['btc_krw_price']
    # 총 KRW 가치 계산
    df['total_value_in_krw'] = df['btc_value_in_krw'] + df['krw_balance']
    
    # 잔고 변화 차트
    fig_balance = go.Figure()
    
    # BTC 잔고
    fig_balance.add_trace(
        go.Scatter(x=df["timestamp"], y=df["btc_balance"], name="BTC Balance", yaxis="y")
    )
    
    # KRW 잔고
    fig_balance.add_trace(
        go.Scatter(
            x=df["timestamp"], y=df["krw_balance"], name="KRW Balance", yaxis="y2"
        )
    )
    
    # 총 KRW 가치
    fig_balance.add_trace(
        go.Scatter(
            x=df["timestamp"], 
            y=df["total_value_in_krw"], 
            name="Total Value (KRW)", 
            yaxis="y3",
            line=dict(color='green')
        )
    )
    
    # 레이아웃 업데이트
    fig_balance.update_layout(
        title="Balance History",
        yaxis=dict(
            title="BTC Balance",
            titlefont=dict(color="#1f77b4"),
            tickfont=dict(color="#1f77b4")
        ),
        yaxis2=dict(
            title="KRW Balance",
            overlaying="y",
            side="right",
            titlefont=dict(color="#ff7f0e"),
            tickfont=dict(color="#ff7f0e")
        ),
        yaxis3=dict(
            title="Total Value (KRW)",
            overlaying="y",
            side="right",
            position=0.85,
            titlefont=dict(color="green"),
            tickfont=dict(color="green")
        ),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )
    st.plotly_chart(fig_balance, use_container_width=True)

# 거래 기록 테이블
st.subheader("📝 Trading History")
# 표시할 컬럼 선택
display_columns = [
    "timestamp",
    "decision",
    "percentage",
    "reason",
    "btc_balance",
    "krw_balance",
    "btc_krw_price",
]
# 데이터 포맷팅
formatted_df = df[display_columns].copy()
formatted_df["timestamp"] = formatted_df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
formatted_df["btc_balance"] = formatted_df["btc_balance"].map("{:.8f}".format)
formatted_df["krw_balance"] = formatted_df["krw_balance"].map("{:,.0f}".format)
formatted_df["btc_krw_price"] = formatted_df["btc_krw_price"].map("{:,.0f}".format)

# 테이블 표시
st.dataframe(formatted_df, use_container_width=True)

# 필터링 섹션
st.subheader("🔍 Filters")
col1, col2 = st.columns(2)
with col1:
    decision_filter = st.multiselect(
        "Filter by Decision",
        options=df["decision"].unique(),
        default=df["decision"].unique(),
    )
with col2:
    date_range = st.date_input(
        "Select Date Range",
        value=(df["timestamp"].min().date(), df["timestamp"].max().date()),
    )
