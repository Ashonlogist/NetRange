import React from 'react';
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  TextInput,
  ActivityIndicator,
  type ViewStyle,
  type TextStyle,
} from 'react-native';

/* ── Design Tokens ─────────────────────────────────────── */

export const T = {
  bg: '#070a14',
  surface: 'rgba(255,255,255,0.05)',
  surfaceHover: 'rgba(255,255,255,0.08)',
  border: 'rgba(255,255,255,0.08)',
  borderLight: 'rgba(255,255,255,0.12)',
  accent: '#7c3aed',
  accentGlow: 'rgba(124,58,237,0.35)',
  accent2: '#06b6d4',
  gradient: ['#7c3aed', '#06b6d4'] as const,
  text: 'rgba(255,255,255,0.95)',
  textSec: 'rgba(255,255,255,0.6)',
  textMuted: 'rgba(255,255,255,0.35)',
  green: '#22c55e',
  yellow: '#eab308',
  orange: '#f97316',
  red: '#ef4444',
  card: 'rgba(15,20,40,0.65)',
  inputBg: 'rgba(0,0,0,0.35)',
  radius: 14,
  radiusSm: 10,
};

/* ── Header ────────────────────────────────────────────── */

export function Header({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}) {
  return (
    <View style={s.header}>
      <View style={{ flex: 1 }}>
        <Text style={s.title}>{title}</Text>
        {subtitle ? <Text style={s.subtitle}>{subtitle}</Text> : null}
      </View>
      {right ? <View>{right}</View> : null}
    </View>
  );
}

/* ── Glass Card ────────────────────────────────────────── */

export function Card({
  children,
  onPress,
  style,
  glow,
  ...props
}: {
  children: React.ReactNode;
  onPress?: () => void;
  style?: ViewStyle;
  glow?: boolean;
  [key: string]: any;
}) {
  const Comp = onPress ? Pressable : View;
  return (
    <Comp
      onPress={onPress}
      style={[s.card, glow && s.cardGlow, style]}
      {...(onPress ? { android_ripple: { color: 'rgba(124,58,237,0.2)' } } : {})}
      {...props}
    >
      {children}
    </Comp>
  );
}

/* ── Button ────────────────────────────────────────────── */

export function Button({
  title,
  onPress,
  variant = 'primary',
  disabled,
  loading,
  style,
  icon,
  ...props
}: {
  title: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  disabled?: boolean;
  loading?: boolean;
  style?: ViewStyle;
  icon?: string;
  [key: string]: any;
}) {
  const bg =
    variant === 'primary'
      ? s.btnPrimary
      : variant === 'secondary'
      ? s.btnSecondary
      : variant === 'danger'
      ? s.btnDanger
      : s.btnGhost;

  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      style={[s.button, bg, disabled && s.btnDisabled, style]}
      android_ripple={{ color: 'rgba(255,255,255,0.1)' }}
      {...props}
    >
      {loading ? (
        <ActivityIndicator color="#fff" size="small" />
      ) : (
        <Text style={s.buttonText}>
          {icon ? `${icon}  ` : ''}
          {title}
        </Text>
      )}
    </Pressable>
  );
}

/* ── Input ─────────────────────────────────────────────── */

export function Input({
  label,
  value,
  onChangeText,
  placeholder,
  keyboardType,
  secureTextEntry,
  style,
  ...props
}: {
  label: string;
  value: string;
  onChangeText: (t: string) => void;
  placeholder?: string;
  keyboardType?: 'default' | 'numeric' | 'decimal-pad';
  secureTextEntry?: boolean;
  style?: ViewStyle;
  [key: string]: any;
}) {
  return (
    <View style={[s.inputContainer, style]}>
      {label ? <Text style={s.label}>{label}</Text> : null}
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={T.textMuted}
        keyboardType={keyboardType}
        secureTextEntry={secureTextEntry}
        style={s.input}
        {...props}
      />
    </View>
  );
}

/* ── Badge ─────────────────────────────────────────────── */

export function Badge({
  label,
  variant = 'default',
  style,
}: {
  label: string;
  variant?: 'default' | 'success' | 'warning' | 'danger' | 'info';
  style?: ViewStyle;
}) {
  const colors: Record<string, { bg: string; text: string }> = {
    default: { bg: 'rgba(255,255,255,0.08)', text: T.textSec },
    success: { bg: 'rgba(34,197,94,0.15)', text: T.green },
    warning: { bg: 'rgba(234,179,8,0.15)', text: T.yellow },
    danger: { bg: 'rgba(239,68,68,0.15)', text: T.red },
    info: { bg: 'rgba(6,182,212,0.15)', text: T.accent2 },
  };
  const c = colors[variant] || colors.default;
  return (
    <View style={[s.badge, { backgroundColor: c.bg }, style]}>
      <Text style={[s.badgeText, { color: c.text }]}>{label}</Text>
    </View>
  );
}

