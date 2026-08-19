import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, Switch, Platform, Alert, ScrollView } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Header, Card, Button, Input, StatRow, T } from '@/components/UI';
import { useApp } from '@/components/Providers';
import { useUpdater } from '@/components/Updater';
import * as SecureStore from 'expo-secure-store';

export default function SettingsScreen() {
  const { apiUrl, deviceId, isOnline } = useApp();
  const updater = useUpdater();
  const [interpolationStep, setInterpolationStep] = useState('0.00005');
  const [interpolationPower, setInterpolationPower] = useState('2');
  const [interpolationRadius, setInterpolationRadius] = useState('0.005');
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
      if (step) setInterpolationStep(step);
      if (power) setInterpolationPower(power);
      if (radius) setInterpolationRadius(radius);
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
        SecureStore.setItemAsync('interpolationStep', interpolationStep),
        SecureStore.setItemAsync('interpolationPower', interpolationPower),
        SecureStore.setItemAsync('interpolationRadius', interpolationRadius),
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
    <ScrollView style={s.container} contentContainerStyle={s.content}>
      <Header title="Settings" subtitle="Configure interpolation and sync" />

      {/* Interpolation */}
      <Card>
        <View style={s.cardHeader}>
          <View style={[s.iconWrap, { backgroundColor: 'rgba(6,182,212,0.15)' }]}>
            <Ionicons name="options-outline" size={18} color={T.accent2} />
          </View>
          <Text style={s.cardTitle}>Interpolation (IDW)</Text>
        </View>
        <Input
          label="Grid Step (degrees)"
          value={interpolationStep}
          onChangeText={setInterpolationStep}
          keyboardType="decimal-pad"
          placeholder="0.00005"
        />
        <Text style={s.helpText}>Smaller = higher resolution. ~5-10m per step.</Text>
        <Input
          label="Power Parameter"
          value={interpolationPower}
          onChangeText={setInterpolationPower}
          keyboardType="numeric"
          placeholder="2"
        />
        <Text style={s.helpText}>Higher = closer points dominate more. 2 is standard.</Text>
        <Input
          label="Max Radius (degrees)"
          value={interpolationRadius}
          onChangeText={setInterpolationRadius}
          keyboardType="decimal-pad"
          placeholder="0.005"
        />
        <Text style={s.helpText}>Max distance to consider. ~500m at equator.</Text>
      </Card>

      {/* Sync */}
      <Card>
        <View style={s.cardHeader}>
          <View style={[s.iconWrap, { backgroundColor: 'rgba(34,197,94,0.15)' }]}>
            <Ionicons name="sync-outline" size={18} color={T.green} />
          </View>
          <Text style={s.cardTitle}>Sync</Text>
        </View>
        <View style={s.toggleRow}>
          <Text style={s.toggleLabel}>Auto-sync scans</Text>
          <Switch
            value={autoSync}
            onValueChange={setAutoSync}
            trackColor={{ false: 'rgba(255,255,255,0.1)', true: 'rgba(124,58,237,0.4)' }}
            thumbColor={autoSync ? T.accent : 'rgba(255,255,255,0.3)'}
          />
        </View>
        <Text style={s.helpText}>Automatically send scans after each scan.</Text>
      </Card>

      {/* Update */}
      <Card>
        <View style={s.cardHeader}>
          <View style={[s.iconWrap, { backgroundColor: 'rgba(249,115,22,0.15)' }]}>
            <Ionicons name="arrow-up-circle-outline" size={18} color={T.orange} />
          </View>
          <Text style={s.cardTitle}>Update</Text>
        </View>
        <StatRow label="Installed" value={`v${updater.currentVersion}`} />
        {updater.updateInfo && updater.updateInfo.version !== updater.currentVersion && (
          <StatRow label="Latest" value={`v${updater.updateInfo.version}`} valueColor={T.accent} />
        )}
        {updater.state === 'downloading' && (
          <Text style={s.helpText}>Downloading... {Math.round(updater.progress * 100)}%</Text>
        )}
        {updater.state === 'error' && (
          <Text style={[s.helpText, { color: T.red }]}>Error: {updater.error}</Text>
        )}
        <View style={{ gap: 8, marginTop: 12 }}>
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
      </Card>

      {/* Device Info */}
      <Card>
        <View style={s.cardHeader}>
          <View style={[s.iconWrap, { backgroundColor: 'rgba(239,68,68,0.15)' }]}>
            <Ionicons name="phone-portrait-outline" size={18} color={T.red} />
          </View>
          <Text style={s.cardTitle}>Device Info</Text>
        </View>
        <StatRow label="Device ID" value={deviceId || 'unknown'} />
        <StatRow
          label="Status"
          value={isOnline ? 'Online' : 'Offline'}
          valueColor={isOnline ? T.green : T.red}
        />
        <StatRow label="Platform" value={Platform.OS} />
      </Card>

      <Button
        title={saving ? 'Saving...' : 'Save Settings'}
        onPress={saveSettings}
        disabled={saving}
        loading={saving}
      />
    </ScrollView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: T.bg },
  content: { padding: 20, paddingTop: 60, paddingBottom: 40, gap: 12 },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 14,
  },
  iconWrap: {
    width: 32,
    height: 32,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cardTitle: {
    fontSize: 15,
    fontWeight: '600',
    color: T.text,
  },
  helpText: {
    fontSize: 12,
    color: T.textMuted,
    marginTop: -6,
    marginBottom: 10,
    lineHeight: 18,
  },
  toggleRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 8,
  },
  toggleLabel: {
    color: T.text,
    fontSize: 14,
  },
});
