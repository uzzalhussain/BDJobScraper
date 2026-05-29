import feedparser
import firebase_admin
from firebase_admin import credentials, firestore, messaging
import schedule
import time
import hashlib
import json
import os
from datetime import datetime
from groq import Groq

# ========================================
# Environment Variables থেকে Keys নিন
# ========================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# ========================================
# Firebase Setup — Environment Variable থেকে
# ========================================
firebase_key = os.environ.get("FIREBASE_KEY", "")

if firebase_key:
    import json as json_module
    key_dict = json_module.loads(firebase_key)
    cred = credentials.Certificate(key_dict)
else:
    cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred)
db = firestore.client()
print("✅ Firebase connected!")

# ========================================
# Groq AI Setup
# ========================================
groq_client = Groq(api_key=GROQ_API_KEY)
print("✅ Groq AI connected!")

# ========================================
# Helper Functions
# ========================================
def generate_id(title):
    return hashlib.md5(title.encode('utf-8')).hexdigest()

def is_duplicate(job_id):
    try:
        doc = db.collection("jobs").document(job_id).get()
        return doc.exists
    except:
        return False

def save_job(title, organization, category, deadline,
             image_url="", pdf_url="", apply_link="", source=""):
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
        "publishDate": datetime.now().strftime("%Y-%m-%d"),
        "timestamp": int(datetime.now().timestamp()),
    }

    try:
        db.collection("jobs").document(job_id).set(job_data)
        print(f"✅ Saved: {title[:55]}")
        send_notification(title, organization)
        return True
    except Exception as e:
        print(f"❌ Save error: {e}")
        return False

def send_notification(title, organization):
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

def get_category(title, source_url):
    title_lower = title.lower()
    if any(w in title_lower for w in ["bank", "ব্যাংক", "banking"]):
        return "ব্যাংক"
    elif any(w in title_lower for w in ["ngo", "brac", "grameen", "care"]):
        return "NGO"
    elif any(w in title_lower for w in ["government", "সরকারি", "govt", "ministry", "মন্ত্রণালয়", "অধিদপ্তর", "bcs", "psc"]):
        return "সরকারি"
    elif "gov.bd" in source_url or "govt" in source_url:
        return "সরকারি"
    else:
        return "বেসরকারি"

# ========================================
# RSS Feeds
# ========================================
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
    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            if not feed.entries:
                continue
            print(f"   📡 {feed_info['source']}: {len(feed.entries)} entries")
            for entry in feed.entries[:15]:
                title = entry.get("title", "").strip()
                if not title or len(title) < 5:
                    continue
                apply_link = entry.get("link", "")
                category = get_category(title, feed_info["url"])
                if category == "বেসরকারি":
                    category = feed_info["default_category"]
                image_url = ""
                if hasattr(entry, "media_content"):
                    media = entry.get("media_content", [])
                    if media:
                        image_url = media[0].get("url", "")
                if save_job(title, "বিভিন্ন প্রতিষ্ঠান", category, "N/A", image_url, "", apply_link, feed_info["source"]):
                    total += 1
        except Exception as e:
            print(f"   ❌ {feed_info['source']}: {str(e)[:50]}")
        time.sleep(1)
    print(f"   RSS Total: {total}")
    return total

def generate_jobs_with_groq(category, query):
    prompt = f"""Generate 10 realistic Bangladesh job circulars for {datetime.now().strftime("%B %Y")}.
Category: {category}, Topic: {query}
Return ONLY JSON array:
[{{"title": "Job title in Bengali", "organization": "Org name", "deadline": "৩০ জুন ২০২৬", "apply_link": ""}}]"""
    try:
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000,
        )
        text = response.choices[0].message.content.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        jobs = json.loads(text)
        saved = 0
        for job in jobs:
            if save_job(job.get("title",""), job.get("organization","বিভিন্ন প্রতিষ্ঠান"),
                       category, job.get("deadline","N/A"), "", "", job.get("apply_link",""), "Groq AI"):
                saved += 1
        return saved
    except Exception as e:
        print(f"   ❌ Groq error: {str(e)[:80]}")
        return 0

def run_all_scrapers():
    print(f"\n{'='*50}")
    print(f"🚀 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    rss_total = scrape_rss_feeds()

    groq_total = 0
    for category, query in [
        ("সরকারি", "Bangladesh government job 2026"),
        ("ব্যাংক", "Bangladesh bank job 2026"),
        ("NGO", "Bangladesh NGO job 2026"),
        ("বেসরকারি", "Bangladesh private job 2026"),
    ]:
        groq_total += generate_jobs_with_groq(category, query)
        time.sleep(2)

    print(f"\n🎉 Total: {rss_total + groq_total} jobs saved!")
    print(f"✅ Done: {datetime.now().strftime('%H:%M:%S')}")

# ========================================
# Main
# ========================================
if __name__ == "__main__":
    run_all_scrapers()
    schedule.every(6).hours.do(run_all_scrapers)
    print("⏰ Scheduler running...")
    while True:
        schedule.run_pending()
        time.sleep(60)
