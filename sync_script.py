import os
import json
import re
import html
import unicodedata
import requests
import firebase_admin
from firebase_admin import credentials, db
from difflib import SequenceMatcher

# --- ১. এপিআই ইউআরএল তালিকা (আপনার সিকোয়েন্স অনুযায়ী) ---
API_URLS = [
    "https://all-rounder-two.vercel.app/Goozapp",
    "https://all-rounder-two.vercel.app/streams-center",
    "https://all-rounder-two.vercel.app/fawna",
    "https://all-rounder-two.vercel.app/Roxi"
]

# --- ২. কমন স্পোর্টস সংক্ষিপ্ত রূপের ডিকশনারি (Aliases) ---
COMMON_ALIASES = {
    # দেশসমূহ
    "eng": "england",
    "nz": "new zealand",
    "usa": "united states",
    "rsa": "south africa",
    "saf": "south africa",
    "ind": "india",
    "aus": "australia",
    "pak": "pakistan",
    "sl": "sri lanka",
    "ban": "bangladesh",
    "afg": "afghanistan",
    "wi": "west indies",
    "ire": "ireland",
    "zim": "zimbabwe",
    "uae": "united arab emirates",
    "ned": "netherlands",
    "sco": "scotland",
    "nep": "nepal",
    "oma": "oman",
    "png": "papua new guinea",
    "can": "canada",
    "nam": "namibia",
    "hkg": "hong kong",
    
    # ফুটবল ক্লাব
    "mci": "manchester city",
    "manc": "manchester city",
    "mun": "manchester united",
    "manu": "manchester united",
    "utd": "united",
    "ars": "arsenal",
    "che": "chelsea",
    "tot": "tottenham",
    "liv": "liverpool",
    "new": "newcastle",
    "whu": "west ham",
    "avl": "aston villa",
    "bvb": "dortmund",
    "fcb": "barcelona",
    "rm": "real madrid",
    "psg": "paris saint germain",
    "atm": "atletico madrid",
    "int": "inter milan",
    "juv": "juventus",
    "bay": "bayern munich",
    
    # ক্রিকেট ফ্র্যাঞ্চাইজি
    "csk": "chennai super kings",
    "mi": "mumbai indians",
    "rcb": "royal challengers bengaluru",
    "kkr": "kolkata knight riders",
    "dc": "delhi capitals",
    "pbks": "punjab kings",
    "kxip": "kings xi punjab",
    "rr": "rajasthan royals",
    "srh": "sunrisers hyderabad",
    "lsg": "lucknow super giants",
    "gt": "gujarat titans",
    "ms": "multan sultans",
    "iu": "islamabad united",
    "lq": "lahore qalandars",
    "kk": "karachi kings",
    "pz": "peshawar zalmi",
    "qg": "quetta gladiators"
}

# --- ৩. অ্যাডভান্সড নাম নরমালাইজেশন ও এক্সপেনশন লজিক ---
def normalize_and_expand(name):
    if not name:
        return ""
    
    # HTML ডিকোড এবং ছোট হাতের অক্ষরে রূপান্তর
    name = html.unescape(name).lower()
    
    # বিশেষ অ্যাকসেন্ট ক্যারেক্টার দূর করা
    name = "".join(
        c for c in unicodedata.normalize('NFD', name) 
        if unicodedata.category(c) != 'Mn'
    )
    
    # শুধু ইংরেজি লেটার, সংখ্যা এবং স্পেস রাখা
    name = re.sub(r'[^a-z0-9\s]', ' ', name)
    
    # শব্দে বিভক্ত করা এবং অপ্রয়োজনীয় শব্দ ছাঁটাই করা
    words = name.split()
    noise_words = {"fc", "cf", "sc", "ac", "rc", "cd", "as", "club", "team", "cricket", "football", "soccer", "vs", "the", "and", "de"}
    
    expanded_words = []
    for w in words:
        if w in noise_words:
            continue
        # সংক্ষিপ্ত রূপ থাকলে তা বড় রূপে রূপান্তর করা
        if w in COMMON_ALIASES:
            expanded_words.extend(COMMON_ALIASES[w].split())
        else:
            expanded_words.append(w)
            
    return " ".join(expanded_words)

# --- ৪. হাইব্রিড ম্যাচিং অ্যালগরিদম (Subset, Overlap, Sequence matching) ---
def is_team_matching(name1, name2):
    n1 = normalize_and_expand(name1)
    n2 = normalize_and_expand(name2)
    
    if not n1 or not n2:
        return False
        
    # ১. সম্পূর্ণ সাধারণ মিল (Exact normalized match)
    if n1 == n2:
        return True
        
    words1 = set(n1.split())
    words2 = set(n2.split())
    
    if not words1 or not words2:
        return False
        
    # ২. যদি কোনো একটি নাম মাত্র একটি শব্দের হয় এবং তা অন্য নামের সাবসেট হয়
    if len(words1) == 1 or len(words2) == 1:
        if words1.issubset(words2) or words2.issubset(words1):
            return True
            
    # ৩. শব্দসমূহের আংশিক ওভারল্যাপ চেক (অন্তত ৫০% শব্দ মিলতে হবে)
    intersection = words1.intersection(words2)
    min_words_count = min(len(words1), len(words2))
    
    if min_words_count > 0 and (len(intersection) / min_words_count) >= 0.5:
        return True
        
    # ৪. সামান্য টাইপো বা বানানের পার্থক্যের জন্য সিকোয়েন্স রেশিও চেক (৮০% মিল থাকতে হবে)
    similarity = SequenceMatcher(None, n1, n2).ratio()
    if similarity >= 0.8:
        return True
        
    return False

