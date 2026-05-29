import time
import feedparser
import hashlib
import json
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore, messaging
from groq import Groq
import os
import schedule

# ========================================
# ⚙️ Groq AI Setup (সম্পূর্ণ ফ্রি ও আনলিমিটেড)
# ========================================
# 🔑 এখানে আপনার আসল Groq API Key-টি বসান
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
client = Groq(api_key=GROQ_API_KEY)

# ========================================
# 🔥 Firebase Setup
# ========================================
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()
print("✅ Firebase connected!")
print("✅ Groq AI Connected!")

# ========================================
# 📡 লাইভ চাকরির RSS Feed লিস্ট
# ========================================
RSS_FEEDS = [
    ("https://bdjobs.com/feed/", "Private"),
    ("https://ejobsbd.com/feed/", "Private"),
    ("https://bdgovtjob.net/feed/", "Government")
]

# ========================================
# 🛠️ Helper Functions
# ========================================
def generate_id(title):
    return hashlib.md5(title.encode('utf-8')).hexdigest()

def is_duplicate(job_id):
    try:
        doc = db.collection("jobs").document(job_id).get()
        return doc.exists
    except:
        return False

def save_job(title, organization, category, deadline, apply_link="", source=""):
    if not title or len(title) < 5:
        return False

    job_id = generate_id(title)

    if is_duplicate(job_id):
        print(f"   skip: ইতিমধ্যে ডাটাবেজে আছে: {title[:30]}...")
        return False

    job_data = {
        "title": title,
        "organization": organization,
        "category": category,
        "deadline": deadline,
        "imageUrl": "",
        "pdfUrl": "",
        "applyLink": apply_link,
        "source": source,
        "publishDate": datetime.now().strftime("%Y-%m-%d"),
        "timestamp": int(datetime.now().timestamp()),
    }

    try:
        db.collection("jobs").document(job_id).set(job_data)
        print(f"   ✅ Saved to Firebase: {title[:40]}")
        send_notification(title, organization)
        return True
    except Exception as e:
        print(f"   ❌ Save error: {e}")
        return False

def send_notification(title, organization):
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title="নতুন চাকরির খবর! 💼",
                body=f"{title[:60]}"
            ),
            topic="all_jobs",
        )
        messaging.send(message)
        print(f"   🔔 Notification sent to users!")
    except Exception as e:
        print(f"   ❌ Notification error: {e}")

# ========================================
# 🚀 মেইন আরএসএস + এআই ইঞ্জিন
# ========================================
def fetch_and_process_jobs():
    print(f"\n{'='*50}")
    print(f"🚀 Live RSS AI Scraper Running: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")

    total_saved = 0

    for url, default_category in RSS_FEEDS:
        print(f"\n📡 Fetching Live Feed: {url}")
        feed = feedparser.parse(url)
        
        if not feed.entries:
            print("⚠️ No entries found or link unavailable. Skipping...")
            continue
            
        print(f"🔍 Found {len(feed.entries)} new posts to analyze.")

        for entry in feed.entries:
            raw_title = entry.get('title', 'Unknown Title')
            job_link = entry.get('link', '')
            raw_summary = entry.get('summary', '')

            potential_id = generate_id(raw_title)
            if is_duplicate(potential_id):
                continue

            print(f"\n⚡ Processing New Job: {raw_title[:50]}...")

            prompt = f"""
            You are an expert job data extraction tool for Bangladesh. Analyze the following job title and summary.
            Extract the details and return ONLY a valid JSON object. Do not include markdown formatting or backticks like ```json.
            
            Job Title: {raw_title}
            Summary: {raw_summary}
            Default Category suggestion: {default_category}

            JSON Format to return:
            {{
                "title": "Clean Job Title in Bengali (e.g., সহকারী প্রোগ্রামার / Sales Officer)",
                "organization": "Exact organization name in Bengali or English (e.g., প্রাণ গ্রুপ / বাংলাদেশ রেলওয়ে)",
                "category": "Identify exact one from these: সরকারি / ব্যাংক / NGO / বেসরকারি",
                "deadline": "Last date to apply (Format: '৩০ জুন ২০২৬' or use English date if Bengali not clear. If missing put 'N/A')"
            }}
            """

            try:
                # 🔄 এখানে নতুন সচল মডেল 'llama-3.1-8b-instant' সেট করা হয়েছে
                completion = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                )
                
                ai_output = completion.choices[0].message.content.strip()
                parsed_job = json.loads(ai_output)
                
                success = save_job(
                    title=parsed_job.get("title", raw_title),
                    organization=parsed_job.get("organization", "বিভিন্ন প্রতিষ্ঠান"),
                    category=parsed_job.get("category", "বেসরকারি"),
                    deadline=parsed_job.get("deadline", "N/A"),
                    apply_link=job_link,
                    source="Live RSS Feed"
                )
                
                if success:
                    total_saved += 1

            except Exception as e:
                print(f"   ❌ Error processing this post: {e}")

            time.sleep(3)

    print(f"\n🎉 Total saved in this cycle: {total_saved} jobs")
    print(f"✅ Cycle Done: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*50}\n")

# ========================================
# ⏰ অটোমেটিক সিডিউলার (Scheduler)
# ========================================
if __name__ == "__main__":
    fetch_and_process_jobs()

    schedule.every(6).hours.do(fetch_and_process_jobs)
    print("⏰ Scheduler running — প্রতি ৬ ঘণ্টায় সম্পূর্ণ ফ্রিতে আপডেট হবে...")

    while True:
        schedule.run_pending()
        time.sleep(60)