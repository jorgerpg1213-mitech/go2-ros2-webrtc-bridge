#!/usr/bin/env bash
# test4_audio_bidir — master de audio bidireccional + video + teleop + YOLO.
# Graba WAV crudo del mic del robot en runs/audio_captures/.
# Requiere: export GO2_AES_KEY="tu_key"  | auriculares en la PC (evita feedback).
# Teleop aparte: source ~/go2_legacy_env/bin/activate && python3 scripts/teleop_client.py
set -u
REPO_DIR="$HOME/go2-ros2-webrtc-bridge"
SCRIPTS_DIR="$REPO_DIR/scripts"
VENV_MASTER="${VENV_MASTER:-$HOME/go2_legacy_env}"
VENV_YOLO="${VENV_YOLO:-$HOME/go2-yolo}"
MASTER_SOCK="/tmp/go2_master.sock"
ENABLE_YOLO="${ENABLE_YOLO:-1}"
RUN_DIR="$REPO_DIR/runs/$(date +%Y-%m-%d_%H-%M-%S)_audio"
die() { echo "ERROR: $*" >&2; exit 1; }
[ -n "${GO2_AES_KEY:-}" ] || die "GO2_AES_KEY no exportada. Corre: export GO2_AES_KEY=\"tu_key\""
[ -f "$SCRIPTS_DIR/go2_master_audio_bidir_test.py" ] || die "Falta go2_master_audio_bidir_test.py"
[ -f "$VENV_MASTER/bin/activate" ] || die "No existe venv master: $VENV_MASTER"
if [ "$ENABLE_YOLO" = "1" ]; then
  [ -f "$VENV_YOLO/bin/activate" ] || die "No existe venv YOLO: $VENV_YOLO"
fi
mkdir -p "$RUN_DIR"
HOST_PIDS=()
cleanup() { echo ""; echo "Cerrando..."; pkill -f "go2_master_audio_bidir_test.py" 2>/dev/null; pkill -f "yolo_viewer.py" 2>/dev/null; for p in "${HOST_PIDS[@]:-}"; do kill "$p" 2>/dev/null; done; rm -f "$MASTER_SOCK"; echo "Listo."; }
trap cleanup INT TERM EXIT
echo "==> MASTER audio bidireccional"
rm -f "$MASTER_SOCK"
( cd "$REPO_DIR" || exit 1; source "$VENV_MASTER/bin/activate"; export GO2_AES_KEY; export OMP_NUM_THREADS=1; exec python3 scripts/go2_master_audio_bidir_test.py ) > "$RUN_DIR/master.log" 2>&1 &
HOST_PIDS+=("$!")
echo "[master] -> $RUN_DIR/master.log"
sleep 6
if [ "$ENABLE_YOLO" = "1" ]; then
  echo "==> YOLO"
  ( cd "$REPO_DIR" || exit 1; source "$VENV_YOLO/bin/activate"; exec python3 scripts/yolo_viewer.py ) > "$RUN_DIR/yolo.log" 2>&1 &
  HOST_PIDS+=("$!")
  echo "[yolo] -> $RUN_DIR/yolo.log"
fi
echo ""; echo "stack arriba: audio + video + yolo=$ENABLE_YOLO"; echo "logs: $RUN_DIR"
echo "Teleop en otra terminal: source $VENV_MASTER/bin/activate && python3 $SCRIPTS_DIR/teleop_client.py"
echo "Ctrl+C cierra todo."
wait
