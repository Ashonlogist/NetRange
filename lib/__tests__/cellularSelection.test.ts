/**
 * Carrier/SIM selection policy.
 *
 * Run with: node --test lib/__tests__/cellularSelection.test.ts
 *
 * The bug these lock down: Android's "default data subscription" is a user
 * preference, not a fact about which SIM is carrying traffic. Preferring the
 * default sub when it was idle made a dual-SIM phone on Telecel in SIM2 report
 * "MTN" with a null signal, and those rows then counted as skipped scans and
 * never reached the coverage map.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  pickSubscriptionId,
  shouldRetryOtherSubscription,
  otherSubscriptionId,
  type SubscriptionLike,
} from '../cellularSelection.ts';

const sub = (
  subscriptionId: number,
  o: { isDefault?: boolean; hasDataBearer?: boolean } = {},
): SubscriptionLike => ({ subscriptionId, ...o });

test('a live bearer outranks an idle default subscription', () => {
  // The exact reported failure: SIM1 is the OS default but idle, SIM2 is live.
  const subs = [sub(1, { isDefault: true, hasDataBearer: false }), sub(2, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 1 }), 2);
});

test('prefers the default subscription when it does have a live bearer', () => {
  const subs = [sub(1, { isDefault: true, hasDataBearer: true }), sub(2, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 1 }), 1);
});

test('falls back to the default sub when no sub has a bearer', () => {
  const subs = [sub(1, { isDefault: true, hasDataBearer: false }), sub(2, { hasDataBearer: false })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 1 }), 1);
});

test('uses the sole subscription when there is only one', () => {
  assert.equal(pickSubscriptionId([sub(7, { hasDataBearer: false })], { defaultSubId: 7 }), 7);
});

test('does not treat an unknown bearer as absent', () => {
  // hasDataBearer is falsy-but-undefined here. Unknown is not the same as
  // "idle": with no positive evidence either way, the OS default is the best
  // available pick, and claiming the other SIM is live would be a guess.
  const subs = [sub(1, { isDefault: true }), sub(2)];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 1 }), 1);
});

test('an explicit false bearer does fall through to the live SIM', () => {
  const subs = [sub(1, { isDefault: true, hasDataBearer: false }), sub(2, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 1 }), 2);
});

test('returns null when there are no subscriptions at all', () => {
  assert.equal(pickSubscriptionId([], { defaultSubId: 1 }), null);
  assert.equal(pickSubscriptionId([], {}), null);
});

test('an explicit pin overrides a live bearer on the other SIM', () => {
  // The user picked this line in the app; no heuristic may overrule that.
  const subs = [sub(1, { isDefault: true, hasDataBearer: true }), sub(2, { hasDataBearer: false })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 1, preferredSubId: 2 }), 2);
});

test('an unknown pin falls back to the normal order rather than returning null', () => {
  const subs = [sub(1, { isDefault: true, hasDataBearer: false }), sub(2, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 1, preferredSubId: 99 }), 2);
});

test('a null signal is retried only when the SIM was chosen heuristically', () => {
  assert.equal(shouldRetryOtherSubscription(undefined, null), true);
  assert.equal(shouldRetryOtherSubscription(null, undefined), true);
  // Pinned: a quiet pinned SIM must not be re-filed under another carrier.
  assert.equal(shouldRetryOtherSubscription(undefined, 2), false);
  // A real reading is never retried.
  assert.equal(shouldRetryOtherSubscription(-84, null), false);
  assert.equal(shouldRetryOtherSubscription(0, null), false);
});

test('retry targets the other sub, and there is none on a single-SIM phone', () => {
  const subs = [sub(1, { isDefault: true, hasDataBearer: true }), sub(2, { hasDataBearer: false })];
  assert.equal(otherSubscriptionId(subs, 1), 2);
  assert.equal(otherSubscriptionId(subs, 2), 1);
  assert.equal(otherSubscriptionId([sub(1)], 1), null);
  assert.equal(otherSubscriptionId([], undefined), null);
});

test('end to end: idle-default phone reports the live SIM', () => {
  // What the user sees on screen after the fix.
  const subs = [sub(1, { isDefault: true, hasDataBearer: false }), sub(2, { hasDataBearer: true })];
  const pick = pickSubscriptionId(subs, { defaultSubId: 1 });
  const carrier = subs.find((s) => s.subscriptionId === pick)!.subscriptionId === 2 ? 'Telecel' : 'MTN';
  assert.equal(carrier, 'Telecel');
});
