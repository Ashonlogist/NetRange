import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, Switch, Platform, Alert } from 'react-native';
import { Header, Card, Button, Input } from '@/components/UI';
import { useApp } from '@/components/Providers';
import { useUpdater } from '@/components/Updater';
import * as SecureStore from 'expo-secure-store';

export default function SettingsScreen() {
  const { apiUrl, setApiUrl, deviceId, isOnline } = useApp();
  const updater = useUpdater();
  const [interpolationStep, setInterpolationStep] = useState(0.00005);
  const [interpolationPower, setInterpolationPower] = useState(2);
  const [interpolationRadius, setInterpolationRadius] = useState(0.005);
  const [autoSync, setAutoSync] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const [step, power, radius, sync] = await Promise.all([
        SecureStore.getItemAsync('interpolationStep'),
        SecureStore.getItemAsync('interpolationPower'),
        SecureStore.getItemAsync('interpolationRadius'),
        SecureStore.getItemAsync('autoSync'),
      ]);
      if (step) setInterpolationStep(parseFloat(step));
      if (power) setInterpolationPower(parseInt(power));
      if (radius) setInterpolationRadius(parseFloat(radius));
      if (sync) setAutoSync(sync === 'true');
    } catch (e) {
      console.warn('Settings load failed:', e);
    }
  };

  const saveSettings = async () => {
    setSaving(true);
    try {
      await Promise.all([
        SecureStore.setItemAsync('apiUrl', apiUrl),
        SecureStore.setItemAsync('interpolationStep', interpolationStep.toString()),
        SecureStore.setItemAsync('interpolationPower', interpolationPower.toString()),
        SecureStore.setItemAsync('interpolationRadius', interpolationRadius.toString()),
        SecureStore.setItemAsync('autoSync', autoSync.toString()),
      ]);
      Alert.alert('Saved', 'Settings saved successfully');
    } catch (e) {
      Alert.alert('Error', 'Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={styles.container}>
      <Header title="Settings" subtitle="Configure API & interpolation" />
      
      <Card>
        <Text style={styles.sectionTitle}>Server</Text>
        <Input
          label="API Base URL"
          value={apiUrl}
          onChangeText={setApiUrl}
          placeholder="https://netrange-backend.onrender.com"
          keyboardType="default"
        />
        <Text style={styles.helpText}>
          Your backend URL. The default is the hosted Render backend; use a LAN IP (e.g., 192.168.x.x:5000) only if running locally.
        </Text>
      </Card>

      <Card>
        <Text style={styles.sectionTitle}>Interpolation (IDW)</Text>
        <Input
          label="Grid Step (degrees)"
          value={interpolationStep.toString()}
          onChangeText={t => setInterpolationStep(parseFloat(t) || 0.00005)}
          keyboardType="decimal-pad"
          placeholder="0.00005"
        />
        <Text style={styles.helpText}>Smaller = higher resolution, slower. ~5-10m per step.</Text>

        <Input
          label="Power Parameter"
          value={interpolationPower.toString()}
          onChangeText={t => setInterpolationPower(parseInt(t) || 2)}
          keyboardType="numeric"
          placeholder="2"
        />
        <Text style={styles.helpText}>Higher = closer points dominate more. 2 is standard.</Text>

        <Input
          label="Max Radius (degrees)"
          value={interpolationRadius.toString()}
          onChangeText={t => setInterpolationRadius(parseFloat(t) || 0.005)}
          keyboardType="decimal-pad"
          placeholder="0.005"
        />
        <Text style={styles.helpText}>Max distance to consider points. ~500m at equator.</Text>
      </Card>

      <Card>
        <Text style={styles.sectionTitle}>Sync</Text>
        <View style={styles.toggleRow}>
          <Text>Auto-sync scans to server</Text>
          <Switch value={autoSync} onValueChange={setAutoSync} />
        </View>
        <Text style={styles.helpText}>Automatically send scans to server after each scan.</Text>
      </Card>

      <Card>
        <Text style={styles.sectionTitle}>Update</Text>
        <View style={styles.infoRow}>
          <Text style={styles.infoLabel}>Installed version</Text>
          <Text style={styles.infoValue}>v{updater.currentVersion}</Text>
        </View>
        {updater.updateInfo && updater.updateInfo.version !== updater.currentVersion && (
          <View style={styles.infoRow}>
            <Text style={styles.infoLabel}>Latest version</Text>
            <Text style={[styles.infoValue, { color: '#e94560' }]}>v{updater.updateInfo.version}</Text>
          </View>
        )}
        {updater.state === 'downloading' ? (
          <Text style={styles.helpText}>Downloading update… {Math.round(updater.progress * 100)}%</Text>
        ) : updater.state === 'error' ? (
          <Text style={styles.helpText}>Update error: {updater.error}</Text>
        ) : null}
        <Button
          title={updater.state === 'checking' ? 'Checking…' : 'Check for Updates'}
          onPress={() => updater.checkForUpdates(false)}
          disabled={updater.state === 'checking' || updater.state === 'downloading'}
        />
        {updater.state === 'available' && (
          <Button title="Download & Install" onPress={updater.downloadUpdate} />
        )}
      </Card>

      <Card>
        <Text style={styles.sectionTitle}>Device Info</Text>
        <View style={styles.infoRow}>
          <Text style={styles.infoLabel}>Device ID</Text>
          <Text style={styles.infoValue}>{deviceId}</Text>
        </View>
        <View style={styles.infoRow}>
          <Text style={styles.infoLabel}>Online</Text>
          <Text style={[styles.infoValue, { color: isOnline ? '#0f0' : '#f44' }]}>
            {isOnline ? 'Connected' : 'Offline'}
          </Text>
        </View>
        <View style={styles.infoRow}>
          <Text style={styles.infoLabel}>Platform</Text>
          <Text style={styles.infoValue}>{Platform.OS}</Text>
        </View>
      </Card>

      <Button title={saving ? 'Saving...' : '💾 Save Settings'} onPress={saveSettings} disabled={saving} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e', padding: 20 },
  sectionTitle: { fontSize: 16, fontWeight: '600', color: '#e94560', marginBottom: 12 },
  helpText: { fontSize: 12, color: '#666', marginTop: 4, marginBottom: 12, lineHeight: 18 },
  toggleRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 8 },
  infoRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: '#0f3460' },
  infoLabel: { color: '#888', fontSize: 14 },
  infoValue: { color: '#eee', fontSize: 14, fontWeight: '500', fontFamily: 'monospace' },
});