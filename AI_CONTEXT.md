# Project Context: Mixxx Playlist Generator

**Goal:** Generate, verify, and automatically maintain smart DJ playlist data (Mixxx) using Google Gemini and MusicBrainz APIs. 
**Hosting:** Fully automated via GitHub Actions.
**Frontend:** A React application that fetches the JSON data.

## 🛠️ The Tech Stack
- **Backend:** Python (`connector.py`)
- **AI Model:** `gemini-2.5-flash` (using a paid billing key to bypass rate limits)
- **Verification:** MusicBrainz API (`musicbrainzngs`)
- **Automation:** GitHub Actions (`update_data.yml` running on a cron schedule + `workflow_dispatch`)
- **Frontend Storage:** IndexedDB with HTTP `HEAD` ETag checking.

## 📂 Repository File Structure
- `connector.py` -> The main Python engine (The "Waiter" & "Chef").
- `.github/workflows/update_data.yml` -> The GitHub Action runner.
- `genres.txt` -> A flat text list of all currently available genres.
- `requested_genres.txt` -> The queue where on-demand user requests are stored.
- `genres/` (Folder) -> Where all the generated `.json` files are physically saved.
- *(Note: `locked_genres.txt` was deprecated and archived. List locking is now handled in the JSON headers).*

## 🧠 Backend Logic (`connector.py`)
The backend uses a **Waiter / Chef** pattern to manage API limits and ensure 100% uptime.

**1. The Waiter (On-Demand Requests):**
When the React app triggers a GitHub webhook with a `CUSTOM_GENRE`, the script wakes up, logs the genre into `requested_genres.txt`, instantly pushes to GitHub, and exits. 

**2. The Chef (Scheduled Maintenance & Queue Processing):**
Runs on a cron schedule. It processes new requests in the queue, then cycles through existing files for routine maintenance. 
- **Incremental Git Saving:** Progress is committed and pushed *immediately* after every single genre. If the 6-hour GitHub runner times out, no data is ever lost.

**3. Gemini "Smart Chunking" (Avoiding Token Limits):**
We ask Gemini for 14 specific DJ categories (e.g., "Peak-time floor fillers", "One-Hit Wonders"). To prevent Gemini from hitting the 8,192 max output token limit and dropping the connection (`503 UNAVAILABLE`), we split the 14 categories into **2 chunks of 7**. It returns exactly 40 tracks per category.

**4. The Append Engine (Avoiding Duplicate API Calls):**
When updating an existing list, the script loads the JSON, creates a string map (`artist||title`) of existing tracks, and filters out duplicates *before* asking MusicBrainz. It only appends brand new tracks.

**5. MusicBrainz "Fuzzy Verification":**
Every track is checked against MusicBrainz. If a strict `artist` AND `recording` match fails, the script automatically falls back to a loose text search (like Google) and accepts the track if the confidence score is > 50.

## 📄 Data Schema (Self-Governing JSON)
Every JSON file in the `genres/` folder governs its own update schedule using headers. 
- `RefreshRate`: How many days before it updates again (0 = Locked, 7 = Weekly, 28 = Monthly).
- `AppendMode`: `append` (add new tracks) or `clear` (wipe old tracks, used for current Top 50 charts).

**Example JSON Structure:**
```json
{
  "SearchField": "80s Pop",
  "RefreshRate": 28,
  "AppendMode": "append",
  "Timestamp": "2026-05-06T12:00:00Z",
  "TotalTracks": 512,
  "Tracks": [
    {
      "TrackArtist": "Tears For Fears",
      "TrackTitle": "Everybody Wants To Rule The World",
      "MBID": "12345-abcde",
      "Category": "Iconic One-Hit Wonders and viral sensations"
    }
  ]
}
