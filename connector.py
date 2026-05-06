import os
import json
import time
import subprocess
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
LOCKED_FILE = "locked_genres.txt"

def load_locked_genres():
    if not os.path.exists(LOCKED_FILE):
        with open(LOCKED_FILE, "w", encoding="utf-8") as f:
            f.write("# Add genres here (one per line) that you NEVER want the bot to update.\n")
            f.write("# (e.g. because you manually curated them or they never change)\n")
            f.write("60s Soul\n")
            f.write("80s New Wave\n")
        return ["60s soul", "80s new wave"]

    with open(LOCKED_FILE, "r", encoding="utf-8") as f:
        return [line.strip().lower() for line in f if line.strip() and not line.startswith("#")]

def load_genres():
    if not os.path.exists(GENRES_FILE):
        default_genres = ["Synthwave", "90s Eurodance"]
        with open(GENRES_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(default_genres) + "\n")
        return default_genres

    with open(GENRES_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def add_genre_to_file(new_genre):
    existing_genres = load_genres()
    if new_genre.lower() not in [g.lower() for g in existing_genres]:
        with open(GENRES_FILE, "a", encoding="utf-8") as f:
            f.write(new_genre + "\n")
        print(f" [+] Added '{new_genre}' to {GENRES_FILE}.")

def should_update_genre(search_field, locked_genres):
    # 1. Is it manually locked by the human?
    if search_field.lower() in locked_genres:
        return False, f"Locked by human in {LOCKED_FILE}"

    filename = os.path.join(GENRES_DIR, f"{search_field.replace(' ', '_').lower()}.json")

    # 2. Does the file even exist yet?
    if not os.path.exists(filename):
        return True, "File does not exist yet."

    # 3. Check the age and track count of the existing file
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)

        total_tracks = data.get("TotalTracks", 0)
        timestamp_str = data.get("Timestamp", "")

        if timestamp_str:
            last_update = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            age_in_days = (now - last_update).days

            if age_in_days < 20 and total_tracks > 350:
                return False, f"Recently updated ({age_in_days} days ago) and has {total_tracks} tracks."
    except Exception as e:
        return True, f"Error reading existing file, forcing update."

    return True, f"Needs update. (Older than 20 days or has < 350 tracks)."

