# Test 1 — Camara + YOLO + Teleop

Modo ligero: video en vivo + deteccion YOLO + teleop. Sin sensores.

## Terminal 1
    export GO2_AES_KEY="<tu_key>"
    bash ~/go2-ros2-webrtc-bridge/scripts/go2_run.sh

## Terminal 2 (teleop)
    source ~/go2_legacy_env/bin/activate
    python3 ~/go2-ros2-webrtc-bridge/scripts/teleop_client.py

Archivos: scripts/go2_run.sh, go2_master.py, yolo_viewer.py, frame_ipc.py, yolo11n.pt, teleop_client.py
