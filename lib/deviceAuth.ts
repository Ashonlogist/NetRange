import * as SecureStore from 'expo-secure-store';

/**
 * Per-install device token, used to authenticate scan writes.
 *
 * The backend's POST /api/scan is guarded by a bearer token minted once per
 * install. Storing it in SecureStore (Keystore/Keychain) means it survives
 * app restarts and uninstall/reinstall cycles that would clear plain state.
 *
 * If registration ever fails, a None token is returned and the caller's
 * request goes out unauthenticated. That is deliberate: the backend will
 * reject it with 401, and we surface that to the user, which is honest. The
 * alternative -- silently dropping the upload -- would look like a successful
 * scan that never arrived.
 */

const TOKEN_KEY = 'deviceToken';

let inFlight: Promise<string | null> | null = null;

export async function getDeviceToken(apiUrl: string, deviceId: string): Promise<string | null> {
  if (!apiUrl || !deviceId) return null;

  try {
    const existing = await SecureStore.getItemAsync(TOKEN_KEY);
    if (existing) return existing;
  } catch {
    // fall through and try to register
  }

  // Collapse concurrent callers (foreground scan + background task can both
  // race on a cold start) onto a single registration request.
  if (!inFlight) {
    inFlight = (async () => {
      try {
        const res = await fetch(`${apiUrl}/api/register-device`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ deviceId }),
        });
        if (!res.ok) return null;
        const data = await res.json();
        if (!data?.token) return null;
        await SecureStore.setItemAsync(TOKEN_KEY, data.token);
        return data.token as string;
      } catch {
        return null;
      } finally {
        inFlight = null;
      }
    })();
  }
  return inFlight;
}

export async function clearDeviceToken(): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(TOKEN_KEY);
  } catch {
    // nothing useful to do if the keystore refuses
  }
}

/**
 * POST a scan payload with the device token attached.
 *
 * On a 401 the stored token is dropped and the request is retried exactly
 * once, which self-heals the case where the token was revoked or the device
 * row deleted server-side while the app still held a stale copy.
 */
export async function postScan(
  apiUrl: string,
  deviceId: string,
  payload: unknown,
  opts: { timeoutMs?: number } = {}
): Promise<Response> {
  const send = async (token: string | null): Promise<Response> => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? 30000);
    try {
      return await fetch(`${apiUrl}/api/scan`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  };

  const token = await getDeviceToken(apiUrl, deviceId);
  const res = await send(token);
  if (res.status !== 401) return res;

  // The token we had is not accepted (or we never had one). Clear it and
  // make exactly one more attempt after a fresh registration.
  await clearDeviceToken();
  const fresh = await getDeviceToken(apiUrl, deviceId);
  return send(fresh);
}
