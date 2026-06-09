#!/usr/bin/env bash
#
# go2_run.sh — Launcher simple del stack Go2 (teleop + YOLO + streaming).
#
# Levanta SOLO:
#   [MASTER]  go2_master.py   (host, venv robot) -> conexion WebRTC, video, teleop
#   [YOLO]    yolo_viewer.py  (host, venv yolo)  -> deteccion sobre el video
#
# NO usa Docker, SLAM, RViz ni sensores (lidar/odom). El master arranca con
# ENABLE_LIDAR=0 ENABLE_ODOM=0 por default.
#
# El teleop se corre APARTE en otra terminal (a proposito):
#   source ~/go2_legacy_env/bin/activate && python3 scripts/teleop_client.py
#
# La GO2_AES_KEY se lee del entorno. Exportala antes:
#   export GO2_AES_KEY="tu_key"
#
# Cierre: Ctrl+C mata todo limpio.
# ---------------------------------------------------------------------------
set -u

REPO_DIR="$HOME/go2-ros2-webrtc-bridge"
SCRIPTS_DIR="$REPO_DIR/scripts"
VENV_MASTER="${VENV_MASTER:-$HOME/go2_legacy_env}"
VENV_YOLO="${VENV_YOLO:-$HOME/go2-yolo}"
MASTER_SOCK="/tmp/go2_master.sock"
ENABLE_YOLO="${ENABLE_YOLO:-1}"

RUN_DIR="$REPO_DIR/runs/$(date +%Y-%m-%d_%H-%M-%S)"

die() { echo "ERROR: $*" >&2; exit 1; }

# --- Checks ---
[ -n "${GO2_AES_KEY:-}" ] || die "GO2_AES_KEY no esta exportada. Corre: export GO2_AES_KEY=\"tu_key\""
[ -f "$SCRIPTS_DIR/go2_master.py" ] || die "Falta go2_master.py"
[ -f "$VENV_MASTER/bin/activate" ] || die "No existe el venv del master: $VENV_MASTER"
if [ "$ENABLE_YOLO" = "1" ]; then
  [ -f "$VENV_YOLO/bin/activate" ] || die "No existe el venv de YOLO: $VENV_YOLO"
  [ -f "$SCRIPTS_DIR/yolo_viewer.py" ] || die "Falta yolo_viewer.py"
fi

mkdir -p "$RUN_DIR"
HOST_PIDS=()

cleanup() {
  echo ""
  echo "Cerrando..."
  pkill -f "go2_master.py" 2>/dev/null
  pkill -f "yolo_viewer.py" 2>/dev/null
  for pid in "${HOST_PIDS[@]:-}"; do kill "$pid" 2>/dev/null; done
  rm -f "$MASTER_SOCK"
  echo "Listo."
}
trap cleanup INT TERM EXIT

# --- MASTER (host, venv robot) ---
echo "==> Arrancando MASTER (solo video + teleop, sin sensores)"
rm -f "$MASTER_SOCK"
(
  cd "$REPO_DIR" || exit 1
  # shellcheck disable=SC1091
  source "$VENV_MASTER/bin/activate"
  export GO2_AES_KEY
  export ENABLE_LIDAR=0
  export ENABLE_ODOM=0
  exec python3 scripts/go2_master.py
) > "$RUN_DIR/master.log" 2>&1 &
HOST_PIDS+=("$!")
echo "[master] lanzado -> $RUN_DIR/master.log"

# darle tiempo a conectar WebRTC antes de abrir YOLO (que depende del buzon IPC)
sleep 6

# --- YOLO (host, venv yolo) ---
if [ "$ENABLE_YOLO" = "1" ]; then
  echo "==> Arrancando YOLO"
  (
    cd "$REPO_DIR" || exit 1
    # shellcheck disable=SC1091
    source "$VENV_YOLO/bin/activate"
    exec python3 scripts/yolo_viewer.py
  ) > "$RUN_DIR/yolo.log" 2>&1 &
  HOST_PIDS+=("$!")
  echo "[yolo] lanzado -> $RUN_DIR/yolo.log"
fi

echo ""
echo "stack arriba: master + yolo=$ENABLE_YOLO  (sin docker/slam/rviz/sensores)"
echo "logs en: $RUN_DIR"
echo ""
echo "Para el teleop, en OTRA terminal:"
echo "  source $VENV_MASTER/bin/activate && python3 $SCRIPTS_DIR/teleop_client.py"
echo ""
echo "Ctrl+C aqui cierra todo."

# esperar a que mueran los procesos (o Ctrl+C)
wait
