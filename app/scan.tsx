import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  Platform,
  Alert,
  TouchableOpacity,
} from 'react-native';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import NetInfo from '@react-native-community/netinfo';
import { Header, Card, Button, Badge, T } from '@/components/UI';
import { useApp } from '@/components/Providers';

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

interface ScanResult {
  wifi: WifiNetwork[];
  cellular: CellularInfo | null;
  location: Location.LocationObjectCoords | null;
  timestamp: string;
}

function signalColor(dbm: number) {
  return dbm > -50 ? T.green : dbm > -60 ? T.yellow : dbm > -70 ? T.orange : T.red;
}

function signalLabel(dbm: number) {
  return dbm > -50 ? 'Excellent' : dbm > -60 ? 'Good' : dbm > -70 ? 'Fair' : 'Weak';
}

export default function ScanScreen() {
  const router = useRouter();
  const { apiUrl, currentLocation, setCurrentLocation, deviceId } = useApp();
  const [scanning, setScanning] = useState(false);
  const [wifiNetworks, setWifiNetworks] = useState<WifiNetwork[]>([]);
  const [cellularInfo, setCellularInfo] = useState<CellularInfo | null>(null);
  const [lastScan, setLastScan] = useState<ScanResult | null>(null);
  const [targetSsid, setTargetSsid] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (Platform.OS !== 'web') {
      loadNetworks();
    }
  }, []);

  const loadNetworks = async () => {
    if (Platform.OS === 'web') {
      setError('Web mode: scanning not available. Use the Android app.');
      return;
    }

    setScanning(true);
    setError('');

    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setError('Location permission denied. Enable it in settings.');
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
              channel: n.frequency
                ? Math.round((n.frequency - 2407) / 5)
                : undefined,
              isConnected: (n.SSID || '') === connectedSsid,
            }))
            .sort((a: WifiNetwork, b: WifiNetwork) => b.strength - a.strength);
        } catch (e: any) {
          const msg = e?.message || 'WiFi scan failed';
          if (msg === 'locationServicesOff') {
            setError('WiFi scanning needs Location Services ON.');
          } else if (msg === 'locationPermissionMissing') {
            setError('Location permission required for WiFi scanning.');
          } else {
            setError(`WiFi scan: ${msg}`);
          }
        }
      }

      let cellular: CellularInfo | null = null;
      try {
        const netInfo = await NetInfo.fetch();
        if (netInfo.type === 'cellular') {
          const d = netInfo.details as any;
          cellular = {
            carrier: d.carrier || d.mobileCarrier || d.networkName || 'Cellular',
            signalStrength: d.strength ?? d.signalStrength ?? undefined,
            networkType: d.cellularGeneration || d.type || 'Unknown',
            isConnected: netInfo.isConnected || false,
          };
        } else if (netInfo.isConnected) {
          cellular = {
            carrier: netInfo.type === 'wifi' ? 'Connected via WiFi' : netInfo.type,
            signalStrength: undefined,
            networkType: netInfo.type,
            isConnected: true,
          };
        }
      } catch {
        // Cellular info is optional
      }

      setWifiNetworks(wifi);
      setCellularInfo(cellular);

      setLastScan({
        wifi,
        cellular,
        location: loc.coords,
        timestamp: new Date().toISOString(),
      });
    } catch (e: any) {
      setError(e.message || 'Scan failed');
    } finally {
      setScanning(false);
    }
  };

  const handleSelectNetwork = (ssid: string) => {
    setTargetSsid(ssid);
  };

  const handleSaveScan = async () => {
    if (!lastScan) return Alert.alert('No Data', 'Scan first');
    if (!targetSsid) return Alert.alert('No Target', 'Tap a network to select it as target');

    setSaving(true);
    try {
      const response = await fetch(`${apiUrl}/api/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          wifi: lastScan.wifi,
          cellular: lastScan.cellular,
          location: lastScan.location,
          targetSsid,
          deviceId,
          timestamp: lastScan.timestamp,
        }),
      });

      if (!response.ok) {
        const errText = await response.text();
        Alert.alert('Save Failed', `Server error (${response.status}): ${errText}`);
        return;
      }

      const data = await response.json();
      Alert.alert('Saved', `Scan saved to server (${data.count} points)`);
    } catch (e) {
      Alert.alert('Save Failed', e instanceof Error ? e.message : 'Network error');
    } finally {
      setSaving(false);
    }
  };

  const handleGenerateCoverage = () => {
    if (!targetSsid) return Alert.alert('No Target', 'Tap a network to select it first');
    router.push(`/map?ssid=${encodeURIComponent(targetSsid)}`);
  };

  if (Platform.OS === 'web') {
    return (
      <ScrollView style={s.container} contentContainerStyle={s.content}>
        <Header title="Scan Networks" subtitle="Web mode unavailable" />
        <Card style={s.infoCard}>
          <Ionicons name="phone-portrait-outline" size={40} color={T.textMuted} />
          <Text style={s.infoTitle}>Scanning requires the Android app</Text>
          <Text style={s.infoDesc}>
            Browsers cannot access WiFi hardware. Download the NetRange app to scan networks and sync data to the server.
          </Text>
        </Card>
        <Button title="Open Coverage Map" onPress={() => router.push('/map')} variant="secondary" />
      </ScrollView>
    );
  }

  return (
    <ScrollView style={s.container} contentContainerStyle={s.content}>
      <Header
        title="Scan Networks"
        subtitle={scanning ? 'Scanning...' : 'Tap a network to select, then save'}
      />

      {error ? (
        <Card style={s.errorCard}>
          <Ionicons name="alert-circle" size={18} color={T.red} />
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
            <Ionicons name="checkmark-circle" size={18} color={T.green} />
            <Text style={s.targetLabel}>Target:</Text>
            <Text style={s.targetValue}>{targetSsid}</Text>
            <TouchableOpacity onPress={() => setTargetSsid('')} style={s.clearBtn}>
              <Ionicons name="close-circle" size={18} color={T.textMuted} />
            </TouchableOpacity>
          </View>
        </Card>
      ) : null}

      {wifiNetworks.length > 0 && (
        <>
          <Text style={s.sectionTitle}>WiFi Networks ({wifiNetworks.length})</Text>
          {wifiNetworks.map((net, i) => {
            const selected = targetSsid === net.ssid;
            return (
              <TouchableOpacity
                key={`${net.bssid}-${i}`}
                onPress={() => handleSelectNetwork(net.ssid)}
                activeOpacity={0.7}
              >
                <Card style={[s.networkCard, selected && s.networkCardSelected]}>
                  <View style={s.networkRow}>
                    <View style={s.networkInfo}>
                      <View style={s.networkNameRow}>
                        {selected && <Ionicons name="checkmark-circle" size={14} color={T.green} />}
                        <Text style={[s.networkSsid, selected && { color: T.green }]} numberOfLines={1}>
                          {net.ssid}
                        </Text>
                        {net.isConnected && <Badge label="Connected" variant="success" />}
                      </View>
                      <Text style={s.networkMeta}>
                        {net.bssid} · Ch {net.channel || '?'} · {net.frequency} MHz
                      </Text>
                    </View>
                    <View style={s.signalCol}>
                      <Text style={[s.signalDbm, { color: signalColor(net.strength) }]}>
                        {net.strength}
                      </Text>
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
            activeOpacity={0.7}
          >
            <Card style={[s.networkCard, targetSsid === cellularInfo.carrier && s.networkCardSelected]}>
              <View style={s.networkRow}>
                <View style={s.networkInfo}>
                  <View style={s.networkNameRow}>
                    {targetSsid === cellularInfo.carrier && <Ionicons name="checkmark-circle" size={14} color={T.green} />}
                    <Text style={[s.networkSsid, targetSsid === cellularInfo.carrier && { color: T.green }]}>
                      {cellularInfo.carrier}
                    </Text>
                    {cellularInfo.isConnected && <Badge label="Connected" variant="success" />}
                  </View>
                  <Text style={s.networkMeta}>
                    {cellularInfo.networkType} · Signal: {cellularInfo.signalStrength ?? 'n/a'}
                  </Text>
                </View>
                <Badge label={cellularInfo.isConnected ? 'Online' : 'Offline'} variant={cellularInfo.isConnected ? 'success' : 'danger'} />
              </View>
            </Card>
          </TouchableOpacity>
        </>
      )}

      {lastScan?.location && (
        <>
          <Text style={s.sectionTitle}>Location</Text>
          <Card>
            <Text style={s.locationText}>
              {lastScan.location.latitude.toFixed(6)}, {lastScan.location.longitude.toFixed(6)}
            </Text>
            <Text style={s.locationTime}>
              {new Date(lastScan.timestamp).toLocaleTimeString()}
            </Text>
          </Card>
        </>
      )}

      {targetSsid && lastScan && (
        <View style={s.actions}>
          <Button
            title={saving ? 'Saving...' : 'Save to Server'}
            onPress={handleSaveScan}
            disabled={saving}
            loading={saving}
            variant="secondary"
            style={{ flex: 1 }}
          />
          <Button
            title="View Coverage"
            onPress={handleGenerateCoverage}
            icon="🗺️"
            style={{ flex: 1 }}
          />
        </View>
      )}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: T.bg },
  content: { padding: 20, paddingTop: 60, paddingBottom: 40, gap: 12 },
  sectionTitle: {
    fontSize: 12,
    fontWeight: '600',
    color: T.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    marginTop: 8,
    marginBottom: 4,
  },
  networkCard: { padding: 14 },
  networkCardSelected: { borderColor: T.green, borderWidth: 1 },
  networkRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  networkInfo: { flex: 1, marginRight: 12 },
  networkNameRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 2 },
  networkSsid: { fontSize: 15, fontWeight: '600', color: T.text, flexShrink: 1 },
  networkMeta: { fontSize: 11, color: T.textMuted, fontFamily: 'monospace' },
  signalCol: { alignItems: 'flex-end', gap: 4 },
  signalDbm: { fontSize: 16, fontWeight: '700', fontFamily: 'monospace' },
  locationText: { fontSize: 14, color: T.text, fontFamily: 'monospace' },
  locationTime: { fontSize: 12, color: T.textMuted, marginTop: 4 },
  actions: { flexDirection: 'row', gap: 10, marginTop: 4 },
  errorCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: 'rgba(239,68,68,0.1)',
    borderColor: 'rgba(239,68,68,0.2)',
  },
  errorText: { color: '#fca5a5', fontSize: 13, flex: 1 },
  infoCard: {
    alignItems: 'center',
    paddingVertical: 32,
    gap: 12,
  },
  infoTitle: { fontSize: 16, fontWeight: '600', color: T.text },
  infoDesc: { fontSize: 13, color: T.textMuted, textAlign: 'center', lineHeight: 20 },
  targetCard: { borderColor: T.green, borderWidth: 1 },
  targetRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  targetLabel: { fontSize: 13, color: T.textMuted },
  targetValue: { fontSize: 14, fontWeight: '600', color: T.green, flex: 1 },
  clearBtn: { padding: 4 },
});
