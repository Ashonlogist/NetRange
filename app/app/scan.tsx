import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView, StyleSheet, Platform, Alert } from 'react-native';
import { useRouter } from 'expo-router';
import * as Network from 'expo-network';
import * as Location from 'expo-location';
import * as Device from 'expo-device';
import NetInfo from '@react-native-community/netinfo';
import { Header, Card, Button, Input } from '@/components/UI';
import { useApp } from '@/components/Providers';

type NetworkType = 'wifi' | 'cellular';

interface WifiNetwork {
  ssid: string;
  bssid: string;
  strength: number; // 0-1 or dBm
  frequency?: number;
  channel?: number;
  isConnected: boolean;
}

interface CellularInfo {
  carrier: string;
  signalStrength: number; // 0-4 or dBm
  networkType: string;
  isConnected: boolean;
}

interface ScanResult {
  wifi: WifiNetwork[];
  cellular: CellularInfo | null;
  location: Location.LocationObjectCoords | null;
  timestamp: string;
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

  useEffect(() => {
    if (Platform.OS !== 'web') {
      requestPermissions();
      loadNetworks();
    }
  }, []);

  const requestPermissions = async () => {
    if (Platform.OS !== 'web') {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setError('Location permission denied — cannot tag scans with GPS');
      }
    }
  };

  const loadNetworks = async () => {
    if (Platform.OS === 'web') {
      setError('Web mode: scanning not available. Run on device for full features.');
      return;
    }

    setScanning(true);
    setError('');

    try {
      // Get location
      const loc = await Location.getCurrentPositionAsync({ 
        accuracy: Location.Accuracy.High,
        timeout: 10000 
      });
      setCurrentLocation(loc.coords);

      // Get WiFi networks (Android only, limited on iOS)
      let wifi: WifiNetwork[] = [];
      if (Platform.OS === 'android') {
        try {
          const networks = await Network.getNetworkListAsync();
          wifi = networks
            .filter(n => n.type === Network.NetworkType.WIFI)
            .map(n => ({
              ssid: n.ssid || 'hidden',
              bssid: n.bssid || '',
              strength: n.strength || 0,
              frequency: n.frequency,
              channel: n.frequency ? Math.round((n.frequency - 2407) / 5) : undefined,
              isConnected: n.isConnected || false,
            }))
            .sort((a, b) => b.strength - a.strength);
        } catch (e) {
          console.warn('WiFi scan failed:', e);
        }
      }

      // Get cellular info
      let cellular: CellularInfo | null = null;
      try {
        const netInfo = await NetInfo.fetch();
        if (netInfo.type === 'cellular' && netInfo.details) {
          cellular = {
            carrier: netInfo.details.carrier || 'Unknown',
            signalStrength: netInfo.details.signalStrength || 0,
            networkType: netInfo.details.cellularGeneration || 'Unknown',
            isConnected: netInfo.isConnected || false,
          };
        }
      } catch (e) {
        console.warn('Cellular info failed:', e);
      }

      setWifiNetworks(wifi);
      setCellularInfo(cellular);
      
      const result: ScanResult = {
        wifi,
        cellular,
        location: loc.coords,
        timestamp: new Date().toISOString(),
      };
      setLastScan(result);

    } catch (e: any) {
      setError(e.message || 'Scan failed');
    } finally {
      setScanning(false);
    }
  };

  const handleScanPress = () => loadNetworks();

  const handleSelectNetwork = (ssid: string) => {
    setTargetSsid(ssid);
    Alert.alert('Target Set', `Now mapping: ${ssid}`, [{ text: 'OK' }]);
  };

  const handleSaveScan = async () => {
    if (!lastScan) return Alert.alert('No Data', 'Scan first');
    if (!targetSsid) return Alert.alert('No Target', 'Select a network SSID first');

    try {
      const response = await fetch(`${apiUrl}/api/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...lastScan,
          targetSsid,
          deviceId,
        }),
      });
      const data = await response.json();
      Alert.alert('Saved', `Scan saved to server (${data.count} points)`);
    } catch (e) {
      Alert.alert('Save Failed', e instanceof Error ? e.message : 'Unknown error');
    }
  };

  const handleGenerateCoverage = () => {
    if (!targetSsid) return Alert.alert('No Target', 'Select a network SSID first');
    router.push(`/map?ssid=${encodeURIComponent(targetSsid)}`);
  };

  if (Platform.OS === 'web') {
    return (
      <View style={styles.container}>
        <Header title="Scan Networks" subtitle="Web mode — scanning unavailable" />
        <Card style={styles.infoCard}>
          <Text style={styles.infoText}>
            📱 Scanning requires native mobile app (iOS/Android).
            <Text style={{marginTop: 8}}>Run on device:</Text>
            <Text>• npx expo start</Text>
            <Text>• Scan QR with Expo Go</Text>
            <Text>• Or build: npx expo run:android / run:ios</Text>
          </Text>
        </Card>
        <Button title="Go to Map" onPress={() => router.push('/map')} variant="secondary" />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Header title="Scan Networks" subtitle={scanning ? 'Scanning...' : 'Tap to scan'} />
      
      {error && <Card style={styles.errorCard}><Text style={styles.errorText}>{error}</Text></Card>}

      <Button 
        title={scanning ? 'Scanning...' : '📡 Scan Now'} 
        onPress={handleScanPress} 
        disabled={scanning}
        loading={scanning}
        style={styles.scanButton}
      />

      {wifiNetworks.length > 0 && (
        <>
          <Text style={styles.sectionTitle}>WiFi Networks ({wifiNetworks.length})</Text>
          {wifiNetworks.map((net, i) => (
            <Card 
              key={`${net.bssid}-${i}`}
              onPress={() => handleSelectNetwork(net.ssid)}
              style={styles.networkCard}
            >
              <View style={styles.networkRow}>
                <View style={styles.networkMain}>
                  <Text style={[styles.networkSsid, net.isConnected && styles.connected]}>{net.ssid}</Text>
                  <Text style={styles.networkMeta}>
                    {net.bssid} • Ch {net.channel || '?'} • {net.frequency} MHz
                  </Text>
                </View>
                <View style={styles.networkStrength}>
                  <Text style={[
                    styles.strengthText,
                    net.strength > -50 ? styles.excellent : net.strength > -60 ? styles.good : net.strength > -70 ? styles.fair : styles.weak
                  ]}>
                    {net.strength} dBm
                  </Text>
                  {net.isConnected && <Text style={styles.connectedBadge}>CONNECTED</Text>}
                </View>
              </View>
            </Card>
          ))}
        </>
      )}

      {cellularInfo && (
        <>
          <Text style={styles.sectionTitle}>Cellular</Text>
          <Card>
            <View style={styles.networkRow}>
              <View>
                <Text style={styles.networkSsid}>{cellularInfo.carrier}</Text>
                <Text style={styles.networkMeta}>
                  {cellularInfo.networkType} • Signal: {cellularInfo.signalStrength}
                </Text>
              </View>
            </View>
          </Card>
        </>
      )}

      {lastScan && (
        <>
          <Text style={styles.sectionTitle}>Location</Text>
          <Card>
            <Text style={styles.locationText}>
              Lat: {lastScan.location?.latitude.toFixed(6)} 
              Lon: {lastScan.location?.longitude.toFixed(6)}
              <Text style={{display: 'block', marginTop: 4}}>
                Time: {new Date(lastScan.timestamp).toLocaleTimeString()}
              </Text>
            </Text>
          </Card>
        </>
      )}

      {targetSsid && lastScan && (
        <View style={styles.actions}>
          <Button title="💾 Save Scan to Server" onPress={handleSaveScan} variant="secondary" />
          <Button title="🗺️ Generate Coverage Map" onPress={handleGenerateCoverage} />
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e' },
  content: { padding: 20, paddingBottom: 40, gap: 16 },
  sectionTitle: { fontSize: 16, fontWeight: '600', color: '#e94560', marginTop: 8, marginBottom: 8 },
  networkCard: { padding: 12 },
  networkRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  networkMain: { flex: 1 },
  networkSsid: { fontSize: 16, fontWeight: '600', color: '#eee' },
  connected: { color: '#0f0' },
  networkMeta: { fontSize: 12, color: '#888', marginTop: 2 },
  networkStrength: { alignItems: 'flex-end', gap: 4 },
  strengthText: { fontSize: 18, fontWeight: '700' },
  excellent: { color: '#0f0' },
  good: { color: '#ff0' },
  fair: { color: '#fa0' },
  weak: { color: '#f44' },
  connectedBadge: { fontSize: 10, color: '#0f0', backgroundColor: '#0f03', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4 },
  locationText: { fontSize: 14, color: '#ccc', fontFamily: 'monospace' },
  actions: { flexDirection: 'row', gap: 12, marginTop: 8 },
  errorCard: { backgroundColor: '#3d0a0a', borderColor: '#f44' },
  errorText: { color: '#f88' },
  infoCard: { backgroundColor: '#0f3460', borderColor: '#1a4a8a' },
  infoText: { color: '#aaa', fontSize: 14, lineHeight: 22 },
  scanButton: { marginTop: 8 },
});