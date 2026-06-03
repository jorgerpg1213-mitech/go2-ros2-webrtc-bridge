#!/usr/bin/env python3
"""
yolo_viewer.py — Visor de detecciones YOLO sobre el video del Go2.

Proceso SEPARADO del bridge del robot. Vive en su propio venv (con torch/
ultralytics) y lee los frames del buzon de memoria compartida que escribe
go2_master.py (ver frame_ipc.py). No conoce ni toca el robot.

Dinamica:
  - Lee SIEMPRE el frame mas reciente del buzon (descarta atrasados => sin lag
    acumulado, mismo principio que el buffer del bridge).
  - Solo corre inferencia cuando llega un frame NUEVO (mismo seq => no reprocesa;
    ahorra GPU y hace la metrica de ms/frame fiel a frames unicos).
  - Si el buzon aun no existe (master no arranco), espera sin romperse.
  - Si dejan de llegar frames nuevos (master detenido), lo indica en pantalla
    pero mantiene la ventana viva.

Aislamiento de fallos: este proceso puede caer, reiniciarse o cerrarse sin que
el robot se entere.

Uso tipico (en el venv de YOLO):
    python3 yolo_viewer.py
    python3 yolo_viewer.py --model yolo11n.pt --imgsz 480 --conf 0.35
"""

import argparse
import time

import cv2
import numpy as np

from frame_ipc import FrameReader


def parse_args():
    p = argparse.ArgumentParser(description="Visor YOLO para el stream del Go2 (memoria compartida).")
    p.add_argument("--name", default="go2_cam", help="nombre del buzon de memoria compartida")
    p.add_argument("--model", default="yolo11n.pt", help="modelo YOLO (nano por defecto)")
    p.add_argument("--imgsz", type=int, default=480, help="tamano de entrada del modelo (menor = mas rapido)")
    p.add_argument("--conf", type=float, default=0.35, help="umbral de confianza")
    p.add_argument("--device", default="0", help="'0' = GPU CUDA, 'cpu' = CPU")
    p.add_argument("--half", action="store_true", default=True, help="FP16 (mas rapido en la GTX 1050)")
    p.add_argument("--no-half", dest="half", action="store_false", help="desactiva FP16")
    return p.parse_args()


def main():
    args = parse_args()

    # Carga del modelo (import aqui para que --help no dependa de ultralytics).
    from ultralytics import YOLO
    print(f"[YOLO] cargando modelo {args.model} en device={args.device} half={args.half} ...", flush=True)
    model = YOLO(args.model)

    reader = FrameReader(args.name)
    window = "Go2 YOLO - Detecciones"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    last_seq = -1
    last_annotated = None
    last_new_frame_t = time.time()
    ema_ms = None          # media movil del tiempo de inferencia
    waiting_logged = False

    print("[YOLO] visor listo. Esperando frames del buzon "
          f"'{args.name}' (arranca go2_master.py con ENABLE_YOLO_EXPORT=True). "
          "ESC/q para salir.", flush=True)

    try:
        while True:
            item = reader.read()

            if item is None:
                # buzon no disponible o sin frame valido todavia
                if not waiting_logged:
                    print("[YOLO] aun sin frames; esperando al master...", flush=True)
                    waiting_logged = True
                canvas = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(canvas, "Esperando video del Go2...", (40, 240),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 215, 255), 2)
                cv2.imshow(window, canvas)
                if _exit_requested(window):
                    break
                time.sleep(0.05)
                continue

            frame, seq, ts = item
            waiting_logged = False

            if seq != last_seq:
                # frame NUEVO: correr inferencia
                t0 = time.perf_counter()
                results = model.predict(
                    frame, imgsz=args.imgsz, conf=args.conf,
                    device=args.device, half=args.half, verbose=False,
                )
                infer_ms = (time.perf_counter() - t0) * 1000.0
                ema_ms = infer_ms if ema_ms is None else (0.9 * ema_ms + 0.1 * infer_ms)

                annotated = results[0].plot()  # ndarray BGR con cajas dibujadas
                last_annotated = annotated
                last_seq = seq
                last_new_frame_t = time.time()
            else:
                # mismo frame: no reprocesar, reusar el ultimo anotado
                annotated = last_annotated if last_annotated is not None else frame

            # overlay de metricas (para la fase de medicion / paper)
            if ema_ms is not None:
                fps = 1000.0 / ema_ms if ema_ms > 0 else 0.0
                stale = time.time() - last_new_frame_t
                txt = f"infer {ema_ms:4.1f} ms  (~{fps:4.1f} fps)"
                cv2.putText(annotated, txt, (10, 24), cv2.FONT_HERSHEY_SIMPLEX,
                            0.6, (0, 255, 0), 2)
                if stale > 2.0:
                    cv2.putText(annotated, "SIN FRAMES NUEVOS (master detenido?)",
                                (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

            cv2.imshow(window, annotated)
            if _exit_requested(window):
                break

    finally:
        reader.close()
        try:
            cv2.destroyAllWindows()
            cv2.waitKey(1)
        except Exception:
            pass
        print("[YOLO] visor cerrado.", flush=True)


def _exit_requested(window) -> bool:
    key = cv2.waitKey(1) & 0xFF
    if key in (27, ord("q")):
        return True
    try:
        if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
            return True
    except Exception:
        return True
    return False


if __name__ == "__main__":
    main()
