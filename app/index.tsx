import React from 'react';
import { View, Text, StyleSheet, Platform } from 'react-native';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { Card, Header, T } from '@/components/UI';

const CARDS = [
  {
    icon: 'wifi' as const,
    iconColor: T.accent,
    title: 'Scan Networks',
    desc: 'WiFi + cellular signal strength',
    route: '/scan' as const,
    iconBg: 'rgba(124,58,237,0.15)',
  },
  {
    icon: 'map' as const,
    iconColor: T.accent2,
    title: 'Coverage Map',
    desc: 'View interpolated heatmap',
    route: '/map' as const,
    iconBg: 'rgba(6,182,212,0.15)',
  },
  {
    icon: 'settings-outline' as const,
    iconColor: T.green,
    title: 'Settings',
    desc: 'API, interpolation, sync',
    route: '/settings' as const,
    iconBg: 'rgba(34,197,94,0.15)',
  },
];

export default function IndexScreen() {
  const router = useRouter();

  return (
    <View style={s.container}>
      <Header
        title="NetRange"
        subtitle="Campus WiFi coverage mapping"
        right={
          <View style={s.badge}>
            <Text style={s.badgeText}>
              {Platform.OS === 'web' ? 'Web' : Platform.OS === 'android' ? 'Android' : 'iOS'}
            </Text>
          </View>
        }
      />

      <View style={s.grid}>
        {CARDS.map((c) => (
          <Card
            key={c.route}
            onPress={() => router.push(c.route)}
            style={s.card}
            glow
          >
            <View style={[s.iconWrap, { backgroundColor: c.iconBg }]}>
              <Ionicons name={c.icon} size={28} color={c.iconColor} />
            </View>
            <View style={s.cardBody}>
              <Text style={s.cardTitle}>{c.title}</Text>
              <Text style={s.cardDesc}>{c.desc}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={T.textMuted} />
          </Card>
        ))}
      </View>

      <View style={s.footer}>
        <Text style={s.footerText}>
          {Platform.OS === 'web'
            ? 'Web mode: view maps only (no scanning)'
            : 'Native: full WiFi + cellular scanning'}
        </Text>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: T.bg,
    padding: 20,
    paddingTop: 60,
  },
  grid: {
    flex: 1,
    gap: 12,
  },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 18,
    gap: 14,
  },
  iconWrap: {
    width: 52,
    height: 52,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cardBody: {
    flex: 1,
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: T.text,
    marginBottom: 2,
  },
  cardDesc: {
    fontSize: 13,
    color: T.textMuted,
  },
  footer: {
    paddingTop: 16,
    borderTopWidth: 1,
    borderTopColor: T.border,
  },
  footerText: {
    fontSize: 12,
    color: T.textMuted,
    textAlign: 'center',
  },
  badge: {
    backgroundColor: T.surface,
    borderWidth: 1,
    borderColor: T.border,
    borderRadius: 20,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '600',
    color: T.textSec,
    textTransform: 'uppercase',
  },
});
