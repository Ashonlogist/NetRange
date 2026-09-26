/**
 * Which SIM a cellular reading should be attributed to.
 *
 * This is deliberately dependency-free (no react-native, no NetInfo) so the
 * policy can be unit tested in plain Node. The Android implementation of the
 * same order lives in
 *   modules/netrange-telephony/.../TelephonyModule.kt -> pickSubscription()
 * and must be kept in step with `pickSubscriptionId` here. Kotlin cannot run in
 * this test suite, so this file is the executable spec of the ordering; the
 * native path is covered by the EAS build plus on-device verification.
 *
 * Why the order is bearer-first: Android's "default data subscription" is a
 * user *preference*, not a statement about which SIM is carrying traffic right
 * now. A default sub with no live bearer used to win over a sub that had one,
 * which is how a phone actually on Telecel in SIM2 kept reporting "MTN" with a
 * null signal. Never let an idle SIM outrank a live one.
 */

/** The parts of a SIM this policy needs. Works for native and test fixtures. */
export interface SubscriptionLike {
  subscriptionId: number;
  /** Android's default data subscription. */
  isDefault?: boolean;
  /** Whether this sub currently has a live data bearer. */
  hasDataBearer?: boolean;
}

/**
 * Return the subscriptionId a reading should be filed under, or null if there
 * are no SIMs to choose from.
 *
 * 1. An explicit pin wins outright -- the user chose it, no heuristic overrules.
 * 2. The default sub, if it has a live bearer.
 * 3. Any other sub with a live bearer.
 * 4. The default sub, even if idle. Nothing has a bearer at this point, so
 *    "which SIM is the OS set up for" beats "which slot is physically first".
 * 5. Whatever is there.
 *
 * Step 3 separates from step 2 only to stay deterministic when both SIMs are
 * live; otherwise the tie would silently resolve to physical slot order.
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

  const live = subs.find((s) => isDefault(s) && s.hasDataBearer);
  if (live) return live.subscriptionId;

  const otherLive = subs.find((s) => !isDefault(s) && s.hasDataBearer);
  if (otherLive) return otherLive.subscriptionId;

  const idleDefault = subs.find(isDefault);
  if (idleDefault) return idleDefault.subscriptionId;

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
