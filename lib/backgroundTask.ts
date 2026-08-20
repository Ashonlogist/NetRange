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
import NetInfo from '@react-native-community/netinfo';

export const BACKGROUND_SCAN_TASK = 'netrange-background-scan';

let _registered = false;

/**
 * Define the task body. Safe to call multiple times -- defineTask only
 * needs to happen once, and it must be called at module scope (not inside
 * a component) so the task handler exists before registration.
 */
TaskManager.defineTask(BACKGROUND_SCAN_TASK, async () => {
  try {
    const [deviceId, apiUrl] = await Promise.all([
      SecureStore.getItemAsync('deviceId'),
      SecureStore.getItemAsync('apiUrl'),
    ]);
    if (!deviceId || !apiUrl) return;

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

    // Cellular
    let cellular: any = null;
    try {
      const savedCarrier = await SecureStore.getItemAsync('carrierName');
      const netInfo = await NetInfo.fetch();
      if (netInfo.type === 'cellular') {
        const d = netInfo.details as any;
        cellular = {
          carrier: savedCarrier || d.carrier || d.mobileCarrier || d.networkName || 'Cellular',
          signalStrength: d.strength ?? d.signalStrength ?? undefined,
          networkType: d.cellularGeneration || d.type || 'Unknown',
          isConnected: netInfo.isConnected || false,
        };
      }
    } catch {}

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
    await fetch(`${apiUrl}/api/scan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        wifi,
        cellular,
        location: loc.coords,
        targetSsid: autoTarget,
        deviceId,
        timestamp: new Date().toISOString(),
        download_speed_mbps: null,
      }),
    });
  } catch {
    // swallow -- background tasks must never crash
  }
});

/**
 * Call once from app/index.tsx useEffect.
 * Registers background location updates if not already running and
 * permissions are granted.
 */
export async function registerBackgroundScan() {
  if (_registered) return;
  _registered = true;

  // Request background permission (Android requires this separately)
  const { status: fgStatus } = await Location.requestForegroundPermissionsAsync();
  if (fgStatus !== 'granted') return;

  const { status: bgStatus } = await Location.requestBackgroundPermissionsAsync();
  if (bgStatus !== 'granted') return;

  // Check if already registered
  const isRegistered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_SCAN_TASK);
  if (isRegistered) return;

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
}

/**
 * Optional: stop background scanning (e.g. from a settings toggle).
 */
export async function unregisterBackgroundScan() {
  const isRegistered = await TaskManager.isTaskRegisteredAsync(BACKGROUND_SCAN_TASK);
  if (!isRegistered) return;
  await Location.stopLocationUpdatesAsync(BACKGROUND_SCAN_TASK);
}
