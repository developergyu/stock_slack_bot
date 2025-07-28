import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from pykrx import stock
from matplotlib.backends.backend_pdf import PdfPages
import math
import os
import requests
import json
import feedparser
import urllib.parse

# 날짜 설정
end_date = datetime.today() + timedelta(days=1)
start_date = end_date - timedelta(days=60)
target_date = datetime.today()
target_dt = pd.to_datetime(target_date.strftime('%Y-%m-%d'))

# 시가총액 상위 100종목
today_str = datetime.today().strftime('%Y%m%d')
market_cap_df = stock.get_market_cap_by_ticker(today_str)
top100_df = market_cap_df.sort_values(by='시가총액', ascending=False).head(100)
top100_tickers = top100_df.index.to_list()
top100_names = [stock.get_market_ticker_name(ticker) for ticker in top100_tickers]
ticker_to_name = dict(zip([t + '.KS' for t in top100_tickers], top100_names))
yf_tickers = [ticker + '.KS' for ticker in top100_tickers]

# 주가 데이터 다운로드
kospi_ticker = '^KS11'
kospi_df = yf.download(kospi_ticker, start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))
stocks_df = yf.download(yf_tickers, start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))['Close']
kospi_close = kospi_df['Close'][kospi_ticker]

# 결합
combined_df = pd.DataFrame({'KOSPI': kospi_close})
for ticker in yf_tickers:
    if ticker in stocks_df.columns:
        combined_df[ticker] = stocks_df[ticker]
combined_df.fillna(method='ffill', inplace=True)
combined_df.dropna(inplace=True)

# 수익률 계산
returns = combined_df.pct_change().dropna()

def send_text_to_slack(text):
    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    CHANNEL_ID = "C097595CPF1"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {slack_token}'
    }
    payload = {
        "channel": CHANNEL_ID,
        "text": text
    }
    resp = requests.post("https://slack.com/api/chat.postMessage", headers=headers, json=payload)
    if resp.ok and resp.json().get("ok"):
        print("✅ Slack 메시지 전송 완료!")
    else:
        print("❌ Slack 메시지 전송 실패:", resp.text)

def get_google_news_rss(query, count=3):
    encoded_query = urllib.parse.quote_plus(query)  # 공백 등 URL 인코딩 처리
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(rss_url)
    news_items = []
    for entry in feed.entries[:count]:
        title = entry.title
        link = entry.link
        news_items.append(f"- {title}\n  {link}")
    return news_items

# 조건 검사 및 메시지 전송
if target_dt not in returns.index:
    msg = f"📉 `{target_date.strftime('%Y-%m-%d')}` 데이터가 없습니다."
    send_text_to_slack(msg)
    exit()

kospi_ret = returns.loc[target_dt]['KOSPI']
if kospi_ret >= 0:
    msg = (
        f"📈 *`{target_date.strftime('%Y-%m-%d')}` 코스피 지수 상승!*\n"
        f"> 🔴 *KOSPI 수익률:* `{kospi_ret:.2%}`\n"
    )
else:
    msg = (
        f"📉 *`{target_date.strftime('%Y-%m-%d')}` 코스피 지수 하락!*\n"
        f"> 🔵 *KOSPI 수익률:* `{kospi_ret:.2%}`\n"
    )
send_text_to_slack(msg)

# 상승 종목 추출
daily_returns = returns.loc[target_dt].drop('KOSPI')
up_stocks = daily_returns[daily_returns > 0]
up_stock_tickers = up_stocks.index.tolist()

if len(up_stock_tickers) == 0:
    print("상승 종목 없음.")
    exit()

# 상승 종목별 뉴스 수집 및 슬랙 메시지 생성
news_message = f"🗞️ *{target_date.strftime('%Y-%m-%d')} 상승 종목별 뉴스 요약 (최대 3건씩)*\n"
for ticker in up_stock_tickers:
    name = ticker_to_name.get(ticker, ticker)
    query = name
    news_list = get_google_news_rss(query, count=3)
    news_message += f"\n*{name} ({ticker})*\n"
    if news_list:
        news_message += "\n".join(news_list)
    else:
        news_message += "- 뉴스 없음\n"

# 슬랙으로 뉴스 메시지 전송
send_text_to_slack(news_message)

# 최근 1개월 데이터
plot_df = combined_df.loc[combined_df.index >= (target_dt - timedelta(days=30))]
normalized = plot_df / plot_df.iloc[0]

# PDF 저장 함수
def save_to_pdf(tickers, norm_df, name_map, filename=None):
    if filename is None:
        today = datetime.now().strftime("%Y%m%d")
        filename = f"report_{today}.pdf"
    per_page = 6
    total_pages = math.ceil(len(tickers) / per_page)

    with PdfPages(filename) as pdf:
        for page in range(total_pages):
            fig, axes = plt.subplots(2, 3, figsize=(12, 8))
            axes = axes.flatten()
            start = page * per_page

            for i in range(per_page):
                idx = start + i
                if idx >= len(tickers):
                    axes[i].axis('off')
                    continue
                ticker = tickers[idx]
                name = name_map.get(ticker, ticker)
                axes[i].plot(norm_df.index, norm_df[ticker], label=name, color='red')
                axes[i].plot(norm_df.index, norm_df['KOSPI'], label='KOSPI', linestyle='--', color='gray')
                axes[i].set_title(name)
                axes[i].tick_params(axis='x', rotation=30)
                axes[i].legend()
                axes[i].grid(True)

            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)
    print(f"✅ PDF 저장 완료: {filename}")
    return filename

# Slack에 PDF 업로드 및 메시지 보내기 함수
def send_pdf_to_slack(pdf_file_path):
    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    CHANNEL_ID = "C097595CPF1"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {slack_token}'
    }
    try:
        with open(pdf_file_path, 'rb') as f:
            content = f.read()
    except FileNotFoundError:
        content = None
    if content is not None:
        data = {
            "filename": pdf_file_path,
            "length": len(content),
        }
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        response = requests.post(url="https://slack.com/api/files.getUploadURLExternal", headers=headers, data=data)
    data = json.loads(response.text)
    upload_url = data.get("upload_url")
    file_id = data.get("file_id")
    upload_response = requests.post(url=upload_url, files={'file': content})
    print(upload_response.text)
    attachment = {
        "files": [{
            "id": file_id,
            "title": pdf_file_path
        }],
        "channel_id": CHANNEL_ID
    }
    headers['Content-Type'] = 'application/json; charset=utf-8'
    upload_response = requests.post(url="https://slack.com/api/files.completeUploadExternal", headers=headers, json=attachment)
    print(upload_response.text)
    print("✅ Slack 파일 업로드 및 메시지 전송 완료!")

# PDF 생성 및 슬랙 전송
pdf_path = save_to_pdf(up_stock_tickers, normalized, ticker_to_name)
send_pdf_to_slack(pdf_path)
