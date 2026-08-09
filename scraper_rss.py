import os
import time
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from bs4 import BeautifulSoup
import feedparser
import json
import re
from datetime import datetime

# ─── Firebase Init ────────────────────────────────────────────────────────────
firebase_key = os.environ.get("FIREBASE_KEY")
if firebase_key:
    key_dict = json.loads(firebase_key)
    cred = credentials.Certificate(key_dict)
else:
    cred = credentials.Certificate("firebase_key.json")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ─── RSS Feed List ────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # Government Jobs
    "https://bdgovtjob.net/feed/",
    "https://www.bdjobs.com/rss/alljobs.xml",
    "https://ejobsbd.com/feed/",
    "https://www.chakri.com.bd/feed/",
    # Bank Jobs
    "https://bankjobsbd.com/feed/",
    "https://www.banknews.com.bd/feed/",
    # NGO / Development
    "https://ngojobsbd.com/feed/",
    "https://devjobsbd.com/feed/",
    # Private / General
    "https://jobsbd24.com/feed/",
    "https://bdcircular.com/feed/",
    "https://alljobscircularbd.com/feed/",
    "https://www.jobsandresult.com/feed/",
    # Defence / Police
    "https://army.mil.bd/feed/",
    "https://bddefencejobs.com/feed/",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BDJobNewsScraper/1.0)"
}

# ─── Image Extraction: Multi-Fallback ────────────────────────────────────────
def extract_image_url(entry, page_url=None):
    """
    Try multiple strategies to get an image URL.
    Returns empty string if none found.
    """

    # 1. media:content or media:thumbnail (RSS media extension)
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            url = m.get("url", "")
            if url and _is_image_url(url):
                return url

    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        url = entry.media_thumbnail[0].get("url", "")
        if url and _is_image_url(url):
            return url

    # 2. enclosures (some feeds use this)
    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            url = enc.get("href") or enc.get("url", "")
            if url and _is_image_url(url):
                return url

    # 3. Parse <img> from entry summary / content
    html_content = ""
    if hasattr(entry, "content") and entry.content:
        html_content = entry.content[0].get("value", "")
    if not html_content and hasattr(entry, "summary"):
        html_content = entry.summary or ""

    if html_content:
        soup = BeautifulSoup(html_content, "html.parser")
        img = soup.find("img")
        if img:
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src", "")
            if src and _is_image_url(src):
                return src

    # 4. Scrape the actual post page (last resort)
    link = page_url or (entry.get("link") if hasattr(entry, "get") else getattr(entry, "link", None))
    if link:
        img_url = _scrape_og_image(link)
        if img_url:
            return img_url

    return ""


def _is_image_url(url):
    """Check if URL looks like an image."""
    return bool(re.search(r"\.(jpg|jpeg|png|webp|gif)(\?.*)?$", url, re.IGNORECASE))


def _scrape_og_image(url):
    """Fetch page and look for og:image or first article image."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")

        # og:image (most reliable)
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]

        # twitter:image
        tw = soup.find("meta", attrs={"name": "twitter:image"})
        if tw and tw.get("content"):
            return tw["content"]

        # First image inside article / post content
        article = soup.find("article") or soup.find("div", class_=re.compile(r"entry-content|post-content|content"))
        if article:
            img = article.find("img")
            if img:
                src = img.get("src") or img.get("data-src", "")
                if src and _is_image_url(src):
                    return src

    except Exception as e:
        print(f"  [scrape_og_image] Failed for {url}: {e}")
    return ""


# ─── Extract Deadline ─────────────────────────────────────────────────────────
def extract_deadline(entry):
    """Try to find application deadline from summary/content."""
    html = ""
    if hasattr(entry, "content") and entry.content:
        html = entry.content[0].get("value", "")
    if not html and hasattr(entry, "summary"):
        html = entry.summary or ""

    text = BeautifulSoup(html, "html.parser").get_text()

    # Look for date patterns like "Last Date: 25 August 2025" or "Deadline: 2025-08-25"
    patterns = [
        r"(?:শেষ তারিখ|আবেদনের শেষ|Last\s*Date|Deadline)[:\s]+([০-৯0-9A-Za-z ,/-]+\d{4})",
        r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b",
        r"\b(\d{1,2}\s+\w+\s+\d{4})\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()

    return ""


# ─── Scrape and Save ──────────────────────────────────────────────────────────
def scrape_and_save():
    saved = 0
    skipped = 0
    jobs_ref = db.collection("jobs")

    for feed_url in RSS_FEEDS:
        print(f"\n📡 Fetching: {feed_url}")
        try:
            feed = feedparser.parse(feed_url)
            entries = feed.entries
            print(f"  → {len(entries)} entries found")
        except Exception as e:
            print(f"  ❌ Failed to parse feed: {e}")
            continue

        for entry in entries[:10]:  # limit per feed to avoid quota issues
            try:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()

                if not title or not link:
                    continue

                # Check duplicate by link
                existing = jobs_ref.where("applyLink", "==", link).limit(1).get()
                if len(list(existing)) > 0:
                    skipped += 1
                    continue

                image_url = extract_image_url(entry, page_url=link)
                deadline = extract_deadline(entry)
                published = entry.get("published", "")
                summary_html = ""
                if hasattr(entry, "content") and entry.content:
                    summary_html = entry.content[0].get("value", "")
                elif hasattr(entry, "summary"):
                    summary_html = entry.summary or ""
                summary_text = BeautifulSoup(summary_html, "html.parser").get_text()[:500]

                # Determine category from feed URL
                category = _guess_category(feed_url, title)

                doc = {
                    "title": title,
                    "organization": _extract_org(title),
                    "category": category,
                    "deadline": deadline,
                    "imageUrl": image_url,
                    "pdfUrl": "",
                    "applyLink": link,
                    "source": feed.feed.get("title", feed_url),
                    "publishDate": published,
                    "timestamp": firestore.SERVER_TIMESTAMP,
                    "summary": summary_text,
                }

                jobs_ref.add(doc)
                saved += 1
                print(f"  ✅ Saved: {title[:60]} | image: {'✓' if image_url else '✗'}")

            except Exception as e:
                print(f"  ⚠️  Entry error: {e}")

        time.sleep(1)  # polite delay between feeds

    print(f"\n🎉 Done! Saved: {saved} | Skipped (duplicate): {skipped}")


def _guess_category(feed_url, title):
    url_lower = feed_url.lower()
    title_lower = title.lower()
    if "bank" in url_lower or "bank" in title_lower:
        return "Bank Job"
    if "ngo" in url_lower or "ngo" in title_lower:
        return "NGO Job"
    if "defence" in url_lower or "army" in url_lower or "police" in title_lower:
        return "Defence"
    if "govt" in url_lower or "government" in title_lower or "সরকারি" in title_lower:
        return "Government"
    return "Private"


def _extract_org(title):
    """Simple heuristic: take first part before common separators."""
    for sep in [" – ", " - ", " | ", " :: "]:
        if sep in title:
            return title.split(sep)[0].strip()
    return title[:50]


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 BD Job News Scraper starting...")
    scrape_and_save()
