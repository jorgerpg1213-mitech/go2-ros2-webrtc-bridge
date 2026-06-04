#!/usr/bin/env python3
"""
wifi_probe.py — sonda de WiFi para correlacionar señal/distancia con el master.
Corre APARTE, en otra terminal, a la par de la corrida. NO toca el master ni el robot.

Cada segundo imprime, con sello de tiempo relativo al inicio de la sonda:
  t=MM:SS  RSSI=-xx dBm  bitrate=xxx Mbps  rx=xx.x KB/s  tx=xx.x KB/s

- RSSI    : fuerza de señal (mas negativo = peor). ~-50 excelente, ~-70 flojo, <-80 critico.
- bitrate : velocidad que el WiFi negocia (cae cuando la señal baja = "el tubo se angosta").
- rx/tx   : datos reales que entran/salen por la interfaz (rx = lo que llega del robot).

Uso:
  python3 wifi_probe.py            # autodetecta la interfaz wifi
  python3 wifi_probe.py wlp3s0     # o le pasas la interfaz a mano

Detecta la interfaz con: iw dev   (o:  ls /sys/class/net)
"""

import sys
import time
import subprocess


def find_wifi_iface() -> str:
    # 1) intento por iw dev
    try:
        out = subprocess.run(["iw", "dev"], capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Interface "):
                return line.split()[1]
    except Exception:
        pass
    # 2) fallback: primera interfaz que tenga carpeta wireless
    import os
    for name in os.listdir("/sys/class/net"):
        if os.path.isdir(f"/sys/class/net/{name}/wireless"):
            return name
    return ""


def read_rx_tx(iface: str):
    """Bytes acumulados rx, tx desde /proc/net/dev."""
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if line.strip().startswith(iface + ":"):
                    parts = line.split(":")[1].split()
                    rx = int(parts[0])   # bytes recibidos
                    tx = int(parts[8])   # bytes transmitidos
                    return rx, tx
    except Exception:
        pass
    return None, None


def read_rssi_bitrate(iface: str):
    """RSSI (dBm) y bitrate negociado (Mbps) via 'iw dev <iface> link'."""
    rssi = None
    bitrate = None
    try:
        out = subprocess.run(["iw", "dev", iface, "link"],
                             capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("signal:"):
                rssi = s.split()[1]          # ej. -54
            elif s.startswith("tx bitrate:"):
                bitrate = s.split()[2]       # ej. 390.0
    except Exception:
        pass
    return rssi, bitrate


def main():
    iface = sys.argv[1] if len(sys.argv) > 1 else find_wifi_iface()
    if not iface:
        print("No encontre interfaz WiFi. Corre 'iw dev' y pasala: python3 wifi_probe.py <iface>")
        sys.exit(1)
    print(f"# wifi_probe en interfaz: {iface}  (Ctrl+C para salir)", flush=True)
    print(f"# columnas: t=MM:SS  RSSI(dBm)  bitrate(Mbps)  rx(KB/s)  tx(KB/s)", flush=True)

    t0 = time.monotonic()
    prev_rx, prev_tx = read_rx_tx(iface)
    prev_t = time.monotonic()
    try:
        while True:
            time.sleep(1.0)
            now = time.monotonic()
            rx, tx = read_rx_tx(iface)
            dt = now - prev_t
            if rx is not None and prev_rx is not None and dt > 0:
                rx_kbs = (rx - prev_rx) / dt / 1024.0
                tx_kbs = (tx - prev_tx) / dt / 1024.0
            else:
                rx_kbs = tx_kbs = float("nan")
            prev_rx, prev_tx, prev_t = rx, tx, now

            rssi, bitrate = read_rssi_bitrate(iface)
            s = int(now - t0)
            tag = f"{s // 60:02d}:{s % 60:02d}"
            rssi_s = f"{rssi:>5}" if rssi is not None else "  n/a"
            br_s = f"{bitrate:>7}" if bitrate is not None else "    n/a"
            print(f"t={tag}  RSSI={rssi_s} dBm  bitrate={br_s} Mbps  "
                  f"rx={rx_kbs:7.1f} KB/s  tx={tx_kbs:6.1f} KB/s", flush=True)
    except KeyboardInterrupt:
        print("\n# wifi_probe detenido.", flush=True)


if __name__ == "__main__":
    main()
