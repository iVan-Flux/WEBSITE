import os
import json
import re
import html
import unicodedata
import requests
import firebase_admin
from firebase_admin import credentials, db
from difflib import SequenceMatcher

COMMON_ALIASES = {
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

def normalize_and_expand(name):
    if not name:
        return ""
    name = html.unescape(name).lower()
    name = "".join(
        c for c in unicodedata.normalize('NFD', name) 
        if unicodedata.category(c) != 'Mn'
    )
    name = re.sub(r'[^a-z0-9\s]', ' ', name)
    words = name.split()
    noise_words = {"fc", "cf", "sc", "ac", "rc", "cd", "as", "club", "team", "cricket", "football", "soccer", "vs", "the", "and", "de"}
    expanded_words = []
    for w in words:
        if w in noise_words:
            continue
        if w in COMMON_ALIASES:
            expanded_words.extend(COMMON_ALIASES[w].split())
        else:
            expanded_words.append(w)
    return " ".join(expanded_words)

def is_team_matching(name1, name2):
    n1 = normalize_and_expand(name1)
    n2 = normalize_and_expand(name2)
    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    words1 = set(n1.split())
    words2 = set(n2.split())
    if not words1 or not words2:
        return False
    if len(words1) == 1 or len(words2) == 1:
        if words1.issubset(words2) or words2.issubset(words1):
            return True
    intersection = words1.intersection(words2)
    min_words_count = min(len(words1), len(words2))
    if min_words_count > 0 and (len(intersection) / min_words_count) >= 0.5:
        return True
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

def clean_drm_url(url):
    if not url:
        return ""
    if ".mpd" in url.lower():
        if "|" in url:
            url = url.split("|")[0].strip()
    return url

def initialize_firebase():
    service_account_env = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    database_url = os.environ.get("FIREBASE_DATABASE_URL")
    if not service_account_env or not database_url:
        exit(1)
    try:
        service_account_info = json.loads(service_account_env)
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred, {
            'databaseURL': database_url
        })
    except Exception:
        exit(1)

