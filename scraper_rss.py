import feedparser
import firebase_admin
from firebase_admin import credentials, firestore, messaging
import schedule
import time
import hashlib
from datetime import datetime

# ========================================
# Firebase Setup
# ========================================
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()
print("✅ Firebase connected!")

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
    elif any(w in title_lower for w in ["ngo", "brac", "grameen", "care", "save the children"]):
        return "NGO"
    elif any(w in title_lower for w in ["government", "সরকারি", "govt", "ministry", "মন্ত্রণালয়", "পরিষদ", "বিসিএস", "bcs", "psc"]):
        return "সরকারি"
    elif "govt" in source_url or "gov.bd" in source_url:
        return "সরকারি"
    elif "bank" in source_url:
        return "ব্যাংক"
    elif "ngo" in source_url:
        return "NGO"
    else:
        return "বেসরকারি"

# ========================================
# RSS Feed গুলো
# ========================================
RSS_FEEDS = [
    # সরকারি
    {
        "url": "https://ejobsbd.com/category/government-job/feed/",
        "default_category": "সরকারি",
        "source": "ejobsbd.com"
    },
    {
        "url": "https://bdjobstoday.info/category/government-jobs/feed/",
        "default_category": "সরকারি",
        "source": "bdjobstoday.info"
    },
    {
        "url": "https://www.bdgovtjob.net/feed/",
        "default_category": "সরকারি",
        "source": "bdgovtjob.net"
    },
    # ব্যাংক
    {
        "url": "https://ejobsbd.com/category/bank-job/feed/",
        "default_category": "ব্যাংক",
        "source": "ejobsbd.com"
    },
    # NGO
    {
        "url": "https://ejobsbd.com/category/ngo-job/feed/",
        "default_category": "NGO",
        "source": "ejobsbd.com"
    },
    # বেসরকারি
    {
        "url": "https://ejobsbd.com/category/private-job/feed/",
        "default_category": "বেসরকারি",
        "source": "ejobsbd.com"
    },
    {
        "url": "https://bdjobstoday.info/feed/",
        "default_category": "বেসরকারি",
        "source": "bdjobstoday.info"
    },
    {
        "url": "https://www.chakribd.com/feed/",
        "default_category": "বেসরকারি",
        "source": "chakribd.com"
    },
    {
        "url": "https://jobbd24.com/feed/",
        "default_category": "বেসরকারি",
        "source": "jobbd24.com"
    },
    {
        "url": "https://www.bdnewjobs.com/feed/",
        "default_category": "বেসরকারি",
        "source": "bdnewjobs.com"
    },
]

# ========================================
# RSS Feed Scraper
# ========================================
def scrape_rss_feed(feed_info):
    url = feed_info["url"]
    default_category = feed_info["default_category"]
    source = feed_info["source"]
    saved = 0

    try:
        feed = feedparser.parse(url)

        if feed.bozo and not feed.entries:
            print(f"   ⚠️  No data: {source}")
            return 0

        print(f"   📡 {source}: {len(feed.entries)} entries found")

        for entry in feed.entries[:15]:
            try:
                # Title
                title = entry.get("title", "").strip()
                if not title or len(title) < 5:
                    continue

                # Link
                apply_link = entry.get("link", "")

                # Organization — summary থেকে বের করার চেষ্টা
                summary = entry.get("summary", "") or entry.get("description", "")
                organization = "বিভিন্ন প্রতিষ্ঠান"

                # Category
                category = get_category(title, url)
                if category == "বেসরকারি":
                    category = default_category

                # Image
                image_url = ""
                if hasattr(entry, "media_content"):
                    media = entry.get("media_content", [])
                    if media:
                        image_url = media[0].get("url", "")

                if not image_url and "enclosures" in entry:
                    for enc in entry.enclosures:
                        if "image" in enc.get("type", ""):
                            image_url = enc.get("href", "")
                            break

                # Deadline
                deadline = "N/A"
                if summary:
                    import re
                    date_patterns = [
                        r'\d{1,2}[/-]\d{1,2}[/-]\d{4}',
                        r'\d{1,2}\s+\w+\s+\d{4}',
                        r'Deadline[:\s]+([^\n<]+)',
                        r'Last Date[:\s]+([^\n<]+)',
                    ]
                    for pattern in date_patterns:
                        match = re.search(pattern, summary, re.IGNORECASE)
                        if match:
                            deadline = match.group(0)[:30]
                            break

                if save_job(title, organization, category, deadline,
                           image_url, "", apply_link, source):
                    saved += 1

            except Exception as e:
                continue

    except Exception as e:
        print(f"   ❌ Error {source}: {str(e)[:60]}")

    return saved

# ========================================
# সব Feed চালান
# ========================================
def run_all_scrapers():
    print(f"\n{'='*50}")
    print(f"🚀 RSS Scraper: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    total = 0
    for feed in RSS_FEEDS:
        saved = scrape_rss_feed(feed)
        total += saved
        time.sleep(2)

    print(f"\n🎉 Total saved: {total} jobs")
    print(f"✅ Done: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*50}\n")

# ========================================
# Main
# ========================================
if __name__ == "__main__":
    run_all_scrapers()

    schedule.every(6).hours.do(run_all_scrapers)
    print("⏰ Scheduler running — প্রতি ৬ ঘণ্টায় update হবে...")

    while True:
        schedule.run_pending()
        time.sleep(60)
