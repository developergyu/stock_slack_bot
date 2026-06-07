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

KRX_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"

headers_krx = {
    "AUTH_KEY": KRX_API_KEY
}

############################################
# 2. 날짜 설정
############################################
today = datetime.today() - 3
target_dt = pd.to_datetime(today.strftime('%Y-%m-%d'))

# 전일 (단순 D-1, 필요시 영업일 계산 로직 추가 가능)
##base_date = (today - timedelta(days=1)).strftime("%Y%m%d")

def get_latest_business_day(base_date, max_retry=10):
    """
    주어진 날짜부터 과거로 내려가면서
    데이터가 존재하는 최근 영업일을 찾는다.
    """
    for i in range(max_retry):
        check_date = (base_date - timedelta(days=i)).strftime("%Y%m%d")

        resp = requests.get(
            KRX_URL,
            headers=headers_krx,
            params={"basDd": check_date}
        )

        if resp.status_code != 200:
            continue

        data = resp.json().get("OutBlock_1", [])
        if data:  # 데이터 있으면 성공
            print(f"✅ 사용 날짜: {check_date}")
            return check_date

    raise Exception("최근 영업일 데이터를 찾을 수 없습니다.")
    
base_date = get_latest_business_day(datetime.today())
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

top100_df = (
    df_krx[["ISU_CD", "ISU_NM", "MKTCAP"]]
    .dropna()
    .astype(str)
)

# 종목코드 정제 (숫자 6자리만 허용)
top100_df = top100_df[top100_df["ISU_CD"].str.match(r"^\d{6}$")]

top100_df["MKTCAP"] = pd.to_numeric(top100_df["MKTCAP"], errors="coerce")
top100_df = top100_df.dropna(subset=["MKTCAP"])

top100_df = top100_df.sort_values("MKTCAP", ascending=False).head(100)

codes = top100_df["ISU_CD"].tolist()
names = top100_df.set_index("ISU_CD")["ISU_NM"].to_dict()

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
combined_df = kospi_df[["Close"]].rename(columns={"Close": "KOSPI"})

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
    if query is None:
        return []

    # ⭐ 무조건 문자열로 변환
    query = str(query).strip()

    if query == "" or query.lower() == "nan":
        return []

    encoded_query = urllib.parse.quote_plus(query)
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"

    feed = feedparser.parse(rss_url)

    news_items = []
    for entry in feed.entries[:count]:
        title = entry.title
        link = entry.link
        news_items.append(f"- {title}\n  {link}")

    return news_items

############################################
# 8. 코스피 지수 메시지
############################################
if target_dt not in returns.index:
    send_text_to_slack(f"📉 `{target_dt.date()}` 데이터 없음")
    exit()

kospi_ret = returns.loc[target_dt]["KOSPI"]

if isinstance(kospi_ret, pd.Series):
    kospi_ret = kospi_ret.iloc[-1]

if kospi_ret >= 0:
    msg = (
        f"📈 *`{target_dt.strftime('%Y-%m-%d')}` 코스피 지수 상승!*\n"
        f"> 🔴 *KOSPI 수익률:* `{kospi_ret:.2%}`\n"
    )
else:
    msg = (
        f"📉 *`{target_dt.strftime('%Y-%m-%d')}` 코스피 지수 하락!*\n"
        f"> 🔵 *KOSPI 수익률:* `{kospi_ret:.2%}`\n"
    )

send_text_to_slack(msg)


############################################
# 9. 상승 종목 + 뉴스
############################################
daily_returns = returns.loc[target_dt].drop("KOSPI")
up_stocks = daily_returns[daily_returns > 0]
up_stock_tickers = up_stocks.index.tolist()

daily_returns = returns.loc[target_dt].drop('KOSPI')
up_stocks = daily_returns[daily_returns > 0]
up_stock_tickers = up_stocks.index.tolist()

if len(up_stock_tickers) == 0:
    print("상승 종목 없음.")
    exit()

# 상승 종목별 뉴스 수집 및 슬랙 메시지 생성
news_message = f"🗞️ *{target_dt.strftime('%Y-%m-%d')} 상승 종목별 뉴스 요약 (최대 3건씩)*\n\n"
for ticker in up_stock_tickers:
    ticker_tuple = ticker[0]           # ('005930.KS', '')
    ticker = ticker_tuple
    name = ticker_to_name.get(ticker, ticker)
    query = name
    news_list = get_google_news_rss(query, count=3)
    # 종목명 강조 및 구분선 추가
    news_message += f"*🔹🔹🔹🔹 {name} ({ticker})🔹🔹🔹🔹*\n"
    if news_list:
        news_message += "\n".join(news_list) + "\n"
    else:
        news_message += "- 뉴스 없음\n"
    news_message += "\n" + ("─" * 30) + "\n\n"  # 구분선

# 슬랙으로 뉴스 메시지 전송
send_text_to_slack(news_message)

############################################
# 10. PDF 생성
############################################
plot_df = combined_df.loc[combined_df.index >= (target_dt - timedelta(days=30))]
normalized = plot_df / plot_df.iloc[0]

def save_to_pdf(tickers, norm_df, name_map, filename=None):
    if filename is None:
        today = datetime.now().strftime("%Y%m%d")
        filename = f"report_{today}.pdf"
    per_page = 6
    total_pages = math.ceil(len(tickers) / per_page)

    plt.rcParams['font.family'] = 'NanumGothic'   # Linux
    plt.rcParams['axes.unicode_minus'] = False      # 음수 기호 깨짐 방지

    # PDF 저장
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
                ticker_tuple = tickers[idx]           # ('005930.KS', '')
                ticker = ticker_tuple[0]              # '005930.KS'
                name = name_map.get(ticker, ticker)   # 종목명 가져오기
                axes[i].plot(norm_df.index, norm_df[ticker], label=name, color='red')
                axes[i].plot(norm_df.index, norm_df['KOSPI'], label='KOSPI', linestyle='--', color='gray')
                axes[i].set_title(name)               # 한글 깨짐 해결됨
                axes[i].tick_params(axis='x', rotation=30)
                axes[i].legend()
                axes[i].grid(True)

            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

    print(f"✅ PDF 저장 완료: {filename}")
    return filename

# pdf_path = save_to_pdf(up_stocks.index.tolist())


############################################
# 11. PDF Slack 업로드
############################################
def send_pdf_to_slack(pdf_file_path):
    
    CHANNEL_ID = "C097595CPF1"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {SLACK_TOKEN}'
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
        result = response.json()
        # 이 부분을 꼭 확인해 보세요!
        print(f"디버그용 응답 결과: {result}")
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


# def send_message_with_file_notice(filename):
#     headers = {
#         "Authorization": f"Bearer {SLACK_TOKEN}",
#         "Content-Type": "application/json"
#     }

#     payload = {
#         "channel": CHANNEL_ID,
#         "text": f"📄 오늘의 주식 리포트가 업로드되었습니다\n• 파일명: `{filename}`"
#     }

#     res = requests.post(
#         "https://slack.com/api/chat.postMessage",
#         headers=headers,
#         json=payload
#     )
#     print(res.text)


# if pdf_path:
pdf_path = save_to_pdf(up_stock_tickers, normalized, ticker_to_name)
send_pdf_to_slack(pdf_path)
