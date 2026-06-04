#!/usr/bin/env bash
#
# go2_launch.sh — Launcher provisional del stack Go2 (un solo Enter).
#
# Levanta, en orden y vigilado:
#   [DOCKER]  contenedor ROS2 Humble (efimero)
#   [LIDAR]   lidar_ros_publisher.py   (dentro de Docker)  -> /scan
#   [ODOM]    odom_ros_publisher.py    (dentro de Docker)  -> /odom + TF
#   [TF]      static_transform base_link -> laser           (dentro de Docker)
#   [RVIZ]    rviz2 con config pre-armada                    (dentro de Docker)
#   [MASTER]  go2_master.py            (host, venv robot)
#   [YOLO]    yolo_viewer.py           (host, venv yolo)
#
# NO arranca teleop (se corre aparte, a proposito).
# NO usa los senders viejos (lidar_sender.py / odom_sender.py): el master ya
# hace ese trabajo. Correrlos duplicaria la conexion WebRTC.
#
# Cada componente escribe su propio log en runs/<fecha>/, mas un resumen.txt
# unico (el archivo que le pasas a quien analice).
#
# Cierre: Ctrl+C mata todo limpio (procesos host + contenedor), sin zombies.
#
# La GO2_AES_KEY NO va escrita aqui: se lee del entorno. Exportala antes:
#     export GO2_AES_KEY="tu_key"
#
# ---------------------------------------------------------------------------
# CONFIG (unica fuente de verdad — editar aqui, no en el cuerpo del script)
# ---------------------------------------------------------------------------
set -u

REPO_DIR="${REPO_DIR:-$HOME/go2-ros2-webrtc-bridge}"
SCRIPTS_DIR="$REPO_DIR/scripts"

VENV_MASTER="${VENV_MASTER:-$HOME/go2_legacy_env}"
VENV_YOLO="${VENV_YOLO:-$HOME/go2-yolo}"

DOCKER_IMAGE="${DOCKER_IMAGE:-osrf/ros:humble-desktop}"
DOCKER_NAME="${DOCKER_NAME:-go2_ros}"
ROS_SETUP="source /opt/ros/humble/setup.bash"

MASTER_SOCK="/tmp/go2_master.sock"
RVIZ_CONFIG="$SCRIPTS_DIR/rviz_go2.rviz"   # se usa si existe; si no, RViz abre en blanco

# Interruptores (para encender/apagar piezas sin tocar el resto)
ENABLE_RVIZ="${ENABLE_RVIZ:-1}"
ENABLE_YOLO="${ENABLE_YOLO:-1}"

# ---------------------------------------------------------------------------
# Preparacion
# ---------------------------------------------------------------------------
STAMP="$(date +%Y-%m-%d_%H-%M-%S)"
RUN_DIR="$SCRIPTS_DIR/../runs/$STAMP"
mkdir -p "$RUN_DIR"
RESUMEN="$RUN_DIR/resumen.txt"

HOST_PIDS=()   # PIDs de procesos host que debemos matar al cerrar

