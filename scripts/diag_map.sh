#!/usr/bin/env bash
# diag_map.sh — Diagnostico para el mapa voxel (TF / odom / scan / frames).
# Corre SOLO, graba todo a un .txt. No necesitas hacer nada en vivo.
#
# USO (en una 3a terminal, con el stack y el teleop ya corriendo):
#   bash ~/go2-ros2-webrtc-bridge/scripts/diag_map.sh
#
# Mientras corre (~40s), MUEVE el robot con el teleop: avanza un poco y gira.
# Eso permite capturar si la pose CAMBIA. Al terminar deja un archivo:
#   ~/go2-ros2-webrtc-bridge/runs/diag_map_<fecha>.txt
# Pasame ese archivo y listo.

set -u
OUT=~/go2-ros2-webrtc-bridge/runs/diag_map_$(date +%Y%m%d_%H%M%S).txt
DEXEC="docker exec go2_ros bash -c"
SRC="source /opt/ros/humble/setup.bash"

say() { echo "" | tee -a "$OUT"; echo "===== $1 =====" | tee -a "$OUT"; }
run() { eval "$DEXEC \"$SRC && $1\"" >>"$OUT" 2>&1; }

mkdir -p ~/go2-ros2-webrtc-bridge/runs
echo "diag_map  $(date)"  >"$OUT"
echo "Graba en: $OUT"
echo ">>> MUEVE el robot (avanza + gira) durante los proximos ~40s <<<"

# 0) Contenedor vivo?
say "DOCKER PS"
docker ps --format '{{.Names}}  {{.Status}}' >>"$OUT" 2>&1

# 1) Topics vivos
say "TOPIC LIST"
run "ros2 topic list"

# 2) Frecuencias (odom y scan en paralelo, 6s c/u)
say "HZ /odom (6s)"
run "timeout 6 ros2 topic hz /odom" &
say "HZ /scan (6s)"
run "timeout 6 ros2 topic hz /scan"
wait

# 3) /odom — contenido (posicion + orientacion reales?)
say "/odom --once"
run "timeout 5 ros2 topic echo /odom --once"

# 4) TF odom->base_link, MUESTRA 1 (mueve el robot ahora)
say "TF odom->base_link  MUESTRA-1  (mueve el robot)"
run "timeout 4 ros2 run tf2_ros tf2_echo odom base_link"

echo ">>> sigue moviendo el robot otros 10s <<<"
sleep 2

# 5) TF odom->base_link, MUESTRA 2 (para comparar si CAMBIO)
say "TF odom->base_link  MUESTRA-2  (debe diferir de MUESTRA-1 si te moviste)"
run "timeout 4 ros2 run tf2_ros tf2_echo odom base_link"

# 6) TF base_link->laser (estatico)
say "TF base_link->laser"
run "timeout 4 ros2 run tf2_ros tf2_echo base_link laser"

# 7) /scan — cabecera + tamano (en que frame viene, cuantos puntos)
say "/scan header (frame_id, stamp) + rangos"
run "timeout 5 ros2 topic echo /scan --once 2>/dev/null | head -25"

# 8) Arbol de frames completo -> PDF + lista
say "FRAMES (arbol TF)"
run "cd /tmp && timeout 8 ros2 run tf2_tools view_frames 2>/dev/null; echo '--- frames generado ---'; ls -la /tmp/frames.* 2>/dev/null"
docker cp go2_ros:/tmp/frames.pdf ~/go2-ros2-webrtc-bridge/runs/diag_frames.pdf 2>/dev/null \
  && echo "(frames.pdf copiado a runs/diag_frames.pdf)" >>"$OUT" \
  || echo "(no se pudo copiar frames.pdf, no critico)" >>"$OUT"

# 9) La config de RViz: Fixed Frame guardado
say "RVIZ rviz_go2.rviz — Fixed Frame y frames"
grep -in "Fixed Frame\|Frame Rate\|Value: odom\|Value: base_link\|Value: laser\|Reference Frame" \
  ~/go2-ros2-webrtc-bridge/scripts/rviz_go2.rviz >>"$OUT" 2>&1

# 10) Que master esta puesto (sanity)
say "MASTER flags"
grep -n "ENABLE_LIDAR = os\|ENABLE_ODOM  = os\|^ENABLE_CAMERA" \
  ~/go2-ros2-webrtc-bridge/scripts/go2_master.py >>"$OUT" 2>&1

echo "" | tee -a "$OUT"
echo "===== LISTO =====" | tee -a "$OUT"
echo "Archivo: $OUT"
echo "Pasame ese .txt (y opcional runs/diag_frames.pdf)."
