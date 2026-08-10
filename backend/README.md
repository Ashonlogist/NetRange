# WiFi Coverage Heatmap

Maps WiFi signal strength across a campus by scanning at different locations and visualizing coverage as a heatmap.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Local Development

```bash
python app.py
```
Open http://localhost:5000

## How It Works

- `scanner.py` uses `nmcli` to scan for nearby WiFi networks and records SSID, BSSID, signal strength (dBm), and channel
- Each scan is tagged with GPS coordinates and saved to `data/scans.json`
- The Flask backend serves scan data and heatmap points
- The frontend uses Leaflet + OpenStreetMap with the `leaflet.heat` plugin to show signal strength density

## Usage

1. Enter your current latitude and longitude (or click **Auto-detect Location** if your browser supports it)
2. Set the **Target Network** SSID (e.g., `STUDENT`) to focus on your school's WiFi
3. Click **Scan WiFi at This Location**
4. Walk to another spot, update coords, scan again
5. Click **Load Heatmap** to see the coverage heatmap build up
6. The heatmap shows red (weak) → yellow → green (strong) signal strength
7. Colored markers on the map show each scan point

### Coverage Summary

The sidebar shows a breakdown of how many readings fall into each signal strength category:
- **Excellent** (> -50 dBm) — strong, reliable
- **Good** (-60 to -50 dBm) — solid connection
- **Fair** (-70 to -60 dBm) — usable but may drop
- **Weak** (< -70 dBm) — likely to disconnect

## Deployment

### Option 1: Render (free tier)
1. Push this repo to GitHub
2. Go to https://render.com and create a new Web Service
3. Connect your GitHub repo
4. Set build command: `pip install -r requirements.txt`
5. Set start command: `gunicorn wsgi:app --bind 0.0.0.0:$PORT`
6. Deploy

### Option 2: VPS (DigitalOcean, AWS, etc.)
```bash
sudo apt install nginx gunicorn
pip install -r requirements.txt
gunicorn wsgi:app --bind 0.0.0.0:8000 --workers 2
```
Then configure Nginx as a reverse proxy.

### Option 3: Run locally and expose to the network
```bash
HOST=0.0.0.0 PORT=5000 python app.py
```
Access from other devices on the same network via your machine's IP.

## Requirements

- Linux with `nmcli` and a WiFi interface
- Python 3.8+
- For hosting: any platform that supports Python (Render, VPS, etc.)

## Notes

- The WiFi scanner requires `nmcli` and a wireless interface — it won't work on cloud servers without WiFi hardware
- For best results, run the app on a laptop or Raspberry Pi that you can carry around campus
- Scan data accumulates in `data/scans.json` — delete it to start fresh
- The app only needs to run on the machine doing the scanning; it can be accessed from any device on the same network