/**
 * Carrier/SIM selection policy.
 *
 * Run with: npm test
 *
 * The bug these lock down, and its cause. An earlier version of this policy was
 * bearer-first: it preferred a sub reporting a live data bearer over the OS's
 * default data sub, on the reasoning that Android's default is a user
 * preference rather than a fact about which SIM is carrying traffic.
 *
 * That reasoning is wrong, because `hasDataBearer` comes from
 * `dataNetworkType != UNKNOWN`, and that is not a liveness signal. On a
 * dual-SIM phone that is on WiFi, no cellular bearer exists for any SIM: the
 * real data SIM reports UNKNOWN while the idle one keeps a stale non-UNKNOWN
 * value. Bearer-first therefore picks the idle SIM exactly when the phone is
 * off cellular.
 *
 * Measured on a TECNO KM5 on WiFi: the OS reported defaultDataSubId=3,
 * Telecel, mnc 62002, and the policy returned the MTN subscription, so the app
 * displayed "MTN GH" to a subscriber who was on Telecel. The tests below pin
 * that scenario down using the real subscription ids and bearer values from
 * that device rather than assumed ones -- the previous suite asserted the
 * buggy order was correct, using invented fixtures.
 *
 * These tests also used to pass while proving nothing: `pickSubscriptionId`
 * had no callers in app code, so the ordering that shipped was the Kotlin one.
 * `readCellular` now calls it and passes the result down as an explicit sub id.
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

test('REGRESSION: off-cellular dual-SIM picks the default data sub, not the stale one', () => {
  // The reported failure, with this device's real values. subId 3 is Telecel
  // (mnc 62002) in slot 1 and is the OS default data sub. subId 4 is MTN in
  // slot 0. The phone was on WiFi, so sub 3 reported no bearer while the idle
  // sub 4 reported a stale one -- and bearer-first picked MTN.
  const subs = [sub(3, { isDefault: true, hasDataBearer: false }), sub(4, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 3 }), 3);
});

test('a stale bearer on another SIM does not outrank the default data sub', () => {
  // Same shape, isolated: the only difference is which sub claims a bearer.
  const subs = [sub(1, { isDefault: true, hasDataBearer: false }), sub(2, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 1 }), 1);
});

test('the default data sub wins even when the other SIM is genuinely live', () => {
  // Deliberately unconditional. If the owner set this SIM for data, it is the
  // one a scan describes, and a live bearer on the other line is a fact about
  // the other line.
  const subs = [sub(1, { isDefault: true, hasDataBearer: false }), sub(2, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 1 }), 1);
});

test('prefers the default subscription when it does have a live bearer', () => {
  const subs = [sub(1, { isDefault: true, hasDataBearer: true }), sub(2, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 1 }), 1);
});

test('falls back to a live sub only when the OS names no default', () => {
  // Reachable below API 26, where getDefaultDataSubscriptionId() does not exist.
  const subs = [sub(1, { hasDataBearer: false }), sub(2, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: null }), 2);
  assert.equal(pickSubscriptionId(subs, {}), 2);
});

test('falls back to physical order when there is neither a default nor a bearer', () => {
  const subs = [sub(1, { hasDataBearer: false }), sub(2, { hasDataBearer: false })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: null }), 1);
});

test('a default sub id that is not in the list is ignored, not trusted blindly', () => {
  // A stale id must not return null and must not veto the real candidates.
  const subs = [sub(1, { hasDataBearer: false }), sub(2, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 99 }), 2);
  assert.equal(pickSubscriptionId([sub(7, { hasDataBearer: false })], { defaultSubId: 99 }), 7);
});

test('uses the sole subscription when there is only one', () => {
  assert.equal(pickSubscriptionId([sub(7, { hasDataBearer: false })], { defaultSubId: 7 }), 7);
  assert.equal(pickSubscriptionId([sub(7)], {}), 7);
});

test('does not treat an unknown bearer as evidence either way', () => {
  // hasDataBearer is undefined rather than false. With no default to go on, the
  // only positive evidence is a sub that claims a bearer, so one that stays
  // silent is not preferred over one that speaks up.
  const subs = [sub(1), sub(2, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: null }), 2);
  // And with a default present, undefined changes nothing.
  const withDefault = [sub(1, { isDefault: true }), sub(2, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(withDefault, { defaultSubId: 1 }), 1);
});

test('returns null when there are no subscriptions at all', () => {
  assert.equal(pickSubscriptionId([], { defaultSubId: 1 }), null);
  assert.equal(pickSubscriptionId([], {}), null);
});

test('an explicit pin overrides the default data sub', () => {
  // The user picked this line in the app; no heuristic may overrule that, and
  // this is the branch the native pin path also takes.
  const subs = [sub(3, { isDefault: true, hasDataBearer: false }), sub(4, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 3, preferredSubId: 4 }), 4);
});

test('an unknown pin falls back to the normal order rather than returning null', () => {
  const subs = [sub(3, { isDefault: true, hasDataBearer: false }), sub(4, { hasDataBearer: true })];
  assert.equal(pickSubscriptionId(subs, { defaultSubId: 3, preferredSubId: 99 }), 3);
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
  const subs = [sub(3, { isDefault: true, hasDataBearer: true }), sub(4, { hasDataBearer: false })];
  assert.equal(otherSubscriptionId(subs, 3), 4);
  assert.equal(otherSubscriptionId(subs, 4), 3);
  assert.equal(otherSubscriptionId([sub(1)], 1), null);
  assert.equal(otherSubscriptionId([], undefined), null);
});

test('end to end: a phone on Telecel in SIM2 reports Telecel, on WiFi', () => {
  // What the user sees on screen. Same inputs as the regression case above,
  // with the carrier names this device actually reported.
  const subs = [
    { subscriptionId: 3, carrierName: 'Telecel', hasDataBearer: false },
    { subscriptionId: 4, carrierName: 'MTN GH', hasDataBearer: true },
  ];
  const pick = pickSubscriptionId(subs, { defaultSubId: 3 });
  const carrier = subs.find((s) => s.subscriptionId === pick)!.carrierName;
  assert.equal(carrier, 'Telecel');
});
