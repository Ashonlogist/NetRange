/**
 * Which SIM a cellular reading should be attributed to.
 *
 * This is deliberately dependency-free (no react-native, no NetInfo) so the
 * policy can be unit tested in plain Node. `readCellular` calls it and passes
 * the result to `getActiveCellularAsync` as an explicit subscription id, which
 * makes this the order that actually ships -- the native path then returns
 * exactly the sub chosen here, via its own pin branch.
 *
 * The native implementation of the same order lives in
 *   modules/netrange-telephony/.../TelephonyModule.kt -> pickSubscription()
 * and is only reached when this policy finds no sub to pin, so its ordering is
 * a fallback rather than the deciding factor. It previously carried a
 * bearer-first order that this file contradicted, and because nothing called
 * this function the contradiction went unnoticed: the tests were green while
 * the app reported the wrong carrier.
 *
 * Why the order is default-first, and why that reverses an earlier decision:
 * `hasDataBearer` is derived from `dataNetworkType != UNKNOWN`, which is not a
 * liveness signal. On a dual-SIM phone that is on WiFi -- no cellular bearer is
 * established for any SIM -- it reports UNKNOWN for the real data SIM and a
 * stale non-UNKNOWN value for the idle one. A bearer-first order therefore picks
 * the *idle* SIM precisely when the phone is off cellular, and the scan is filed
 * under a carrier the user is not on. That was measured on a TECNO KM5 on
 * WiFi, where the OS reported defaultDataSubId=3 (Telecel, mnc 62002) and this
 * policy returned the MTN sub instead.
 *
 * `getDefaultDataSubscriptionId()` has none of that fragility: it is Android's
 * per-subscription answer to which SIM the owner configured for data, and it
 * stays correct while WiFi is in use. So the default sub is trusted outright,
 * and bearer state is only consulted when there is no default to trust.
 */

/** The parts of a SIM this policy needs. Works for native and test fixtures. */
export interface SubscriptionLike {
  subscriptionId: number;
  /** Android's default data subscription. */
  isDefault?: boolean;
  /**
   * Whether this sub currently has a live data bearer.
   *
   * Advisory only, and unreliable: see the note at the top of this file. It is
   * a tiebreaker for devices that report no default data sub, never a veto over
   * one that does.
   */
  hasDataBearer?: boolean;
}

/**
 * Return the subscriptionId a reading should be filed under, or null if there
 * are no SIMs to choose from.
 *
 * 1. An explicit pin wins outright -- the user chose it, no heuristic overrules.
 * 2. The default data sub, whatever its bearer state.
 * 3. Failing that, any sub reporting a live bearer. Only reachable when the OS
 *    names no default, e.g. below API 26 where the accessor does not exist.
 * 4. Whatever is there.
 *
 * Step 2 is unconditional on purpose. Treating a bearer as authoritative here
 * is what made an off-cellular dual-SIM phone report the wrong carrier, and a
 * stale bearer is indistinguishable from a real one at this layer.
 */
export function pickSubscriptionId(
  subs: readonly SubscriptionLike[],
  opts: {
    defaultSubId?: number | null;
    preferredSubId?: number | null;
  } = {},
): number | null {
  if (!subs.length) return null;
  const { defaultSubId = null, preferredSubId = null } = opts;

  if (preferredSubId != null) {
    const pinned = subs.find((s) => s.subscriptionId === preferredSubId);
    if (pinned) return pinned.subscriptionId;
  }

  const isDefault = (s: SubscriptionLike) =>
    defaultSubId != null && s.subscriptionId === defaultSubId;

  const fallback = subs.find(isDefault);
  if (fallback) return fallback.subscriptionId;

  const live = subs.find((s) => s.hasDataBearer);
  if (live) return live.subscriptionId;

  return subs[0].subscriptionId;
}

/**
 * Whether a null reading from the picked SIM is worth a second attempt.
 *
 * Only when the SIM was chosen by heuristic. A pinned SIM that reads null is
 * the user's own line being genuinely quiet, and re-filing that under another
 * carrier is exactly what the pin exists to prevent -- it should stay a
 * skipped scan instead of becoming someone else's signal.
 */
export function shouldRetryOtherSubscription(
  signalDbm: number | undefined | null,
  preferredSubId?: number | null,
): boolean {
  return signalDbm == null && preferredSubId == null;
}

/**
 * The one other subscription to try, or null if this is the only SIM.
 *
 * Deliberately returns the *other* sub rather than "the best other sub": a
 * second round of preference logic here would just re-introduce the guesswork
 * the retry exists to escape. If both readings are null the scan is skipped,
 * which is the honest outcome.
 */
export function otherSubscriptionId(
  subs: readonly SubscriptionLike[],
  currentSubId: number | undefined,
): number | null {
  const other = subs.find((s) => s.subscriptionId !== currentSubId);
  return other ? other.subscriptionId : null;
}
