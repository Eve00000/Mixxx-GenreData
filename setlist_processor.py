import os
import json
import subprocess
from datetime import datetime

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

        # Ensure required fields and formatting
        # We use PlaylistName for the filename
        playlist_name = data.get("PlaylistName", "unknown_set").replace(" ", "_")
        filename = f"{playlist_name}.json"
        filepath = os.path.join(SETLISTS_DIR, filename)

        # Save the setlist file
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        print(f" [+] Saved setlist: {filename}")

        # --- Update setlists_manifest.json (Genre-like Structure) ---
        manifest = {"categories": []}
        if os.path.exists(MANIFEST_FILE):
            with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
                manifest = json.load(f)

        target_category_name = data.get("Category", "Community Sets")
        if not target_category_name: target_category_name = "Community Sets"

        # Find or create the category
        target_category = next((cat for cat in manifest["categories"] if cat["name"] == target_category_name), None)
        
        if not target_category:
            target_category = {"name": target_category_name, "genres": []}
            manifest["categories"].append(target_category)

        # In the genre manifest, 'genres' is a list of strings (the search keys).
        # We will store the 'PlaylistName' here so the app knows which file to fetch.
        if playlist_name not in target_category["genres"]:
            target_category["genres"].append(playlist_name)
            # Optional: Sort genres alphabetically within category
            target_category["genres"].sort()

        with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=4)

        git_save_and_push(f"ShareMySet: Added '{playlist_name}' to category '{target_category_name}'")

    except Exception as e:
        print(f"[!] Error processing setlist: {e}")

if __name__ == "__main__":
    process_setlist()
