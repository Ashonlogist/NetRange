import { NativeModules, Platform } from 'react-native';
import NetInfo from '@react-native-community/netinfo';

/**
 * One cellular reading, however it was obtained.
 *
 * `signalDbm` is deliberately nullable. A missing reading must stay missing:
 * the coverage map discards null signals, and inventing a plausible dBm would
 * draw a confident, wrong coverage map. Before the native module existed,
 * NetInfo had no signal field at all, so *every* cellular scan was stored with
 * a null signal and the map was permanently, silently empty.
 */
export interface CellularReading {
  carrier: string;
  signalDbm?: number;
  networkType: string;
  isConnected: boolean;
  /** Which SIM this came from, when the OS will say. */
  simSlot?: number;
  subscriptionId?: number;
  /** True when the carrier name came from a user override, not the network. */
  overridden?: boolean;
}

interface NativeSubscription {
  subscriptionId: number;
  simSlot: number;
  carrierName: string | null;
  carrierNumeric: string | null;
  isDataActive: boolean;
  isDefault: boolean;
  signalDbm: number | null;
  networkType: string | null;
}

interface NativeTelephony {
  getCellularStatusAsync(): Promise<{
    permissionGranted: boolean;
    defaultSubscriptionId: number | null;
    subscriptions: NativeSubscription[];
  }>;
  getActiveCellularAsync(): Promise<{
    available: boolean;
    reason?: 'no_sim' | 'no_permission';
    subscriptionId?: number;
    simSlot?: number;
    carrierName?: string | null;
    carrierNumeric?: string | null;
    isDataActive?: boolean;
    isDefault?: boolean;
    signalDbm?: number | null;
    networkType?: string | null;
  }>;
}

const native = (): NativeTelephony | null => {
  if (Platform.OS !== 'android') return null;
  return (NativeModules as Record<string, unknown>).NetRangeTelephony as NativeTelephony | null ?? null;
};

export function isTelephonyAvailable(): boolean {
  return native() !== null;
}

/**
 * Ask for READ_PHONE_STATE at runtime.
 *
 * The native module can enumerate SIMs and read a signal level with this, and
 * without it every cellular scan is stored with a null signal -- which the
 * coverage map then discards. So it is a hard requirement for the app doing
 * its actual job, not a nice-to-have. Android only prompts once, and a denial
 * is reported rather than swallowed: silent degradation here is what made the
 * empty map so hard to explain.
 *
 * Best effort by design. A denial is not fatal; the app still records the
 * carrier, just not a signal.
 */
export async function requestPhonePermission(): Promise<boolean> {
  if (Platform.OS !== 'android') return false;
  try {
    const { PermissionsAndroid } = require('react-native');
    if (PermissionsAndroid?.PERMISSIONS?.READ_PHONE_STATE == null) return false;
    if (await PermissionsAndroid.check(PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE)) {
      return true;
    }
    const res = await PermissionsAndroid.request(
      PermissionsAndroid.PERMISSIONS.READ_PHONE_STATE,
    );
    return res === PermissionsAndroid.RESULTS.GRANTED;
  } catch {
    return false;
  }
}

/** Every SIM the OS will describe, for the SIM picker. */
export async function listSubscriptions(): Promise<NativeSubscription[]> {
  const n = native();
  if (!n) return [];
  try {
    const res = await n.getCellularStatusAsync();
    return res.subscriptions ?? [];
  } catch {
    return [];
  }
}

/** NetInfo fallback, used when the native module is missing or throws. */
async function netInfoReading(): Promise<CellularReading | null> {
  try {
    const netInfo = await NetInfo.fetch();
    if (netInfo.type !== 'cellular') return null;
    const d = netInfo.details as Record<string, unknown>;
    const carrier =
      (d.carrier as string) || (d.mobileCarrier as string) || (d.networkName as string) || '';
    if (!carrier) return null;
    const strength = d.strength ?? d.signalStrength;
    return {
      carrier,
      signalDbm: typeof strength === 'number' ? strength : undefined,
      networkType: (d.cellularGeneration as string) || 'Unknown',
      isConnected: netInfo.isConnected || false,
    };
  } catch {
    return null;
  }
}

/**
 * The reading a scan should record.
 *
 * `overrideCarrier` is a user-supplied name. It still wins when set, but the
 * signal is always the real one: overriding the *label* must not mean
 * inventing the *measurement*.
 */
export async function readCellular(
  overrideCarrier?: string,
): Promise<CellularReading | null> {
  const n = native();
  let base: CellularReading | null = null;

  if (n) {
    try {
      const active = await n.getActiveCellularAsync();
      if (active.available) {
        base = {
          carrier: active.carrierName || '',
          signalDbm: typeof active.signalDbm === 'number' ? active.signalDbm : undefined,
          networkType: active.networkType || 'Unknown',
          isConnected: active.isDataActive ?? false,
          simSlot: active.simSlot,
          subscriptionId: active.subscriptionId,
        };
      }
    } catch {
      base = null;
    }
  }

  if (!base) base = await netInfoReading();
  if (!base) return null;

  const override = overrideCarrier?.trim();
  if (override) return { ...base, carrier: override, overridden: true };
  if (!base.carrier) return null;
  return base;
}
