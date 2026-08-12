import feedparser
import firebase_admin
from firebase_admin import credentials, firestore, messaging
import hashlib
import time
import json
import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# Firebase Setup
firebase_key = os.environ.get("FIREBASE_KEY", "")
if firebase_key:
    key_dict = json.loads(firebase_key)
    cred = credentials.Certificate(key_dict)
else:
    cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred)
db = firestore.client()
print("✅ Firebase connected!")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BDJobNewsScraper/1.0)"}

# ---------------- Groq Setup ----------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

DEFAULT_EXTRACTION = {
    "organization": "বিভিন্ন প্রতিষ্ঠান",
    "deadline": "N/A",
    "positionCategory": "",
    "numberOfPosts": "",
}


def clean_html_text(html_content):
    """HTML theke plain text ber kore, Groq-e pathanor jonno"""
    if not html_content:
        return ""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        return text[:1000]
    except Exception:
        return html_content[:1000]


def extract_job_details_with_groq(title, summary_text):
    """
    Groq AI diye title + summary theke organization, deadline,
    positionCategory, numberOfPosts extract kore.
    Groq key na thakle ba error hole default value return kore.
    """
    if not GROQ_API_KEY:
        return DEFAULT_EXTRACTION.copy()

    prompt = f"""নিচের বাংলাদেশি চাকরির বিজ্ঞপ্তির শিরোনাম ও বিবরণ পড়ে তথ্য বের করো।
শুধুমাত্র নিচের JSON ফরম্যাটে উত্তর দাও, অন্য কোনো লেখা, ব্যাখ্যা বা মার্কডাউন যোগ কোরো না।

শিরোনাম: {title}
বিবরণ: {summary_text}

JSON ফরম্যাট:
{{
  "organization": "প্রতিষ্ঠান/কোম্পানির নাম, না পেলে খালি স্ট্রিং",
  "deadline": "আবেদনের শেষ তারিখ (যেমন: ৩০ আগস্ট ২০২৬), না পেলে খালি স্ট্রিং",
  "positionCategory": "পদের নাম বা ক্যাটাগরি (যেমন: সহকারী পরিচালক, অফিসার), না পেলে খালি স্ট্রিং",
  "numberOfPosts": "মোট পদ সংখ্যা (যেমন: ৭২ বা অনির্ধারিত), না পেলে খালি স্ট্রিং"
}}"""

    try:
        resp = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 300,
            },
            timeout=15,
        )

        if resp.status_code != 200:
            print(f"  [Groq] API error {resp.status_code}: {resp.text[:100]}")
            return DEFAULT_EXTRACTION.copy()

        content = resp.json()["choices"][0]["message"]["content"].strip()
        # Kono somoy Groq ```json ... ``` wrap kore dey, seta clean kori
        content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()

        data = json.loads(content)
        return {
            "organization": (data.get("organization") or "").strip() or DEFAULT_EXTRACTION["organization"],
            "deadline": (data.get("deadline") or "").strip() or DEFAULT_EXTRACTION["deadline"],
            "positionCategory": (data.get("positionCategory") or "").strip(),
            "numberOfPosts": (data.get("numberOfPosts") or "").strip(),
        }

    except json.JSONDecodeError:
        print(f"  [Groq] JSON parse failed for: {title[:40]}")
        return DEFAULT_EXTRACTION.copy()
    except Exception as e:
        print(f"  [Groq] Extraction failed: {str(e)[:80]}")
        return DEFAULT_EXTRACTION.copy()


# ---------------- Duplicate / ID helpers ----------------

def generate_id(title):
    return hashlib.md5(title.encode('utf-8')).hexdigest()


def is_duplicate(job_id):
    try:
        doc = db.collection("jobs").document(job_id).get()
        return doc.exists
    except:
        return False


# ---------------- Image extraction (5-layer fallback) ----------------

