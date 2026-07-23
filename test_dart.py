import os
import json
import requests
import feedparser
from dotenv import load_dotenv
from datetime import date, timedelta
import smtplib
from email.mime.text import MIMEText

load_dotenv()
DART_API_KEY = os.getenv("DART_API_KEY")
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

SEEN_FILE = "seen_reports.json"

DART_KEYWORDS = ["기술수출", "라이선스아웃", "기술이전", "오픈이노베이션", "희귀질환치료제 지정"]
FDA_KEYWORDS = ["orphan drug", "rare disease", "breakthrough therapy", "accelerated approval"]
FDA_RSS_URL = "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_ids):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids), f, ensure_ascii=False, indent=2)


def fetch_dart_disclosures():
    url = "https://opendart.fss.or.kr/api/list.json"
    end_de = date.today().strftime("%Y%m%d")
    bgn_de = (date.today() - timedelta(days=2)).strftime("%Y%m%d")

    all_items = []
    page_no = 1
    while True:
        params = {
            "crtfc_key": DART_API_KEY,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": page_no,
            "page_count": 100,
        }
        try:
            res = requests.get(url, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()
        except requests.exceptions.RequestException as e:
            print(f"DART API 요청 실패: {e}")
            return all_items

        status = data.get("status")
        if status != "000":
            print(f"DART API 응답: {status} {data.get('message')}")
            break

        all_items.extend(data.get("list", []))

        total_page = data.get("total_page", 1)
        if page_no >= total_page:
            break
        page_no += 1

    return all_items


def fetch_fda_news():
    try:
        feed = feedparser.parse(FDA_RSS_URL)
        return feed.entries
    except Exception as e:
        print(f"FDA RSS 요청 실패: {e}")
        return []


def filter_dart(disclosures):
    matched = []
    for item in disclosures:
        if any(kw in item["report_nm"] for kw in DART_KEYWORDS):
            matched.append({
                "id": f"DART:{item['rcept_no']}",
                "title": f"[DART] {item['corp_name']} - {item['report_nm']}",
            })
    return matched


def filter_fda(entries):
    matched = []
    for entry in entries:
        title = entry.get("title", "")
        if any(kw.lower() in title.lower() for kw in FDA_KEYWORDS):
            matched.append({
                "id": f"FDA:{entry.get('link')}",
                "title": f"[FDA] {title}",
            })
    return matched


def send_email(items):
    if not items:
        print("신규 매칭 없음 — 이메일 발송 생략")
        return
    body = "\n".join(item["title"] for item in items)
    msg = MIMEText(body)
    msg["Subject"] = f"[바이오 공시/뉴스 알림] 신규 {len(items)}건"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = GMAIL_ADDRESS
    try:
        smtp = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
        smtp.starttls()
        smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())
        smtp.quit()
        print("이메일 발송 완료")
    except smtplib.SMTPException as e:
        print(f"이메일 발송 실패: {e}")


def main():
    seen_ids = load_seen()

    dart_matched = filter_dart(fetch_dart_disclosures())
    fda_matched = filter_fda(fetch_fda_news())
    all_matched = dart_matched + fda_matched
    new_items = [item for item in all_matched if item["id"] not in seen_ids]

    print(f"DART 매칭 {len(dart_matched)}건, FDA 매칭 {len(fda_matched)}건, 신규 {len(new_items)}건")
    send_email(new_items)

    for item in new_items:
        seen_ids.add(item["id"])
    save_seen(seen_ids)


if __name__ == "__main__":
    main()