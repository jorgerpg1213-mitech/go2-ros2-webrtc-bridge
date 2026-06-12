#!/usr/bin/env python3
import os, asyncio, logging
logging.basicConfig(level=logging.FATAL)

AES_KEY=os.environ.get("GO2_AES_KEY")
if not AES_KEY:
    raise SystemExit("Falta GO2_AES_KEY")

from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
import builtins
from unitree_webrtc_connect.webrtc_audiohub import WebRTCAudioHub
_real_print = builtins.print
def quiet_print(*args, **kwargs):
    txt = ' '.join(str(a) for a in args)
    if 'block_content' in txt and len(txt) > 500:
        return
    return _real_print(*args, **kwargs)
builtins.print = quiet_print

import aioice.ice as ice
_orig=ice.get_host_addresses
ice.get_host_addresses=lambda use_ipv4,use_ipv6:[ip for ip in _orig(use_ipv4,use_ipv6) if ip.startswith("192.168.12.")]

async def main():
    conn=UnitreeWebRTCConnection(WebRTCConnectionMethod.LocalAP,aes_128_key=AES_KEY)
    await conn.connect()
    hub=WebRTCAudioHub(conn)

    print("[MEGA] enter")
    await hub.enter_megaphone()

    print("[MEGA] upload")
    await hub.upload_megaphone("/tmp/go2_test_beep.wav")

    print("[MEGA] exit")
    await hub.exit_megaphone()

    if hasattr(conn, 'disconnect'):
        await conn.disconnect()
    elif hasattr(conn, 'pc'):
        await conn.pc.close()
    print("[DONE]")

asyncio.run(main())