def extract_image_from_entry(entry):
    # Method 1: media:content
    if hasattr(entry, "media_content") and entry.media_content:
        url = entry.media_content[0].get("url", "")
        if url:
            return url

    # Method 2: media:thumbnail
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        url = entry.media_thumbnail[0].get("url", "")
        if url:
            return url

    # Method 3: enclosure
    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image"):
                url = enc.get("href", "") or enc.get("url", "")
                if url:
                    return url

    # Method 4: summary/content theke <img> tag
    html_content = ""
    if hasattr(entry, "content") and entry.content:
        html_content = entry.content[0].get("value", "")
    elif hasattr(entry, "summary"):
        html_content = entry.summary or ""

    if html_content:
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html_content)
        if img_match:
            url = img_match.group(1)
            if url and not url.startswith("data:") and len(url) > 10:
                return url

    # Method 5: post page theke og:image scrape (last resort)
    link = getattr(entry, "link", None)
    if link:
        try:
            resp = requests.get(link, headers=HEADERS, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")

                og = soup.find("meta", property="og:image")
                if og and og.get("content"):
                    return og["content"]

                tw = soup.find("meta", attrs={"name": "twitter:image"})
                if tw and tw.get("content"):
                    return tw["content"]

                article = soup.find("article") or soup.find("div", class_=re.compile(r"entry-content|post-content|content"))
                if article:
                    img = article.find("img")
                    if img:
                        src = img.get("src") or img.get("data-src", "")
                        if src and not src.startswith("data:"):
                            return src
        except Exception as e:
            print(f"  [og:image] Failed: {e}")

    return ""


# ---------------- Save & Notify ----------------

def save_job(title, organization, category, deadline, image_url="", pdf_url="",
             apply_link="", source="", position_category="", number_of_posts=""):
    if not title or len(title) < 5:
        return False
    job_id = generate_id(title)
    if is_duplicate(job_id):
        print(f"⏭️  Already exists: {title[:40]}")
        return False
    job_data = {
        "title": title,
        "organization": organization,
        "category": category,
        "deadline": deadline,
        "imageUrl": image_url,
        "pdfUrl": pdf_url,
        "applyLink": apply_link,
        "source": source,
        "positionCategory": position_category,
        "numberOfPosts": number_of_posts,
        "publishDate": datetime.now().strftime("%Y-%m-%d"),
        "timestamp": int(datetime.now().timestamp()),
    }
    try:
        db.collection("jobs").document(job_id).set(job_data)
        img_status = "🖼️" if image_url else "📄"
        print(f"✅ {img_status} Saved: {title[:50]}")
        send_notification(title)
        return True
    except Exception as e:
        print(f"❌ Save error: {e}")
        return False


def send_notification(title):
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title="নতুন চাকরি! 🎉",
                body=f"{title[:60]}"
            ),
            topic="all_jobs",
        )
        messaging.send(message)
        print(f"🔔 Notification sent!")
    except Exception as e:
        print(f"❌ Notification error: {e}")


def get_category(title, default):
    t = title.lower()
    if any(w in t for w in ["bank", "ব্যাংক", "banking"]):
        return "ব্যাংক"
    elif any(w in t for w in ["ngo", "brac", "grameen"]):
        return "NGO"
    elif any(w in t for w in ["সরকারি", "govt", "ministry", "মন্ত্রণালয়", "অধিদপ্তর", "bcs", "psc", "পুলিশ"]):
        return "সরকারি"
    return default


RSS_FEEDS = [
    {"url": "https://www.bdgovtjob.net/feed/", "default_category": "সরকারি", "source": "bdgovtjob.net"},
    {"url": "https://ejobsbd.com/feed/", "default_category": "বেসরকারি", "source": "ejobsbd.com"},
    {"url": "https://ejobsbd.com/category/government-job/feed/", "default_category": "সরকারি", "source": "ejobsbd.com"},
    {"url": "https://ejobsbd.com/category/bank-job/feed/", "default_category": "ব্যাংক", "source": "ejobsbd.com"},
    {"url": "https://ejobsbd.com/category/ngo-job/feed/", "default_category": "NGO", "source": "ejobsbd.com"},
    {"url": "https://ejobsbd.com/category/private-job/feed/", "default_category": "বেসরকারি", "source": "ejobsbd.com"},
    {"url": "https://bdjobstoday.info/feed/", "default_category": "বেসরকারি", "source": "bdjobstoday.info"},
    {"url": "https://jobbd24.com/feed/", "default_category": "বেসরকারি", "source": "jobbd24.com"},
]


def scrape_rss_feeds():
    print("\n📡 RSS Feed Scraping...")
    total = 0
    img_found = 0
    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            if not feed.entries:
                print(f"   ⚠️  No data: {feed_info['source']}")
                continue
            print(f"   📡 {feed_info['source']}: {len(feed.entries)} entries")
            for entry in feed.entries[:15]:
                title = entry.get("title", "").strip()
                if not title or len(title) < 5:
                    continue

                job_id = generate_id(title)
                if is_duplicate(job_id):
                    print(f"⏭️  Already exists: {title[:40]}")
                    continue

                apply_link = entry.get("link", "")
                category = get_category(title, feed_info["default_category"])

                image_url = extract_image_from_entry(entry)
                if image_url:
                    img_found += 1

                # Summary theke plain text ber kore Groq-ke deই
                raw_summary = entry.get("summary", "") or ""
                summary_text = clean_html_text(raw_summary)

                extracted = extract_job_details_with_groq(title, summary_text)
                time.sleep(0.3)  # Groq free-tier rate limit respect korar jonno

                if save_job(
                    title=title,
                    organization=extracted["organization"],
                    category=category,
                    deadline=extracted["deadline"],
                    image_url=image_url,
                    pdf_url="",
                    apply_link=apply_link,
                    source=feed_info["source"],
                    position_category=extracted["positionCategory"],
                    number_of_posts=extracted["numberOfPosts"],
                ):
                    total += 1
        except Exception as e:
            print(f"   ❌ {feed_info['source']}: {str(e)[:50]}")
        time.sleep(1)
    print(f"   ✅ Total saved: {total} | 🖼️ With image: {img_found}")
    return total


def run_all_scrapers():
    print(f"\n{'='*50}")
    print(f"🚀 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")
    total = scrape_rss_feeds()
    print(f"\n🎉 Done! Total: {total} jobs saved!")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    run_all_scrapers()
