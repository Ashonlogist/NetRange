# WiFi Mapper — Expo Mobile App

Scans WiFi + Cellular signal strength on your phone, interpolates coverage maps, syncs to your Flask backend.

## Features

| Platform | WiFi Scan | Cellular Info | GPS | Map | Sync |
|----------|-----------|---------------|-----|-----|------|
| **Android** | ✅ Full | ✅ Carrier + signal | ✅ | ✅ | ✅ |
| **iOS** | Limited* | ✅ Carrier + signal | ✅ | ✅ | ✅ |
| **Web** | ❌ | ❌ | ❌ | View only | ✅ |

*iOS restricts WiFi scanning — only connected network visible.

---

## Quick Start

```bash
cd wifi-mapper
npm install
npx expo start
```

- **Mobile**: Scan QR with Expo Go (iOS/Android)
- **Web**: Press `w` in terminal
- **Native build**: `npx expo run:android` / `npx expo run:ios`

---

## Architecture

```
┌─────────────────┐     HTTP/JSON      ┌──────────────────┐
│  Expo App       │ ─────────────────► │  Flask Backend   │
│  (Phone)        │ ◄───────────────── │  (Laptop/Server) │
└─────────────────┘   Coverage data    └──────────────────┘
       │
       ▼
┌─────────────────┐
│  Native APIs    │
│  • expo-network │  (WiFi scan)
│  • @react-native-│
│    community/   │  (Cellular)
│    netinfo      │
│  • expo-location│  (GPS)
│  • react-native-│
│    maps         │  (MapView)
└─────────────────┘
```

---

## Project Structure

```
wifi-mapper/
├── app/
│   ├── _layout.tsx       # Root stack navigator
│   ├── index.tsx         # Home dashboard
│   ├── scan.tsx          # WiFi + Cellular scanner
│   ├── map.tsx           # Coverage heatmap
│   └── settings.tsx      # API URL, interpolation params
├── components/
│   ├── Providers.tsx     # App context (API URL, location, device ID)
│   └── UI.tsx            # Reusable Header, Card, Button, Input
├── app.json              # Expo config (permissions, plugins)
├── babel.config.js       # Expo Router + module resolver
├── tsconfig.json         # TypeScript config
└── package.json
```

---

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `expo-router` | File-based navigation |
| `expo-network` | WiFi scanning (Android) |
| `@react-native-community/netinfo` | Cellular + network state |
| `expo-location` | GPS coordinates |
| `react-native-maps` | MapView with heatmap overlay |
| `expo-secure-store` | Encrypted settings storage |
| `expo-device` | Device info |

---

## API Integration

The app syncs with your Flask backend (`/api/scan`, `/api/coverage`):

```typescript
// Scan payload sent to Flask
{
  wifi: [{ ssid, bssid, strength, frequency, channel, isConnected }],
  cellular: { carrier, signalStrength, networkType, isConnected },
  location: { latitude, longitude, accuracy },
  targetSsid: "STUDENT",
  deviceId: "device_123...",
  timestamp: "2026-08-06T12:34:56.789Z"
}
```

Flask endpoints expected:
- `POST /api/scan` — Save scan point
- `GET /api/coverage?ssid=...&step=...&power=...&radius=...` — Get interpolated grid

---

## Configuration

Settings screen stores in `expo-secure-store`:

| Setting | Default | Description |
|---------|---------|-------------|
| `apiUrl` | `http://localhost:5000` | Flask backend URL |
| `interpolationStep` | `0.00005` | Grid resolution (~5m) |
| `interpolationPower` | `2` | IDW power parameter |
| `interpolationRadius` | `0.005` | Max search radius (~500m) |
| `autoSync` | `false` | Auto-send scans after scan |

---

## Building for Production

```bash
# Android APK
eas build --platform android --profile preview

# iOS (requires Apple Developer)
eas build --platform ios --profile preview

# Web deploy
npx expo export --platform web
# Deploy dist/ to Vercel, Netlify, etc.
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| WiFi scan empty on iOS | iOS blocks WiFi scanning — only connected network visible |
| Location permission denied | Settings → App → Location → "While Using" |
| Can't connect to Flask | Use LAN IP (not localhost) in Settings → API URL |
| Map not loading | Check `react-native-maps` config in `app.json` |
| Metro bundler issues | `npx expo start -c` (clear cache) |

---

## License

MIT