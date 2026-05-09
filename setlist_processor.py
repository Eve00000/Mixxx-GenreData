import os
import json
import subprocess

SETLISTS_DIR = "setlists"
MANIFEST_FILE = "setlists_manifest.json"
os.makedirs(SETLISTS_DIR, exist_ok=True)

def git_save_and_push(commit_message):
    print(" -> Saving progress to GitHub...")
    try:
        subprocess.run(["git", "add", "-A"], check=True, capture_output=True)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip(): return
        subprocess.run(["git", "commit", "-m", commit_message], check=True, capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
        print(f" [+] Successfully saved! ({commit_message})")
    except Exception as e:
        print(f" [!] Git Error: {e}")

def process_setlist():
    payload_str = os.environ.get("SETLIST_PAYLOAD")
    if not payload_str:
        print("[!] No payload found.")
        return

    try:
        data = json.loads(payload_str)
        
        # Enforce 250 character limit on description
        desc = data.get("PlaylistDescription", "")
        if len(desc) > 250:
            data["PlaylistDescription"] = desc[:247] + "..."

        # Unique Key for filename and indexing
        playlist_key = data.get("PlaylistName", "unknown_set").replace(" ", "_")
        filename = f"{playlist_key}.json"
        filepath = os.path.join(SETLISTS_DIR, filename)

        # 1. Save the Full Schema
        # This preserves: PlaylistTitle, PlaylistStyle, PlaylistUserName, PlaylistUserEmail, 
        # PlaylistCreationDate, Timestamp, Category, Focus, TotalTracks, and Tracks list.
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        print(f" [+] Saved setlist: {filename}")

        # 2. Update Manifest with terminolgy 'sets'
        manifest = {"categories": []}
        if os.path.exists(MANIFEST_FILE):
            with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
                manifest = json.load(f)

        category_name = data.get("Category", "Community Sets")
        
        target_category = next((cat for cat in manifest["categories"] if cat["name"] == category_name), None)
        if not target_category:
            target_category = {"name": category_name, "sets": []}
            manifest["categories"].append(target_category)

        # Handle migration from 'genres' to 'sets' if necessary
        if "sets" not in target_category:
            target_category["sets"] = target_category.pop("genres", [])

        # Index the PlaylistName
        if playlist_key not in target_category["sets"]:
            target_category["sets"].append(playlist_key)
            target_category["sets"].sort()

        with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=4)

        git_save_and_push(f"ShareMySet: {data.get('PlaylistTitle')} shared by {data.get('PlaylistUserName')}")

    except Exception as e:
        print(f"[!] Processing Error: {e}")

if __name__ == "__main__":
    process_setlist()
