#!/usr/bin/env python3
"""
diag_slam_topics.py — Sonda de los topics del SLAM interno del Go2.
Verifica si GRID_MAP, LIDAR_MAPPING_CLOUD_POINT y LIDAR_MAPPING_ODOM
están activos y qué datos mandan. NO toca el master, corre aparte.

Uso:
  source ~/go2_legacy_env/bin/activate
  export GO2_AES_KEY="..."
  python3 diag_slam_topics.py

Corre ~40s, reporta en consola y sale.
"""
import os, sys, time, asyncio, json, logging

logging.basicConfig(level=logging.FATAL)

# --- AES key ---
AES_KEY = os.environ.get("GO2_AES_KEY", "")
if not AES_KEY:
    print("ERROR: exporta GO2_AES_KEY primero"); sys.exit(1)

from unitree_webrtc_connect.webrtc_driver import (
    UnitreeWebRTCConnection, WebRTCConnectionMethod
)
from unitree_webrtc_connect.constants import RTC_TOPIC

# Filtro ICE (igual que el master)
import aioice.ice as _aioice_ice
_orig = _aioice_ice.get_host_addresses
_aioice_ice.get_host_addresses = lambda use_ipv4, use_ipv6: [
    ip for ip in _orig(use_ipv4, use_ipv6) if ip.startswith("192.168.12.")
]

# Topics a probar
TARGETS = {
    "GRID_MAP":                   RTC_TOPIC["GRID_MAP"],
    "LIDAR_MAPPING_CLOUD_POINT":  RTC_TOPIC["LIDAR_MAPPING_CLOUD_POINT"],
    "LIDAR_MAPPING_ODOM":         RTC_TOPIC["LIDAR_MAPPING_ODOM"],
    "LIDAR_MAPPING_PCD_FILE":     RTC_TOPIC["LIDAR_MAPPING_PCD_FILE"],
    "LIDAR_LOCALIZATION_CLOUD_POINT": RTC_TOPIC["LIDAR_LOCALIZATION_CLOUD_POINT"],
    "LIDAR_LOCALIZATION_ODOM":    RTC_TOPIC["LIDAR_LOCALIZATION_ODOM"],
}

# Acumuladores
_stats = {k: {"count": 0, "bytes": 0, "first": None, "last": None, "sample": None}
          for k in TARGETS}

def make_callback(name):
    def cb(msg):
        s = _stats[name]
        now = time.monotonic()
        raw = json.dumps(msg) if isinstance(msg, dict) else str(msg)
        s["count"] += 1
        s["bytes"] += len(raw)
        if s["first"] is None:
            s["first"] = now
            # guardar una muestra (primeros 500 chars)
            s["sample"] = raw[:500]
        s["last"] = now
    return cb

DURATION = 40  # segundos

async def main():
    print(f"Conectando al Go2 (LocalAP)...")
    conn = UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalAP, aes_128_key=AES_KEY
    )
    await conn.connect()
    print("Conectado. Suscribiendo a topics del SLAM interno...")

    for name, topic in TARGETS.items():
        conn.datachannel.pub_sub.subscribe(topic, make_callback(name))
        print(f"  suscrito: {name} ({topic})")

    print(f"\nEsperando datos {DURATION}s... (mueve el robot un poco si puedes)")
    await asyncio.sleep(DURATION)

    print("\n" + "=" * 60)
    print("RESULTADOS:")
    print("=" * 60)
    found_any = False
    for name, s in _stats.items():
        if s["count"] > 0:
            found_any = True
            elapsed = s["last"] - s["first"] if s["first"] != s["last"] else 1.0
            hz = s["count"] / elapsed if elapsed > 0 else 0
            avg_bytes = s["bytes"] / s["count"]
            print(f"\n  {name}:")
            print(f"    mensajes: {s['count']}")
            print(f"    frecuencia: {hz:.1f} Hz")
            print(f"    bytes promedio: {avg_bytes:.0f}")
            print(f"    muestra (primeros 500 chars):")
            print(f"      {s['sample']}")
        else:
            print(f"\n  {name}: SIN DATOS (no publicó nada en {DURATION}s)")

    if not found_any:
        print("\n>>> NINGÚN topic del SLAM interno publicó datos.")
        print(">>> El SLAM del robot probablemente necesita ACTIVACIÓN.")
        print(f">>> Candidato: LIDAR_MAPPING_CMD ({RTC_TOPIC['LIDAR_MAPPING_CMD']})")
        print(">>> Siguiente paso: investigar el comando de activación.")
    else:
        print("\n>>> Hay topics activos. El SLAM interno ESTÁ corriendo.")

    print("=" * 60)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
