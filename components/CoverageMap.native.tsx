import React from 'react';
import { View, StyleSheet, ActivityIndicator, Text } from 'react-native';
import { WebView } from 'react-native-webview';
import { useLocalSearchParams } from 'expo-router';
import { Header, T } from '@/components/UI';
import { useApp } from '@/components/Providers';

export default function CoverageMap() {
  const { apiUrl } = useApp();
  const { ssid } = useLocalSearchParams<{ ssid?: string }>();
  const ssidParam = ssid ? `ssid=${encodeURIComponent(ssid)}` : '';
  const params = [ssidParam, 'app=1'].filter(Boolean).join('&');
  const url = `${apiUrl}/map?${params}`;

  return (
    <View style={styles.container}>
      <Header
        title="Coverage Map"
        subtitle={ssid ? `Network: ${ssid}` : 'Live map from server'}
      />
      <View style={styles.webWrap}>
        <WebView
          source={{ uri: url }}
          style={styles.web}
          javaScriptEnabled
          domStorageEnabled
          startInLoadingState
          renderLoading={() => (
            <View style={styles.loadingOverlay}>
              <ActivityIndicator size="large" color={T.accent} />
              <Text style={styles.loadingText}>Loading map...</Text>
            </View>
          )}
          onError={() => (
            <View style={styles.loadingOverlay}>
              <Text style={styles.errorText}>Failed to load map. Check server URL in Settings.</Text>
            </View>
          )}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: T.bg },
  webWrap: { flex: 1, borderRadius: 0, overflow: 'hidden' },
  web: { flex: 1, backgroundColor: '#070a14' },
  loadingOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: T.bg,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
  },
  loadingText: { color: T.textMuted, fontSize: 14 },
  errorText: { color: T.red, fontSize: 14, textAlign: 'center', padding: 20 },
});
