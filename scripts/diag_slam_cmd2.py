#!/usr/bin/env python3
"""
diag_slam_cmd2.py — Prueba formatos de activación SLAM.
Strings simples + topic alternativo rt/utlidar/mapping_cmd.
Se detiene al primer éxito. Métricas completas.
"""
import os, sys, time, asyncio, json, logging

logging.basicConfig(level=logging.FATAL)

AES_KEY = os.environ.get("GO2_AES_KEY", "")
if not AES_KEY:
    print("ERROR: exporta GO2_AES_KEY"); sys.exit(1)

from unitree_webrtc_connect.webrtc_driver import (
    UnitreeWebRTCConnection, WebRTCConnectionMethod
)
from unitree_webrtc_connect.constants import RTC_TOPIC

import aioice.ice as _aioice_ice
_orig = _aioice_ice.get_host_addresses
_aioice_ice.get_host_addresses = lambda use_ipv4, use_ipv6: [
    ip for ip in _orig(use_ipv4, use_ipv6) if ip.startswith("192.168.12.")
]

LISTEN = {
    "GRID_MAP":                   RTC_TOPIC["GRID_MAP"],
    "LIDAR_MAPPING_CLOUD_POINT":  RTC_TOPIC["LIDAR_MAPPING_CLOUD_POINT"],
    "LIDAR_MAPPING_ODOM":         RTC_TOPIC["LIDAR_MAPPING_ODOM"],
    "LIDAR_MAPPING_SERVER_LOG":   RTC_TOPIC["LIDAR_MAPPING_SERVER_LOG"],
    "SLAM_QT_NOTICE":             RTC_TOPIC["SLAM_QT_NOTICE"],
    "SLAM_ODOMETRY":              RTC_TOPIC["SLAM_ODOMETRY"],
}

_t0 = [0.0]
_stats = {k: {
    "count": 0, "bytes_total": 0, "bytes_max": 0,
    "t_first": None, "t_last": None, "samples": []
} for k in LISTEN}

def make_cb(name):
    def cb(msg):
        now = time.monotonic()
        raw = json.dumps(msg) if isinstance(msg, dict) else str(msg)
        b = len(raw.encode("utf-8"))
        s = _stats[name]
        s["count"] += 1
        s["bytes_total"] += b
        if b > s["bytes_max"]:
            s["bytes_max"] = b
        if s["t_first"] is None:
            s["t_first"] = now
            print(f"  >>> {name}: primer dato a t={now - _t0[0]:.1f}s ({b} bytes)")
        s["t_last"] = now
        if len(s["samples"]) < 2:
            s["samples"].append(raw[:800])
    return cb

def any_data():
    return any(s["count"] > 0 for k, s in _stats.items()
               if k != "LIDAR_MAPPING_SERVER_LOG")

# Comandos a probar — basados en pistas del keyDemo y la librería
TOPIC_USLAM = RTC_TOPIC["LIDAR_MAPPING_CMD"]       # rt/uslam/client_command
TOPIC_UTLIDAR = "rt/utlidar/mapping_cmd"             # topic alternativo (DDS)

CMD_VARIANTS = [
    (TOPIC_USLAM,   "1",               "uslam: string '1'"),
    (TOPIC_USLAM,   "start",           "uslam: string 'start'"),
    (TOPIC_USLAM,   "mapping_start",   "uslam: string 'mapping_start'"),
    (TOPIC_UTLIDAR, "1",               "utlidar: string '1'"),
    (TOPIC_UTLIDAR, "start",           "utlidar: string 'start'"),
    (TOPIC_UTLIDAR, "mapping_start",   "utlidar: string 'mapping_start'"),
]

async def main():
    print("Conectando al Go2...")
    conn = UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalAP, aes_128_key=AES_KEY
    )
    await conn.connect()
    _t0[0] = time.monotonic()
    print("Conectado.\n")

    for name, topic in LISTEN.items():
        conn.datachannel.pub_sub.subscribe(topic, make_cb(name))
    print(f"Escuchando {len(LISTEN)} topics.\n")

    print("Fase 1: pasivo 5s...")
    await asyncio.sleep(5)
    if any_data():
        print("SLAM ya activo.\n")
    else:
        print("Sin datos. Probando formatos...\n")
        for i, (topic, data, desc) in enumerate(CMD_VARIANTS):
            print(f"  Intento {i+1}/{len(CMD_VARIANTS)}: {desc}")
            print(f"    topic: {topic}")
            print(f"    data:  {repr(data)}")
            try:
                conn.datachannel.pub_sub.publish_without_callback(topic, data)
                print(f"    enviado OK")
            except Exception as e:
                print(f"    error: {e}")

            await asyncio.sleep(6)
            if any_data():
                print(f"\n  >>> ACTIVADO con: {desc}")
                break
            else:
                print(f"    sin activación.")

    print(f"\nFase 3: escuchando 15s más...")
    await asyncio.sleep(15)

    print("\n" + "=" * 60)
    print("RESULTADOS:")
    print("=" * 60)
    for name, s in _stats.items():
        if s["count"] > 0:
            elapsed = max(s["t_last"] - s["t_first"], 0.1)
            hz = s["count"] / elapsed
            avg_b = s["bytes_total"] / s["count"]
            t1 = s["t_first"] - _t0[0]
            print(f"\n  {name}:")
            print(f"    mensajes:     {s['count']}")
            print(f"    Hz:           {hz:.1f}")
            print(f"    bytes avg:    {avg_b:.0f}")
            print(f"    bytes max:    {s['bytes_max']}")
            print(f"    primer msg:   {t1:.1f}s")
            for j, sample in enumerate(s["samples"]):
                print(f"    muestra {j+1}:  {sample}")
        else:
            print(f"\n  {name}: SIN DATOS")

    if any_data():
        print("\n>>> SLAM activado. Topics publicando.")
    else:
        print("\n>>> Ningún formato activó. Siguiente: TheRoboVerse o sniffing.")
    print("=" * 60)

    print("\nCerrando...")
    try:
        await conn.conn.close()
    except Exception:
        pass
    print("Cerrado.")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    finally:
        loop.close()
