import subprocess
import shutil
from datetime import datetime, timezone


NMCLI = shutil.which("nmcli")


def split_terse(line):
    parts = []
    current = ""
    i = 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line) and line[i + 1] == ":":
            current += ":"
            i += 2
        elif line[i] == ":":
            parts.append(current)
            current = ""
            i += 1
        else:
            current += line[i]
            i += 1
    parts.append(current)
    return parts


def scan():
    if not NMCLI:
        return []
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL,CHAN,ACTIVE", "device", "wifi", "list"],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    networks = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = split_terse(line)
        if len(parts) < 5:
            continue
        ssid = parts[0].strip()
        bssid = parts[1].strip()
        signal_pct = int(parts[2].strip())
        channel = parts[3].strip()
        active = parts[4].strip() == "yes"

        dbm = signal_pct_to_dbm(signal_pct)
        networks.append({
            "ssid": ssid,
            "bssid": bssid,
            "signal_pct": signal_pct,
            "signal_dbm": dbm,
            "channel": channel,
            "active": active,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    return networks


def signal_pct_to_dbm(pct):
    """Convert nmcli's 0-100 SIGNAL percentage to dBm."""
    return round((pct / 2) - 100, 1)


def signal_to_dbm(value):
    """Normalize a client-reported signal strength to dBm, or None if unusable.

    The two producers of signal strength in this system report in different
    units, and the scan endpoint used to disambiguate them with a three-branch
    heuristic that was never documented:

      * Android's WifiManager `ScanResult.level` -- which react-native-wifi-reborn
        exposes as `n.level`, and which the app sends as `strength` -- is
        already dBm. It is negative (roughly -30 strong to -90 weak), which is
        also how the phone renders it: the network list compares it directly
        against -50/-60 and labels the result in dBm. Both WiFi code paths
        send `n.level`, falling back to the dBm literal -70.
      * `nmcli -f SIGNAL` (the server-side scan path) reports 0-100 percent,
        where 0% is -100 dBm and 100% is 0 dBm -- hence pct/2 - 100.

    So: negative input is dBm and is passed through; anything else is treated
    as a percentage and converted.

    The old `0 <= strength <= 1` branch (`strength * 50 - 100`) was meant for a
    0-1 normalized fraction. Nothing in this codebase ever produced one -- both
    mobile WiFi paths send `n.level`, and the cellular value is always absent
    because NetInfo exposes no signal field (see below) -- so that branch only
    ever risked mis-scaling a value that happened to land in 0..1. It is gone.

    Cellular strength arrives as None and stays None rather than being guessed
    at: NetInfo's `details` has no signal-strength field, so there is no
    measurement to convert. An absent reading must not become a fake one --
    a fabricated dBm value would flow into the coverage averages and read as
    real data.

    Known ambiguity: because the transport is unitless, an exact 0 is read as
    0% (-100 dBm) rather than 0 dBm. Real reported 0 dBm is implausible
    (nothing that close to an AP), so this is left as-is; the durable fix
    would be an explicit unit or `signal_dbm` field in the scan payload, which
    would change the client contract and belongs in its own change.
    """
    # bool is an int subclass; True must not become 1% signal.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v < 0:
        return round(v, 1)
    if v <= 100:
        return round(v / 2 - 100, 1)
    return None  # out of range for either unit -- refuse rather than invent


def get_current_connection():
    if not NMCLI:
        return {"ssid": None, "uuid": None, "device": None, "connected": False}
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"ssid": None, "uuid": None, "device": None, "connected": False}
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(":")
        if len(parts) >= 4 and parts[2].strip() == "802-11-wireless":
            return {
                "ssid": parts[0].strip(),
                "uuid": parts[1].strip(),
                "device": parts[3].strip(),
                "connected": True,
            }
    return {"ssid": None, "uuid": None, "device": None, "connected": False}


if __name__ == "__main__":
    conn = get_current_connection()
    print(f"Connected: {conn['connected']}, SSID: {conn['ssid']}")
    networks = scan()
    for n in networks:
        print(f"{n['ssid']:30s} {n['bssid']:17s} {n['signal_dbm']:>5.1f} dBm  ch{n['channel']}")
