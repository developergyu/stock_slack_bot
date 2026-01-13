import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from matplotlib.backends.backend_pdf import PdfPages
import math
import os
import requests
import json
import feedparser
import urllib.parse

############################################
# 1. 기본 설정
############################################
KRX_API_KEY = os.environ.get("KRX_API_KEY")
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
CHANNEL_ID = "C097595CPF1"

KRX_URL = "https://openapi.krx.co.kr/svc/apis/sto/stk_bydd_trd"

headers_krx = {
    "AUTH_KEY": KRX_API_KEY
}

############################################
# 2. 날짜 설정
############################################
today = datetime.today()
target_dt = pd.to_datetime(today.strftime('%Y-%m-%d'))

# 전일 (단순 D-1, 필요시 영업일 계산 로직 추가 가능)
base_date = (today - timedelta(days=1)).strftime("%Y%m%d")

############################################
# 3. KRX 전일 시가총액 TOP100
############################################
resp = requests.get(
    KRX_URL,
    headers=headers_krx,
    params={"basDd": base_date}
)
resp.raise_for_status()

df_krx = pd.DataFrame(resp.json()["OutBlock_1"])
df_krx["MKTCAP"] = pd.to_numeric(df_krx["MKTCAP"], errors="coerce")
df_krx = df_krx.dropna(subset=["MKTCAP"])

top100_df = df_krx.sort_values("MKTCAP", ascending=False).head(100)

codes = top100_df["ISU_CD"].tolist()
names = dict(zip(top100_df["ISU_CD"], top100_df["ISU_NM"]))

yf_tickers = [f"{code}.KS" for code in codes]
ticker_to_name = {f"{code}.KS": name for code, name in names.items()}

############################################
# 4. yfinance 주가 데이터
############################################
start_date = today - timedelta(days=60)
end_date = today + timedelta(days=1)

kospi_df = yf.download("^KS11", start=start_date, end=end_date, progress=False)
stock_df = yf.download(yf_tickers, start=start_date, end=end_date, progress=False)["Close"]

kospi_close = kospi_df["Close"]

############################################
# 5. 데이터 결합 및 수익률
############################################
combined_df = pd.DataFrame({"KOSPI": kospi_close})

for t in yf_tickers:
    if t in stock_df.columns:
        combined_df[t] = stock_df[t]

combined_df = combined_df.ffill().dropna()
returns = combined_df.pct_change().dropna()

############################################
# 6. Slack 함수
############################################
def send_text_to_slack(text):
    headers = {
        "Authorization": f"Bearer {SLACK_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "channel": CHANNEL_ID,
        "text": text
    }
    requests.post("https://slack.com/api/chat.postMessage", headers=headers, json=payload)

############################################
# 7. Google News RSS
############################################
def get_google_news_rss(query, count=3):
    q = urllib.parse.quote_plus(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(url)

    items = []
    for e in feed.entries[:count]:
        items.append(f"- {e.title}\n  {e.link}")
    return items

############################################
# 8. 코스피 지수 메시지
############################################
if target_dt not in returns.index:
    send_text_to_slack(f"📉 `{target_dt.date()}` 데이터 없음")
    exit()

kospi_ret = returns.loc[target_dt]["KOSPI"]

if kospi_ret >= 0:
    send_text_to_slack(
        f"📈 *{target_dt.date()} 코스피 상승*\n"
        f"> 🔴 수익률: `{kospi_ret:.2%}`"
    )
else:
    send_text_to_slack(
        f"📉 *{target_dt.date()} 코스피 하락*\n"
        f"> 🔵 수익률: `{kospi_ret:.2%}`"
    )

############################################
# 9. 상승 종목 + 뉴스
############################################
daily_returns = returns.loc[target_dt].drop("KOSPI")
up_stocks = daily_returns[daily_returns > 0]

news_msg = f"🗞️ *{target_dt.date()} 상승 종목 뉴스 요약*\n\n"

for ticker in up_stocks.index:
    name = ticker_to_name.get(ticker, ticker)
    news = get_google_news_rss(name, 3)

    news_msg += f"*🔹 {name} ({ticker})*\n"
    news_msg += "\n".join(news) if news else "- 뉴스 없음"
    news_msg += "\n\n" + "─" * 30 + "\n\n"

send_text_to_slack(news_msg)

############################################
# 10. PDF 생성
############################################
plot_df = combined_df.loc[combined_df.index >= (target_dt - timedelta(days=30))]
normalized = plot_df / plot_df.iloc[0]

def save_to_pdf(tickers):
    filename = f"report_{target_dt.strftime('%Y%m%d')}.pdf"
    per_page = 6

    with PdfPages(filename) as pdf:
        for i in range(0, len(tickers), per_page):
            fig, axes = plt.subplots(2, 3, figsize=(12, 8))
            axes = axes.flatten()

            for ax, t in zip(axes, tickers[i:i+per_page]):
                ax.plot(normalized.index, normalized[t], label=ticker_to_name[t])
                ax.plot(normalized.index, normalized["KOSPI"], "--", label="KOSPI")
                ax.set_title(ticker_to_name[t])
                ax.legend()
                ax.grid()

            pdf.savefig(fig)
            plt.close(fig)

    return filename

pdf_path = save_to_pdf(up_stocks.index.tolist())

############################################
# 11. PDF Slack 업로드
############################################
def send_pdf_to_slack(path):
    with open(path, "rb") as f:
        content = f.read()

    headers = {
        "Authorization": f"Bearer {SLACK_TOKEN}"
    }

    res = requests.post(
        "https://slack.com/api/files.upload",
        headers=headers,
        files={"file": content},
        data={"channels": CHANNEL_ID, "filename": path}
    )
    print(res.text)

send_pdf_to_slack(pdf_path)
