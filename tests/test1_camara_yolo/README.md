# Test 1 — Cámara + YOLO + Teleop

Configuración **más ligera y estable** del banco. Solo video y comandos: ningún sensor
satura el canal, por lo que es el modo de mayor autonomía y alcance.

## Qué entrega

- Streaming de video en vivo del Go2.
- Detección de objetos **YOLO** sobre ese video (proceso aislado).
- Teleoperación estable en paralelo.

## Arquitectura del flujo

```
Go2 (WebRTC) --video--> go2_master.py --IPC(shared mem)--> yolo_viewer.py (venv YOLO)
                              ^
        teleop_client.py -----+ (socket /tmp/go2_master.sock)
```

El maestro consume el video y lo deja en memoria compartida; YOLO corre en **otro venv**
(`~/go2-yolo`) para no cargar el event loop de la conexión. El LiDAR y la odometría están
**apagados** (`ENABLE_LIDAR=0`, `ENABLE_ODOM=0`), que es lo que mantiene el carril libre y
el teleop fluido.

## Archivos que usa

- `scripts/go2_run.sh` — lanzador (sin Docker, sin ROS 2, sin RViz).
- `scripts/go2_master.py` — conexión, video, teleop.
- `scripts/yolo_viewer.py`, `scripts/frame_ipc.py`, `scripts/yolo11n.pt` — detección.
- `scripts/teleop_client.py` — teleoperación.

## Cómo lanzarla

**Terminal 1 — stack:**
```bash
export GO2_AES_KEY="<tu_key>"
bash ~/go2-ros2-webrtc-bridge/scripts/go2_run.sh
```

**Terminal 2 — teleop:**
```bash
source ~/go2_legacy_env/bin/activate
python3 ~/go2-ros2-webrtc-bridge/scripts/teleop_client.py
```

## Qué observar

- Ventana de video con las cajas de detección de YOLO.
- Teleop responde de forma estable y sostenida (es el modo que aguanta más tiempo, al no
  haber decode de LiDAR compitiendo por el event loop).

## Limpieza previa

```bash
pkill -9 -f "go2_master.py" ; pkill -9 -f "teleop_client" ; pkill -9 -f "yolo" ; \
pkill -9 -f "go2_run" ; rm -f /tmp/go2_master.sock ; sleep 2 ; echo "LIMPIO"
```
