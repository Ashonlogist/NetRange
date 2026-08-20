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
    return round((pct / 2) - 100, 1)


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
        print(f"{n['ssid']:30s} {n['bssid']:17s} {n['signal_dbm']:>5d} dBm  ch{n['channel']}")
