#!/usr/bin/env bash
#
# go2_launch_voxel3d.sh — Tercer test: VOXEL 3D + teleop, sin camara.
#
# Levanta:
#   [DOCKER] contenedor ROS2 Humble (efimero, red host para UDP)
#   [ODOM]   odom_ros_publisher.py  (Docker) -> /odom + TF odom->base_link
#   [TF]     static base_link -> laser
#   [CLOUD]  cloud_ros_publisher.py (Docker) -> escucha UDP 5007 -> /cloud (PointCloud2)
#   [RVIZ]   rviz2 con rviz_voxel3d.rviz (Fixed Frame odom, PointCloud2 /cloud)
#   [MASTER] go2_master.py (host) con ENABLE_LIDAR=1 ENABLE_ODOM=1 ENABLE_CAMERA=0 ENABLE_CLOUD3D=1
#
# NO usa slam_toolbox, NO camara, NO YOLO. El teleop se corre aparte.
#
#   export GO2_AES_KEY="tu_key"
#   bash go2_launch_voxel3d.sh
#
# Opcional: LIDAR_DECODE_EVERY_N=4 (mas ligero) si el patch skip-decode esta aplicado.
# Ctrl+C cierra todo limpio.
# ---------------------------------------------------------------------------
set -u

REPO_DIR="${REPO_DIR:-$HOME/go2-ros2-webrtc-bridge}"
SCRIPTS_DIR="$REPO_DIR/scripts"
VENV_MASTER="${VENV_MASTER:-$HOME/go2_legacy_env}"
DOCKER_IMAGE="${DOCKER_IMAGE:-osrf/ros:humble-desktop}"
DOCKER_NAME="${DOCKER_NAME:-go2_ros}"
ROS_SETUP="source /opt/ros/humble/setup.bash"
MASTER_SOCK="/tmp/go2_master.sock"
RVIZ_CONFIG="$SCRIPTS_DIR/rviz_voxel3d.rviz"
ENABLE_RVIZ="${ENABLE_RVIZ:-1}"

STAMP="$(date +%Y-%m-%d_%H-%M-%S)"
RUN_DIR="$SCRIPTS_DIR/../runs/$STAMP"
mkdir -p "$RUN_DIR"
HOST_PIDS=()

die() { echo "ERROR: $*" >&2; exit 1; }
log_head() { echo "==> $*"; }

# --- Checks ---
[ -n "${GO2_AES_KEY:-}" ] || die "GO2_AES_KEY no exportada. export GO2_AES_KEY=\"tu_key\""
[ -f "$SCRIPTS_DIR/go2_master.py" ]          || die "Falta go2_master.py"
[ -f "$SCRIPTS_DIR/cloud_ros_publisher.py" ] || die "Falta cloud_ros_publisher.py"
[ -f "$SCRIPTS_DIR/odom_ros_publisher.py" ]  || die "Falta odom_ros_publisher.py"
[ -f "$VENV_MASTER/bin/activate" ]           || die "No existe venv master: $VENV_MASTER"

cleanup() {
  echo ""
  echo "Cerrando..."
  pkill -f "go2_master.py" 2>/dev/null
  for pid in "${HOST_PIDS[@]:-}"; do kill "$pid" 2>/dev/null; done
  docker rm -f "$DOCKER_NAME" >/dev/null 2>&1
  rm -f "$MASTER_SOCK"
  [ "$ENABLE_RVIZ" = "1" ] && xhost -local:docker >/dev/null 2>&1
  echo "Listo."
}
trap cleanup INT TERM EXIT

[ "$ENABLE_RVIZ" = "1" ] && xhost +local:docker >/dev/null 2>&1

# --- 1) Docker ---
log_head "Levantando contenedor ROS2 ($DOCKER_IMAGE)"
docker rm -f "$DOCKER_NAME" >/dev/null 2>&1
docker run -d --rm --name "$DOCKER_NAME" \
  --network host \
  -e DISPLAY="${DISPLAY:-:0}" \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "$SCRIPTS_DIR":/scripts \
  "$DOCKER_IMAGE" sleep infinity >/dev/null \
  || die "No se pudo levantar el contenedor."
for i in $(seq 1 10); do
  docker ps -q -f "name=^${DOCKER_NAME}$" | grep -q . && break
  sleep 0.5
done
docker ps -q -f "name=^${DOCKER_NAME}$" | grep -q . || die "El contenedor no arranco."
echo "Contenedor arriba."

dexec_bg() {
  local label="$1"; shift
  local logf="$RUN_DIR/${label}.log"
  ( docker exec "$DOCKER_NAME" bash -lc "$ROS_SETUP && $*" > "$logf" 2>&1 ) &
  HOST_PIDS+=("$!")
  echo "[$label] lanzado -> $logf"
}

# --- 2) Nodos ROS2 (sin slam_toolbox) ---
log_head "Arrancando nodos ROS2 (odom + tf + cloud)"
dexec_bg "odom"  "python3 /scripts/odom_ros_publisher.py"
sleep 1
dexec_bg "tf"    "ros2 run tf2_ros static_transform_publisher --x 0 --y 0 --z 0 --roll 0 --pitch 0 --yaw 0 --frame-id base_link --child-frame-id laser"
sleep 1
dexec_bg "cloud" "python3 /scripts/cloud_ros_publisher.py"
sleep 1

if [ "$ENABLE_RVIZ" = "1" ]; then
  if [ -f "$RVIZ_CONFIG" ]; then
    dexec_bg "rviz" "rviz2 -d /scripts/$(basename "$RVIZ_CONFIG")"
  else
    dexec_bg "rviz" "rviz2"
  fi
  sleep 1
fi

# --- 3) MASTER (host) con nube 3D activada, sin camara ---
log_head "Arrancando MASTER (voxel 3D, sin camara)"
rm -f "$MASTER_SOCK"
(
  cd "$REPO_DIR" || exit 1
  # shellcheck disable=SC1091
  source "$VENV_MASTER/bin/activate"
  export GO2_AES_KEY
  export ENABLE_LIDAR=1
  export ENABLE_ODOM=1
  export ENABLE_CAMERA=0
  export ENABLE_CLOUD3D=1
  exec python3 scripts/go2_master.py
) > "$RUN_DIR/master.log" 2>&1 &
HOST_PIDS+=("$!")
echo "[master] lanzado -> $RUN_DIR/master.log"

echo ""
echo "stack VOXEL 3D arriba (odom + tf + cloud + rviz + master). Sin camara/yolo/slam."
echo "logs en: $RUN_DIR"
echo ""
echo "Teleop, en OTRA terminal:"
echo "  source $VENV_MASTER/bin/activate && python3 $SCRIPTS_DIR/teleop_client.py"
echo ""
echo "Ctrl+C aqui cierra todo."
wait
