#!/usr/bin/env python3
"""
audio_min_inout.py — prueba mínima full-duplex audio Go2

Objetivo:
  - Robot mic  -> PC speaker/headphones
  - PC mic     -> Robot speaker

No modifica go2_master.py.
No activa cámara, LiDAR, odom, cloud, ROS2 ni teleop.
Usa una sola conexión WebRTC LocalAP, como el master estable.
"""

import asyncio
import os
import signal
import sys
import time
from fractions import Fraction

import av
import numpy as np
import sounddevice as sd

from aiortc import MediaStreamTrack
from unitree_webrtc_connect.webrtc_driver import (
    UnitreeWebRTCConnection,
    WebRTCConnectionMethod,
)

# Mismo criterio del master: si hay ethernet u otras redes, forzar candidatos ICE 192.168.12.*
import aioice.ice as _aioice_ice
_orig_get_host_addresses = _aioice_ice.get_host_addresses
_aioice_ice.get_host_addresses = lambda use_ipv4, use_ipv6: [
    ip for ip in _orig_get_host_addresses(use_ipv4, use_ipv6)
    if ip.startswith("192.168.12.")
]


SAMPLE_RATE = 48000
CHANNELS = 1
FRAME_MS = 20
BLOCK_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)
QUEUE_MAX = 8
STATUS_INTERVAL_S = 2.0

AES_KEY = os.environ.get("GO2_AES_KEY", None)

RUN_T0 = time.monotonic()


def stamp():
    s = int(time.monotonic() - RUN_T0)
    return f"{s // 60:02d}:{s % 60:02d}"


def log(tag, msg):
    print(f"[t={stamp()}] [{tag}] {msg}", flush=True)


class Stats:
    def __init__(self):
        self.mic_callbacks = 0
        self.mic_frames_sent = 0
        self.mic_frames_dropped = 0
        self.mic_peak = 0.0
        self.mic_rms_last = 0.0
        self.robot_frames_recv = 0
        self.robot_frames_played = 0
        self.robot_play_errors = 0
        self.last_robot_format = "none"


stats = Stats()


