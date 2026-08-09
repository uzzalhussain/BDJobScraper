import feedparser

FEEDS = [
    {"url": "https://www.bdjobs.com/rss/governmentjobs.xml",     "category": "সরকারি"},
    {"url": "https://bdgovtjob.net/feed/",                        "category": "সরকারি"},
    {"url": "https://ejobsbd.com/feed/",                          "category": "বেসরকারি"},
    {"url": "https://chakri.com/feed/",                           "category": "বেসরকারি"},
    {"url": "https://www.bdjobs.com/rss/bankjobs.xml",            "category": "ব্যাংক"},
    {"url": "https://bdgovtjob.net/category/bank-job/feed/",      "category": "ব্যাংক"},
    {"url": "https://bdgovtjob.net/category/bima-job/feed/",      "category": "বীমা"},
    {"url": "https://www.bdjobs.com/rss/ngojobs.xml",             "category": "NGO"},
    {"url": "https://bdgovtjob.net/category/ngo-job-circular/feed/", "category": "NGO"},
    {"url": "https://www.bdjobs.com/rss/multinationaljobs.xml",   "category": "কোম্পানি"},
    {"url": "https://bdgovtjob.net/category/defense-job/feed/",   "category": "প্রতিরক্ষা"},
    {"url": "https://www.bdjobs.com/rss/parttimejobs.xml",        "category": "পার্ট-টাইম"},
]

for f in FEEDS:
    try:
        feed = feedparser.parse(f["url"])
        count = len(feed.entries)
        if count > 0:
            print(f"OK [{f['category']}] {count} entries - {f['url']}")
        else:
            print(f"EMPTY [{f['category']}] - {f['url']}")
    except Exception as e:
        print(f"ERROR - {f['url']} - {e}")
