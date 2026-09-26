package expo.modules.telephony

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.telephony.SignalStrength
import android.telephony.SubscriptionInfo
import android.telephony.SubscriptionManager
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/**
 * Reads the cellular facts that JS cannot reach.
 *
 * NetInfo's `details` only ever describes Android's *default data*
 * subscription, so on a dual-SIM phone it happily reports SIM1's carrier while
 * the user is standing on SIM2 -- which is how scans ended up filed under the
 * wrong network. It exposes no signal level at all, so every cellular scan was
 * stored with signal_dbm = NULL, and both /api/heatmap and the mesh builder
 * discard a null signal by design. The result was an app that saved rows it
 * then refused to draw.
 *
 * Both problems are the same problem: this data lives behind TelephonyManager.
 * So read it per subscription, and report which SIM each reading came from
 * instead of pretending there is only one.
 *
 * Every call below is wrapped: a missing permission, a missing SIM and an OEM
 * that throws are all normal, and none of them should crash a scan.
 */
class TelephonyModule : Module() {

  private val ctx: Context
    get() = appContext.reactContext ?: throw IllegalStateException("no react context")

  private fun hasPhoneState(): Boolean =
    ContextCompat.checkSelfPermission(ctx, Manifest.permission.READ_PHONE_STATE) ==
      PackageManager.PERMISSION_GRANTED

  private fun telephony(): TelephonyManager =
    ctx.getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager

