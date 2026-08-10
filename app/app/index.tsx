import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Platform } from 'react-native';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { Card, Button, Header } from '@/components/UI';

export default function IndexScreen() {
  const router = useRouter();

  return (
    <View style={styles.container}>
      <Header title="WiFi Mapper" subtitle="Map WiFi & Cellular coverage" />
      
      <View style={styles.grid}>
        <Card onPress={() => router.push('/scan')} style={styles.card}>
          <Ionicons name="wifi" size={48} color="#e94560" />
          <Text style={styles.cardTitle}>Scan Networks</Text>
          <Text style={styles.cardDesc}>WiFi + Cellular signal strength</Text>
        </Card>

        <Card onPress={() => router.push('/map')} style={styles.card}>
          <Ionicons name="map-outline" size={48} color="#0f3460" />
          <Text style={styles.cardTitle}>Coverage Map</Text>
          <Text style={styles.cardDesc}>View interpolated signal heatmap</Text>
        </Card>

        <Card onPress={() => router.push('/settings')} style={styles.card}>
          <Ionicons name="settings-outline" size={48} color="#1a4a8a" />
          <Text style={styles.cardTitle}>Settings</Text>
          <Text style={styles.cardDesc}>API, interpolation, sync</Text>
        </Card>
      </View>

      <View style={styles.footer}>
        <Text style={styles.footerText}>
          {Platform.OS === 'web' 
            ? 'Web mode: View maps only (no scanning)' 
            : 'Native: Full WiFi + Cellular scanning'}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e', padding: 20 },
  grid: { flex: 1, gap: 16 },
  card: { padding: 24, alignItems: 'center', gap: 12 },
  cardTitle: { fontSize: 18, fontWeight: '600', color: '#eee' },
  cardDesc: { fontSize: 14, color: '#888', textAlign: 'center' },
  footer: { paddingTop: 20, borderTopWidth: 1, borderTopColor: '#0f3460' },
  footerText: { fontSize: 12, color: '#666', textAlign: 'center' },
});