class PcMicAudioTrack(MediaStreamTrack):
    """
    Track WebRTC de audio vivo desde micrófono local.

    sounddevice captura bloques de 20 ms.
    recv() entrega AudioFrame s16/mono/48k a aiortc.
    """
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.pts = 0
        self.stream = None
        self.closed = False

    def start(self):
        log("AUDIO_OUT", "abriendo micrófono default con sounddevice")
        log("AUDIO_OUT", f"sample_rate={SAMPLE_RATE} channels={CHANNELS} block_samples={BLOCK_SAMPLES}")
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=BLOCK_SAMPLES,
            callback=self._callback,
        )
        self.stream.start()
        log("AUDIO_OUT", "micrófono iniciado")

    def _callback(self, indata, frames, time_info, status):
        if self.closed:
            return

        stats.mic_callbacks += 1

        if status:
            self.loop.call_soon_threadsafe(
                log, "AUDIO_OUT_STATUS", str(status)
            )

        audio_f32 = np.asarray(indata[:, 0], dtype=np.float32).copy()

        rms = float(np.sqrt(np.mean(audio_f32 * audio_f32)))
        peak = float(np.max(np.abs(audio_f32)))

        stats.mic_rms_last = rms
        if peak > stats.mic_peak:
            stats.mic_peak = peak

        # limitador básico: evita saturación dura si el micrófono entra demasiado alto
        audio_f32 = np.clip(audio_f32, -0.95, 0.95)
        audio_i16 = np.clip(audio_f32 * 32767.0, -32768, 32767).astype(np.int16)

        self.loop.call_soon_threadsafe(self._push_audio, audio_i16)

    def _push_audio(self, audio_i16):
        if self.closed:
            return

        if self.queue.full():
            try:
                self.queue.get_nowait()
                stats.mic_frames_dropped += 1
            except asyncio.QueueEmpty:
                pass

        try:
            self.queue.put_nowait(audio_i16)
        except asyncio.QueueFull:
            stats.mic_frames_dropped += 1

    async def recv(self):
        audio_i16 = await self.queue.get()

        audio_2d = audio_i16.reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(audio_2d, format="s16", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self.pts
        frame.time_base = Fraction(1, SAMPLE_RATE)

        self.pts += frame.samples
        stats.mic_frames_sent += 1

        return frame

    def stop(self):
        self.closed = True
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception as e:
            log("AUDIO_OUT", f"error cerrando micrófono: {e}")


class RobotAudioPlayer:
    """
    Reproduce en PC el audio recibido desde el robot.

    Abre la salida local de forma perezosa cuando llega el primer frame,
    porque ahí sabemos formato/layout/sample_rate reales.
    """

    def __init__(self):
        self.stream = None
        self.channels = None
        self.samplerate = None
        self.dtype = "int16"

    async def play(self, frame):
        stats.robot_frames_recv += 1

        try:
            arr = frame.to_ndarray()
            sr = int(getattr(frame, "sample_rate", SAMPLE_RATE) or SAMPLE_RATE)

            # PyAV suele entregar audio como (channels, samples)
            if arr.ndim == 1:
                data = arr.reshape(-1, 1)
                channels = 1
            elif arr.ndim == 2:
                channels = arr.shape[0]
                data = arr.T
            else:
                raise ValueError(f"ndarray audio inesperado shape={arr.shape}")

            if data.dtype != np.int16:
                # fallback conservador: convertir a int16 si llega distinto
                if np.issubdtype(data.dtype, np.floating):
                    data = np.clip(data, -1.0, 1.0)
                    data = (data * 32767.0).astype(np.int16)
                else:
                    data = data.astype(np.int16)

            if self.stream is None:
                self.channels = channels
                self.samplerate = sr
                self.stream = sd.OutputStream(
                    samplerate=self.samplerate,
                    channels=self.channels,
                    dtype="int16",
                    blocksize=0,
                )
                self.stream.start()
                log(
                    "AUDIO_IN",
                    f"salida PC abierta sample_rate={self.samplerate} channels={self.channels}"
                )

            self.stream.write(data)
            stats.robot_frames_played += 1
            stats.last_robot_format = (
                f"format={frame.format.name} layout={frame.layout.name} "
                f"samples={frame.samples} sr={sr} ndarray_shape={arr.shape}"
            )

        except Exception as e:
            stats.robot_play_errors += 1
            if stats.robot_play_errors <= 5:
                log("AUDIO_IN_ERROR", str(e))

    def stop(self):
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception as e:
            log("AUDIO_IN", f"error cerrando salida: {e}")


async def status_loop(conn):
    while True:
        await asyncio.sleep(STATUS_INTERVAL_S)
        try:
            pc_state = conn.pc.connectionState
            ice_state = conn.pc.iceConnectionState
        except Exception:
            pc_state = "unknown"
            ice_state = "unknown"

        log(
            "STATUS",
            "pc_state={} ice={} | mic_cb={} sent={} drop={} rms={:.5f} peak={:.5f} | "
            "robot_recv={} played={} play_err={} last_robot=[{}]".format(
                pc_state,
                ice_state,
                stats.mic_callbacks,
                stats.mic_frames_sent,
                stats.mic_frames_dropped,
                stats.mic_rms_last,
                stats.mic_peak,
                stats.robot_frames_recv,
                stats.robot_frames_played,
                stats.robot_play_errors,
                stats.last_robot_format,
            )
        )


async def main():
    stop_event = asyncio.Event()

    def _stop():
        log("MAIN", "shutdown solicitado")
        stop_event.set()

    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGINT, _stop)
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, _stop)
    except NotImplementedError:
        pass

    log("INIT", "audio_min_inout.py — prueba aislada full-duplex")
    log("INIT", "NO cámara, NO LiDAR, NO odom, NO cloud, NO teleop, NO ROS2")
    log("INIT", f"sounddevice default_device={sd.default.device}")
    log("INIT", f"GO2_AES_KEY={'SET' if AES_KEY else 'NOT_SET'}")
    log("INIT", "connection=LocalAP ice_filter=192.168.12.*")
    log("INIT", "recomendado: usar audífonos para evitar acople")

    conn = UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalAP,
        aes_128_key=AES_KEY,
    )

    mic_track = None
    robot_player = RobotAudioPlayer()
    status_task = None

    try:
        log("WEBRTC", "conectando...")
        await conn.connect()
        log("WEBRTC", "conectado")

        # Audio robot -> PC
        conn.audio.switchAudioChannel(True)
        log("AUDIO_IN", "switchAudioChannel(True) enviado")

        async def recv_audio_stream(frame):
            await robot_player.play(frame)

        conn.audio.add_track_callback(recv_audio_stream)
        log("AUDIO_IN", "callback robot mic -> PC speaker registrado")

        # Audio PC -> robot
        mic_track = PcMicAudioTrack()
        mic_track.start()
        sender = conn.pc.addTrack(mic_track)
        log("AUDIO_OUT", f"track mic PC -> robot agregado sender={type(sender).__name__}")

        status_task = asyncio.create_task(status_loop(conn))

        log("READY", "habla al micrófono de la PC y escucha el robot; escucha también el mic del robot en la PC")
        log("READY", "Ctrl+C para terminar")

        await stop_event.wait()

    except Exception as e:
        log("ERROR", repr(e))

    finally:
        log("SHUTDOWN", "cerrando audio y WebRTC")

        if status_task is not None:
            status_task.cancel()
            try:
                await status_task
            except asyncio.CancelledError:
                pass

        if mic_track is not None:
            mic_track.stop()

        robot_player.stop()

        try:
            await conn.disconnect()
        except Exception as e:
            log("SHUTDOWN", f"conn.disconnect error: {e}")

        log("SHUTDOWN", "terminado")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