def get_teams_from_rivels(rivels):
    if not rivels:
        return None, None
    clean_rivels = html.unescape(rivels)
    parts = re.split(r'\s+vs\.?\s+', clean_rivels, flags=re.IGNORECASE)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, None

# --- ৫. ফায়ারবেস সংযোগ স্থাপন ---
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
        print("🚀 Firebase Connected Successfully.")
    except Exception as e:
        print(f"❌ Firebase Connection Failed: {str(e)}")
        exit(1)

# --- ৬. মূল সমন্বয় প্রক্রিয়া ---
def main():
    initialize_firebase()
    
    # ক. সবকটি API থেকে ডেটা এনে গ্রুপ করা
    all_api_groups = []
    
    for url in API_URLS:
        try:
            print(f"📡 Requesting: {url}")
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            api_data = response.json()
            live_data = api_data.get("Live_Data", [])
            print(f"   📊 Fetched {len(live_data)} matches")
            
            for item in live_data:
                rivels = item.get("Rivels", "")
                t1, t2 = get_teams_from_rivels(rivels)
                if t1 and t2:
                    # এপিআই-এর প্রতিটি ম্যাচের টিম দুটি সেভ রাখা হচ্ছে ম্যাচিংয়ের জন্য
                    all_api_groups.append({
                        "team1": t1,
                        "team2": t2,
                        "stream": item
                    })
        except Exception as e:
            print(f"⚠️ Warning: Skip API {url} due to error: {str(e)}")

    # খ. ডাটাবেজ থেকে ম্যাচ রিড করা
    try:
        events_ref = db.reference('sports_live/events')
        db_events = events_ref.get()
    except Exception as e:
        print(f"❌ Failed to fetch Firebase DB: {str(e)}")
        exit(1)

    if not db_events:
        print("ℹ️ No matches active in Firebase database.")
        return

    if isinstance(db_events, list):
        iterator = enumerate(db_events)
    else:
        iterator = db_events.items()

    # গ. ম্যাচগুলোর উপর ইন্টেলিজেন্ট কম্পারিজন লুপ
    for key, event in iterator:
        if not event:
            continue
        
        event_info = event.get("eventInfo", {})
        db_teamA = event_info.get("teamA", "")
        db_teamB = event_info.get("teamB", "")

        if not db_teamA or not db_teamB:
            continue

        matched_streams = []
        
        # এপিআই গ্রুপ থেকে এই ডাটাবেজ ম্যাচের জন্য যোগ্য স্ট্রিমিং লিংক খুঁজে বের করা
        for item in all_api_groups:
            api_t1 = item["team1"]
            api_t2 = item["team2"]

            # সোজা এবং উল্টো দুইভাবেই ম্যাচ চেক করা হচ্ছে (যেমন: A vs B অথবা B vs A)
            match_direct = is_team_matching(db_teamA, api_t1) and is_team_matching(db_teamB, api_t2)
            match_reverse = is_team_matching(db_teamA, api_t2) and is_team_matching(db_teamB, api_t1)

            if match_direct or match_reverse:
                matched_streams.append(item["stream"])

        # ঘ. ডাটাবেজ রাইট করার সিদ্ধান্ত
        if matched_streams:
            # লিংকগুলোর ডুপ্লিকেট দূর করা (URL বেসড ডুপ্লিকেশন প্রোটেকশন)
            seen_links = set()
            updated_channels = []
            server_count = 1

            for stream in matched_streams:
                link = stream.get("Link", "")
                if not link:
                    continue
                
                clean_link = link.strip().lower()
                if clean_link.endswith("/"):
                    clean_link = clean_link[:-1]

                if clean_link not in seen_links:
                    seen_links.add(clean_link)
                    updated_channels.append({
                        "link": link,
                        "title": f"SERVER {server_count}",
                        "tokenApi": ""
                    })
                    server_count += 1

            if updated_channels:
                try:
                    db.ref = db.reference(f'sports_live/events/{key}/channels_data')
                    db.ref.set(updated_channels)
                    print(f"✅ Matched & Updated: {db_teamA} vs {db_teamB} -> Found {len(updated_channels)} Server(s).")
                except Exception as e:
                    print(f"⚠️ Failed to write to Firebase for {db_teamA} vs {db_teamB}: {str(e)}")
            else:
                print(f"ℹ️ Skipped: {db_teamA} vs {db_teamB} -> Matched streams had null links.")
        else:
            # এপিআইতে না পাওয়া গেলে ডাটাবেজের আগের লিংকগুলোতে হাত দেওয়া হবে না
            print(f"ℹ️ Skipped: {db_teamA} vs {db_teamB} -> Not found in APIs (Database kept as is).")

    print("🏁 Sync Workflow Executed Successfully.")

if __name__ == "__main__":
    main()
