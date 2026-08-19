# Setting up shared/global data (Supabase)

Scans used to live in `backend/data/scans.json` on the Render instance's
local disk. That's why coverage looked "per-person": Render's free tier
wipes local disk on every restart (redeploys, and the automatic
spin-down after ~15 min idle), so whatever was there routinely
disappeared before the next person loaded the map.

Scans now go into a `scans` table in your Supabase project instead --
one shared, durable table every client reads from.

## 1. Create the table

In your Supabase project: **SQL Editor -> New query**, paste the
contents of `db/schema.sql`, and run it. That creates the `scans`
table, its indexes, and locks it down with Row Level Security so
nothing but your backend's service key can touch it.

## 2. Get your credentials

Project **Settings -> API**:
- `SUPABASE_URL` -- the Project URL
- `SUPABASE_SERVICE_KEY` -- the **service_role** key (not the `anon`
  key). This stays server-side only -- it's set as an env var on
  Render, never shipped to the mobile app or the browser.

## 3. Set the environment variables

**On Render** (your backend host): Dashboard -> netrange-backend ->
Environment -> add:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`

**Locally**, for `python app.py` dev: create `backend/.env` (don't
commit it -- add it to `.gitignore` if it isn't already) or just export
them in your shell before running:

```bash
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_SERVICE_KEY="your-service-role-key"
python3 app.py
```

## 4. Install the new dependency

```bash
cd backend
pip install -r requirements.txt
```

(`supabase` was added to `requirements.txt` alongside `numpy`/`scipy`
from the earlier triangulation change.)

## 5. Deploy

Push to Render as usual (`render.yaml` already auto-deploys on push).
Once the env vars are set and the table exists, every device hitting
`https://netrange-backend.onrender.com` -- phones and your laptop
browser alike -- reads and writes the same shared dataset. No more
disk wipes, no more "only I can see my scans."

## What changed in code

- `backend/db.py` (new) -- `save_scan()` / `load_scans()` now talk to
  Supabase instead of a local JSON file, but return the exact same
  shapes as before, so `algorithm.py` and the rest of `app.py` didn't
  need to change.
- `backend/app.py` -- imports `save_scan`/`load_scans` from `db.py`
  instead of `scanner.py`; dropped the now-unused `SCANS_FILE`/
  `DATA_DIR` plumbing.
- `backend/scanner.py` -- untouched. `scan()` and
  `get_current_connection()` (both about the *server's* own local wifi
  state, unrelated to storage) still live there. Its old file-based
  `save_scan`/`load_scans` functions are now unused dead code -- safe
  to delete later, left alone for now so nothing else that might
  reference them breaks.
