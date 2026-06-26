import os
import json
import re
import html
import unicodedata
import requests
import firebase_admin
from firebase_admin import credentials, db

# --- ১. নাম নরমালাইজেশন এবং ক্লিনিং লজিক ---
def normalize_name(name):
    if not name:
        return ""
    
    # HTML এন্টিটি ডিকোড করা (যেমন: &amp; থেকে & করা)
    name = html.unescape(name)
    name = name.lower()
    
    # অ্যাকসেন্ট বা স্পেশাল ডায়াক্রিটিক্স বাদ দেওয়া
    name = "".join(
        c for c in unicodedata.normalize('NFD', name) 
        if unicodedata.category(c) != 'Mn'
    )

    # সাধারণ সংক্ষিপ্ত রূপ পরিবর্তন করা
    name = name.replace("&", "and")
    name = re.sub(r'\batl\.?\b', 'atletico', name)
    name = re.sub(r'\butd\.?\b', 'united', name)
    name = re.sub(r'\bman\.?\b', 'manchester', name)
    name = re.sub(r'\bst\.?\b', 'saint', name)
    name = re.sub(r'\bint\.?\b', 'inter', name)

    # ক্লাবের অতিরিক্ত শব্দ বাদ দেওয়া
    name = re.sub(r'\b(fc|cf|sc|ac|rc|cd|as|club|team)\b', '', name)

    # শুধুমাত্র আলফানিউমেরিক অক্ষর রাখা (স্পেস বা অন্য চিহ্ন বাদ)
    return re.sub(r'[^a-z0-9]', '', name).strip()

def get_teams_from_rivels(rivels):
    if not rivels:
        return None, None
    
    # HTML ডিকোড করা
    clean_rivels = html.unescape(rivels)
    
    # ' vs ' বা ' vs. ' দিয়ে টিম দুটি আলাদা করা
    parts = re.split(r'\s+vs\.?\s+', clean_rivels, flags=re.IGNORECASE)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, None

# --- ২. ফায়ারবেস সংযোগ স্থাপন ---
def initialize_firebase():
    service_account_env = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    database_url = os.environ.get("FIREBASE_DATABASE_URL")

    if not service_account_env or not database_url:
        print("❌ Error: Missing FIREBASE_SERVICE_ACCOUNT or FIREBASE_DATABASE_URL environment variables.")
        exit(1)

    try:
        service_account_info = json.loads(service_account_env)
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred, {
            'databaseURL': database_url
        })
        print("🚀 Firebase Initialized Successfully.")
    except Exception as e:
        print(f"❌ Firebase Initialization Failed: {str(e)}")
        exit(1)

# --- ৩. মূল সিঙ্ক লজিক ---
def main():
    initialize_firebase()
    
    api_url = os.environ.get("GOOZAPP_API_URL", "https://all-rounder-two.vercel.app/Goozapp")
    
    # ক. API থেকে ডেটা নিয়ে আসা
    try:
        print(f"📡 Fetching data from API: {api_url}")
        response = requests.get(api_url, timeout=15)
        response.raise_for_status()
        api_data = response.json()
        live_data = api_data.get("Live_Data", [])
        print(f"📊 Total Matches found in API: {len(live_data)}")
    except Exception as e:
        print(f"❌ Failed to fetch API data: {str(e)}")
        exit(1)

    # খ. API ম্যাচের গ্রুপ তৈরি করা (একই ম্যাচের একাধিক সার্ভার হ্যান্ডেল করতে)
    gooz_groups = {}
    for item in live_data:
        rivels = item.get("Rivels", "")
        t1, t2 = get_teams_from_rivels(rivels)
        
        if t1 and t2:
            n1 = normalize_name(t1)
            n2 = normalize_name(t2)
            # অর্ডার নিরপেক্ষ রাখতে টিমদ্বয়ের নাম সর্ট করে কী (Key) বানানো হলো
            match_key = tuple(sorted([n1, n2]))
            
            if match_key not in gooz_groups:
                gooz_groups[match_key] = []
            gooz_groups[match_key].append(item)

    # গ. ডাটাবেজ থেকে ম্যাচ রিড করা
    try:
        events_ref = db.reference('sports_live/events')
        db_events = events_ref.get()
    except Exception as e:
        print(f"❌ Failed to read from Firebase: {str(e)}")
        exit(1)

    if not db_events:
        print("ℹ️ No events found in Firebase database under 'sports_live/events'.")
        return

    # ঘ. ইভেন্টগুলোর উপর লুপ চালিয়ে মিল খোঁজা
    # ডাটাবেজ রিটার্ন অবজেক্ট লিস্ট বা ডিকশনারি যেকোনোটি হতে পারে, তা হ্যান্ডেল করা হলো
    if isinstance(db_events, list):
        iterator = enumerate(db_events)
    else:
        iterator = db_events.items()

    for key, event in iterator:
        if not event:
            continue
        
        event_info = event.get("eventInfo", {})
        db_teamA = event_info.get("teamA", "")
        db_teamB = event_info.get("teamB", "")

        if not db_teamA or not db_teamB:
            continue

        ndb1 = normalize_name(db_teamA)
        ndb2 = normalize_name(db_teamB)

        matched_streams = None
        # Goozapp গ্রুপের সাথে ডাটাবেজের টিমের মিল খোঁজা
        for (g1, g2), streams in gooz_groups.items():
            # আংশিক মিল এবং রিভার্স ম্যাচিং হ্যান্ডেল করার লজিক
            match_1 = (ndb1 in g1 or g1 in ndb1) and (ndb2 in g2 or g2 in ndb2)
            match_2 = (ndb1 in g2 or g2 in ndb1) and (ndb2 in g1 or g1 in ndb2)

            if match_1 or match_2:
                matched_streams = streams
                break

        # ঙ. ম্যাচের সিদ্ধান্ত এবং ডাটাবেজ আপডেট
        if matched_streams:
            # যদি এপিআই-তে স্ট্রিমিং সার্ভার পাওয়া যায়, তবে আগের লিংক ডিলিট করে নতুন লিংক বসবে
            updated_channels = []
            for index, stream in enumerate(matched_streams):
                server_num = index + 1
                updated_channels.append({
                    "link": stream.get("Link", ""),
                    "title": f"SERVER {server_num}",
                    "tokenApi": ""
                })

            try:
                # সুনির্দিষ্ট ম্যাচের channels_data নোডটি নতুন ডেটা দিয়ে ওভাররাইট করা হচ্ছে
                db.reference(f'sports_live/events/{key}/channels_data').set(updated_channels)
                print(f"✅ Updated: {db_teamA} vs {db_teamB} -> Added {len(updated_channels)} servers (Previous links replaced).")
            except Exception as e:
                print(f"⚠️ Failed to update database for {db_teamA} vs {db_teamB}: {str(e)}")
        else:
            # যদি এপিআই-তে কোনো স্ট্রিম লিংক না থাকে, তবে পূর্বের ডাটাবেজ ডেটা স্পর্শ করা হবে না
            print(f"ℹ️ Skipped: {db_teamA} vs {db_teamB} -> No stream found in API (Database kept as is).")

    print("🏁 Synchronization Completed.")

if __name__ == "__main__":
    main()
