import React from 'react';
import { View, StyleSheet } from 'react-native';
import { WebView } from 'react-native-webview';
import { useLocalSearchParams } from 'expo-router';
import { Header } from '@/components/UI';
import { useApp } from '@/components/Providers';

export default function CoverageMap() {
  const { apiUrl } = useApp();
  const { ssid } = useLocalSearchParams<{ ssid?: string }>();
  const url = `${apiUrl}/?ssid=${encodeURIComponent(ssid || '')}`;

  return (
    <View style={styles.container}>
      <Header title="Coverage Map" subtitle={ssid ? `Network: ${ssid}` : 'Live map from server'} />
      <WebView
        source={{ uri: url }}
        style={styles.web}
        javaScriptEnabled
        domStorageEnabled
        startInLoadingState
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#1a1a2e' },
  web: { flex: 1 },
});
