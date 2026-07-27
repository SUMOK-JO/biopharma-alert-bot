import os
import re
import json
import html
import requests
import feedparser
import anthropic
from dotenv import load_dotenv
from datetime import date, datetime, timedelta, timezone
import smtplib
from email.mime.text import MIMEText

load_dotenv()
DART_API_KEY = os.getenv("DART_API_KEY")
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

SEEN_FILE = "seen_reports.json"
PENDING_FILE = "pending_digest.json"
DIGEST_HOUR_UTC = 0  # UTC 0시 = 한국시간 오전 9시, 이 시간대 실행에서만 다이제스트 발송

DART_KEYWORDS = [
    "기술수출", "라이선스아웃", "기술이전", "오픈이노베이션", "희귀질환치료제 지정",
    "라이선스인", "기술도입", "공동연구개발", "실시권", "옵션계약",
]
FDA_KEYWORDS = [
    "orphan drug", "rare disease", "breakthrough therapy", "accelerated approval",
    "priority review", "fast track designation", "biologics license", "gene therapy", "cell therapy",
]
PHARMA_CONTEXT_WORDS = ["제약", "바이오", "신약", "임상", "식약처", "치료제", "백신", "항체", "세포치료", "유전자치료"]
HIGH_PRIORITY_KEYWORDS = [
    "기술수출", "라이선스아웃", "기술이전", "희귀질환치료제 지정",
    "orphan drug", "breakthrough therapy", "accelerated approval",
]

FDA_RSS_URL = "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"
CLINICALTRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_ids):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids), f, ensure_ascii=False, indent=2)


def load_pending():
    if os.path.exists(PENDING_FILE):
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_pending(items):
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


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


def fetch_clinicaltrials():
    params = {"query.cond": "rare disease", "pageSize": 50, "format": "json"}
    try:
        res = requests.get(CLINICALTRIALS_URL, params=params, timeout=10)
        res.raise_for_status()
        return res.json().get("studies", [])
    except requests.exceptions.RequestException as e:
        print(f"ClinicalTrials.gov 요청 실패: {e}")
        return []


def fetch_naver_news():
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        print("네이버 API 키 없음 — 뉴스 소스 건너뜀")
        return []
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    all_items = []
    for kw in DART_KEYWORDS:
        params = {"query": kw, "display": 20, "sort": "date"}
        try:
            res = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=10)
            res.raise_for_status()
            all_items.extend(res.json().get("items", []))
        except requests.exceptions.RequestException as e:
            print(f"네이버 뉴스 API 요청 실패({kw}): {e}")
    return all_items


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
            matched.append({"id": f"FDA:{entry.get('link')}", "title": f"[FDA] {title}"})
    return matched


def filter_clinicaltrials(studies):
    matched = []
    for s in studies:
        try:
            ident = s["protocolSection"]["identificationModule"]
        except KeyError:
            continue
        nct_id = ident.get("nctId")
        title = ident.get("briefTitle", "")
        matched.append({"id": f"CT:{nct_id}", "title": f"[ClinicalTrials] {title} ({nct_id})"})
    return matched


def filter_naver(items):
    matched = []
    for item in items:
        title = html.unescape(re.sub("<.*?>", "", item.get("title", "")))
        desc = html.unescape(re.sub("<.*?>", "", item.get("description", "")))
        combined = title + " " + desc
        if not any(w in combined for w in PHARMA_CONTEXT_WORDS):
            continue
        link = item.get("link") or item.get("originallink")
        matched.append({
            "id": f"NAVER:{link}",
            "title": f"[뉴스] {title}\n   {desc}\n   {link}",
        })
    return matched


def dedupe(items):
    unique = {}
    for item in items:
        unique[item["id"]] = item
    return list(unique.values())


def is_high_priority(item):
    return any(kw in item["title"] for kw in HIGH_PRIORITY_KEYWORDS)


