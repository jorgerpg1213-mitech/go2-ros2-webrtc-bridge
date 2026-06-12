#!/usr/bin/env python3
import time
from fractions import Fraction

import av
import numpy as np
import sounddevice as sd


SAMPLE_RATE = 48000
CHANNELS = 1
FRAME_MS = 20
BLOCK_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)
DURATION_SEC = 5


def main():
    print("=== AUDIO FRAME PROBE ===")
    print("[INFO] Esta prueba NO usa robot, NO usa WebRTC, NO modifica go2_master.py")
    print("[INFO] Objetivo: micrófono -> bloques 20 ms -> PyAV AudioFrame")
    print()

    print("=== SOUNDDEVICE INFO ===")
    print(f"sounddevice version: {getattr(sd, '__version__', 'unknown')}")
    print(f"default device: {sd.default.device}")
    print()

    print("=== DEVICES ===")
    print(sd.query_devices())
    print()

    print("=== CONFIG ===")
    print(f"SAMPLE_RATE={SAMPLE_RATE}")
    print(f"CHANNELS={CHANNELS}")
    print(f"FRAME_MS={FRAME_MS}")
    print(f"BLOCK_SAMPLES={BLOCK_SAMPLES}")
    print(f"DURATION_SEC={DURATION_SEC}")
    print()

    frame_count = 0
    callback_count = 0
    first_t = None
    last_log_t = 0.0
    pts = 0
    max_peak_seen = 0.0

    def callback(indata, frames, time_info, status):
        nonlocal frame_count, callback_count, first_t, last_log_t, pts, max_peak_seen

        now = time.monotonic()
        if first_t is None:
            first_t = now

        callback_count += 1

        if status:
            print(f"[AUDIO_STATUS] {status}")

        # indata llega float32 [-1.0, 1.0]
        audio_f32 = np.asarray(indata[:, 0], dtype=np.float32)

        rms = float(np.sqrt(np.mean(audio_f32 * audio_f32)))
        peak = float(np.max(np.abs(audio_f32)))
        max_peak_seen = max(max_peak_seen, peak)

        # Convertimos a int16 PCM, típico para audio WebRTC/Opus path.
        audio_i16 = np.clip(audio_f32 * 32767.0, -32768, 32767).astype(np.int16)

        # PyAV espera forma (channels, samples) para layout mono.
        audio_i16_2d = audio_i16.reshape(1, -1)

        frame = av.AudioFrame.from_ndarray(audio_i16_2d, format="s16", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = pts
        frame.time_base = Fraction(1, SAMPLE_RATE)

        pts += frame.samples
        frame_count += 1

        # Log cada ~0.5 s para no saturar terminal.
        if now - last_log_t >= 0.5:
            last_log_t = now
            elapsed = now - first_t
            print(
                "[FRAME] "
                f"t={elapsed:6.3f}s "
                f"cb={callback_count:04d} "
                f"frames={frame_count:04d} "
                f"in_frames={frames} "
                f"rms={rms:.6f} "
                f"peak={peak:.6f} "
                f"av_format={frame.format.name} "
                f"layout={frame.layout.name} "
                f"samples={frame.samples} "
                f"sample_rate={frame.sample_rate} "
                f"pts={frame.pts} "
                f"time_base={frame.time_base}"
            )

    print("=== START CAPTURE ===")
    print("[ACTION] Habla cerca del micrófono durante 5 segundos.")
    print()

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="float32",
        blocksize=BLOCK_SAMPLES,
        callback=callback,
    ):
        time.sleep(DURATION_SEC)

    print()
    print("=== SUMMARY ===")
    print(f"callback_count={callback_count}")
    print(f"frame_count={frame_count}")
    print(f"max_peak_seen={max_peak_seen:.6f}")

    expected_frames = int(DURATION_SEC * 1000 / FRAME_MS)
    print(f"expected_frames_approx={expected_frames}")

    if frame_count > 0 and max_peak_seen > 0.01:
        print("[OK] Micrófono capturado y AudioFrame PyAV generado correctamente.")
    elif frame_count > 0:
        print("[WARN] Se generaron AudioFrames, pero la señal fue baja.")
    else:
        print("[FAIL] No se generaron AudioFrames.")


if __name__ == "__main__":
    main()
