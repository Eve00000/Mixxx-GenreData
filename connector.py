import os
import json
import time
import musicbrainzngs
from google import genai
from google.genai import types

# --- Configuration ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not found!")

client = genai.Client(api_key=GEMINI_API_KEY)
musicbrainzngs.set_useragent("MixxxGenreDataConnector", "3.0", "https://github.com/Eve00000/Mixxx-GenreData")

GENRES_FILE = "genres.txt"

def load_genres():
    """Loads genres from the text file. Creates it with defaults if missing."""
    if not os.path.exists(GENRES_FILE):
        default_genres = [
            "New Wave", "80s New Wave", "Punk", "70s Punk", "80s Punk",
            "Postpunk", "Pop", "60s Pop", "70s Pop", "80s Pop", "90s Pop"
        ]
        with open(GENRES_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(default_genres) + "\n")
        return default_genres
        
    with open(GENRES_FILE, "r", encoding="utf-8") as f:
        # Strip whitespace and ignore empty lines
        return [line.strip() for line in f if line.strip()]

def add_genre_to_file(new_genre):
    """Appends a new genre to the text file if it doesn't already exist."""
    existing_genres = load_genres()
    # Case-insensitive check to avoid duplicates (e.g. "Pop" vs "pop")
    if new_genre.lower() not in [g.lower() for g in existing_genres]:
        with open(GENRES_FILE, "a", encoding="utf-8") as f:
            f.write(new_genre + "\n")
        print(f"  [+] Added '{new_genre}' to {GENRES_FILE} for future monthly updates.")

def get_tracks_from_gemini(search_field, category_prompt, num_tracks=100):
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
    
    # 14 Highly specific DJ categories x 50 tracks = 700 tracks
    # Smaller batches prevent the AI from getting "lazy" and stopping early.
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
        # Ask for 50 at a time!
        gemini_tracks = get_tracks_from_gemini(search_field, category, num_tracks=50)
        for t in gemini_tracks:
            t['Category'] = category 
            all_tracks.append(t)
        time.sleep(3) 
        
    if not all_tracks:
        print(f"[!] No tracks returned for {search_field}. Aborting.")
        return

    print(f"  -> Verifying {len(all_tracks)} tracks against MusicBrainz... (This takes a few minutes)")
    
    final_tracks = []
    seen_mbids = set() # This is our Deduplicator!
    
    for track in all_tracks:
        raw_artist = track.get("TrackArtist", "")
        raw_title = track.get("TrackTitle", "")
        
        mbid, clean_artist, clean_title = get_recording_mbid(raw_artist, raw_title)
        
        # If we found it, AND we haven't already added this exact MBID to our list
        if mbid and mbid not in seen_mbids:
            seen_mbids.add(mbid)
            final_tracks.append({
                "TrackArtist": clean_artist, 
                "TrackTitle": clean_title,   
                "MBID": mbid,
                "Category": track['Category']
            })
            
        time.sleep(1) # Respect MusicBrainz rate limit
        
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
        print(f"SUCCESS! Saved {len(final_tracks)} canonical tracks to {target_filename}")
    except Exception as e:
        print(f"[!] File save error: {e}")
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

if __name__ == "__main__":
    custom_genre = os.environ.get("CUSTOM_GENRE")
    
    if custom_genre:
        print(f"\n--- ON-DEMAND REQUEST DETECTED ---")
        # 1. Generate the JSON for this specific request
        generate_playlist_data(custom_genre)
        # 2. Add it to our master list so it gets updated every month!
        add_genre_to_file(custom_genre)
        
    else:
        print("\n--- MONTHLY BATCH UPDATE STARTED ---")
        # 1. Load the dynamic list from the text file
        genres_to_process = load_genres()
        print(f"Loaded {len(genres_to_process)} genres from {GENRES_FILE}")
        
        for genre in genres_to_process:
            generate_playlist_data(genre)
            time.sleep(5) 
