import os
import json
import re
import html
import unicodedata
import requests
import firebase_admin
from firebase_admin import credentials, db

# --- ১. এপিআই ইউআরএল তালিকা (আপনার দেওয়া ক্রমানুসারে) ---
API_URLS = [
    "https://all-rounder-two.vercel.app/Goozapp",
    "https://all-rounder-two.vercel.app/streams-center",
    "https://all-rounder-two.vercel.app/fawna",
    "https://all-rounder-two.vercel.app/Roxi"
]

# --- ২. নাম নরমালাইজেশন এবং ক্লিনিং লজিক ---
def normalize_name(name):
    if not name:
        return ""
    
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
    
    clean_rivels = html.unescape(rivels)
    
    # ' vs ' বা ' vs. ' দিয়ে টিম দুটি আলাদা করা
    parts = re.split(r'\s+vs\.?\s+', clean_rivels, flags=re.IGNORECASE)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, None

# --- ৩. ফায়ারবেস সংযোগ স্থাপন ---
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

# --- ৪. মূল সিঙ্ক লজিক ---
def main():
    initialize_firebase()
    
    # ক. সবকটি এপিআই থেকে ডেটা সংগ্রহ এবং একই ম্যাচের লিংকগুলো একত্রিত করা
    all_api_groups = {}
    
    for url in API_URLS:
        try:
            print(f"📡 Fetching data from API: {url}")
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            api_data = response.json()
            live_data = api_data.get("Live_Data", [])
            print(f"   Matches found: {len(live_data)}")
            
            for item in live_data:
                rivels = item.get("Rivels", "")
                t1, t2 = get_teams_from_rivels(rivels)
                
                if t1 and t2:
                    n1 = normalize_name(t1)
                    n2 = normalize_name(t2)
                    match_key = tuple(sorted([n1, n2]))
                    
                    if match_key not in all_api_groups:
                        all_api_groups[match_key] = []
                    all_api_groups[match_key].append(item)
        except Exception as e:
            # একটি এপিআই ডাউন থাকলেও যাতে অন্যগুলো কাজ করে, তাই ওয়ার্নিং দিয়ে কাজ চলমান রাখা হবে
            print(f"⚠️ Warning: Failed to fetch API data from {url}: {str(e)}")

    # খ. ডাটাবেজ থেকে ম্যাচ রিড করা
    try:
        events_ref = db.reference('sports_live/events')
        db_events = events_ref.get()
    except Exception as e:
        print(f"❌ Failed to read from Firebase: {str(e)}")
        exit(1)

    if not db_events:
        print("ℹ️ No events found in Firebase database under 'sports_live/events'.")
        return

    # গ. ইভেন্টগুলোর উপর লুপ চালিয়ে মিল খোঁজা
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
        for (g1, g2), streams in all_api_groups.items():
            match_1 = (ndb1 in g1 or g1 in ndb1) and (ndb2 in g2 or g2 in ndb2)
            match_2 = (ndb1 in g2 or g2 in ndb1) and (ndb2 in g1 or g1 in ndb2)

            if match_1 or match_2:
                matched_streams = streams
                break

        # ঘ. ডাটাবেজ আপডেট করার সিদ্ধান্ত
        if matched_streams:
            # ডুপ্লিকেট লিংক বাদ দেওয়ার জন্য সেট (Set) ব্যবহার করা হলো
            seen_links = set()
            updated_channels = []
            server_count = 1

            for stream in matched_streams:
                link = stream.get("Link", "")
                if not link:
                    continue
                
                # যদি লিংকটি ইতিমধ্যে অন্য কোনো এপিআই থেকে এসে থাকে, তবে তা বাদ দেওয়া হবে
                if link not in seen_links:
                    seen_links.add(link)
                    updated_channels.append({
                        "link": link,
                        "title": f"SERVER {server_count}",
                        "tokenApi": ""
                    })
                    server_count += 1

            if updated_channels:
                try:
                    db.reference(f'sports_live/events/{key}/channels_data').set(updated_channels)
                    print(f"✅ Updated: {db_teamA} vs {db_teamB} -> Added {len(updated_channels)} servers sequentially.")
                except Exception as e:
                    print(f"⚠️ Failed to update database for {db_teamA} vs {db_teamB}: {str(e)}")
            else:
                print(f"ℹ️ Skipped: {db_teamA} vs {db_teamB} -> Matched streams had empty links.")
        else:
            # এপিআই-তে কোনো লিংক না পাওয়া গেলে ডাটাবেজ অপরিবর্তিত থাকবে
            print(f"ℹ️ Skipped: {db_teamA} vs {db_teamB} -> No stream found in any API (Database kept as is).")

    print("🏁 Synchronization Completed.")

if __name__ == "__main__":
    main()
