import React, { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import * as SecureStore from 'expo-secure-store';
import * as Location from 'expo-location';
import NetInfo from '@react-native-community/netinfo';
import { Platform } from 'react-native';

interface AppState {
  apiUrl: string;
  setApiUrl: (url: string) => void;
  currentLocation: Location.LocationObjectCoords | null;
  setCurrentLocation: (loc: Location.LocationObjectCoords | null) => void;
  deviceId: string;
  isOnline: boolean;
}

const AppContext = createContext<AppState | null>(null);

export function Providers({ children }: { children: ReactNode }) {
  const [apiUrl, setApiUrl] = useState('https://netrange-backend.onrender.com');
  const [currentLocation, setCurrentLocation] = useState<Location.LocationObjectCoords | null>(null);
  const [deviceId, setDeviceId] = useState('');
  const [isOnline, setIsOnline] = useState(true);

  useEffect(() => {
    loadSettings();
    const unsubscribe = NetInfo.addEventListener(state => {
      setIsOnline(state.isConnected ?? false);
    });
    requestInitialLocation();
    return () => unsubscribe();
  }, []);

  const loadSettings = async () => {
    try {
      const [url, id] = await Promise.all([
        SecureStore.getItemAsync('apiUrl'),
        SecureStore.getItemAsync('deviceId'),
      ]);
      if (url) setApiUrl(url);
      if (id) {
        setDeviceId(id);
      } else {
        const newId = `device_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        await SecureStore.setItemAsync('deviceId', newId);
        setDeviceId(newId);
      }
    } catch (e) {
      console.warn('SecureStore unavailable:', e);
    }
  };

  const requestInitialLocation = async () => {
    if (Platform.OS === 'web') return;
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status === 'granted') {
        const loc = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.High,
        });
        setCurrentLocation(loc.coords);
      }
    } catch (e) {
      console.warn('Initial location failed:', e);
    }
  };

  return (
    <AppContext.Provider value={{ apiUrl, setApiUrl, currentLocation, setCurrentLocation, deviceId, isOnline }}>
      {children}
    </AppContext.Provider>
  );
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used within Providers');
  return ctx;
}
