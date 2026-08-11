import { Stack } from 'expo-router';
import { Providers } from '@/components/Providers';
import { UpdaterProvider } from '@/components/Updater';
import { StatusBar } from 'expo-status-bar';

export default function RootLayout() {
  return (
    <Providers>
      <UpdaterProvider>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="index" options={{ title: 'WiFi Mapper' }} />
        <Stack.Screen name="scan" options={{ title: 'Scan' }} />
        <Stack.Screen name="map" options={{ title: 'Coverage Map' }} />
        <Stack.Screen name="settings" options={{ title: 'Settings' }} />
      </Stack>
      <StatusBar style="light" backgroundColor="#1a1a2e" />
      </UpdaterProvider>
    </Providers>
  );
}