import os
import json
import time
import requests
import musicbrainzngs
from google import genai
from google.genai import types

# --- Configuration ---
# This safely pulls the API key from GitHub Secrets
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not found!")

# Initialize the new Gemini Client
client = genai.Client(api_key=GEMINI_API_KEY)

# Using your GitHub repo as the User Agent
musicbrainzngs.set_useragent("MixxxGenreDataConnector", "1.0", "https://github.com/Eve00000/Mixxx-GenreData")

def get_artists_from_gemini(search_field, num_artists=30):
    print(f"Asking Gemini for {num_artists} artists defining: '{search_field}'...")
    
    prompt = f"""
    You are an expert music historian and DJ. 
    Provide the {num_artists} most definitive and popular musical artists for the category: "{search_field}".
    Output ONLY a valid JSON array of strings containing the artist names.
    Example: ["Joy Division", "The Cure", "Depeche Mode"]
    """
    
    try:
        # The new v2 Gemini SDK format
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Error communicating with Gemini: {e}")
        return []

def get_artist_mbid(artist_name):
    try:
        result = musicbrainzngs.search_artists(query=f'artist:"{artist_name}"', limit=1)
        if result.get('artist-list'):
            return result['artist-list'][0]['id']
    except Exception as e:
        print(f"MusicBrainz API error for {artist_name}: {e}")
    return None

def get_top_tracks_from_listenbrainz(artist_mbid):
    url = f"https://api.listenbrainz.org/1/popularity/top-recordings-for-artist/{artist_mbid}"
    headers = {"User-Agent": "MixxxGenreDataConnector/1.0"}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get('payload', data) if isinstance(data, dict) else data
    except Exception as e:
        print(f"ListenBrainz request error: {e}")
    return []

def generate_playlist_data(search_field, num_artists=50, tracks_per_artist=10):
    artists = get_artists_from_gemini(search_field, num_artists)
    if not artists:
        return

    all_tracks = []
    print(f"Resolving {len(artists)} artists against MusicBrainz & ListenBrainz...")
    
    for artist in artists:
        mbid = get_artist_mbid(artist)
        if not mbid:
            continue
            
        lb_data = get_top_tracks_from_listenbrainz(mbid)
        if isinstance(lb_data, list):
            for track in lb_data[:tracks_per_artist]:
                all_tracks.append({
                    "TrackArtist": track.get("artist_name", artist),
                    "TrackTitle": track.get("recording_name", "Unknown Title"),
                    "MBID": track.get("recording_mbid"),
                    "ListenCount": track.get("total_listen_count", 0)
                })
        time.sleep(1) # Be polite to APIs
        
    # Sort by popularity
    all_tracks.sort(key=lambda x: x["ListenCount"], reverse=True)
    final_tracks = all_tracks[:500]
    
    final_playlist = {
        "SearchField": search_field,
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "TotalTracks": len(final_tracks),
        "Tracks": final_tracks
    }
    
    # Save file with a clean name (e.g., "80s_new_wave.json")
    filename = f"{search_field.replace(' ', '_').lower()}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(final_playlist, f, indent=4)
        
    print(f"Success! Saved {len(final_tracks)} tracks to {filename}")

if __name__ == "__main__":
    # Add or remove genres here!
    GENRES_TO_PROCESS = [
        "New Wave", "80s New Wave", 
        "Punk", "70s Punk", "80s Punk", "Postpunk",
        "Pop", "60s Pop", "70s Pop", "80s Pop", "90s Pop"
    ]
    
    for genre in GENRES_TO_PROCESS:
        print(f"\n--- Starting: {genre} ---")
        generate_playlist_data(genre, num_artists=50, tracks_per_artist=10)
        time.sleep(5) # Pause between genres
