import os
import json
import time
import subprocess
import re
from datetime import datetime, timezone
import musicbrainzngs
from google import genai
from google.genai import types

# --- Configuration ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not found!")

client = genai.Client(api_key=GEMINI_API_KEY)
musicbrainzngs.set_useragent("MixxxGenreDataConnector", "4.0", "https://github.com/YOUR_USERNAME/Mixxx-GenreData")

GENRES_DIR = "genres"
os.makedirs(GENRES_DIR, exist_ok=True)

GENRES_FILE = "genres.txt"

def load_genres():
    MANIFEST_FILE = "manifest.json"
    if os.path.exists(MANIFEST_FILE):
        with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Flatten all genres from all categories into one list
            return [genre for cat in data.get("categories", []) for genre in cat.get("genres", [])]
    
    # Fallback to old flat file if manifest is missing
    if os.path.exists(GENRES_FILE):
        return [line.strip() for line in open(GENRES_FILE) if line.strip()]
    return []

def add_genre_to_file(new_genre):
    existing_genres = load_genres()
    if new_genre.lower() not in [g.lower() for g in existing_genres]:
        with open(GENRES_FILE, "a", encoding="utf-8") as f:
            f.write(new_genre + "\n")
        print(f" [+] Added '{new_genre}' to {GENRES_FILE}.")