def summarize_items(items):
    if not items or not ANTHROPIC_API_KEY:
        return {}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    items_text = "\n".join(f"{i + 1}. {item['title']}" for i, item in enumerate(items))

    prompt = f"""다음은 제약바이오 관련 공시/뉴스 목록이다. 각 항목을 한국어로 한 줄(30자 이내)로 요약해줘.

{items_text}

아래 JSON 배열 형식으로만 답해줘. 다른 설명은 절대 붙이지 마.
[
  {{"index": 1, "summary": "한 줄 요약"}}
]
"""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
        results = json.loads(raw)
        return {r["index"]: r["summary"] for r in results}
    except Exception as e:
        print(f"AI 요약 실패: {e}")
        return {}


def format_item(item, index, summaries):
    summary = summaries.get(index)
    if summary:
        return f"[{summary}] {item['title']}"
    return item["title"]


def send_immediate_alert(items):
    if not items:
        return
    body = "\n\n".join(item["title"] for item in items)
    msg = MIMEText(body)
    msg["Subject"] = f"[B.A.B 긴급] 중요 항목 {len(items)}건"
    msg["From"] = f"B.A.B <{GMAIL_ADDRESS}>"
    msg["To"] = GMAIL_ADDRESS
    try:
        smtp = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
        smtp.starttls()
        smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())
        smtp.quit()
        print("긴급 알림 발송 완료")
    except Exception as e:
        print(f"긴급 알림 발송 실패: {e}")


def send_daily_digest(items):
    if not items:
        print("다이제스트 대상 없음 — 발송 생략")
        return True

    summaries = summarize_items(items)

    high_priority = [(i, item) for i, item in enumerate(items, start=1) if is_high_priority(item)]
    normal = [(i, item) for i, item in enumerate(items, start=1) if not is_high_priority(item)]

    lines = []
    if high_priority:
        lines.append(f"★ 중요 항목 ({len(high_priority)}건) — 오늘 긴급 알림으로 이미 받으신 것 포함")
        lines.append("=" * 40)
        for index, item in high_priority:
            lines.append("★ " + format_item(item, index, summaries))
        lines.append("")

    if normal:
        lines.append(f"일반 항목 ({len(normal)}건)")
        lines.append("=" * 40)
        for index, item in normal:
            lines.append(format_item(item, index, summaries))

    body = "\n\n".join(lines)
    msg = MIMEText(body)
    msg["Subject"] = f"[B.A.B] 일일 다이제스트 - 중요 {len(high_priority)} / 전체 {len(items)}건"
    msg["From"] = f"B.A.B <{GMAIL_ADDRESS}>"
    msg["To"] = GMAIL_ADDRESS
    try:
        smtp = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
        smtp.starttls()
        smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())
        smtp.quit()
        print("일일 다이제스트 발송 완료")
        return True
    except Exception as e:
        print(f"일일 다이제스트 발송 실패: {e}")
        return False


def main():
    seen_ids = load_seen()

    dart_matched = filter_dart(fetch_dart_disclosures())
    fda_matched = filter_fda(fetch_fda_news())
    ct_matched = filter_clinicaltrials(fetch_clinicaltrials())
    naver_matched = filter_naver(fetch_naver_news())

    all_matched = dedupe(dart_matched + fda_matched + ct_matched + naver_matched)
    new_items = [item for item in all_matched if item["id"] not in seen_ids]

    print(
        f"DART {len(dart_matched)}건, FDA {len(fda_matched)}건, "
        f"ClinicalTrials {len(ct_matched)}건, 뉴스 {len(naver_matched)}건 / 신규 {len(new_items)}건"
    )

    high_priority_now = [item for item in new_items if is_high_priority(item)]
    send_immediate_alert(high_priority_now)

    pending = load_pending()
    pending.extend(new_items)
    save_pending(pending)

    for item in new_items:
        seen_ids.add(item["id"])
    save_seen(seen_ids)

    current_hour_utc = datetime.now(timezone.utc).hour
    if current_hour_utc == DIGEST_HOUR_UTC and pending:
        digest_sent = send_daily_digest(pending)
        if digest_sent:
            save_pending([])


if __name__ == "__main__":
    main()