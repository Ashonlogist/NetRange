import React, { useEffect, useState, useRef } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  Platform,
  Alert,
  TouchableOpacity,
  Animated,
  Dimensions,
  Pressable,
  Linking,
  ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { WebView } from 'react-native-webview';
import { Header, Card, Button, Input, Badge, StatRow, T } from '@/components/UI';
import { useApp } from '@/components/Providers';
import { useUpdater } from '@/components/Updater';
import * as SecureStore from 'expo-secure-store';
import { registerBackgroundScan, getBackgroundScanStatus, type BackgroundScanState } from '@/lib/backgroundTask';
import { postScan } from '@/lib/deviceAuth';
import { readCellular, requestPhonePermission } from '@/lib/cellular';

const { height: SCREEN_H } = Dimensions.get('window');
const PANEL_H = SCREEN_H * 0.7;

interface WifiNetwork {
  ssid: string;
  bssid: string;
  strength: number;
  frequency?: number;
  channel?: number;
  isConnected: boolean;
}

interface CellularInfo {
  carrier: string;
  /** Real dBm from the active SIM, or undefined when the OS withholds it. */
  signalDbm?: number;
  networkType: string;
  isConnected: boolean;
  simSlot?: number;
  overridden?: boolean;
}

function signalColor(dbm: number) {
  return dbm > -50 ? T.green : dbm > -60 ? T.yellow : dbm > -70 ? T.orange : T.red;
}
function signalLabel(dbm: number) {
  return dbm > -50 ? 'Excellent' : dbm > -60 ? 'Good' : dbm > -70 ? 'Fair' : 'Weak';
}