def migrate_database_schema():
    """Updates all existing JSON files to include Category, Focus, and TrackComposer fields."""
    if not os.path.exists(GENRES_DIR): return

    print(f"\n[i] MIGRATION: Standardizing JSON schema for all existing genre lists...")
    
    for filename in os.listdir(GENRES_DIR):
        if filename.endswith(".json"):
            filepath = os.path.join(GENRES_DIR, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as jf:
                    data = json.load(jf)

                # Skip if already migrated
                if "Category" in data and "Focus" in data:
                    continue

                # Default values for existing lists
                data["Category"] = ""
                data["Focus"] = "artist"
                
                # Add TrackComposer to every existing track
                for track in data.get("Tracks", []):
                    if "TrackComposer" not in track:
                        track["TrackComposer"] = ""

                # Rewrite file in the correct order
                new_data = {
                    "SearchField": data.get("SearchField", ""),
                    "Category": data["Category"],
                    "Focus": data["Focus"],
                    "RefreshRate": data.get("RefreshRate", 28),
                    "AppendMode": data.get("AppendMode", "append"),
                    "Timestamp": data.get("Timestamp", ""),
                    "TotalTracks": data.get("TotalTracks", 0),
                    "Tracks": data.get("Tracks", [])
                }

                with open(filepath, 'w', encoding='utf-8') as jf:
                    json.dump(new_data, jf, indent=4)
                print(f"     -> Schema updated: {filename}")
            except Exception as e:
                print(f"     [!] Failed to update {filename}: {e}")
    git_save_and_push("System: Re-ordered New TrackComposer Field & JSON Headers Focus & Categorie to the top")
    print("[i] MIGRATION COMPLETE! All files are now compatible.\n")
    
def should_update_genre(search_field):
    filename = os.path.join(GENRES_DIR, f"{search_field.replace(' ', '_').lower()}.json")

    if not os.path.exists(filename):
        return True, "File does not exist yet."

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Get the Refresh Rate (Default to 28 if it's an old file)
        refresh_rate = data.get("RefreshRate", 28)
        
        # If the header says 0, NEVER update it!
        if refresh_rate == 0:
            return False, "Locked by JSON header (RefreshRate=0)"

        timestamp_str = data.get("Timestamp", "")
        if timestamp_str:
            last_update = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            age_in_days = (now - last_update).days

            if age_in_days < refresh_rate:
                return False, f"Recently updated ({age_in_days} days ago, requires {refresh_rate})."
                
    except Exception as e:
        return True, f"Error reading existing file, forcing update."

    return True, f"Needs update (Older than {refresh_rate} days)."

def get_all_tracks_from_gemini(search_field, category, categories, max_retries=4):
    is_classical = (category == "Classical")
    chunks = [categories[i:i + 7] for i in range(0, len(categories), 7)]
    all_flat_tracks = []

    for i, chunk in enumerate(chunks):
        print(f" -> Asking Gemini to build Crate Part {i+1} of {len(chunks)} (7 categories)...")
        
        composer_instruction = (
            "3. CRITICAL: Because this is a Classical genre, 'TrackComposer' is MANDATORY. Identify the specific composer (e.g., 'Frédéric Chopin')."
            if is_classical else 
            "3. 'TrackComposer' is an optional addon. Provide the primary songwriter/composer if known, otherwise leave it as an empty string."
        )

        prompt = f"""
        You are an expert music historian, curator, club DJ and music lover. 
        Provide a massive playlist for the genre/theme: "{search_field}".

        You must categorize the tracks into the exact following vibes:
        {json.dumps(chunk, indent=2)}

        CRITICAL RULES:
        1. DO NOT REPEAT TRACKS. Every single track across the entire JSON must be 100% unique.
        2. DO NOT INVENT REMIXES. If a genre does not typically have "12-inch remixes", provide standard recordings instead. Real tracks only.
        {composer_instruction}

        For EACH category, provide exactly 40 tracks.

        Output ONLY a single valid JSON object. Do not use markdown blocks.
        The keys of the JSON object must be the exact category strings provided above.
        The value for each key must be an array of objects.
        Each object must have keys "TrackArtist", "TrackTitle", and "TrackComposer".
        """

        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash', 
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )

                raw_text = response.text.strip()
                if raw_text.startswith("```"):
                    lines = raw_text.split('\n')
                    if len(lines) > 0 and lines[0].startswith("```"): lines = lines[1:]
                    if len(lines) > 0 and lines[-1].startswith("```"): lines = lines[:-1]
                    raw_text = "\n".join(lines).strip()

                parsed_json = json.loads(raw_text)

                for cat, tracks in parsed_json.items():
                    if isinstance(tracks, list):
                        print(f"  [+] {len(tracks)} tracks returned for: '{cat}'")
                        for t in tracks:
                            if "TrackArtist" in t and "TrackTitle" in t:
                                t['Category'] = cat
                                # Ensure TrackComposer key exists for downstream processing
                                if "TrackComposer" not in t: t["TrackComposer"] = ""
                                all_flat_tracks.append(t)
                break 

            except Exception as e:
                error_msg = str(e)
                if any(err in error_msg for err in ["429", "503", "UNAVAILABLE", "disconnected", "Connection"]):
                    wait_time = 30 * (attempt + 1)
                    print(f"  [!] Server hiccup: {error_msg}")
                    print(f"  [!] Resting for {wait_time} seconds... (Attempt {attempt + 1} of {max_retries})")
                    time.sleep(wait_time)
                else:
                    print(f"  [!] JSON format error. Retrying... (Attempt {attempt + 1})")
                    time.sleep(5)
        
        time.sleep(10)

    return all_flat_tracks

def get_recording_mbid(artist, title):
    try:
        safe_artist = artist.replace('"', '').replace(':', '').replace('-', ' ')
        safe_title = title.replace('"', '').replace(':', '').replace('-', ' ')

        query = f'artist:"{safe_artist}" AND recording:"{safe_title}"'
        result = musicbrainzngs.search_recordings(query=query, limit=1)

        if not result.get('recording-list'):
            loose_query = f'{safe_artist} {safe_title}'
            result = musicbrainzngs.search_recordings(query=loose_query, limit=1)

        if result.get('recording-list'):
            rec = result['recording-list'][0]
            if int(rec.get('ext:score', 0)) > 50:
                return rec['id'], rec.get('artist-credit-phrase', artist), rec.get('title', title)
    except: pass
    return None, artist, title

def generate_playlist_data(search_field, category=""):
    print(f"\n========================================")
    print(f" CRATE DIGGING: {search_field} {'(' + category + ')' if category else ''}")
    print(f"========================================")

    # --- 0. SET FOCUS LOGIC (NEW ADAPTATION) ---
    focus_type = "composer" if category == "Classical" else "artist"

    safe_path = search_field.replace(' ', '_').lower()
    target_filename = os.path.join(GENRES_DIR, f"{safe_path}.json")
    temp_filename = os.path.join(GENRES_DIR, f"{safe_path}_new.json")

    # --- 1. LOAD EXISTING DATA (APPENDER / CLEAR LOGIC) ---
    existing_tracks = []
    seen_mbids = set()
    seen_strings = set()
    
    refresh_rate = 28
    append_mode = "append"
    
    # YOUR ORIGINAL CHART LOGIC (RESTORED)
    is_chart = any(kw in search_field.lower() for kw in ["top 50", "top 100", "chart"])
    has_year = bool(re.search(r'\b(19|20)\d{2}\b', search_field))

    if is_chart:
        if has_year:
            refresh_rate = 0
            append_mode = "clear" 
        else:
            refresh_rate = 7
            append_mode = "clear"

    if os.path.exists(target_filename):
        try:
            with open(target_filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # Maintenance logic: read existing category if not passed
            if not category:
                category = data.get("Category", "")
                focus_type = data.get("Focus", focus_type)

            refresh_rate = data.get("RefreshRate", refresh_rate)
            append_mode = data.get("AppendMode", append_mode)
            
            if append_mode == "append":
                existing_tracks = data.get("Tracks", [])
                for t in existing_tracks:
                    if "MBID" in t: seen_mbids.add(t["MBID"])
                    artist_str = str(t.get("TrackArtist", "")).lower().strip()
                    title_str = str(t.get("TrackTitle", "")).lower().strip()
                    seen_strings.add(f"{artist_str}||{title_str}")
                print(f" [i] Operating in APPEND mode ({len(existing_tracks)} current tracks).")
            else:
                print(f" [i] Operating in CLEAR mode. Old tracks will be wiped.")
        except Exception as e:
            print(f" [!] Error loading existing file, starting fresh. {e}")

    # --- 2. GET NEW TRACKS ---
    categories = [
        "The absolute biggest mainstream pop/radio hits of this genre",
        "Iconic One-Hit Wonders and viral sensations",
        "Underground, deep cuts, and cult club classics",
        "Essential 12-inch remixes and extended DJ versions",
        "Hidden gems, B-sides, and influential album tracks",
        "High-energy peak-time floor fillers",
        "Warm-up tracks, early evening grooves, and mid-tempo hits",
        "Late-night anthems and closing tracks",
        "Crossover hits that also charted in other genres",
        "Critically acclaimed masterpieces and award-winning tracks",
        "Songs occuring most on playlists on youtube for this genre",
        "Songs occuring most on playlists on spotify for this genre",
        "Songs occuring most on playlists on deezer for this genre",
        "Songs occuring most on playlists on soundcloud for this genre"  
    ]

    # ADAPTATION: Passing category to the gemini call
    all_tracks = get_all_tracks_from_gemini(search_field, category, categories)

    if not all_tracks:
        print(f"[!] No tracks returned for {search_field}. Aborting.")
        return False

    print(f" -> Cross-checking against existing database and MusicBrainz...")

    # --- 3. FILTER AND VERIFY ---
    new_verified_tracks = []
    for track in all_tracks:
        raw_artist = track.get("TrackArtist", "")
        raw_title = track.get("TrackTitle", "")
        raw_composer = track.get("TrackComposer", "") # NEW ADAPTATION

        match_str = f"{str(raw_artist).lower().strip()}||{str(raw_title).lower().strip()}"
        if match_str in seen_strings:
            continue 

        mbid, clean_artist, clean_title = get_recording_mbid(raw_artist, raw_title)

        if mbid and mbid not in seen_mbids:
            seen_mbids.add(mbid)
            seen_strings.add(f"{clean_artist.lower().strip()}||{clean_title.lower().strip()}")
            new_verified_tracks.append({
                "TrackArtist": clean_artist, 
                "TrackTitle": clean_title,  
                "TrackComposer": raw_composer, # NEW ADAPTATION
                "MBID": mbid,
                "Category": track.get('Category', 'Unknown')
            })
        time.sleep(1) 

    print(f" -> Discovered and verified {len(new_verified_tracks)} BRAND NEW tracks!")

    # --- 4. MERGE AND SAVE ---
    final_tracks = existing_tracks + new_verified_tracks

    if len(existing_tracks) == 0 and len(final_tracks) < 50:
        print(f"[!] Only verified {len(final_tracks)} tracks on a fresh run. Aborting.")
        return False

    # UPDATED SCHEMA (NEW ADAPTATION)
    final_playlist = {
        "SearchField": search_field,
        "Category": category,
        "Focus": focus_type,
        "RefreshRate": refresh_rate,
        "AppendMode": append_mode,
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "TotalTracks": len(final_tracks),
        "Tracks": final_tracks
    }

    os.makedirs(os.path.dirname(target_filename), exist_ok=True)

    try:
        with open(temp_filename, 'w', encoding='utf-8') as f:
            json.dump(final_playlist, f, indent=4)
        os.replace(temp_filename, target_filename)
        
        if len(existing_tracks) > 0 and append_mode == "append":
            print(f"SUCCESS! Appended {len(new_verified_tracks)} tracks. New Total: {len(final_tracks)}")
        else:
            print(f"SUCCESS! Created/Cleared list with {len(final_tracks)} tracks.")
        return True 
    except Exception as e:
        print(f"[!] File save error: {e}")
        if os.path.exists(temp_filename):
            os.remove(temp_filename)
        return False

def add_genre_to_manifest(genre_name):
    MANIFEST_FILE = "manifest.json"
    if not os.path.exists(MANIFEST_FILE): return
    try:
        with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        for category in manifest.get("categories", []):
            if category.get("name") == "New Arrivals":
                if genre_name not in category["genres"]:
                    category["genres"].append(genre_name)
                    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
                        json.dump(manifest, f, indent=4)
                    print(f" [+] Added '{genre_name}' to 'New Arrivals' in manifest.json")
                break
    except: pass
        
def load_queue():
    if not os.path.exists(QUEUE_FILE): return []
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def git_save_and_push(commit_message):
    print(" -> Saving progress to GitHub...")
    try:
        subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip(): return
        subprocess.run(["git", "commit", "-m", commit_message], check=True, capture_output=True)
        subprocess.run(["git", "pull", "origin", "main", "--no-rebase"], check=True, capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
        print(f" [+] Successfully saved! ({commit_message})")
    except: pass     

if __name__ == "__main__":
    migrate_database_schema()
    custom_genre = os.environ.get("CUSTOM_GENRE")
    
    if custom_genre:
        print(f"\n--- ON-DEMAND REQUEST: {custom_genre} ---")
        # Use load_genres() which now reads from manifest
        if custom_genre.lower() not in [g.lower() for g in load_genres()]:
            add_genre_to_manifest(custom_genre)
            git_save_and_push(f"User requested new genre via Manifest: {custom_genre}")
        else:
            print(f" [i] {custom_genre} already exists in manifest.")
            
    else:
        print("\n--- BATCH PROCESSING STARTED ---")
        # We no longer load_queue(). 
        # We just process everything currently in the manifest.
        
        genres_to_process = load_genres()
        for genre in genres_to_process:
            should_up, reason = should_update_genre(genre)
            if should_up:
                print(f"[*] Processing '{genre}': {reason}")
                
                # Determine if it's classical
                category = "Classical" if genre.lower().startswith("classical ") else ""
                
                if generate_playlist_data(genre, category):
                    # We don't need to 'remove' from a queue anymore.
                    # The file now exists, so should_update_genre will return False next time.
                    git_save_and_push(f"Chef: Updated/Generated playlist for: {genre}")
                    time.sleep(60) 
            else:
                print(f"⏭️ SKIPPING '{genre}': {reason}")

                
