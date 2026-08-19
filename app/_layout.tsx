import { Stack } from 'expo-router';
import { Providers } from '@/components/Providers';
import { UpdaterProvider } from '@/components/Updater';
import { StatusBar } from 'expo-status-bar';

export default function RootLayout() {
  return (
    <Providers>
      <UpdaterProvider>
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="index" options={{ title: 'NetRange' }} />
        </Stack>
        <StatusBar style="light" backgroundColor="#070a14" />
      </UpdaterProvider>
    </Providers>
  );
}
