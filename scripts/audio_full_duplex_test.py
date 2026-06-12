#!/usr/bin/env python3
import os, asyncio, time, logging
logging.basicConfig(level=logging.FATAL)

AES_KEY=os.environ.get("GO2_AES_KEY")
if not AES_KEY:
    raise SystemExit("Falta GO2_AES_KEY")

from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
import aioice.ice as ice
_orig=ice.get_host_addresses
ice.get_host_addresses=lambda v4,v6:[ip for ip in _orig(v4,v6) if ip.startswith("192.168.12.")]

async def audio_cb(frame):
    print(f"[AUDIO_IN] frame sr={frame.sample_rate} samples={frame.samples} fmt={frame.format.name} layout={frame.layout.name}", flush=True)

async def main():
    print("[INIT] conectando")
    conn=UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalAP,aes_128_key=AES_KEY)
    await conn.connect()
    print("[INIT] conectado")

    conn.audio.add_track_callback(audio_cb)
    conn.audio.switchAudioChannel(True)
    print("[AUDIO] canal robot->PC activado, esperando 30s")

    await asyncio.sleep(30)

    print("[END] cerrando")
    await conn.close()

asyncio.run(main())