/* ── Signal Badge (specialized) ────────────────────────── */

export function SignalBadge({ dbm }: { dbm: number }) {
  const variant =
    dbm > -50 ? 'success' : dbm > -60 ? 'warning' : dbm > -70 ? 'danger' : 'danger';
  const label =
    dbm > -50 ? 'Excellent' : dbm > -60 ? 'Good' : dbm > -70 ? 'Fair' : 'Weak';
  return (
    <View style={s.signalRow}>
      <Text style={s.signalDbm}>{dbm} dBm</Text>
      <Badge label={label} variant={variant} />
    </View>
  );
}

/* ── Divider ───────────────────────────────────────────── */

export function Divider({ style }: { style?: ViewStyle }) {
  return <View style={[s.divider, style]} />;
}

/* ── Section Title ─────────────────────────────────────── */

export function SectionTitle({
  children,
  style,
}: {
  children: React.ReactNode;
  style?: TextStyle;
}) {
  return <Text style={[s.sectionTitle, style]}>{children}</Text>;
}

/* ── Stat Row ──────────────────────────────────────────── */

export function StatRow({
  label,
  value,
  valueColor,
}: {
  label: string;
  value: string;
  valueColor?: string;
}) {
  return (
    <View style={s.statRow}>
      <Text style={s.statLabel}>{label}</Text>
      <Text style={[s.statValue, valueColor ? { color: valueColor } : null]}>
        {value}
      </Text>
    </View>
  );
}

/* ── Empty State ───────────────────────────────────────── */

export function EmptyState({
  icon,
  title,
  description,
}: {
  icon: string;
  title: string;
  description: string;
}) {
  return (
    <View style={s.emptyState}>
      <Text style={s.emptyIcon}>{icon}</Text>
      <Text style={s.emptyTitle}>{title}</Text>
      <Text style={s.emptyDesc}>{description}</Text>
    </View>
  );
}

/* ── Styles ────────────────────────────────────────────── */

const s = StyleSheet.create({
  header: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    marginBottom: 24,
    paddingTop: 8,
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: T.text,
    letterSpacing: -0.5,
  },
  subtitle: {
    fontSize: 14,
    color: T.textMuted,
    marginTop: 4,
  },

  card: {
    backgroundColor: T.card,
    borderRadius: T.radius,
    borderWidth: 1,
    borderColor: T.border,
    padding: 16,
  },
  cardGlow: {
    borderColor: 'rgba(124,58,237,0.2)',
  },

  button: {
    paddingVertical: 14,
    borderRadius: T.radiusSm,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 50,
  },
  btnPrimary: {
    backgroundColor: T.accent,
    shadowColor: T.accent,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 12,
    elevation: 6,
  },
  btnSecondary: {
    backgroundColor: T.surface,
    borderWidth: 1,
    borderColor: T.border,
  },
  btnDanger: {
    backgroundColor: 'rgba(239,68,68,0.15)',
    borderWidth: 1,
    borderColor: 'rgba(239,68,68,0.2)',
  },
  btnGhost: {
    backgroundColor: 'transparent',
    borderWidth: 1,
    borderColor: T.border,
    borderStyle: 'dashed',
  },
  btnDisabled: {
    opacity: 0.4,
  },
  buttonText: {
    color: '#fff',
    fontSize: 15,
    fontWeight: '600',
    letterSpacing: 0.2,
  },

  inputContainer: {
    marginBottom: 14,
  },
  label: {
    fontSize: 12,
    fontWeight: '500',
    color: T.textMuted,
    marginBottom: 6,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  input: {
    backgroundColor: T.inputBg,
    borderWidth: 1,
    borderColor: T.border,
    borderRadius: T.radiusSm,
    padding: 12,
    color: T.text,
    fontSize: 15,
    fontFamily: 'monospace',
  },

  badge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 20,
    alignSelf: 'flex-start',
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '600',
  },

  signalRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  signalDbm: {
    fontSize: 14,
    fontWeight: '600',
    color: T.text,
    fontFamily: 'monospace',
  },

  divider: {
    height: 1,
    backgroundColor: T.border,
    marginVertical: 12,
  },

  sectionTitle: {
    fontSize: 12,
    fontWeight: '600',
    color: T.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    marginBottom: 10,
  },

  statRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: T.border,
  },
  statLabel: {
    color: T.textMuted,
    fontSize: 14,
  },
  statValue: {
    color: T.text,
    fontSize: 14,
    fontWeight: '500',
    fontFamily: 'monospace',
  },

  emptyState: {
    alignItems: 'center',
    paddingVertical: 40,
    paddingHorizontal: 20,
  },
  emptyIcon: {
    fontSize: 48,
    marginBottom: 16,
  },
  emptyTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: T.text,
    marginBottom: 8,
  },
  emptyDesc: {
    fontSize: 14,
    color: T.textMuted,
    textAlign: 'center',
    lineHeight: 20,
  },
});