def get_all_tracks_from_gemini(search_field, categories, max_retries=4):
    # THE FIX: Split the 14 categories into 2 batches of 7 so Google doesn't time out!
    chunks = [categories[i:i + 7] for i in range(0, len(categories), 7)]
    all_flat_tracks = []

    for i, chunk in enumerate(chunks):
        print(f" -> Asking Gemini to build Crate Part {i+1} of {len(chunks)} (7 categories)...")
        
        prompt = f"""
        You are an expert music historian and club DJ. 
        Provide a massive playlist for the genre/theme: "{search_field}".

        You must categorize the tracks into the exact following vibes:
        {json.dumps(chunk, indent=2)}

        CRITICAL RULES:
        1. DO NOT REPEAT TRACKS. Every single track across the entire JSON must be 100% unique.
        2. DO NOT INVENT REMIXES. If a genre (like Opera, Jazz, or Classical) does not typically have "12-inch remixes" or "club floor fillers", provide the most essential, famous standard recordings for those categories instead. Real tracks only.

        For EACH category, provide exactly 40 tracks.

        Output ONLY a single valid JSON object. Do not use markdown blocks.
        The keys of the JSON object must be the exact category strings provided above.
        The value for each key must be an array of objects.
        Each object must have keys "TrackArtist" and "TrackTitle".
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
                    if len(lines) > 0 and lines.startswith("```"): lines = lines[1:]
                    if len(lines) > 0 and lines[-1].startswith("```"): lines = lines[:-1]
                    raw_text = "\n".join(lines).strip()

                parsed_json = json.loads(raw_text)

                for cat, tracks in parsed_json.items():
                    if isinstance(tracks, list):
                        print(f"  [+] {len(tracks)} tracks returned for: '{cat}'")
                        for t in tracks:
                            if "TrackArtist" in t and "TrackTitle" in t:
                                t['Category'] = cat
                                all_flat_tracks.append(t)
                
                # Success! Break out of the retry loop and move to the next chunk
                break 

            except Exception as e:
                error_msg = str(e)
                # Catch normal rate limits AND the "Server disconnected" timeout error
                if any(err in error_msg for err in ["429", "503", "UNAVAILABLE", "disconnected", "Connection"]):
                    wait_time = 30 * (attempt + 1)
                    print(f"  [!] Server hiccup: {error_msg}")
                    print(f"  [!] Resting for {wait_time} seconds... (Attempt {attempt + 1} of {max_retries})")
                    time.sleep(wait_time)
                else:
                    print(f"  [!] JSON format error (AI hit output limit). Retrying... (Attempt {attempt + 1})")
                    time.sleep(5)
        
        # Rest 10 seconds before asking for Part 2
        time.sleep(10)

    return all_flat_tracks

def get_recording_mbid(artist, title):
    try:
        # Clean the text so special characters don't break the MusicBrainz search engine
        safe_artist = artist.replace('"', '').replace(':', '').replace('-', ' ')
        safe_title = title.replace('"', '').replace(':', '').replace('-', ' ')

        # Attempt 1: Strict Exact Match
        query = f'artist:"{safe_artist}" AND recording:"{safe_title}"'
        result = musicbrainzngs.search_recordings(query=query, limit=1)

        # Attempt 2: Loose/Fuzzy Match (If strict fails)
        if not result.get('recording-list'):
            loose_query = f'{safe_artist} {safe_title}'
            result = musicbrainzngs.search_recordings(query=loose_query, limit=1)

        if result.get('recording-list'):
            rec = result['recording-list'][0]  # <--- THE MISSING [0] HAS BEEN ADDED!
            
            # Make sure MusicBrainz is at least 50% confident it's the right track
            if int(rec.get('ext:score', 0)) > 50:
                return rec['id'], rec.get('artist-credit-phrase', artist), rec.get('title', title)
                
    except Exception as e:
        # If MusicBrainz throws an error, we ignore it and return None
        pass

    return None, artist, title
    
def generate_playlist_data(search_field):
    print(f"\n========================================")
    print(f" CRATE DIGGING: {search_field} (Target: 560 Tracks)")
    print(f"========================================")

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

    all_tracks = get_all_tracks_from_gemini(search_field, categories)

    print(f"\n========================================")
    print(f" [i] AI Generation Complete: {len(all_tracks)} total raw tracks collected.")
    print(f"========================================\n")

    if not all_tracks:
        print(f"[!] No tracks returned for {search_field}. Aborting.")
        return False

    print(f" -> Verifying tracks against MusicBrainz... (This takes a few minutes)")

    final_tracks = []
    seen_mbids = set() 

    for track in all_tracks:
        raw_artist = track.get("TrackArtist", "")
        raw_title = track.get("TrackTitle", "")

        mbid, clean_artist, clean_title = get_recording_mbid(raw_artist, raw_title)

        if mbid and mbid not in seen_mbids:
            seen_mbids.add(mbid)
            final_tracks.append({
                "TrackArtist": clean_artist, 
                "TrackTitle": clean_title,  
                "MBID": mbid,
                "Category": track.get('Category', 'Unknown')
            })
        time.sleep(1) 

    print(f" -> Successfully verified and deduplicated {len(final_tracks)} unique tracks!")

    if len(final_tracks) < 50:
        print(f"[!] Only verified {len(final_tracks)} tracks. Aborting to protect existing file.")
        return False

    final_playlist = {
        "SearchField": search_field,
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "TotalTracks": len(final_tracks),
        "Tracks": final_tracks
    }

    safe_path = search_field.replace(' ', '_').lower()
    target_filename = os.path.join(GENRES_DIR, f"{safe_path}.json")
    temp_filename = os.path.join(GENRES_DIR, f"{safe_path}_new.json")

    os.makedirs(os.path.dirname(target_filename), exist_ok=True)

    try:
        with open(temp_filename, 'w', encoding='utf-8') as f:
            json.dump(final_playlist, f, indent=4)
        os.replace(temp_filename, target_filename)
        print(f"SUCCESS! Saved {len(final_tracks)} tracks to {target_filename}")
        return True 
    except Exception as e:
        print(f"[!] File save error: {e}")
        if os.path.exists(temp_filename):
            os.remove(temp_filename)
        return False 

QUEUE_FILE = "requested_genres.txt"

def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def clear_queue():
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        f.write("")

def remove_from_queue(completed_genre):
    if not os.path.exists(QUEUE_FILE): return

    genres = load_queue()
    remaining = [g for g in genres if g.lower() != completed_genre.lower()]

    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(remaining) + ("\n" if remaining else ""))

def git_save_and_push(commit_message):
    print(" -> Saving progress to GitHub...")
    try:
        subprocess.run(["git", "add", "-A"], check=True, capture_output=True)

        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip():
            print(" -> No changes to commit.")
            return

        subprocess.run(["git", "commit", "-m", commit_message], check=True, capture_output=True)
        subprocess.run(["git", "pull", "origin", "main", "--no-rebase"], check=True, capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
        print(f" [+] Successfully saved! ({commit_message})")

    except subprocess.CalledProcessError as e:
        print(f" [!] Git Error: {e.stderr if e.stderr else e}")     

if __name__ == "__main__":
    locked_genres = load_locked_genres()
    custom_genre = os.environ.get("CUSTOM_GENRE")

    if custom_genre:
        print(f"\n--- ON-DEMAND REQUEST LOGGED: {custom_genre} ---")
        existing_genres = load_genres()
        queued_genres = load_queue()
        all_known = [g.lower() for g in existing_genres + queued_genres]

        if custom_genre.lower() not in all_known:
            with open(QUEUE_FILE, "a", encoding="utf-8") as f:
                f.write(custom_genre + "\n")
            print(f" [+] Successfully added to {QUEUE_FILE}. Waiting for batch run.")
            git_save_and_push(f"User requested new genre: {custom_genre}")
        else:
            print(f" [-] Genre already exists in database or queue. Skipping.")

    else:
        print("\n--- BATCH PROCESSING STARTED ---")

        queued_genres = load_queue()
        if queued_genres:
            queued_genres = list(set(queued_genres))
            print(f"Found {len(queued_genres)} new requests in queue!")

            for genre in queued_genres:
                do_update, reason = should_update_genre(genre, locked_genres)
                if do_update:
                    success = generate_playlist_data(genre)

                    if success:
                        add_genre_to_file(genre)
                        remove_from_queue(genre) 
                        git_save_and_push(f"Generated playlist for: {genre}")
                    else:
                        print(f" [!] Generation failed for '{genre}'. Keeping in queue for next run.")

                    print("\n[API PROTECTION] Waiting 60 seconds before the next genre...")
                    time.sleep(60) 
                else:
                    remove_from_queue(genre)

        genres_to_process = load_genres()
        print(f"\nChecking {len(genres_to_process)} existing genres for maintenance...")

        for genre in genres_to_process:
            do_update, reason = should_update_genre(genre, locked_genres)
            if do_update:
                print(f"Routine Maintenance: Updating '{genre}'")
                success = generate_playlist_data(genre)

                if success:
                    git_save_and_push(f"Routine maintenance update: {genre}")
                else:
                    print(f" [!] Maintenance failed for '{genre}'. Original file untouched.")

                print("\n[API PROTECTION] Waiting 60 seconds before the next genre...")
                time.sleep(60)
            else:
                print(f"⏭️ SKIPPING '{genre}': {reason}")
