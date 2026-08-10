# NetRange

Campus WiFi/cellular coverage mapping: scan signal strength at GPS-tagged locations on your phone, push scans to a backend, and view an interpolated coverage heatmap on a map.

## Structure

- `backend/` — Flask API. Stores scans and computes an IDW-interpolated coverage grid.
  - Endpoints: `/api/scan`, `/api/scans`, `/api/heatmap`, `/api/current`, `/api/networks`, `/api/coverage`
- `app/` — Expo (React Native) mobile app. Scans WiFi + cellular networks with GPS, syncs to the backend, and renders the coverage overlay on a map.

## Backend (local dev)

```bash
cd backend
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python app.py          # serves on 0.0.0.0:5000
```

## Mobile app (local dev)

```bash
cd app
npm install
npx expo start                  # scan QR with Expo Go on your phone
```

Set the backend URL in the app Settings (e.g. `http://<laptop-ip>:5000` or the hosted URL).

## Coverage API

`POST /api/scan` accepts scan data (network BSSID/SSID, signal dBm, lat/lon). `GET /api/coverage` returns an interpolated grid for a given network using inverse distance weighting.
