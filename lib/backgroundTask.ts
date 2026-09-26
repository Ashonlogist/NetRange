/**
 * Background location + scan task.
 *
 * Registered once at app start via Location.startLocationUpdatesAsync().
 * Android triggers on significant location changes (FusedLocationProvider),
 * not on a fixed timer -- so it fires when the user actually moves, not
 * wastefully while they're sitting still.
 *
 * Runs outside the React tree, so it reads deviceId/apiUrl from
 * SecureStore directly and does its own WiFi/cellular/POST.
 */

import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import * as SecureStore from 'expo-secure-store';
import { postScan } from '@/lib/deviceAuth';
import { readCellular } from '@/lib/cellular';
import { Platform } from 'react-native';

export const BACKGROUND_SCAN_TASK = 'netrange-background-scan';
const DEFAULT_API_URL = 'https://netrange.ashonlogist.website';
const LEGACY_API_URLS = [
  'https://netrange.onrender.com',
  'https://netrange-dkb6.onrender.com',
  'https://netrange-backend.onrender.com',
];

let _registered = false;

/**
 * Define the task body. Safe to call multiple times -- defineTask only
 * needs to happen once, and it must be called at module scope (not inside
 * a component) so the task handler exists before registration.
 */
TaskManager.defineTask(BACKGROUND_SCAN_TASK, async () => {
  try {
    const [deviceId, savedApiUrl] = await Promise.all([
      SecureStore.getItemAsync('deviceId'),
      SecureStore.getItemAsync('apiUrl'),
    ]);
    const cleanedUrl = (savedApiUrl || '').trim().replace(/\/+$/, '');
    const apiUrl = !cleanedUrl || LEGACY_API_URLS.includes(cleanedUrl)
      ? DEFAULT_API_URL
      : cleanedUrl;
    if (!deviceId) return;

    // Location -- the task only fires on location updates, so one read is enough
    let loc: Location.LocationObject;
    try {
      loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
    } catch {
      return; // can't get location, nothing to report
    }

    // WiFi scan
    let wifi: any[] = [];
    try {
      const WifiManager = require('react-native-wifi-reborn').default;
      const networks = await WifiManager.loadWifiList();
      const connectedSsid = await WifiManager.getCurrentWifiSSID().catch(() => '');
      wifi = (networks || []).map((n: any) => ({
        ssid: n.SSID || '',
        bssid: n.BSSID || '',
        strength: n.level ?? n.signalStrength ?? -70,
        frequency: n.frequency,
        channel: n.channel,
        isConnected: (n.SSID || '') === connectedSsid,
      }));
    } catch {}

    // Cellular. Same reader as the foreground path, so a background scan
    // cannot quietly file itself under a different carrier than the app shows.
    const savedCarrier = await SecureStore.getItemAsync('carrierName').catch(() => null);
    const cellular: any = await readCellular(savedCarrier || undefined);

    // Determine target (WiFi or carrier)
    let autoTarget = '';
    const connectedWifi = wifi.find((n: any) => n.isConnected);
    if (connectedWifi) {
      autoTarget = connectedWifi.ssid;
    } else if (cellular?.isConnected) {
      autoTarget = cellular.carrier;
    }
    if (!autoTarget) return; // nothing connected, skip

    // Skip speed test in background -- too resource-intensive; background scans
    // contribute signal/location data, speed is measured when app is foregrounded.
    await postScan(apiUrl, deviceId, {
      wifi,
      cellular,
      location: loc.coords,
      targetSsid: autoTarget,
      deviceId,
      timestamp: new Date().toISOString(),
      download_speed_mbps: null,
    });
  } catch {
    // swallow -- background tasks must never crash
  }
});

/**
 * Call once from app/index.tsx useEffect.
 * Registers background location updates if not already running and
 * permissions are granted.
 *
 * Note the deliberate asymmetry with `getBackgroundScanStatus()` below:
 * this function is the *opt-in* path and prompts for permission, while the
 * status query is a pure read that must never prompt. The Settings tab uses
 * the read-only query so simply opening Settings can't trigger a system
 * permission dialog.
 */
export async function registerBackgroundScan() {
  if (_registered) return;

  // NB: _registered is set only once updates are genuinely running. Setting
  // it before the permission prompts made a first-run denial permanent -- the
  // Settings "Grant permission" button called this again, hit the
  // `if (_registered) return` guard, and silently did nothing, so the status
  // stayed on "Permission needed" with no way forward.
  const { status: fgStatus } = await Location.requestForegroundPermissionsAsync();
  if (fgStatus !== 'granted') return;

  const { status: bgStatus } = await Location.requestBackgroundPermissionsAsync();
  if (bgStatus !== 'granted') return;

  // Check if already registered
  const isRegistered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_SCAN_TASK);
  if (isRegistered) {
    _registered = true;
    return;
  }

  await Location.startLocationUpdatesAsync(BACKGROUND_SCAN_TASK, {
    accuracy: Location.Accuracy.Balanced,
    distanceInterval: 50, // meters -- only trigger after moving 50m
    deferredUpdatesInterval: 15 * 60 * 1000, // 15 min minimum between batches
    showsBackgroundLocationIndicator: false, // iOS only, no indicator needed
    foregroundService: {
      notificationTitle: 'NetRange',
      notificationBody: 'Scanning coverage in background',
      notificationColor: '#7c3aed',
    },
  });

  _registered = true;
}

export type BackgroundScanState =
  | 'active'
  | 'permission-needed'
  | 'not-supported';

/**
 * Read-only diagnosis of background scanning, for the Settings tab.
 *
 * Never prompts for permission -- it reports what is true right now so the
 * UI can explain why scans aren't arriving, instead of the old behaviour
 * where `registerBackgroundScan()` returned early in silence.
 *
 * `permission-needed` covers both "never asked" and "denied"; the caller
 * distinguishes those with `canAskAgain` to decide between prompting and
 * deep-linking to system settings.
 */
export async function getBackgroundScanStatus(): Promise<{
  state: BackgroundScanState;
  canAskAgain: boolean;
  foregroundGranted: boolean;
  backgroundGranted: boolean;
}> {
  if (Platform.OS !== 'android') {
    return {
      state: 'not-supported',
      canAskAgain: false,
      foregroundGranted: false,
      backgroundGranted: false,
    };
  }
  try {
    const fg = await Location.getForegroundPermissionsAsync();
    const bg = await Location.getBackgroundPermissionsAsync();
    const foregroundGranted = fg.status === 'granted';
    const backgroundGranted = bg.status === 'granted';
    // The task only actually runs if the task is registered *and* the OS has
    // background location, so a registered task without the permission is
    // still "permission needed" from the user's point of view.
    if (foregroundGranted && backgroundGranted) {
      const registered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_SCAN_TASK);
      if (registered) {
        return { state: 'active', canAskAgain: true, foregroundGranted, backgroundGranted };
      }
    }
    return {
      state: 'permission-needed',
      // canAskAgain is false once the user has permanently denied; Android
      // won't show the prompt again and the only route left is Settings.
      canAskAgain: bg.canAskAgain ?? false,
      foregroundGranted,
      backgroundGranted,
    };
  } catch {
    return {
      state: 'not-supported',
      canAskAgain: false,
      foregroundGranted: false,
      backgroundGranted: false,
    };
  }
}

/**
 * Optional: stop background scanning (e.g. from a settings toggle).
 */
export async function unregisterBackgroundScan() {
  const isRegistered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_SCAN_TASK);
  if (!isRegistered) return;
  await Location.stopLocationUpdatesAsync(BACKGROUND_SCAN_TASK);
}
