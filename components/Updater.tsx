import React, { createContext, useContext, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Modal,
  Platform,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import * as Application from 'expo-application';
import { useApp } from '@/components/Providers';

export type UpdateState = 'idle' | 'checking' | 'available' | 'downloading' | 'ready' | 'error';

export interface UpdateInfo {
  version: string;
  apkUrl: string;
  notes: string[];
}

interface UpdaterContextValue {
  state: UpdateState;
  progress: number;
  error: string;
  updateInfo: UpdateInfo | null;
  currentVersion: string;
  checkForUpdates: (silent?: boolean) => Promise<void>;
  downloadUpdate: () => Promise<void>;
}

const UpdaterContext = createContext<UpdaterContextValue | null>(null);

function compareVersions(a: string, b: string): number {
  const pa = a.split('.').map(Number);
  const pb = b.split('.').map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const va = pa[i] || 0;
    const vb = pb[i] || 0;
    if (va !== vb) return va - vb;
  }
  return 0;
}

export function UpdaterProvider({ children }: { children: React.ReactNode }) {
  const { apiUrl } = useApp();
  const [state, setState] = useState<UpdateState>('idle');
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [currentVersion, setCurrentVersion] = useState('0.0.0');
  const promptShown = useRef(false);

  useEffect(() => {
    setCurrentVersion(Application.nativeApplicationVersion || '0.0.0');
  }, []);

  useEffect(() => {
    if (apiUrl && !promptShown.current) {
      checkForUpdates();
    }
  }, [apiUrl]);

  const checkForUpdates = async (silent = false) => {
    if (!apiUrl) return;
    try {
      setState('checking');
      setError('');
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      const resp = await fetch(`${apiUrl}/api/version`, { signal: controller.signal });
      clearTimeout(timer);
      const data = await resp.json();
      const installed = Application.nativeApplicationVersion || '0.0.0';
      setUpdateInfo(data);
      if (compareVersions(data.version, installed) <= 0) {
        setState('idle');
        if (!silent) Alert.alert('Up to date', `You're on the latest version (v${installed}).`);
        return;
      }
      setState('available');
      if (!silent || !promptShown.current) {
        promptShown.current = true;
        Alert.alert(
          `Update available (v${data.version})`,
          `${(data.notes || []).join('\n')}\n\nDownload and install now?`,
          [
            { text: 'Later', style: 'cancel' },
            { text: 'Download', onPress: () => downloadUpdate() },
          ]
        );
      }
    } catch (e) {
      setState('error');
      setError(e instanceof Error ? e.message : 'Check failed');
      if (!silent) Alert.alert('Update check failed', 'Could not reach the update server.');
    }
  };

  const downloadUpdate = async () => {
    if (!updateInfo) return;

    if (Platform.OS === 'web') {
      Alert.alert('Not supported', 'Download updates from the GitHub releases page.');
      return;
    }

    const RnBlobUtil = require('react-native-blob-util').default;
    const APK_PATH = `${RnBlobUtil.fs.dirs.DownloadDir}/netrange.apk`;

    setState('downloading');
    setProgress(0);
    setError('');
    try {
      await RnBlobUtil.config({
        path: APK_PATH,
        overwrite: true,
        addAndroidDownloads: {
          useDownloadManager: true,
          notification: true,
          title: 'NetRange update',
          mime: 'application/vnd.android.package-archive',
          description: 'Downloading update...',
        },
      })
        .fetch('GET', updateInfo.apkUrl)
        .progress({ interval: 200 }, (received: number, total: number) => {
          setProgress(total > 0 ? Math.min(received / total, 1) : 0);
        });
      setProgress(1);
      setState('ready');
      if (Platform.OS === 'android') {
        try {
          await RnBlobUtil.android.actionViewIntent(APK_PATH, 'application/vnd.android.package-archive');
        } catch {
          Alert.alert(
            'Update downloaded',
            `APK saved to Downloads/netrange.apk\n\nOpen your file manager to install it.`,
          );
        }
      }
    } catch (e) {
      setState('error');
      setError(e instanceof Error ? e.message : 'Download failed');
      Alert.alert('Download failed', 'The update could not be downloaded. Please try again later.');
    }
  };

  const value: UpdaterContextValue = {
    state,
    progress,
    error,
    updateInfo,
    currentVersion,
    checkForUpdates,
    downloadUpdate,
  };

  return (
    <UpdaterContext.Provider value={value}>
      {children}
      {state === 'downloading' && (
        <Modal transparent visible onRequestClose={() => {}}>
          <View style={styles.backdrop}>
            <View style={styles.card}>
              <Text style={styles.title}>Downloading update...</Text>
              <View style={styles.barTrack}>
                <View style={[styles.barFill, { width: `${Math.round(progress * 100)}%` }]} />
              </View>
              <Text style={styles.percent}>{Math.round(progress * 100)}%</Text>
              <Text style={styles.hint}>Please keep the app open.</Text>
            </View>
          </View>
        </Modal>
      )}
    </UpdaterContext.Provider>
  );
}

export function useUpdater(): UpdaterContextValue {
  const ctx = useContext(UpdaterContext);
  if (!ctx) throw new Error('useUpdater must be used within UpdaterProvider');
  return ctx;
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.7)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  card: {
    width: '100%',
    maxWidth: 360,
    backgroundColor: 'rgba(15,20,40,0.9)',
    borderRadius: 16,
    padding: 28,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
  },
  title: { color: 'rgba(255,255,255,0.95)', fontSize: 17, fontWeight: '600', marginBottom: 16 },
  barTrack: {
    width: '100%',
    height: 8,
    borderRadius: 4,
    backgroundColor: 'rgba(255,255,255,0.08)',
    overflow: 'hidden',
  },
  barFill: { height: '100%', backgroundColor: '#7c3aed', borderRadius: 4 },
  percent: { color: '#7c3aed', fontSize: 22, fontWeight: '700', marginTop: 14 },
  hint: { color: 'rgba(255,255,255,0.35)', fontSize: 12, marginTop: 8 },
});
