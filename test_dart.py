import os
import json
import requests
from dotenv import load_dotenv
from datetime import date, timedelta
import smtplib
from email.mime.text import MIMEText

load_dotenv()
API_KEY = os.getenv("DART_API_KEY")

SEEN_FILE = "seen_reports.json"

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_seen(seen_ids):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, ensure_ascii=False, indent=2)

url = "https://opendart.fss.or.kr/api/list.json"
end_de = date.today().strftime("%Y%m%d")
bgn_de = (date.today() - timedelta(days=7)).strftime("%Y%m%d")

params = {
    "crtfc_key": API_KEY,
    "bgn_de": bgn_de,
    "end_de": end_de,
    "page_no": 1,
    "page_count": 20,
}

res = requests.get(url, params=params)
data = res.json()
print("응답 상태:", data.get("status"), data.get("message"))

KEYWORDS = ["기술수출", "라이선스아웃", "기술이전", "오픈이노베이션", "희귀질환치료제 지정"]

def filter_by_keywords(disclosures, keywords):
    matched = []
    for item in disclosures:
        if any(kw in item["report_nm"] for kw in keywords):
            matched.append(item)
    return matched

def send_email(matched):
    if not matched:
        print("신규 매칭 없음 — 이메일 발송 생략")
        return
    body = "\n".join(f"{item['corp_name']} - {item['report_nm']}" for item in matched)
    msg = MIMEText(body)
    msg["Subject"] = f"[공시 알림] 신규 {len(matched)}건"
    msg["From"] = os.getenv("GMAIL_ADDRESS")
    msg["To"] = os.getenv("GMAIL_ADDRESS")

    smtp = smtplib.SMTP("smtp.gmail.com", 587)
    smtp.starttls()
    smtp.login(os.getenv("GMAIL_ADDRESS"), os.getenv("GMAIL_APP_PASSWORD"))
    smtp.sendmail(os.getenv("GMAIL_ADDRESS"), os.getenv("GMAIL_ADDRESS"), msg.as_string())
    smtp.quit()
    print("이메일 발송 완료")

disclosures = data.get("list", [])
matched = filter_by_keywords(disclosures, KEYWORDS)

seen_ids = load_seen()
new_matched = [item for item in matched if item["rcept_no"] not in seen_ids]

print(f"전체 {len(disclosures)}건 중 키워드 일치 {len(matched)}건, 신규 {len(new_matched)}건")
send_email(new_matched)

for item in new_matched:
    seen_ids.add(item["rcept_no"])
save_seen(seen_ids)