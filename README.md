# Mixxx-GenreData
Runner to get tracks for different genres in json files that can be used in playlist generators


🎧 Mixxx AI Playlist Generator & Community Explorer

Current State: Fully Operational (Production)
Frontend Version: MIXXX AGENT v3.50
🏗️ High-Level Architecture

This project is a two-part ecosystem.

    The Web App (Vercel): A frontend UI where users can analyze their local Mixxx database, bring their own Gemini API key (BYOK), and generate custom DJ crates.
    The Database Backend (GitHub Actions): A self-healing, automated Python pipeline that curates, verifies, and hosts "Community" genre lists.

🖥️ Component 1: The Frontend Web App (Vercel)

Repository: Mixxx-EI-Playlist-Creator-
Hosting: Vercel (Edge Runtime)
Key Features & Optimizations:

    Client-Side SQLite (sql.js): The user's mixxxdb.sqlite file is never uploaded to the internet. It is parsed entirely inside the browser using a WebAssembly CDN fallback. This guarantees user privacy and bypasses Vercel's strict 4.5MB upload limits.
    Smart Caching (IndexedDB + ETag): Community playlists downloaded from GitHub are cached locally in IndexedDB to avoid the 5MB localStorage limit. The app performs a 0-byte HTTP HEAD request to check the GitHub ETag. If the list hasn't changed, it loads instantly from the user's disk.
    Bring Your Own Key (BYOK): Users provide their own Google Gemini API key. This protects the creator's billing account and prevents global rate-limiting.
    Vercel Edge Streaming Backend:
        API routes (/api/create-genre/index.ts) run on Vercel's Edge Runtime (maxDuration: 60), preventing standard 10-second serverless timeouts.
        Uses Streaming (ReadableStream) to keep the connection alive while Gemini thinks, bypassing Vercel's 25-second silent-drop limit.
        Forced routing to Washington D.C. (iad1) to avoid high-latency bottlenecks and 503 errors in European Google data centers.
        Uses gemini-2.5-flash-lite for absolute maximum speed and quota reliability.

⚙️ Component 2: The Backend Data Engine (GitHub)

Repository: Mixxx-GenreData
Hosting: GitHub Actions (update_data.yml cron & webhook)
Key Features & Optimizations:

    The Waiter/Chef Queue System:
        Waiter: When a user requests a new Community Genre via the app, it hits a GitHub Webhook, logs the request in requested_genres.txt, and exits instantly.
        Chef: Every 6 hours, a GitHub Action batch-processes the queue.
    Self-Governing JSON Schema:
    Lists manage their own update cycles via JSON headers.
        RefreshRate: 0 (Locked forever, e.g. "Top 50 2006"), 7 (Weekly), or 28 (Monthly).
        AppendMode: "append" (add new tracks) or "clear" (wipe and refresh, e.g. "Current Top 50").
    The Append Engine: Before verifying new tracks, Python loads the existing JSON file, memorizes the Artist/Title strings, and silently drops duplicates. This saves hundreds of API calls and ensures the list naturally grows over time.
    Smart Chunking: The Python script queries gemini-2.5-flash in 2 chunks of 7 categories (one API call each). This keeps the JSON payload under Google's 8,192 maximum output token limit, preventing JSON truncation errors.
    MusicBrainz "Fuzzy Verification": Every track is cross-referenced with MusicBrainz. If a strict match fails, the script automatically falls back to a loose string search (Google-style) and approves tracks with a confidence score > 50.

📂 Data Structure Example

The auto-generated JSON files hosted on GitHub look like this:

{
  "SearchField": "EuroBeat",
  "RefreshRate": 28,
  "AppendMode": "append",
  "Timestamp": "2026-05-06T12:00:00Z",
  "TotalTracks": 538,
  "Tracks": [
    {
      "TrackArtist": "Initial D",
      "TrackTitle": "Deja Vu",
      "MBID": "abcde-12345",
      "Category": "High-energy peak-time floor fillers"
    }
  ]
}

🚀 Known State as of v3.50

    Firebase authentication and permissions successfully configured.
    429 (Rate Limit) and 503 (High Demand) errors successfully mitigated via Edge streaming, iad1 routing, and chunked prompting.
    Both the Python Generator and the Vercel Playlist Maker are working flawlessly with both Free and Paid API keys.
