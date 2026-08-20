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
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import NetInfo from '@react-native-community/netinfo';
import { WebView } from 'react-native-webview';
import { Header, Card, Button, Input, Badge, StatRow, T } from '@/components/UI';
import { useApp } from '@/components/Providers';
import { useUpdater } from '@/components/Updater';
import * as SecureStore from 'expo-secure-store';

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
  signalStrength?: number;
  networkType: string;
  isConnected: boolean;
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

  const [interpolationStep, setInterpolationStep] = useState('0.00005');
  const [interpolationPower, setInterpolationPower] = useState('2');
  const [interpolationRadius, setInterpolationRadius] = useState('0.005');
  const [autoSync, setAutoSync] = useState(false);

  const [carrierOverride, setCarrierOverride] = useState('');

  const webViewRef = useRef<WebView>(null);

  useEffect(() => {
    loadSettings();
    if (Platform.OS !== 'web') loadNetworks();
  }, []);

  const togglePanel = () => {
    const toValue = panelOpen ? PANEL_H : 0;
    Animated.spring(slideAnim, { toValue, useNativeDriver: true }).start();
    setPanelOpen(!panelOpen);
  };

  const loadSettings = async () => {
    try {
      const [step, power, radius, sync, carrier] = await Promise.all([
        SecureStore.getItemAsync('interpolationStep'),
        SecureStore.getItemAsync('interpolationPower'),
        SecureStore.getItemAsync('interpolationRadius'),
        SecureStore.getItemAsync('autoSync'),
        SecureStore.getItemAsync('carrierName'),
      ]);
      if (step) setInterpolationStep(step);
      if (power) setInterpolationPower(power);
      if (radius) setInterpolationRadius(radius);
      if (sync) setAutoSync(sync === 'true');
      if (carrier) setCarrierOverride(carrier);
    } catch {}
  };

  const loadNetworks = async () => {
    if (Platform.OS === 'web') return;
    setScanning(true);
    setError('');
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setError('Location permission denied.');
        setScanning(false);
        return;
      }
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

      let cellular: CellularInfo | null = null;
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

      setWifiNetworks(wifi);
      setCellularInfo(cellular);
    } catch (e: any) {
      setError(e.message || 'Scan failed');
    } finally {
      setScanning(false);
    }
  };

  const handleSelectNetwork = (ssid: string) => setTargetSsid(ssid);

  const handleEditCarrier = () => {
    if (!cellularInfo) return;
    setPanelTab('settings');
  };

  const [refreshing, setRefreshing] = useState(false);
  const [generating, setGenerating] = useState(false);

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
      const response = await fetch(`${apiUrl}/api/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          wifi: wifiNetworks,
          cellular: cellularInfo,
          location: currentLocation,
          targetSsid,
          deviceId,
          timestamp: new Date().toISOString(),
        }),
      });
      if (!response.ok) {
        Alert.alert('Save Failed', `Server error ${response.status}`);
        return;
      }
      const data = await response.json();
      Alert.alert('Saved', `${data.count} points saved`);
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
      const meshData = await meshResp.json();
      if (meshData.triangles && meshData.triangles.length > 0) {
        const meshJson = JSON.stringify(meshData.triangles);
        webViewRef.current?.injectJavaScript(`
          (function() {
            if (typeof L === 'undefined' || typeof map === 'undefined') return;
            if (typeof contourLayer !== 'undefined' && contourLayer) map.removeLayer(contourLayer);
            contourLayer = L.layerGroup();
            var triangles = ${meshJson};
            var allPts = [];
            triangles.forEach(function(t) {
              var lls = t.vertices.map(function(v) { return L.latLng(v.lat, v.lng); });
              lls.forEach(function(ll) { allPts.push(ll); });
              var dbm = t.avg_signal_dbm;
              var pct = Math.max(0, Math.min(1, (dbm + 100) / 60));
              var r = Math.round(255 * (1 - pct));
              var g = Math.round(200 * pct);
              var color = 'rgb(' + r + ',' + g + ',80)';
              L.polygon(lls, {
                color: color,
                fillColor: color,
                fillOpacity: 0.45,
                weight: 1,
                opacity: 0.8,
              }).bindPopup(dbm.toFixed(1) + ' dBm').addTo(contourLayer);
            });
            contourLayer.addTo(map);
            if (allPts.length > 0) {
              map.fitBounds(L.latLngBounds(allPts), { padding: [50, 50] });
            }
          })();
          true;
        `);
        Alert.alert('Map Loaded', meshData.triangles.length + ' Delaunay triangles rendered');
        setGenerating(false);
        return;
      }
      const hUrl = `${apiUrl}/api/heatmap?ssid=${encodeURIComponent(targetSsid)}`;
      const hResp = await fetch(hUrl);
      const hData = await hResp.json();
      if (!hData.points || hData.points.length === 0) {
        Alert.alert('No Data', 'No coverage data. Save scan points at different locations first.');
        setGenerating(false);
        return;
      }
      const heatJson = JSON.stringify(hData.points);
      webViewRef.current?.injectJavaScript(`
        (function() {
          if (typeof L === 'undefined' || typeof map === 'undefined') return;
          if (typeof contourLayer !== 'undefined' && contourLayer) map.removeLayer(contourLayer);
          contourLayer = L.layerGroup();
          var pts = ${heatJson};
          var allPts = [];
          pts.forEach(function(p) {
            var color = p.weight > 0.8 ? '#22c55e' : p.weight > 0.6 ? '#06b6d4' : p.weight > 0.4 ? '#eab308' : p.weight > 0.2 ? '#f97316' : '#ef4444';
            L.circleMarker([p.lat, p.lng], {
              radius: 20,
              color: color,
              fillColor: color,
              fillOpacity: 0.35,
              weight: 1,
              opacity: 0.6,
            }).bindPopup((p.ssid || 'Unknown') + '<br>' + p.signal_dbm + ' dBm').addTo(contourLayer);
            allPts.push([p.lat, p.lng]);
          });
          contourLayer.addTo(map);
          if (allPts.length > 0) {
            map.fitBounds(L.latLngBounds(allPts), { padding: [50, 50] });
          }
        })();
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
      await Promise.all([
        SecureStore.setItemAsync('interpolationStep', interpolationStep),
        SecureStore.setItemAsync('interpolationPower', interpolationPower),
        SecureStore.setItemAsync('interpolationRadius', interpolationRadius),
        SecureStore.setItemAsync('autoSync', autoSync.toString()),
      ]);
      Alert.alert('Saved', 'Settings saved');
    } catch {
      Alert.alert('Error', 'Failed to save');
    }
  };

  const mapUrl = `${apiUrl}/map?app=1`;

  return (
    <View style={s.container}>
      <WebView
        ref={webViewRef}
        source={{ uri: mapUrl }}
        style={s.web}
        javaScriptEnabled
        domStorageEnabled
      />

      {panelOpen && (
        <TouchableOpacity style={s.mapOverlay} onPress={closePanel} activeOpacity={1} />
      )}

      <TouchableOpacity style={s.fab} onPress={togglePanel} activeOpacity={0.8}>
        <Ionicons name={panelOpen ? 'close' : 'menu'} size={24} color="white" />
      </TouchableOpacity>

      {targetSsid && !panelOpen && (
        <View style={s.targetBadge}>
          <Ionicons name="checkmark-circle" size={14} color={T.green} />
          <Text style={s.targetBadgeText} numberOfLines={1}>{targetSsid}</Text>
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
                            {cellularInfo.isConnected && <Badge label="Connected" variant="success" />}
                            <Ionicons name="pencil" size={10} color={T.textMuted} style={{ marginLeft: 4 }} />
                          </View>
                          <Text style={s.networkMeta}>{cellularInfo.networkType}</Text>
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

              <Text style={s.sectionTitle}>Interpolation (IDW)</Text>
              <Input label="Grid Step" value={interpolationStep} onChangeText={setInterpolationStep} keyboardType="decimal-pad" placeholder="0.00005" />
              <Input label="Power" value={interpolationPower} onChangeText={setInterpolationPower} keyboardType="numeric" placeholder="2" />
              <Input label="Max Radius" value={interpolationRadius} onChangeText={setInterpolationRadius} keyboardType="decimal-pad" placeholder="0.005" />

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
  errorCard: { flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: 'rgba(239,68,68,0.1)', borderColor: 'rgba(239,68,68,0.2)' },
  errorText: { color: '#fca5a5', fontSize: 12, flex: 1 },
  targetCard: { borderColor: T.green, borderWidth: 1 },
  targetRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  targetLabel: { fontSize: 12, color: T.textMuted },
  targetValue: { fontSize: 13, fontWeight: '600', color: T.green, flex: 1 },
  locCard: { paddingVertical: 8, paddingHorizontal: 12 },
  locRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  locText: { fontSize: 12, color: T.accent2, fontFamily: 'monospace' },
});
