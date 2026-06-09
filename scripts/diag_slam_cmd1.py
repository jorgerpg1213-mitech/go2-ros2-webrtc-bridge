#!/usr/bin/env python3
"""
diag_slam_cmd1.py — Prueba activa: activa SLAM interno del Go2.
Usa publish/publish_without_callback (NO publish_request_new).
Prueba hasta 3 formatos del mismo comando (start mapping) al mismo topic.
Se detiene al primer éxito. Mide Hz, bytes, tiempo al primer msg.
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
    return any(s["count"] > 0 for s in _stats.values())

# Formatos a probar (mismo comando, distinto dialecto) — se detiene al primer éxito
CMD_TOPIC = RTC_TOPIC["LIDAR_MAPPING_CMD"]
CMD_VARIANTS = [
    ({"command": "start_mapping"}, "JSON {command: start_mapping}"),
    ("start_mapping",              "string start_mapping"),
    ({"type": "start"},            "JSON {type: start}"),
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
    print(f"Escuchando {len(LISTEN)} topics del SLAM interno.\n")

    # Fase 1: pasivo 5s
    print("Fase 1: escucha pasiva 5s...")
    await asyncio.sleep(5)
    if any_data():
        print("SLAM ya activo espontáneamente.\n")
    else:
        print("Sin datos. Probando activación...\n")

        # Fase 2: probar formatos uno por uno con publish_without_callback
        for i, (data, desc) in enumerate(CMD_VARIANTS):
            print(f"  Intento {i+1}/3: {desc}")
            print(f"    topic: {CMD_TOPIC}")
            print(f"    data:  {json.dumps(data) if isinstance(data, dict) else data}")
            try:
                conn.datachannel.pub_sub.publish_without_callback(
                    CMD_TOPIC, data
                )
                print(f"    enviado OK (fire-and-forget)")
            except Exception as e:
                print(f"    error: {e}")

            # Esperar 8s y ver si algo despertó
            await asyncio.sleep(8)
            if any_data():
                print(f"\n  >>> ACTIVADO con: {desc}")
                break
            else:
                print(f"    sin respuesta aún.")

    # Fase 3: escuchar 15s más
    print(f"\nFase 3: escuchando 15s más...")
    await asyncio.sleep(15)

    # Reporte
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
            print(f"    primer msg:   {t1:.1f}s desde conexión")
            for j, sample in enumerate(s["samples"]):
                print(f"    muestra {j+1}:  {sample}")
        else:
            print(f"\n  {name}: SIN DATOS")

    if any_data():
        print("\n>>> SLAM interno ACTIVADO. Topics publicando.")
        print(">>> Revisar Hz y bytes para evaluar viabilidad.")
    else:
        print("\n>>> Ningún formato activó los topics.")
        print(">>> Siguiente: interceptar app o preguntar TheRoboVerse.")
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