log_head() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }
die()      { printf '\033[1;31m[ERROR] %s\033[0m\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Chequeos previos (fallar temprano y claro, no a media corrida)
# ---------------------------------------------------------------------------
log_head "Chequeos previos"

[ -n "${GO2_AES_KEY:-}" ] || die "GO2_AES_KEY no esta exportada. Corre:  export GO2_AES_KEY=\"tu_key\""
command -v docker >/dev/null 2>&1 || die "docker no esta instalado o no esta en PATH."
[ -d "$SCRIPTS_DIR" ]            || die "No existe $SCRIPTS_DIR"
[ -f "$SCRIPTS_DIR/go2_master.py" ]          || die "Falta go2_master.py"
[ -f "$SCRIPTS_DIR/lidar_ros_publisher.py" ] || die "Falta lidar_ros_publisher.py"
[ -f "$SCRIPTS_DIR/odom_ros_publisher.py" ]  || die "Falta odom_ros_publisher.py"
[ -f "$VENV_MASTER/bin/activate" ] || die "No existe el venv del master: $VENV_MASTER"
if [ "$ENABLE_YOLO" = "1" ]; then
  [ -f "$VENV_YOLO/bin/activate" ] || die "No existe el venv de YOLO: $VENV_YOLO"
  [ -f "$SCRIPTS_DIR/yolo_viewer.py" ] || die "Falta yolo_viewer.py"
fi
echo "OK — entorno verificado."

# Permitir que el contenedor use la pantalla (RViz)
if [ "$ENABLE_RVIZ" = "1" ]; then
  xhost +local:docker >/dev/null 2>&1 || echo "[aviso] xhost fallo; RViz podria no abrir."
fi

# ---------------------------------------------------------------------------
# Limpieza (se ejecuta SIEMPRE al salir: Ctrl+C, error o fin normal)
# ---------------------------------------------------------------------------
cleanup() {
  trap '' INT TERM   # evitar reentradas
  log_head "Cerrando todo (limpio)"

  # 1) procesos host (master, yolo, tail)
  for pid in "${HOST_PIDS[@]:-}"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill -INT "$pid" 2>/dev/null
    fi
  done
  sleep 2
  for pid in "${HOST_PIDS[@]:-}"; do
    [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null
  done

  # 2) por si quedaron sueltos por nombre
  pkill -f "go2_master.py"   2>/dev/null
  pkill -f "yolo_viewer.py"  2>/dev/null

  # 3) contenedor (mata de paso todos los nodos ROS de adentro)
  if docker ps -q -f "name=^${DOCKER_NAME}$" | grep -q .; then
    echo "Deteniendo contenedor $DOCKER_NAME ..."
    docker stop "$DOCKER_NAME" >/dev/null 2>&1
  fi

  # 4) socket y permisos de pantalla
  rm -f "$MASTER_SOCK"
  [ "$ENABLE_RVIZ" = "1" ] && xhost -local:docker >/dev/null 2>&1

  echo "Listo. Logs de esta corrida en: $RUN_DIR"
  echo "Resumen para analisis:          $RESUMEN"
}
trap cleanup INT TERM EXIT

# ---------------------------------------------------------------------------
# 1) Contenedor Docker (efimero, red del host para que el UDP del master llegue)
# ---------------------------------------------------------------------------
log_head "Levantando contenedor ROS2 ($DOCKER_IMAGE)"
docker rm -f "$DOCKER_NAME" >/dev/null 2>&1   # por si quedo uno viejo
docker run -d --rm --name "$DOCKER_NAME" \
  --network host \
  -e DISPLAY="${DISPLAY:-:0}" \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "$SCRIPTS_DIR":/scripts \
  "$DOCKER_IMAGE" sleep infinity >/dev/null \
  || die "No se pudo levantar el contenedor."

# esperar a que este vivo
for i in $(seq 1 10); do
  docker ps -q -f "name=^${DOCKER_NAME}$" | grep -q . && break
  sleep 0.5
done
docker ps -q -f "name=^${DOCKER_NAME}$" | grep -q . || die "El contenedor no arranco."
echo "Contenedor arriba."

# helper: corre un comando dentro del contenedor, en segundo plano, logueando a archivo
dexec_bg() {  # $1 = etiqueta  $2 = comando-ros
  local label="$1"; shift
  local logf="$RUN_DIR/${label}.log"
  ( docker exec "$DOCKER_NAME" bash -lc "$ROS_SETUP && $*" \
      > "$logf" 2>&1 ) &
  HOST_PIDS+=("$!")
  echo "[$label] lanzado -> $logf"
}

# ---------------------------------------------------------------------------
# 2-5) Nodos ROS2 dentro del contenedor
# ---------------------------------------------------------------------------
log_head "Arrancando nodos ROS2"
dexec_bg "lidar" "python3 /scripts/lidar_ros_publisher.py"
sleep 1
dexec_bg "odom"  "python3 /scripts/odom_ros_publisher.py"
sleep 1
dexec_bg "tf"    "ros2 run tf2_ros static_transform_publisher --x 0 --y 0 --z 0 --roll 0 --pitch 0 --yaw 0 --frame-id base_link --child-frame-id laser"
sleep 1

if [ "$ENABLE_RVIZ" = "1" ]; then
  if [ -f "$RVIZ_CONFIG" ]; then
    dexec_bg "rviz" "rviz2 -d /scripts/$(basename "$RVIZ_CONFIG")"
  else
    echo "[rviz] sin config guardada; abre en blanco (Fixed Frame manual = odom)"
    dexec_bg "rviz" "rviz2"
  fi
fi

# ---------------------------------------------------------------------------
# 6) MASTER (host, venv del robot)
# ---------------------------------------------------------------------------
log_head "Arrancando MASTER (host)"
rm -f "$MASTER_SOCK"
(
  cd "$REPO_DIR" || exit 1
  # shellcheck disable=SC1091
  source "$VENV_MASTER/bin/activate"
  export GO2_AES_KEY
  exec python3 scripts/go2_master.py
) > "$RUN_DIR/master.log" 2>&1 &
HOST_PIDS+=("$!")
echo "[master] lanzado -> $RUN_DIR/master.log"

# darle tiempo a conectar WebRTC antes de abrir YOLO (que depende del buzon)
sleep 6

# ---------------------------------------------------------------------------
# 7) YOLO (host, venv yolo)
# ---------------------------------------------------------------------------
if [ "$ENABLE_YOLO" = "1" ]; then
  log_head "Arrancando YOLO viewer (host)"
  (
    cd "$SCRIPTS_DIR" || exit 1
    # shellcheck disable=SC1091
    source "$VENV_YOLO/bin/activate"
    exec python3 yolo_viewer.py
  ) > "$RUN_DIR/yolo.log" 2>&1 &
  HOST_PIDS+=("$!")
  echo "[yolo] lanzado -> $RUN_DIR/yolo.log"
fi

# ---------------------------------------------------------------------------
# Resumen + monitor en pantalla
# ---------------------------------------------------------------------------
{
  echo "==== RESUMEN DE CORRIDA $STAMP ===="
  echo "stack: docker($DOCKER_IMAGE) + lidar + odom + tf + rviz=$ENABLE_RVIZ + master + yolo=$ENABLE_YOLO"
  echo "teleop: NO (se corre aparte)"
  echo "logs por componente en: $RUN_DIR"
  echo "------------------------------------"
} | tee "$RESUMEN"

log_head "TODO ARRIBA — Ctrl+C para cerrar todo limpio"
echo "Teleop (aparte, otra terminal):"
echo "  source $VENV_MASTER/bin/activate && python3 $SCRIPTS_DIR/teleop_client.py"
echo
echo "Mostrando logs en vivo (etiquetados). Los archivos quedan limpios para analisis."
echo

# monitor: tail de todos los logs, con etiqueta por linea SOLO en pantalla
( tail -n +1 -F "$RUN_DIR"/*.log 2>/dev/null \
    | sed -u -e "s#^==> .*/\([a-z]*\)\.log <==#\n--- \1 ---#" ) &
HOST_PIDS+=("$!")

# esperar (el trap hace el cleanup al Ctrl+C)
wait