export default function HomeScreen() {
  const { apiUrl, currentLocation, setCurrentLocation, deviceId } = useApp();
  const updater = useUpdater();

  const [panelOpen, setPanelOpen] = useState(false);
  const [panelTab, setPanelTab] = useState<'scan' | 'settings'>('scan');
  const slideAnim = useRef(new Animated.Value(PANEL_H)).current;

  const closePanel = () => {
    if (!panelOpen) return;
    Animated.spring(slideAnim, { toValue: PANEL_H, useNativeDriver: true }).start();
    setPanelOpen(false);
  };

  const [scanning, setScanning] = useState(false);
  const [wifiNetworks, setWifiNetworks] = useState<WifiNetwork[]>([]);
  const [cellularInfo, setCellularInfo] = useState<CellularInfo | null>(null);
  const [targetSsid, setTargetSsid] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const [autoSync, setAutoSync] = useState(false);

  const [carrierOverride, setCarrierOverride] = useState('');
  const [showDisclosure, setShowDisclosure] = useState(false);
  const [bgScan, setBgScan] = useState<{
    state: BackgroundScanState;
    canAskAgain: boolean;
  } | null>(null);
  const [bgBusy, setBgBusy] = useState(false);

  const [webError, setWebError] = useState<string | null>(null);

  const webViewRef = useRef<WebView>(null);

  // A reload that succeeds must clear the previous failure, otherwise the
  // error view would stay pinned over a map that is now fine.
  const handleWebRetry = () => {
    setWebError(null);
    webViewRef.current?.reload();
  };

  // Background scan status is a read-only query that must not prompt for
  // permission, so it is refreshed on mount and on demand from Settings --
  // deliberately not on every panel open, to avoid a redundant call.
  const refreshBgScan = async () => {
    const s = await getBackgroundScanStatus();
    setBgScan({ state: s.state, canAskAgain: s.canAskAgain });
  };

  useEffect(() => {
    refreshBgScan();
  }, []);

  const handleBgScanAction = async () => {
    setBgBusy(true);
    try {
      const s = await getBackgroundScanStatus();
      if (s.state === 'active') return;
      if (s.canAskAgain) {
        // Re-run the opt-in registration, which triggers the system prompt.
        await registerBackgroundScan();
      } else {
        // Permanently denied -- the OS won't show a prompt again, so the
        // only route left is the app's settings page.
        await Linking.openSettings();
      }
      await refreshBgScan();
    } catch {
      Alert.alert('Error', 'Could not update background scanning.');
    } finally {
      setBgBusy(false);
    }
  };

  useEffect(() => {
    loadSettings();
    if (Platform.OS !== 'web') {
      loadNetworks();
      registerBackgroundScan().catch(() => {
        // Deliberately silent here: this runs on every app start, and a
        // failure is not actionable for the user at this point. The Settings
        // tab surfaces the real state via getBackgroundScanStatus().
      });
      SecureStore.getItemAsync('disclosureAccepted').then(v => {
        if (!v) setShowDisclosure(true);
      });
    }
  }, []);

  useEffect(() => {
    if (Platform.OS === 'web') return;
    let mounted = true;
    const startTracking = async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted' || !mounted) return;
      void requestPhonePermission();
      const sub = await Location.watchPositionAsync(
        { accuracy: Location.Accuracy.Balanced, distanceInterval: 10, timeInterval: 15000 },
        (loc) => {
          if (!mounted) return;
          setCurrentLocation(loc.coords);
          webViewRef.current?.injectJavaScript(`
            (function() {
              if (typeof L !== 'undefined' && typeof map === 'undefined') return;
              if (typeof locMarker !== 'undefined' && locMarker) map.removeLayer(locMarker);
              if (typeof locCircle !== 'undefined' && locCircle) map.removeLayer(locCircle);
              locMarker = L.circleMarker([${loc.coords.latitude}, ${loc.coords.longitude}], {
                radius: 8, color: '#7c3aed', fillColor: '#7c3aed', fillOpacity: 0.9, weight: 3
              }).addTo(map).bindPopup('You are here');
              locCircle = L.circle([${loc.coords.latitude}, ${loc.coords.longitude}], {
                radius: 50, color: 'rgba(124,58,237,0.3)', fillColor: 'rgba(124,58,237,0.1)', fillOpacity: 0.5, weight: 1
              }).addTo(map);
              map.setView([${loc.coords.latitude}, ${loc.coords.longitude}], map.getZoom());
            })();
            true;
          `);
        }
      );
    };
    startTracking();
    return () => { mounted = false; };
  }, []);

  const measureDownloadSpeed = async (): Promise<number | null> => {
    try {
      const url = 'https://speed.cloudflare.com/__down?bytes=1000000';
      const start = Date.now();
      const resp = await fetch(url);
      const blob = await resp.blob();
      const elapsed = (Date.now() - start) / 1000;
      if (elapsed < 0.05) return null;
      const mbps = parseFloat(((blob.size * 8) / (elapsed * 1000000)).toFixed(2));
      return mbps;
    } catch {
      return null;
    }
  };

  useEffect(() => {
    if (Platform.OS === 'web') return;
    const interval = setInterval(async () => {
      try {
        const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
        setCurrentLocation(loc.coords);

        let wifi: WifiNetwork[] = [];
        let cellular: CellularInfo | null = null;
        const savedCarrier = await SecureStore.getItemAsync('carrierName').catch(() => null);
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

        // Reads the SIM that is actually carrying data, and its real dBm. The
        // old NetInfo path reported whichever SIM Android considered default,
        // which on a dual-SIM handset meant scans filed under the wrong
        // carrier, and it had no signal field at all.
        cellular = await readCellular(carrierOverride || savedCarrier || undefined);

        let autoTarget = '';
        const connectedWifi = wifi.find(n => n.isConnected);
        if (connectedWifi) {
          autoTarget = connectedWifi.ssid;
        } else if (cellular?.isConnected) {
          autoTarget = cellular.carrier;
        }
        setCellularInfo(cellular);
        setWifiNetworks(wifi);
        if (autoTarget && autoTarget !== targetSsid) {
          setTargetSsid(autoTarget);
        }
        if (!autoTarget) return;

        const speed = await measureDownloadSpeed();

        await postScan(apiUrl, deviceId, {
          wifi,
          cellular,
          location: loc.coords,
          targetSsid: autoTarget,
          deviceId,
          timestamp: new Date().toISOString(),
          download_speed_mbps: speed,
        });

        const meshResp = await fetch(`${apiUrl}/api/mesh?ssid=${encodeURIComponent(autoTarget)}`);
        const meshData = await meshResp.json();
        if (meshData.triangles && meshData.triangles.length > 0) {
          webViewRef.current?.injectJavaScript(`
            window.renderMeshOnMap(${JSON.stringify(meshData.triangles)});
            true;
          `);
        }
      } catch {}
    }, 120000);
    return () => clearInterval(interval);
  }, [targetSsid, apiUrl, deviceId]);

  const [coverageMetric, setCoverageMetricState] = useState<'speed' | 'signal'>('speed');

  const handleMetric = (metric: 'speed' | 'signal') => {
    setCoverageMetricState(metric);
    webViewRef.current?.injectJavaScript(`
      if (typeof setCoverageMetric === 'function') setCoverageMetric('${metric}');
      true;
    `);
  };

  const togglePanel = () => {
    const toValue = panelOpen ? PANEL_H : 0;
    Animated.spring(slideAnim, { toValue, useNativeDriver: true }).start();
    setPanelOpen(!panelOpen);
  };

  const loadSettings = async () => {
    try {
      const [sync, carrier] = await Promise.all([
        SecureStore.getItemAsync('autoSync'),
        SecureStore.getItemAsync('carrierName'),
      ]);
      if (sync) setAutoSync(sync === 'true');
      if (carrier) setCarrierOverride(carrier);
    } catch {}
  };

  const loadNetworks = async () => {
    if (Platform.OS === 'web') return;
    setScanning(true);
    setError('');
    let cellular: CellularInfo | null = null;
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setError('Location permission denied.');
        setScanning(false);
        return;
      }
      void requestPhonePermission();
      let loc: Location.LocationObject;
      try {
        loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      } catch {
        loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
      }
      setCurrentLocation(loc.coords);

      let wifi: WifiNetwork[] = [];
      if (Platform.OS === 'android') {
        try {
          const WifiManager = require('react-native-wifi-reborn').default;
          const networks = await WifiManager.loadWifiList();
          const connectedSsid = await WifiManager.getCurrentWifiSSID().catch(() => '');
          wifi = networks
            .map((n: any) => ({
              ssid: n.SSID || 'hidden',
              bssid: n.BSSID || '',
              strength: n.level || 0,
              frequency: n.frequency,
              channel: n.frequency ? Math.round((n.frequency - 2407) / 5) : undefined,
              isConnected: (n.SSID || '') === connectedSsid,
            }))
            .sort((a: WifiNetwork, b: WifiNetwork) => b.strength - a.strength);
        } catch (e: any) {
          const msg = e?.message || 'WiFi scan failed';
          if (msg === 'locationServicesOff') setError('Location Services must be ON.');
          else setError(`WiFi: ${msg}`);
        }
      }

      const savedCarrier = await SecureStore.getItemAsync('carrierName').catch(() => null);
      cellular = await readCellular(carrierOverride || savedCarrier || undefined);

      setWifiNetworks(wifi);
      setCellularInfo(cellular);

      const connectedWifi = wifi.find(n => n.isConnected);
      if (connectedWifi) {
        setTargetSsid(connectedWifi.ssid);
      } else if (cellular?.isConnected) {
        setTargetSsid(cellular.carrier);
      }
    } catch (e: any) {
      setError(e.message || 'Scan failed');
    } finally {
      setScanning(false);
    }
  };

  const handleSelectNetwork = (ssid: string) => setTargetSsid(ssid);

  /**
   * Drop the stored override and re-detect immediately. Without this the
   * override is permanent: every detection path prefers `carrierName` over
   * what the network actually reports, so once set there was previously no
   * way back to auto-detect.
   */
  const handleClearCarrier = async () => {
    try {
      await SecureStore.deleteItemAsync('carrierName');
      setCarrierOverride('');
      // Re-run detection so the UI reflects the real carrier immediately
      // rather than waiting for the next poll tick.
      await loadNetworks();
    } catch {
      Alert.alert('Error', 'Failed to clear the carrier override.');
    }
  };

  const handleEditCarrier = () => {
    if (!cellularInfo) return;
    setPanelTab('settings');
  };

  const [refreshing, setRefreshing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [showAd, setShowAd] = useState(false);

  const handleAutoDetectLocation = async () => {
    setRefreshing(true);
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setError('Location permission denied.');
        return;
      }
      const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      setCurrentLocation(loc.coords);
      webViewRef.current?.injectJavaScript(`
        (function() {
          if (typeof L !== 'undefined' && typeof map !== 'undefined') {
            if (typeof locMarker !== 'undefined' && locMarker) map.removeLayer(locMarker);
            if (typeof locCircle !== 'undefined' && locCircle) map.removeLayer(locCircle);
            locMarker = L.circleMarker([${loc.coords.latitude}, ${loc.coords.longitude}], {
              radius: 8, color: '#7c3aed', fillColor: '#7c3aed', fillOpacity: 0.9, weight: 3
            }).addTo(map).bindPopup('You are here');
            locCircle = L.circle([${loc.coords.latitude}, ${loc.coords.longitude}], {
              radius: 50, color: 'rgba(124,58,237,0.3)', fillColor: 'rgba(124,58,237,0.1)', fillOpacity: 0.5, weight: 1
            }).addTo(map);
            map.setView([${loc.coords.latitude}, ${loc.coords.longitude}], 17);
          }
        })();
        true;
      `);
    } catch (e: any) {
      setError(e.message || 'Location failed');
    } finally {
      setRefreshing(false);
    }
  };

  const handleSaveScan = async () => {
    if (!targetSsid) return Alert.alert('No Target', 'Tap a network first');
    if (!currentLocation) return Alert.alert('No Location', 'Wait for GPS');
    setSaving(true);
    try {
      const speed = await measureDownloadSpeed();
      const response = await postScan(apiUrl, deviceId, {
        wifi: wifiNetworks,
        cellular: cellularInfo,
        location: currentLocation,
        targetSsid,
        deviceId,
        timestamp: new Date().toISOString(),
        download_speed_mbps: speed,
      });
      if (response.status === 401) {
        Alert.alert(
          'Save Failed',
          'This device is not authorised to write scans. Reopen the app to register, then try again.'
        );
        return;
      }
      if (!response.ok) {
        Alert.alert('Save Failed', `Server error ${response.status}`);
        return;
      }
      const data = await response.json();
      Alert.alert('Saved', `${data.count} points saved`);
      handleGenerateCoverage();
    } catch (e) {
      Alert.alert('Save Failed', e instanceof Error ? e.message : 'Network error');
    } finally {
      setSaving(false);
    }
  };

  const handleGenerateCoverage = async () => {
    if (!targetSsid) return Alert.alert('No Target', 'Tap a network first');
    setGenerating(true);
    try {
      const meshUrl = `${apiUrl}/api/mesh?ssid=${encodeURIComponent(targetSsid)}`;
      const meshResp = await fetch(meshUrl);
      if (!meshResp.ok) {
        throw new Error(`Server returned ${meshResp.status} for the coverage mesh`);
      }
      const meshData = await meshResp.json();
      if (meshData.triangles && meshData.triangles.length > 0) {
        webViewRef.current?.injectJavaScript(`
          window.renderMeshOnMap(${JSON.stringify(meshData.triangles)});
          true;
        `);
        Alert.alert('Map Loaded', meshData.triangles.length + ' Delaunay triangles rendered');
        setShowAd(true);
        setTimeout(() => setShowAd(false), 8000);
        setGenerating(false);
        return;
      }
      const hUrl = `${apiUrl}/api/heatmap?ssid=${encodeURIComponent(targetSsid)}`;
      const hResp = await fetch(hUrl);
      if (!hResp.ok) {
        throw new Error(`Server returned ${hResp.status} for the scan points`);
      }
      const hData = await hResp.json();
      if (!hData.points || hData.points.length === 0) {
        // The old message was a flat "no data", which is what you saw while
        // every cellular scan was being stored with a null signal: the server
        // was discarding rows the app had happily saved. Say which of the two
        // reasons it is, so this is diagnosable instead of a dead end.
        const skipped = hData.skipped_no_signal || 0;
        Alert.alert(
          'No Mappable Data',
          skipped > 0
            ? `${skipped} scan${skipped === 1 ? '' : 's'} saved for ${targetSsid}, but none carried a signal reading, so there is nothing to draw.\n\n` +
              'Android only reports a cellular signal level once the Phone permission is granted. Grant it, then scan again.'
            : `Nothing saved for "${targetSsid}" yet. Tap Save at a few different locations first.`,
        );
        setGenerating(false);
        return;
      }
      const heatJson = JSON.stringify(hData.points);
      // No mesh yet, so render the raw points. The colouring lives in
      // map.html (METRIC_COLORS) so this path speaks the same colour
      // language as the mesh path; it used to inject its own conflicting
      // green/cyan/yellow/orange/red ramp here.
      webViewRef.current?.injectJavaScript(`
        if (typeof window.renderPointsOnMap === 'function') window.renderPointsOnMap(${heatJson});
        true;
      `);
      Alert.alert('Map Loaded', hData.points.length + ' scan points rendered');
    } catch (e) {
      Alert.alert('Error', e instanceof Error ? e.message : 'Failed to load coverage');
    } finally {
      setGenerating(false);
    }
  };

  const saveSettings = async () => {
    try {
      await SecureStore.setItemAsync('autoSync', autoSync.toString());
      Alert.alert('Saved', 'Settings saved');
    } catch {
      Alert.alert('Error', 'Failed to save');
    }
  };

  const mapUrl = `${apiUrl}/map?app=1`;

  return (
    <View style={s.container}>
      {showDisclosure && (
        <View style={s.disclosureOverlay}>
          <View style={s.disclosureCard}>
            <Ionicons name="shield-checkmark" size={36} color={T.accent} style={{ marginBottom: 12 }} />
            <Text style={s.disclosureTitle}>Data &amp; Privacy Notice</Text>
            <Text style={s.disclosureBody}>
              NetRange collects anonymized, aggregated coverage data (signal strength,
              network name, and approximate location) to build a public coverage map.
            </Text>
            <Text style={s.disclosureBody}>
              This data may be shared or sold in aggregated form to third parties such as
              telecom providers and urban planners. Your individual location is never
              identified — only area-level averages from 3+ contributors are published.
            </Text>
            <Text style={s.disclosureBody}>
              Background scanning runs passively while you move. You can disable it in
              Settings at any time.
            </Text>
            <TouchableOpacity style={s.disclosureBtn} onPress={async () => {
              await SecureStore.setItemAsync('disclosureAccepted', 'true');
              setShowDisclosure(false);
            }}>
              <Text style={s.disclosureBtnText}>I Understand</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      <WebView
        ref={webViewRef}
        source={{ uri: mapUrl }}
        style={s.web}
        javaScriptEnabled
        domStorageEnabled
        onLoadStart={() => setWebError(null)}
        onError={(e) => {
          // navigationFailure is not a hard load failure -- the main frame
          // failing is. Treat only main-frame errors as "the map is broken".
          const navFailure = e.nativeEvent as unknown as { navigationFailure?: boolean };
          if (navFailure?.navigationFailure) return;
          setWebError(
            e.nativeEvent?.description ||
            'Could not load the map. Check your connection and that the server is reachable.'
          );
        }}
        renderLoading={() => (
          <View style={s.webState}>
            <ActivityIndicator size="large" color={T.accent} />
            <Text style={s.webStateText}>Loading map…</Text>
          </View>
        )}
        renderError={() => (
          <View style={s.webState}>
            <Ionicons name="cloud-offline-outline" size={36} color={T.red} />
            <Text style={s.webStateTitle}>Map unavailable</Text>
            <Text style={s.webStateText}>
              {webError || 'Could not load the map. Check your connection and that the server is reachable.'}
            </Text>
            <Button title="Retry" onPress={handleWebRetry} icon="↺" />
          </View>
        )}
      />

      {panelOpen && (
        <TouchableOpacity style={s.mapOverlay} onPress={closePanel} activeOpacity={1} />
      )}

      {/* The FAB is a menu button, not a close button. It used to turn into an
          X while the sheet was open, which sat on top of the panel and swallowed
          taps meant for whatever was underneath it. Tapping the map closes the
          sheet, as does the drag handle, so the X was redundant *and* harmful. */}
      {!panelOpen && (
        <TouchableOpacity style={s.fab} onPress={togglePanel} activeOpacity={0.8}>
          <Ionicons name="menu" size={24} color="white" />
        </TouchableOpacity>
      )}

      {targetSsid && !panelOpen && (
        <View style={s.targetBadge}>
          <Ionicons name="checkmark-circle" size={14} color={T.green} />
          <Text style={s.targetBadgeText} numberOfLines={1}>{targetSsid}</Text>
        </View>
      )}

      {showAd && (
        <View style={s.adBanner}>
          <Text style={s.adLabel}>SPONSORED</Text>
          <Text style={s.adText}>Coverage data powered by NetRange</Text>
        </View>
      )}

      <Animated.View style={[s.panel, { transform: [{ translateY: slideAnim }] }]}>
        <TouchableOpacity style={s.panelHandle} onPress={closePanel} activeOpacity={0.7}>
          <View style={s.panelBar} />
        </TouchableOpacity>

        <View style={s.tabRow}>
          <TouchableOpacity
            style={[s.tab, panelTab === 'scan' && s.tabActive]}
            onPress={() => setPanelTab('scan')}
          >
            <Ionicons name="radio-outline" size={16} color={panelTab === 'scan' ? T.accent : T.textMuted} />
            <Text style={[s.tabText, panelTab === 'scan' && s.tabTextActive]}>Scan</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[s.tab, panelTab === 'settings' && s.tabActive]}
            onPress={() => setPanelTab('settings')}
          >
            <Ionicons name="settings-outline" size={16} color={panelTab === 'settings' ? T.accent : T.textMuted} />
            <Text style={[s.tabText, panelTab === 'settings' && s.tabTextActive]}>Settings</Text>
          </TouchableOpacity>
        </View>

        <ScrollView style={s.panelScroll} contentContainerStyle={s.panelContent}>
          {panelTab === 'scan' ? (
            <>
              {error ? (
                <Card style={s.errorCard}>
                  <Ionicons name="alert-circle" size={16} color={T.red} />
                  <Text style={s.errorText}>{error}</Text>
                </Card>
              ) : null}

              <Button
                title={scanning ? 'Scanning...' : 'Scan Now'}
                onPress={loadNetworks}
                disabled={scanning}
                loading={scanning}
                icon={scanning ? undefined : '📡'}
              />

              {targetSsid ? (
                <Card style={s.targetCard}>
                  <View style={s.targetRow}>
                    <Ionicons name="checkmark-circle" size={16} color={T.green} />
                    <Text style={s.targetLabel}>Target:</Text>
                    <Text style={s.targetValue}>{targetSsid}</Text>
                    <TouchableOpacity onPress={() => setTargetSsid('')}>
                      <Ionicons name="close-circle" size={16} color={T.textMuted} />
                    </TouchableOpacity>
                  </View>
                </Card>
              ) : null}

              {currentLocation && (
                <Card style={s.locCard}>
                  <View style={s.locRow}>
                    <Ionicons name="location" size={14} color={T.accent2} />
                    <Text style={s.locText}>
                      {currentLocation.latitude.toFixed(4)}, {currentLocation.longitude.toFixed(4)}
                    </Text>
                  </View>
                </Card>
              )}

              {currentLocation && (
                <Button title={refreshing ? 'Locating...' : 'Refresh Location'} onPress={handleAutoDetectLocation} disabled={refreshing} loading={refreshing} variant="secondary" icon="📍" />
              )}

              {wifiNetworks.length > 0 && (
                <>
                  <Text style={s.sectionTitle}>WiFi ({wifiNetworks.length})</Text>
                  {wifiNetworks.map((net, i) => {
                    const selected = targetSsid === net.ssid;
                    return (
                      <TouchableOpacity key={`${net.bssid}-${i}`} onPress={() => handleSelectNetwork(net.ssid)} activeOpacity={0.7}>
                        <Card style={[s.networkCard, selected && s.networkCardSelected]}>
                          <View style={s.networkRow}>
                            <View style={s.networkInfo}>
                              <View style={s.networkNameRow}>
                                {selected && <Ionicons name="checkmark-circle" size={12} color={T.green} />}
                                <Text style={[s.networkSsid, selected && { color: T.green }]} numberOfLines={1}>{net.ssid}</Text>
                                {net.isConnected && <Badge label="Connected" variant="success" />}
                              </View>
                              <Text style={s.networkMeta}>Ch {net.channel || '?'} · {net.frequency} MHz</Text>
                            </View>
                            <View style={s.signalCol}>
                              <Text style={[s.signalDbm, { color: signalColor(net.strength) }]}>{net.strength}</Text>
                              <Badge label={signalLabel(net.strength)} variant={net.strength > -50 ? 'success' : net.strength > -60 ? 'warning' : 'danger'} />
                            </View>
                          </View>
                        </Card>
                      </TouchableOpacity>
                    );
                  })}
                </>
              )}

              {cellularInfo && (
                <>
                  <Text style={s.sectionTitle}>Cellular</Text>
                  <TouchableOpacity
                    onPress={() => handleSelectNetwork(cellularInfo.carrier)}
                    onLongPress={handleEditCarrier}
                    activeOpacity={0.7}
                  >
                    <Card style={[s.networkCard, targetSsid === cellularInfo.carrier && s.networkCardSelected]}>
                      <View style={s.networkRow}>
                        <View style={s.networkInfo}>
                          <View style={s.networkNameRow}>
                            {targetSsid === cellularInfo.carrier && <Ionicons name="checkmark-circle" size={12} color={T.green} />}
                            <Text style={[s.networkSsid, targetSsid === cellularInfo.carrier && { color: T.green }]}>{cellularInfo.carrier}</Text>
                            {/* Which SIM this reading came from. On a dual-SIM
                                handset the old code silently reported the other
                                one, so this is the difference between "wrong
                                carrier" being a mystery and being obvious. */}
                            {cellularInfo.simSlot != null && (
                              <Badge label={`SIM${cellularInfo.simSlot + 1}`} variant="info" />
                            )}
                            {cellularInfo.overridden && <Badge label="Renamed" variant="warning" />}
                            {cellularInfo.isConnected && <Badge label="Connected" variant="success" />}
                            <Ionicons name="pencil" size={10} color={T.textMuted} style={{ marginLeft: 4 }} />
                          </View>
                          <Text style={s.networkMeta}>
                            {cellularInfo.networkType}
                            {cellularInfo.signalDbm != null
                              ? ` · ${Math.round(cellularInfo.signalDbm)} dBm`
                              : ' · no signal reading'}
                          </Text>
                        </View>
                        <Badge label={cellularInfo.isConnected ? 'Online' : 'Offline'} variant={cellularInfo.isConnected ? 'success' : 'danger'} />
                      </View>
                    </Card>
                  </TouchableOpacity>
                  <Text style={{ fontSize: 10, color: T.textMuted, textAlign: 'center', marginTop: 2 }}>
                    Long press to rename carrier
                  </Text>
                </>
              )}

              {targetSsid && (
                <View style={s.actions}>
                  <Button title={saving ? 'Saving...' : 'Save'} onPress={handleSaveScan} disabled={saving} loading={saving} variant="secondary" style={{ flex: 1 }} />
                  <Button title={generating ? 'Loading...' : 'Generate Map'} onPress={handleGenerateCoverage} disabled={generating} loading={generating} icon="🗺️" style={{ flex: 1 }} />
                </View>
              )}

              <View style={s.metricRow}>
                <Text style={s.metricLabel}>Colour map by</Text>
                <View style={s.metricSeg}>
                  {(['speed', 'signal'] as const).map((m) => (
                    <TouchableOpacity
                      key={m}
                      style={[s.metricSegBtn, coverageMetric === m && s.metricSegBtnActive]}
                      onPress={() => handleMetric(m)}
                    >
                      <Text style={[s.metricSegText, coverageMetric === m && s.metricSegTextActive]}>
                        {m === 'speed' ? 'Speed' : 'Signal'}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>
            </>
          ) : (
            <>
              <Text style={s.sectionTitle}>Carrier Override</Text>
              <Input
                label="Carrier Name"
                value={carrierOverride}
                onChangeText={setCarrierOverride}
                placeholder={cellularInfo?.carrier || 'e.g. Telecel'}
              />
              <Text style={s.hintText}>
                {carrierOverride.trim()
                  ? 'Active — this name is used for every cellular scan, overriding what the network reports.'
                  : 'No override set. NetRange uses the carrier detected from the network.'}
              </Text>
              <View style={{ gap: 8 }}>
                <Button
                  title="Save Carrier"
                  onPress={async () => {
                    if (!carrierOverride.trim()) return;
                    await SecureStore.setItemAsync('carrierName', carrierOverride.trim());
                    setCellularInfo(prev => prev ? { ...prev, carrier: carrierOverride.trim() } : null);
                    Alert.alert('Saved', `Carrier set to ${carrierOverride.trim()}`);
                  }}
                  variant="secondary"
                />
                {carrierOverride.trim() ? (
                  <Button
                    title="Clear override & use auto-detect"
                    onPress={handleClearCarrier}
                    variant="secondary"
                    icon="↺"
                  />
                ) : null}
              </View>

              <Text style={s.sectionTitle}>Background Scanning</Text>
              <Card style={s.statusCard}>
                <View style={s.statusRow}>
                  {bgScan?.state === 'active' ? (
                    <Ionicons name="checkmark-circle" size={16} color={T.green} />
                  ) : bgScan?.state === 'permission-needed' ? (
                    <Ionicons name="alert-circle" size={16} color={T.yellow} />
                  ) : bgScan?.state === 'not-supported' ? (
                    <Ionicons name="remove-circle-outline" size={16} color={T.textMuted} />
                  ) : (
                    <ActivityIndicator size="small" color={T.textMuted} />
                  )}
                  <Text
                    style={[
                      s.statusText,
                      bgScan?.state === 'active' && { color: T.green },
                      bgScan?.state === 'permission-needed' && { color: T.yellow },
                    ]}
                  >
                    {!bgScan
                      ? 'Checking...'
                      : bgScan.state === 'active'
                        ? 'Active'
                        : bgScan.state === 'permission-needed'
                          ? 'Permission needed'
                          : 'Not supported on this device'}
                  </Text>
                </View>
                <Text style={s.hintText}>
                  {!bgScan
                    ? 'Reading current permission and task state.'
                    : bgScan.state === 'active'
                      ? 'Scans are recorded while you move, even with the app closed.'
                      : bgScan.state === 'permission-needed'
                        ? 'NetRange needs "Allow all the time" location access to record scans in the background. Foreground scanning still works without it.'
                        : 'Background scanning requires Android. Foreground scanning still works.'}
                </Text>
                {bgScan?.state === 'permission-needed' && (
                  <Button
                    title={bgBusy ? 'Working...' : bgScan.canAskAgain ? 'Grant permission' : 'Open app settings'}
                    onPress={handleBgScanAction}
                    disabled={bgBusy}
                    loading={bgBusy}
                    variant="secondary"
                  />
                )}
                {bgScan?.state === 'active' && (
                  <Button
                    title="Re-check"
                    onPress={refreshBgScan}
                    variant="secondary"
                    icon="↺"
                  />
                )}
              </Card>
              <Text style={s.hintText}>
                Some Android OEMs (Samsung, Xiaomi, Huawei, OnePlus and others) stop
                background tasks unless the app is exempted from battery optimisation.
                If status stays on "Active" but no new scans arrive, check
                Settings → Apps → NetRange → Battery and set it to Unrestricted.
              </Text>

              <Text style={s.sectionTitle}>Update</Text>
              <StatRow label="Installed" value={`v${updater.currentVersion}`} />
              {updater.updateInfo && updater.updateInfo.version !== updater.currentVersion && (
                <StatRow label="Latest" value={`v${updater.updateInfo.version}`} valueColor={T.accent} />
              )}
              <View style={{ gap: 8, marginTop: 8 }}>
                <Button
                  title={updater.state === 'checking' ? 'Checking...' : 'Check for Updates'}
                  onPress={() => updater.checkForUpdates(false)}
                  disabled={updater.state === 'checking' || updater.state === 'downloading'}
                  variant="secondary"
                />
                {updater.state === 'available' && (
                  <Button title="Download & Install" onPress={updater.downloadUpdate} />
                )}
              </View>

              <Text style={s.sectionTitle}>Device</Text>
              <StatRow label="ID" value={deviceId || 'unknown'} />

              <Button title="Save Settings" onPress={saveSettings} variant="secondary" />
            </>
          )}
        </ScrollView>
      </Animated.View>
    </View>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: T.bg },
  web: { flex: 1, backgroundColor: '#070a14' },
  fab: {
    position: 'absolute',
    bottom: 24,
    right: 24,
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: T.accent,
    alignItems: 'center',
    justifyContent: 'center',
    elevation: 8,
    shadowColor: '#7c3aed',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 12,
    zIndex: 300,
  },
  mapOverlay: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 150,
  },
  targetBadge: {
    position: 'absolute',
    bottom: 90,
    right: 24,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: 'rgba(15,20,40,0.85)',
    borderRadius: 20,
    borderWidth: 1,
    borderColor: 'rgba(34,197,94,0.3)',
    maxWidth: 200,
    zIndex: 99,
  },
  targetBadgeText: { color: T.green, fontSize: 12, fontWeight: '600', flexShrink: 1 },
  adBanner: {
    position: 'absolute',
    bottom: 100,
    left: 24,
    right: 24,
    backgroundColor: 'rgba(15,20,40,0.92)',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    paddingVertical: 10,
    paddingHorizontal: 14,
    alignItems: 'center',
    zIndex: 98,
  },
  adLabel: { fontSize: 8, fontWeight: '700', color: T.textMuted, letterSpacing: 1, marginBottom: 2 },
  adText: { fontSize: 12, fontWeight: '600', color: T.accent },
  disclosureOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.85)',
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 200,
  },
  disclosureCard: {
    width: '85%',
    maxWidth: 360,
    backgroundColor: 'rgba(15,20,40,0.98)',
    borderRadius: 16,
    borderWidth: 1,
    borderColor: 'rgba(124,58,237,0.3)',
    padding: 24,
    alignItems: 'center',
  },
  disclosureTitle: { fontSize: 17, fontWeight: '800', color: '#fff', marginBottom: 12, textAlign: 'center' },
  disclosureBody: { fontSize: 13, fontWeight: '400', color: 'rgba(255,255,255,0.7)', lineHeight: 19, marginBottom: 10, textAlign: 'center' },
  disclosureBtn: {
    marginTop: 12,
    backgroundColor: T.accent,
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 32,
  },
  disclosureBtnText: { fontSize: 14, fontWeight: '700', color: '#fff' },
  panel: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    height: PANEL_H,
    backgroundColor: 'rgba(10,14,28,0.97)',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: 1,
    borderBottomWidth: 0,
    borderColor: 'rgba(255,255,255,0.08)',
    zIndex: 200,
  },
  panelHandle: { alignItems: 'center', paddingVertical: 10 },
  panelBar: { width: 40, height: 4, borderRadius: 2, backgroundColor: 'rgba(255,255,255,0.2)' },
  tabRow: {
    flexDirection: 'row',
    paddingHorizontal: 20,
    gap: 8,
    marginBottom: 8,
  },
  tab: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: 'rgba(255,255,255,0.05)',
  },
  tabActive: { backgroundColor: 'rgba(124,58,237,0.2)' },
  tabText: { fontSize: 13, fontWeight: '600', color: T.textMuted },
  tabTextActive: { color: T.accent },
  panelScroll: { flex: 1 },
  panelContent: { padding: 20, paddingBottom: 40, gap: 10 },
  sectionTitle: {
    fontSize: 11,
    fontWeight: '600',
    color: T.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    marginTop: 6,
    marginBottom: 2,
  },
  networkCard: { padding: 12 },
  networkCardSelected: { borderColor: T.green, borderWidth: 1 },
  networkRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  networkInfo: { flex: 1, marginRight: 10 },
  networkNameRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 2 },
  networkSsid: { fontSize: 14, fontWeight: '600', color: T.text, flexShrink: 1 },
  networkMeta: { fontSize: 10, color: T.textMuted, fontFamily: 'monospace' },
  signalCol: { alignItems: 'flex-end', gap: 3 },
  signalDbm: { fontSize: 14, fontWeight: '700', fontFamily: 'monospace' },
  actions: { flexDirection: 'row', gap: 10, marginTop: 4 },
  metricRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginTop: 10 },
  metricLabel: { fontSize: 11, fontWeight: '600', color: T.textMuted, textTransform: 'uppercase', letterSpacing: 0.6 },
  metricSeg: { flexDirection: 'row', backgroundColor: 'rgba(255,255,255,0.05)', borderRadius: 10, padding: 3, gap: 3 },
  metricSegBtn: { paddingHorizontal: 16, paddingVertical: 7, borderRadius: 8 },
  metricSegBtnActive: { backgroundColor: T.accent },
  metricSegText: { fontSize: 12, fontWeight: '600', color: T.textMuted },
  metricSegTextActive: { color: '#fff' },
  errorCard: { flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: 'rgba(239,68,68,0.1)', borderColor: 'rgba(239,68,68,0.2)' },
  errorText: { color: '#fca5a5', fontSize: 12, flex: 1 },
  targetCard: { borderColor: T.green, borderWidth: 1 },
  targetRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  targetLabel: { fontSize: 12, color: T.textMuted },
  targetValue: { fontSize: 13, fontWeight: '600', color: T.green, flex: 1 },
  locCard: { paddingVertical: 8, paddingHorizontal: 12 },
  locRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  locText: { fontSize: 12, color: T.accent2, fontFamily: 'monospace' },
  hintText: { fontSize: 11, color: T.textMuted, lineHeight: 15, marginBottom: 2 },
  webState: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: T.bg,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    padding: 24,
  },
  webStateTitle: { fontSize: 16, fontWeight: '700', color: T.text },
  statusCard: { gap: 8 },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  statusText: { fontSize: 14, fontWeight: '700', color: T.text },
});
