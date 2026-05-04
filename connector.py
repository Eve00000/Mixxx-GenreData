import os
import json
import time
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

GENRES_FILE = "genres.txt"
LOCKED_FILE = "locked_genres.txt"

def load_locked_genres():
    """Loads the list of genres that should NEVER be updated by the bot."""
    if not os.path.exists(LOCKED_FILE):
        with open(LOCKED_FILE, "w", encoding="utf-8") as f:
            f.write("# Add genres here (one per line) that you NEVER want the bot to update.\n")
            f.write("# (e.g. because you manually curated them or they never change)\n")
            f.write("60s Soul\n")
            f.write("80s New Wave\n")
        return ["60s soul", "80s new wave"]
        
    with open(LOCKED_FILE, "r", encoding="utf-8") as f:
        # Ignore empty lines and comments, return lowercase for easy matching
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
        print(f"  [+] Added '{new_genre}' to {GENRES_FILE}.")

def should_update_genre(search_field, locked_genres):
    """The Smart Cache: Decides if we actually need to spend time generating this genre."""
    
    # 1. Is it manually locked by the human?
    if search_field.lower() in locked_genres:
        return False, f"Locked by human in {LOCKED_FILE}"
        
    filename = f"{search_field.replace(' ', '_').lower()}.json"
    
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
            # Parse the time from the JSON and compare to right now
            last_update = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            age_in_days = (now - last_update).days
            
            # The rule: < 20 days old AND > 350 tracks = Skip it!
            if age_in_days < 20 and total_tracks > 350:
                return False, f"Recently updated ({age_in_days} days ago) and has {total_tracks} tracks."
                
    except Exception as e:
        return True, f"Error reading existing file, forcing update."
        
    return True, f"Needs update. (Older than 20 days or has < 350 tracks)."

def get_tracks_from_gemini(search_field, category_prompt, num_tracks=50):
    print(f"  -> Asking Gemini for {num_tracks} tracks: '{category_prompt}'...")
    prompt = f"""
    You are an expert music historian and club DJ. 
    Provide exactly {num_tracks} tracks for the genre/theme: "{search_field}".
    Specifically, these tracks must fit this vibe: {category_prompt}.
    Output ONLY a valid JSON array of objects. Each object must have keys "TrackArtist" and "TrackTitle".
    Example: [{{"TrackArtist": "Joy Division", "TrackTitle": "Love Will Tear Us Apart"}}]
    """
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"  [!] Error communicating with Gemini: {e}")
        return []

def get_recording_mbid(artist, title):
    try:
        query = f'artist:"{artist}" AND recording:"{title}"'
        result = musicbrainzngs.search_recordings(query=query, limit=1)
        if result.get('recording-list'):
            rec = result['recording-list'][0]
            return rec['id'], rec.get('artist-credit-phrase', artist), rec.get('title', title)
    except Exception as e:
        pass
    return None, artist, title

def generate_playlist_data(search_field):
    print(f"\n========================================")
    print(f" CRATE DIGGING: {search_field} (Target: 500 Tracks)")
    print(f"========================================")
    
    all_tracks = []
    
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
    
    for category in categories:
        gemini_tracks = get_tracks_from_gemini(search_field, category, num_tracks=50)
        for t in gemini_tracks:
            t['Category'] = category 
            all_tracks.append(t)
        time.sleep(3) 
        
    if not all_tracks:
        print(f"[!] No tracks returned for {search_field}. Aborting.")
        return

    print(f"  -> Verifying tracks against MusicBrainz... (This takes a few minutes)")
    
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
                "Category": track['Category']
            })
        time.sleep(1) 
        
    print(f"  -> Successfully verified and deduplicated {len(final_tracks)} unique tracks!")
        
    if len(final_tracks) < 50:
        print(f"[!] Only verified {len(final_tracks)} tracks. Aborting to protect existing file.")
        return
        
    final_playlist = {
        "SearchField": search_field,
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "TotalTracks": len(final_tracks),
        "Tracks": final_tracks
    }
    
    base_filename = f"{search_field.replace(' ', '_').lower()}"
    target_filename = f"{base_filename}.json"
    temp_filename = f"{base_filename}_new.json"
    
    try:
        with open(temp_filename, 'w', encoding='utf-8') as f:
            json.dump(final_playlist, f, indent=4)
        os.replace(temp_filename, target_filename)
        print(f"SUCCESS! Saved {len(final_tracks)} tracks to {target_filename}")
    except Exception as e:
        print(f"[!] File save error: {e}")
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

QUEUE_FILE = "requested_genres.txt"

def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def clear_queue():
    """Empties the queue file after successful nightly processing."""
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        f.write("")

if __name__ == "__main__":
    locked_genres = load_locked_genres()
    custom_genre = os.environ.get("CUSTOM_GENRE")
    
    if custom_genre:
        # THE WAITER: Fast mode. Just log the request and exit! (Takes 5 seconds)
        print(f"\n--- ON-DEMAND REQUEST LOGGED: {custom_genre} ---")
        
        # Check if we already have it in the queue or main list
        existing_genres = load_genres()
        queued_genres = load_queue()
        all_known = [g.lower() for g in existing_genres + queued_genres]
        
        if custom_genre.lower() not in all_known:
            with open(QUEUE_FILE, "a", encoding="utf-8") as f:
                f.write(custom_genre + "\n")
            print(f"  [+] Successfully added to {QUEUE_FILE}. Waiting for nightly run.")
        else:
            print(f"  [-] Genre already exists in database or queue. Skipping.")
            
    else:
        # THE CHEF: Nightly/Monthly Batch Processing
        print("\n--- BATCH UPDATE STARTED ---")
        
        # 1. Process the Queue first!
        queued_genres = load_queue()
        if queued_genres:
            print(f"Found {len(queued_genres)} new requests in {QUEUE_FILE}!")
            # Remove any exact duplicates just in case!
            queued_genres = list(set(queued_genres))
            for genre in queued_genres:
                do_update, reason = should_update_genre(genre, locked_genres)
                if do_update:
                    generate_playlist_data(genre)
                    # Move to the permanent list so we update it next month
                    add_genre_to_file(genre) 
                    time.sleep(5)
            
            # Wipe the queue clean now that we are done!
            clear_queue()
            print("Queue cleared.")
            
        # 2. Process the regular Monthly Updates
        genres_to_process = load_genres()
        print(f"\nChecking {len(genres_to_process)} existing genres from {GENRES_FILE}")
        
        for genre in genres_to_process:
            do_update, reason = should_update_genre(genre, locked_genres)
            if do_update:
                generate_playlist_data(genre)
                time.sleep(5) 
            else:
                print(f"⏭️ SKIPPING '{genre}': {reason}")
