import feedparser
import firebase_admin
from firebase_admin import credentials, firestore, messaging
import hashlib
import time
import json
import os
import re
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

def generate_id(title):
    return hashlib.md5(title.encode('utf-8')).hexdigest()

def is_duplicate(job_id):
    try:
        doc = db.collection("jobs").document(job_id).get()
        return doc.exists
    except:
        return False

def extract_image_from_entry(entry):
    """
    RSS entry থেকে image URL বের করার ৪টা fallback method
    """
    image_url = ""

    # Method 1: media:content বা media:thumbnail
    if hasattr(entry, "media_content") and entry.media_content:
        image_url = entry.media_content[0].get("url", "")
        if image_url:
            return image_url

    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        image_url = entry.media_thumbnail[0].get("url", "")
        if image_url:
            return image_url

    # Method 2: enclosure (podcast/image attachment)
    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image"):
                image_url = enc.get("href", "") or enc.get("url", "")
                if image_url:
                    return image_url

    # Method 3: summary বা content এর HTML থেকে <img> tag extract
    html_content = ""
    if hasattr(entry, "content") and entry.content:
        html_content = entry.content[0].get("value", "")
    elif hasattr(entry, "summary"):
        html_content = entry.summary or ""

    if html_content:
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html_content)
        if img_match:
            image_url = img_match.group(1)
            # data:image বা ছোট icon বাদ দাও
            if image_url and not image_url.startswith("data:") and len(image_url) > 10:
                return image_url

    # Method 4: links এর মধ্যে image type খোঁজো
    if hasattr(entry, "links"):
        for link in entry.links:
            if link.get("type", "").startswith("image"):
                image_url = link.get("href", "")
                if image_url:
                    return image_url

    return ""

def save_job(title, organization, category, deadline, image_url="", pdf_url="", apply_link="", source=""):
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
                apply_link = entry.get("link", "")
                category = get_category(title, feed_info["default_category"])

                # ✅ নতুন image extract function
                image_url = extract_image_from_entry(entry)
                if image_url:
                    img_found += 1

                if save_job(title, "বিভিন্ন প্রতিষ্ঠান", category, "N/A", image_url, "", apply_link, feed_info["source"]):
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
