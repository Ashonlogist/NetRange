import React, { useEffect, useState, useRef } from 'react';
import { View, Text, StyleSheet, Platform, Alert, ActivityIndicator } from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import MapView, { Marker } from 'react-native-maps';
import { Header, Button, Card } from '@/components/UI';
import { useApp } from '@/components/Providers';

interface CoveragePoint {
  lat: number;
  lng: number;
  signal_dbm: number;
  weight: number;
}

interface CoverageStats {
  min: number;
  max: number;
  avg: number;
  points: number;
}

export default function MapScreen() {
  const router = useRouter();
  const { apiUrl } = useApp();
  const { ssid } = useLocalSearchParams<{ ssid?: string }>();
  const [coverage, setCoverage] = useState<CoveragePoint[]>([]);
  const [stats, setStats] = useState<CoverageStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [region, setRegion] = useState({
    latitude: 0,
    longitude: 0,
    latitudeDelta: 0.01,
    longitudeDelta: 0.01,
  });
  const mapRef = useRef<MapView>(null);

  useEffect(() => {
    if (ssid) {
      loadCoverage(ssid);
    }
  }, [ssid]);

  const loadCoverage = async (targetSsid: string) => {
    setLoading(true);
    setError('');
    try {
      const resp = await fetch(`${apiUrl}/api/coverage?ssid=${encodeURIComponent(targetSsid)}&step=0.00005&power=2&radius=0.005`);
      if (!resp.ok) throw new Error(`Server error: ${resp.status}`);
      const data = await resp.json();
      
      if (data.grid.length === 0) {
        setError(data.message || 'No coverage data. Scan more locations.');
        setCoverage([]);
        return;
      }

      setCoverage(data.grid);
      setStats(data.stats);

      // Fit map to coverage bounds
      const lats = data.grid.map((p: CoveragePoint) => p.lat);
      const lngs = data.grid.map((p: CoveragePoint) => p.lng);
      const minLat = Math.min(...lats);
      const maxLat = Math.max(...lats);
      const minLng = Math.min(...lngs);
      const maxLng = Math.max(...lngs);
      const centerLat = (minLat + maxLat) / 2;
      const centerLng = (minLng + maxLng) / 2;
      const latDelta = (maxLat - minLat) * 1.3 + 0.005;
      const lngDelta = (maxLng - minLng) * 1.3 + 0.005;

      setRegion({
        latitude: centerLat,
        longitude: centerLng,
        latitudeDelta: latDelta,
        longitudeDelta: lngDelta,
      });

    } catch (e: any) {
      setError(e.message || 'Failed to load coverage');
    }
  };

  const getColor = (dbm: number) => {
    if (dbm > -50) return '#00ff00';
    if (dbm > -60) return '#ccff00';
    if (dbm > -70) return '#ffcc00';
    if (dbm > -80) return '#ff6600';
    return '#ff0000';
  };

  const renderCoverage = () => {
    if (coverage.length === 0) return null;
    
    // Group nearby points into a grid for performance
    const gridSize = 0.0001;
    const grid = new Map<string, CoveragePoint>();
    
    coverage.forEach(p => {
      const gridLat = Math.round(p.lat / gridSize) * gridSize;
      const gridLng = Math.round(p.lng / gridSize) * gridSize;
      const key = `${gridLat},${gridLng}`;
      const existing = grid.get(key);
      if (!existing || p.weight > existing.weight) {
        grid.set(key, p);
      }
    });

    return Array.from(grid.values()).map((p, i) => (
      <View
        key={i}
        style={[
          styles.coveragePoint,
          { 
            backgroundColor: getColor(p.signal_dbm),
            opacity: 0.6,
            transform: [{ translateX: (p.lng - region.longitude) * 111000 * Math.cos(region.latitude * Math.PI / 180) }, 
                       { translateY: (p.lat - region.latitude) * 111000 }]
          }
        }>
      </View>
    ));
  };

  if (Platform.OS === 'web') {
    return (
      <View style={styles.container}>
        <Header title="Coverage Map" subtitle={ssid ? `Network: ${ssid}` : 'Select network from scan screen'} />
        {ssid && (
          <Button title="Load Coverage" onPress={() => loadCoverage(ssid!)} loading={loading} />
        )}
        {error && <Card style={styles.errorCard}><Text>{error}</Text></Card>}
        <Card>
          <Text>Web view: Use native app for interactive MapView with heatmap overlay</Text>
        </Card>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Header title="Coverage Map" subtitle={ssid ? `Network: ${ssid}` : 'No network selected'} />
      
      {error && <Card style={styles.errorCard}><Text style={styles.errorText}>{error}</Text></Card>}
      
      {stats && (
        <View style={styles.statsBar}>
          <Text style={styles.statText}>Points: {stats.points}</Text>
          <Text style={styles.statText}>Avg: {stats.avg} dBm</Text>
          <Text style={styles.statText}>Range: {stats.min} → {stats.max} dBm</Text>
        </View>
      )}

      <View style={styles.mapContainer}>
        <MapView
          ref={mapRef}
          style={styles.map}
          region={region}
          showsUserLocation={true}
          showsMyLocationButton={true}
          zoomEnabled={true}
          scrollEnabled={true}
          pitchEnabled={true}
          rotateEnabled={true}
          mapType="hybrid"
        >
          {renderCoverage()}
        </MapView>
        
        {loading && (
          <View style={styles.loadingOverlay}>
            <ActivityIndicator size="large" color="#e94560" />
            <Text style={styles.loadingText}>Generating coverage map...</Text>
          </View>
        )}
      </View>

      {!ssid && (
        <Card style={styles.infoCard}>
          <Text>Select a network from the Scan screen, or add ?ssid=NETWORK_NAME to URL</Text>
        </Card>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e' },
  mapContainer: { flex: 1, position: 'relative' },
  map: { ...StyleSheet.absoluteFillObject },
  loadingOverlay: { 
    ...StyleSheet.absoluteFillObject, 
    backgroundColor: 'rgba(26,26,46,0.9)', 
    justifyContent: 'center', 
    alignItems: 'center', 
    gap: 12 
  },
  loadingText: { color: '#e94560', fontSize: 16, marginTop: 12 },
  statsBar: { 
    flexDirection: 'row', 
    justifyContent: 'space-around', 
    padding: 12, 
    backgroundColor: '#16213e', 
    borderBottomWidth: 1, 
    borderColor: '#0f3460' 
  },
  statText: { fontSize: 12, color: '#ccc' },
  errorCard: { margin: 16, backgroundColor: '#3d0a0a', borderColor: '#f44' },
  errorText: { color: '#f88', margin: 16 },
  infoCard: { margin: 16, backgroundColor: '#0f3460' },
});