  /**
   * TelephonyManager bound to one subscription.
   *
   * `createForSubscriptionId` only exists from N onwards; below that there is
   * no per-SIM view at all, so callers get the single (implicit) manager and
   * the caller has to treat the result as "the device", not "a SIM".
   */
  private fun tmFor(subId: Int): TelephonyManager? = try {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N)
      telephony().createForSubscriptionId(subId) else telephony()
  } catch (t: Throwable) {
    null
  }

  /** Subscriptions we are allowed to enumerate. Empty if permission is absent. */
  private fun subscriptions(): List<SubscriptionInfo> = try {
    if (!hasPhoneState()) emptyList()
    else SubscriptionManager.from(ctx).activeSubscriptionInfoList ?: emptyList()
  } catch (t: Throwable) {
    // SecurityException on some OEM builds, and a null list is normal when the
    // device has no SIM at all.
    emptyList()
  }

  /**
   * Android's notion of "the SIM that carries mobile data".
   *
   * This is a static and only exists from O. `TelephonyManager.isDataActive`
   * would be the direct answer but it is @SystemApi -- not callable from an app
   * -- so the default subscription plus a registered data bearer is as close as
   * public API gets.
   */
  private fun defaultDataSubId(): Int? = try {
    if (!hasPhoneState() || Build.VERSION.SDK_INT < Build.VERSION_CODES.O) null
    else SubscriptionManager.getDefaultDataSubscriptionId().takeIf { it != -1 }
  } catch (t: Throwable) {
    null
  }

  /**
   * Whether this subscription has a live data bearer, i.e. it is registered
   * and attached to a network. A registered-but-idle SIM reports UNKNOWN here,
   * which is what separates "this is the default SIM" from "this is the SIM
   * the phone is actually using".
   */
  @SuppressLint("MissingPermission")
  private fun hasDataBearer(subId: Int): Boolean = try {
    if (!hasPhoneState() || Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
      false
    } else {
      // A null manager means "cannot tell", which is not the same as UNKNOWN --
      // and `null != UNKNOWN` is true, so this has to be an explicit null check.
      val type = tmFor(subId)?.dataNetworkType
      type != null && type != TelephonyManager.NETWORK_TYPE_UNKNOWN
    }
  } catch (t: Throwable) {
    false
  }

  /**
   * dBm for one subscription, or null when the OS will not say.
   *
   * The modern signal callbacks are async and only fire on change, so the
   * synchronous SignalStrength snapshot is the right source for a one-shot
   * read. Anything genuinely absent is reported as absent: a fabricated number
   * here would poison the coverage map, which is the one thing this app must
   * not do.
   */
  @SuppressLint("MissingPermission")
  private fun signalDbm(subId: Int): Int? = try {
    if (!hasPhoneState()) {
      null
    } else {
      val strength: SignalStrength? = tmFor(subId)?.signalStrength
      val dbms = mutableListOf<Int>()

      if (strength != null) {
        // Per-cell dBm. getCellSignalStrengths() arrived in Q; on anything
        // older the platform exposes no real dBm at all.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
          strength.cellSignalStrengths?.forEach { cell ->
            // dbm is Int.MAX_VALUE when the radio has nothing to report, and 0
            // is not a physically meaningful reading -- both mean "unknown".
            val dbm = cell.dbm
            if (dbm != Int.MAX_VALUE && dbm != 0) dbms.add(dbm)
          }
        }

        // GSM predates dBm entirely: the radio reports an ASU 0..31, where
        // dBm = -113 + 2 * ASU. That is a documented conversion, not a guess,
        // so it is safe to use where no dBm exists.
        if (dbms.isEmpty() && strength.isGsm) {
          val asu = strength.gsmSignalStrength
          if (asu in 0..31) dbms.add(-113 + 2 * asu)
        }
      }
      dbms.minOrNull()
    }
  } catch (t: Throwable) {
    null
  }

  @SuppressLint("MissingPermission")
  private fun networkTypeName(subId: Int): String? = try {
    if (!hasPhoneState() || Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
      null
    } else {
      when (tmFor(subId)?.dataNetworkType) {
        TelephonyManager.NETWORK_TYPE_NR -> "5G"
        TelephonyManager.NETWORK_TYPE_LTE -> "4G"
        TelephonyManager.NETWORK_TYPE_HSPAP -> "3G+"
        TelephonyManager.NETWORK_TYPE_HSPA -> "3G"
        TelephonyManager.NETWORK_TYPE_UMTS -> "3G"
        TelephonyManager.NETWORK_TYPE_EVDO_0 -> "3G"
        TelephonyManager.NETWORK_TYPE_EVDO_A -> "3G"
        TelephonyManager.NETWORK_TYPE_EDGE -> "2G"
        TelephonyManager.NETWORK_TYPE_GPRS -> "2G"
        TelephonyManager.NETWORK_TYPE_CDMA -> "2G"
        TelephonyManager.NETWORK_TYPE_1xRTT -> "1x"
        else -> null
      }
    }
  } catch (t: Throwable) {
    null
  }

  /**
   * Best available name for a subscription.
   *
   * `getCarrierName()` only exists from Q, and the MCC/MNC accessors are
   * deprecated from Q too, so everything is guarded: below N the per-SIM
   * TelephonyManager carries the operator name, and below that the display
   * name is all we have.
   */
  @SuppressLint("MissingPermission")
  private fun carrierName(sub: SubscriptionInfo, subId: Int): String? = try {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
      sub.carrierName?.toString()?.takeIf { it.isNotBlank() }
    } else {
      null
    } ?: tmFor(subId)?.networkOperatorName?.takeIf { it.isNotBlank() }
      ?: sub.displayName?.toString()?.takeIf { it.isNotBlank() }
  } catch (t: Throwable) {
    null
  }

  @SuppressLint("MissingPermission")
  private fun carrierNumeric(subId: Int): String? = try {
    tmFor(subId)?.networkOperator?.takeIf { it.isNotBlank() && it != "0" }
  } catch (t: Throwable) {
    null
  }

  /** simSlot is Q+ only; below that Android exposes no slot index at all. */
  private fun simSlot(sub: SubscriptionInfo): Int =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) sub.simSlotIndex else -1

  private fun describe(sub: SubscriptionInfo, defaultSub: Int?): Map<String, Any?> {
    val subId = sub.subscriptionId
    return mapOf(
      "subscriptionId" to subId,
      "simSlot" to simSlot(sub),
      "carrierName" to carrierName(sub, subId),
      "carrierNumeric" to carrierNumeric(subId),
      "hasDataBearer" to hasDataBearer(subId),
      "isDefault" to (defaultSub != null && subId == defaultSub),
      "signalDbm" to signalDbm(subId),
      "networkType" to networkTypeName(subId)
    )
  }

  /**
   * The SIM a scan should be filed under.
   *
   * Preference order matters, and the order that is wrong here is expensive:
   * filing a scan under an idle SIM is how a phone on Telecel in SIM2 keeps
   * reporting "MTN" with a null signal. `defaultDataSubId()` is the OS's
   * *preference*, not a statement about what is currently carrying traffic, so
   * a default sub with no live bearer must never outrank a sub that has one.
   *
   * So a live data bearer is checked first, and only then do we fall back to
   * the default as a last resort for when nothing has a bearer at all. Among
   * candidates that do have a bearer we prefer the default one, purely to keep
   * the choice deterministic when both SIMs are live -- otherwise it would
   * silently be "whichever slot is physically first".
   *
   * An explicit `preferredSubId` still wins outright: the user picked that SIM
   * in the app, so no heuristic gets to overrule them. If their pick turns out
   * to be reading null the TypeScript layer retries the other sub, rather than
   * silently swapping the carrier label out from under the pinned choice.
   */
  private fun pickSubscription(
    subs: List<SubscriptionInfo>,
    defaultSub: Int?,
    preferredSubId: Int?
  ): SubscriptionInfo? {
    if (preferredSubId != null) {
      subs.firstOrNull { it.subscriptionId == preferredSubId }?.let { return it }
    }
    val defaultLive = subs.firstOrNull { it.subscriptionId == defaultSub && hasDataBearer(it.subscriptionId) }
    if (defaultLive != null) return defaultLive
    val otherLive = subs.firstOrNull { it.subscriptionId != defaultSub && hasDataBearer(it.subscriptionId) }
    if (otherLive != null) return otherLive
    return subs.firstOrNull { it.subscriptionId == defaultSub } ?: subs.firstOrNull()
  }

  override fun definition() = ModuleDefinition {
    Name("NetRangeTelephony")

    /** Every SIM, so the UI can name them and the user can see the choice. */
    AsyncFunction("getCellularStatusAsync") {
      val defaultSub = defaultDataSubId()
      val subs = subscriptions()
      mapOf(
        "permissionGranted" to hasPhoneState(),
        "sdkInt" to Build.VERSION.SDK_INT,
        "defaultSubscriptionId" to defaultSub,
        "subscriptions" to subs.map { describe(it, defaultSub) }
      )
    }

    /**
     * The single reading a scan should record. Returns nulls rather than
     * placeholders when nothing is known, and says which of the two reasons
     * -- no permission, no SIM -- is actually in play, because "grant the
     * permission" and "put a SIM in the phone" are different advice.
     */
    AsyncFunction("getActiveCellularAsync") { preferredSubId: Int? ->
      val defaultSub = defaultDataSubId()
      val subs = subscriptions()
      val pick = pickSubscription(subs, defaultSub, preferredSubId)
      if (pick == null) {
        return@AsyncFunction mapOf<String, Any?>(
          "available" to false,
          "permissionGranted" to hasPhoneState(),
          "reason" to if (hasPhoneState()) "no_sim" else "no_permission"
        )
      }
      val subId = pick.subscriptionId
      val info = describe(pick, defaultSub)
      mapOf<String, Any?>(
        "available" to true,
        "permissionGranted" to hasPhoneState(),
        "defaultSubscriptionId" to defaultSub
      ) + info
    }
  }
}