def fetch_and_parse_all_apis():
    all_streams = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    standard_apis = [
        ("Goozapp", os.environ.get("API_URL_GOOZAPP")),
        ("streams_center", os.environ.get("API_URL_STREAMS_CENTER")),
        ("fawna", os.environ.get("API_URL_FAWNA")),
        ("Roxi", os.environ.get("API_URL_ROXI"))
    ]
    for name, url in standard_apis:
        if not url:
            continue
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            live_data = data.get("Live_Data", [])
            for item in live_data:
                rivels = item.get("Rivels", "")
                link = item.get("Link", "")
                t1, t2 = get_teams_from_rivels(rivels)
                if t1 and t2 and link:
                    all_streams.append({
                        "api_name": name,
                        "t1": t1, 
                        "t2": t2, 
                        "url": link,
                        "api": "",
                        "type": "Direct"
                    })
        except Exception:
            pass

    cr7_url = os.environ.get("API_URL_CR7")
    if cr7_url:
        try:
            response = requests.get(cr7_url, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            events = data.get("events", [])
            for event in events:
                t1 = event.get("home_team_name", "")
                t2 = event.get("away_team_name", "")
                servers = event.get("servers", [])
                if t1 and t2:
                    for server in servers:
                        link = server.get("url", "")
                        s_type = server.get("type", "").lower()
                        s_key = server.get("key", "")
                        if link:
                            clean_link = clean_drm_url(link)
                            if ".mpd" in clean_link.lower() or s_type == "drm":
                                all_streams.append({
                                    "api_name": "CR7",
                                    "t1": t1,
                                    "t2": t2,
                                    "url": clean_link,
                                    "api": s_key,
                                    "type": "drm"
                                })
                            else:
                                all_streams.append({
                                    "api_name": "CR7",
                                    "t1": t1,
                                    "t2": t2,
                                    "url": clean_link,
                                    "api": "",
                                    "type": "Direct"
                                })
        except Exception:
            pass

    bing_url = os.environ.get("API_URL_BING")
    if bing_url:
        try:
            response = requests.get(bing_url, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            channels = data.get("channels", [])
            for channel in channels:
                t1 = channel.get("Team 1 Name", "")
                t2 = channel.get("Team 2 Name", "")
                stream_urls = channel.get("Stream URL", [])
                if t1 and t2:
                    for server in stream_urls:
                        link = server.get("play_url", "")
                        if link:
                            all_streams.append({
                                "api_name": "BING",
                                "t1": t1,
                                "t2": t2,
                                "url": link,
                                "api": "",
                                "type": "Direct"
                            })
        except Exception:
            pass

    fluxy_url = os.environ.get("API_URL_FLUXY")
    if fluxy_url:
        try:
            response = requests.get(fluxy_url, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            events = data.get("events", [])
            for event in events:
                title = event.get("title", "")
                event_info = event.get("eventInfo", {})
                event_name = event_info.get("eventName", "")
                is_fifa_blocked = "fifa world cup" in title.lower() or "fifa world cup" in event_name.lower()
                if is_fifa_blocked:
                    continue
                t1 = event_info.get("teamA", "")
                t2 = event_info.get("teamB", "")
                channels_data = event.get("channels_data", [])
                if t1 and t2:
                    for ch in channels_data:
                        link = ch.get("link", "")
                        api_key = ch.get("api", "")
                        is_drm_link = ".mpd" in link.lower() or bool(api_key)
                        if is_drm_link and link:
                            clean_link = clean_drm_url(link)
                            all_streams.append({
                                "api_name": "FLUXY",
                                "t1": t1,
                                "t2": t2,
                                "url": clean_link,
                                "api": api_key,
                                "type": "drm"
                            })
        except Exception:
            pass

    main_stream_url = os.environ.get("API_URL_MAIN_STREAM")
    if main_stream_url:
        try:
            response = requests.get(main_stream_url, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            live_data = data.get("Live_Data", [])
            for item in live_data:
                rivels = item.get("Rivels", "")
                link = item.get("Link", "")
                t1, t2 = get_teams_from_rivels(rivels)
                if t1 and t2 and link:
                    all_streams.append({
                        "api_name": "MAIN_STREAM",
                        "t1": t1,
                        "t2": t2,
                        "url": link,
                        "api": "",
                        "type": "Direct"
                    })
        except Exception:
            pass

    return all_streams

def extract_from_api(matched_by_api, api_key, count):
    extracted = []
    for _ in range(count):
        if matched_by_api[api_key]:
            extracted.append(matched_by_api[api_key].pop(0))
    return extracted

def main():
    initialize_firebase()
    all_api_streams = fetch_and_parse_all_apis()
    try:
        events_ref = db.reference('sports_live/events')
        db_events = events_ref.get()
    except Exception:
        exit(1)
    if not db_events:
        return
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
        cat_lower = str(event.get("cat", "")).lower()
        is_cricket = "cricket" in cat_lower
        matched_by_api = {api: [] for api in ["CR7", "fawna", "Goozapp", "MAIN_STREAM", "streams_center", "FLUXY", "BING", "Roxi"]}
        seen_urls = set()
        has_match = False
        for stream_item in all_api_streams:
            api_t1 = stream_item["t1"]
            api_t2 = stream_item["t2"]
            api_url = stream_item["url"]
            api_name = stream_item["api_name"]
            if is_cricket and api_name == "CR7":
                continue
            match_direct = is_team_matching(db_teamA, api_t1) and is_team_matching(db_teamB, api_t2)
            match_reverse = is_team_matching(db_teamA, api_t2) and is_team_matching(db_teamB, api_t1)
            if match_direct or match_reverse:
                clean_url = api_url.strip().lower()
                if clean_url.endswith("/"):
                    clean_url = clean_url[:-1]
                if clean_url not in seen_urls:
                    if len(matched_by_api[api_name]) < 5:
                        seen_urls.add(clean_url)
                        matched_by_api[api_name].append(stream_item)
                        has_match = True
        if has_match:
            cr7_top = extract_from_api(matched_by_api, "CR7", 2)
            fawna_top = extract_from_api(matched_by_api, "fawna", 1)
            goozapp_top = extract_from_api(matched_by_api, "Goozapp", 1)
            main_stream_top = extract_from_api(matched_by_api, "MAIN_STREAM", 1)
            streams_center_top = extract_from_api(matched_by_api, "streams_center", 1)
            fluxy_top = extract_from_api(matched_by_api, "FLUXY", 1)
            bing_top = extract_from_api(matched_by_api, "BING", 1)
            roxi_top = extract_from_api(matched_by_api, "Roxi", 1)
            ordered_list = []
            ordered_list.extend(cr7_top)
            ordered_list.extend(fawna_top)
            ordered_list.extend(goozapp_top)
            ordered_list.extend(main_stream_top)
            ordered_list.extend(streams_center_top)
            ordered_list.extend(fluxy_top)
            ordered_list.extend(bing_top)
            ordered_list.extend(roxi_top)
            leftovers = []
            api_priority_order = ["CR7", "fawna", "Goozapp", "MAIN_STREAM", "streams_center", "FLUXY", "BING", "Roxi"]
            for api_name in api_priority_order:
                leftovers.extend(matched_by_api[api_name])
            final_ordered_list = ordered_list + leftovers
            is_group_a = any(term in cat_lower for term in ["football", "mlb", "nba", "wnba", "basketball", "baseball"])
            if not is_group_a:
                drm_streams = [s for s in final_ordered_list if s["type"] == "drm"]
                non_drm_streams = [s for s in final_ordered_list if s["type"] != "drm"]
                final_ordered_list = drm_streams + non_drm_streams
            updated_channels = []
            for i, stream in enumerate(final_ordered_list):
                server_num = i + 1
                updated_channels.append({
                    "title": f"SERVER {server_num}",
                    "url": stream["url"],
                    "api": stream["api"], 
                    "type": stream["type"] 
                })
            try:
                db_ref = db.reference(f'sports_live/events/{key}/channels_data')
                db_ref.set(updated_channels)
            except Exception:
                pass

if __name__ == "__main__":
    main()
