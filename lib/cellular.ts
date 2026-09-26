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

export interface NativeSubscription {
  subscriptionId: number;
  simSlot: number;
  carrierName: string | null;
  carrierNumeric: string | null;
  hasDataBearer: boolean;
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
  getActiveCellularAsync(preferredSubId?: number | null): Promise<{
    available: boolean;
    reason?: 'no_sim' | 'no_permission';
    subscriptionId?: number;
    simSlot?: number;
    carrierName?: string | null;
    carrierNumeric?: string | null;
    hasDataBearer?: boolean;
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
 * What the OS will actually do if we ask again.
 *
 * 'unknown' is the honest starting value: nothing has been asked yet, so the
 * app must not claim the permission is missing before it has looked.
 *
 * This distinction is the whole reason the app could not get unstuck. Android
 * prompts once. After a denial with "don't ask again" -- which is what happens
 * if the user swipes the dialog away twice, and what "clear storage" does not
 * reliably undo -- `request()` resolves to `never_ask_again` and shows no
 * dialog at all. Calling it again looks like a dead button, which is exactly
 * how it presented: the user tapped "Grant permission" and nothing happened,
 * over and over.
 */
export type PhonePermState = 'unknown' | 'granted' | 'denied' | 'never_ask_again' | 'unavailable';

function permResult(res: string): PhonePermState {
  if (res === 'granted') return 'granted';
  if (res === 'never_ask_again') return 'never_ask_again';
  return 'denied';
}

/** Read the current permission state without prompting. */
export async function getPhonePermissionState(): Promise<PhonePermState> {
  if (Platform.OS !== 'android') return 'unavailable';
  try {
    const { PermissionsAndroid } = require('react-native');
    const p = PermissionsAndroid?.PERMISSIONS?.READ_PHONE_STATE;
    if (p == null) return 'unavailable';
    return permResult(await PermissionsAndroid.check(p));
  } catch {
    return 'unavailable';
  }
}

/**
 * Ask for READ_PHONE_STATE at runtime, and report precisely what happened.
 *
 * Best effort by design: a denial is not fatal, the app still records the
 * carrier, just not a signal. But the *reason* is reported rather than
 * swallowed, because the difference between "denied" and "never_ask_again" is
 * the difference between a working prompt and a button that does nothing.
 */
export async function requestPhonePermission(): Promise<PhonePermState> {
  if (Platform.OS !== 'android') return 'unavailable';
  try {
    const { PermissionsAndroid } = require('react-native');
    const p = PermissionsAndroid?.PERMISSIONS?.READ_PHONE_STATE;
    if (p == null) return 'unavailable';
    if (await PermissionsAndroid.check(p)) return 'granted';
    const res = await PermissionsAndroid.request(p);
    return permResult(res);
  } catch {
    return 'unavailable';
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
  preferredSubId?: number | null,
): Promise<CellularReading | null> {
  const n = native();
  let base: CellularReading | null = null;

  if (n) {
    try {
      const active = await n.getActiveCellularAsync(preferredSubId ?? null);
      if (active.available) {
        base = {
          carrier: active.carrierName || '',
          signalDbm: typeof active.signalDbm === 'number' ? active.signalDbm : undefined,
          networkType: active.networkType || 'Unknown',
          isConnected: active.hasDataBearer ?? false,
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
