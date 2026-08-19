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
      const [step, power, radius, sync] = await Promise.all([
        SecureStore.getItemAsync('interpolationStep'),
        SecureStore.getItemAsync('interpolationPower'),
        SecureStore.getItemAsync('interpolationRadius'),
        SecureStore.getItemAsync('autoSync'),
      ]);
      if (step) setInterpolationStep(step);
      if (power) setInterpolationPower(power);
      if (radius) setInterpolationRadius(radius);
      if (sync) setAutoSync(sync === 'true');
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
        const netInfo = await NetInfo.fetch();
        if (netInfo.type === 'cellular') {
          let carrierName = 'Cellular';
          try {
            const WifiManager = require('react-native-wifi-reborn').default;
            const simCarrier = await WifiManager.getCarrier();
            if (simCarrier && simCarrier.trim()) carrierName = simCarrier.trim();
          } catch {
            const d = netInfo.details as any;
            carrierName = d.carrier || d.mobileCarrier || d.networkName || 'Cellular';
          }
          const d = netInfo.details as any;
          cellular = {
            carrier: carrierName,
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
    try {
      const url = `${apiUrl}/api/coverage?ssid=${encodeURIComponent(targetSsid)}&step=${interpolationStep}&power=${interpolationPower}&radius=${interpolationRadius}`;
      const resp = await fetch(url);
      const data = await resp.json();
      if (!data.grid || data.grid.length === 0) {
        Alert.alert('No Data', 'No coverage data for this network. Save scan points first.');
        return;
      }
      const points = JSON.stringify(data.grid.map((p: any) => [p.lat, p.lng, p.weight]));
      webViewRef.current?.injectJavaScript(`
        (function() {
          if (typeof L !== 'undefined' && typeof map !== 'undefined') {
            if (typeof heatmap !== 'undefined' && heatmap) map.removeLayer(heatmap);
            var pts = ${points};
            heatmap = L.heatLayer(pts, { radius: 35, blur: 25, maxZoom: 18, max: 1.0 }).addTo(map);
            var bounds = L.latLngBounds(pts.map(function(p) { return [p[0], p[1]]; }));
            map.fitBounds(bounds, { padding: [50, 50] });
          }
        })();
        true;
      `);
      Alert.alert('Map Loaded', `${data.grid.length} points rendered`);
    } catch (e) {
      Alert.alert('Error', e instanceof Error ? e.message : 'Failed to load coverage');
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
                  <TouchableOpacity onPress={() => handleSelectNetwork(cellularInfo.carrier)} activeOpacity={0.7}>
                    <Card style={[s.networkCard, targetSsid === cellularInfo.carrier && s.networkCardSelected]}>
                      <View style={s.networkRow}>
                        <View style={s.networkInfo}>
                          <View style={s.networkNameRow}>
                            {targetSsid === cellularInfo.carrier && <Ionicons name="checkmark-circle" size={12} color={T.green} />}
                            <Text style={[s.networkSsid, targetSsid === cellularInfo.carrier && { color: T.green }]}>{cellularInfo.carrier}</Text>
                            {cellularInfo.isConnected && <Badge label="Connected" variant="success" />}
                          </View>
                          <Text style={s.networkMeta}>{cellularInfo.networkType}</Text>
                        </View>
                        <Badge label={cellularInfo.isConnected ? 'Online' : 'Offline'} variant={cellularInfo.isConnected ? 'success' : 'danger'} />
                      </View>
                    </Card>
                  </TouchableOpacity>
                </>
              )}

              {targetSsid && (
                <View style={s.actions}>
                  <Button title={saving ? 'Saving...' : 'Save'} onPress={handleSaveScan} disabled={saving} loading={saving} variant="secondary" style={{ flex: 1 }} />
                  <Button title="Generate Map" onPress={handleGenerateCoverage} icon="🗺️" style={{ flex: 1 }} />
                </View>
              )}
            </>
          ) : (
            <>
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
});
