import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Pressable } from 'react-native';

export function Header({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <View style={styles.header}>
      <Text style={styles.title}>{title}</Text>
      {subtitle && <Text style={styles.subtitle}>{subtitle}</Text>}
    </View>
  );
}

export function Card({ children, onPress, style, ...props }: { 
  children: React.ReactNode; 
  onPress?: () => void;
  style?: any;
}) {
  const Component = onPress ? Pressable : View;
  return (
    <Component
      onPress={onPress}
      style={[styles.card, style]}
      {...props}
      android_ripple={{ color: '#e9456033' }}
    >
      {children}
    </Component>
  );
}

export function Button({ 
  title, 
  onPress, 
  variant = 'primary', 
  disabled, 
  loading,
  style,
  ...props 
}: { 
  title: string; 
  onPress: () => void; 
  variant?: 'primary' | 'secondary' | 'danger';
  disabled?: boolean;
  loading?: boolean;
  style?: any;
}) {
  const bg = variant === 'primary' ? '#e94560' : variant === 'secondary' ? '#0f3460' : '#c0392b';
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      style={[
        styles.button,
        { backgroundColor: disabled ? '#444' : bg },
        style
      ]}
      android_ripple={{ color: '#fff33' }}
      {...props}
    >
      {loading ? (
        <Text style={styles.buttonText}>Loading...</Text>
      ) : (
        <Text style={styles.buttonText}>{title}</Text>
      )}
    </Pressable>
  );
}

export function Input({ 
  label, 
  value, 
  onChangeText, 
  placeholder, 
  keyboardType,
  secureTextEntry,
  ...props 
}: { 
  label: string; 
  value: string; 
  onChangeText: (t: string) => void; 
  placeholder?: string;
  keyboardType?: 'default' | 'numeric' | 'decimal-pad';
  secureTextEntry?: boolean;
}) {
  return (
    <View style={styles.inputContainer}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        keyboardType={keyboardType}
        secureTextEntry={secureTextEntry}
        style={styles.input}
        {...props}
      />
    </View>
  );
}

import { TextInput } from 'react-native';

const styles = StyleSheet.create({
  header: { marginBottom: 24 },
  title: { fontSize: 28, fontWeight: '700', color: '#e94560' },
  subtitle: { fontSize: 14, color: '#888', marginTop: 4 },
  card: { 
    backgroundColor: '#16213e', 
    borderRadius: 12, 
    borderWidth: 1, 
    borderColor: '#0f3460',
    padding: 16,
  },
  button: { 
    paddingVertical: 14, 
    borderRadius: 8, 
    alignItems: 'center', 
    justifyContent: 'center',
    minHeight: 50,
  },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  inputContainer: { marginBottom: 16 },
  label: { fontSize: 13, color: '#aaa', marginBottom: 6 },
  input: { 
    backgroundColor: '#1a1a2e', 
    borderWidth: 1, 
    borderColor: '#0f3460', 
    borderRadius: 8, 
    padding: 12, 
    color: '#eee', 
    fontSize: 16,
  },
});