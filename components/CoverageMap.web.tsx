import React, { useEffect, useRef } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { Header, T } from '@/components/UI';
import { useApp } from '@/components/Providers';

export default function CoverageMap() {
  const { apiUrl } = useApp();
  const { ssid } = useLocalSearchParams<{ ssid?: string }>();
  const frameHost = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!frameHost.current) return;
    const ssidParam = ssid ? `?ssid=${encodeURIComponent(ssid)}` : '';
    const iframe = document.createElement('iframe');
    iframe.src = `${apiUrl}/map${ssidParam}`;
    iframe.style.width = '100%';
    iframe.style.height = '100%';
    iframe.style.border = '0';
    iframe.style.borderRadius = '0';
    frameHost.current.innerHTML = '';
    frameHost.current.appendChild(iframe);
  }, [apiUrl, ssid]);

  return (
    <View style={styles.container}>
      <Header
        title="Coverage Map"
        subtitle={ssid ? `Network: ${ssid}` : 'Live map from backend'}
      />
      <Text style={styles.hint}>
        Full interactive map with coverage heatmap. Scan on the phone app to add data.
      </Text>
      <div
        ref={frameHost}
        style={{ flex: 1, minHeight: 480, position: 'relative' }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: T.bg },
  hint: {
    color: T.textMuted,
    paddingHorizontal: 16,
    paddingBottom: 8,
    fontSize: 12,
  },
});
