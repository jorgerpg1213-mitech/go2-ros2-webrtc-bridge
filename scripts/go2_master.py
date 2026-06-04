"""
go2_master.py — Go2 Master Bridge
Fase 2: IPC activo + SPORT_MOD teleop

SDK: unitree_webrtc_connect (patrón validado de lidar_sender.py + teleop_video2.py)
"""

import asyncio
import json
import struct
import math
import numpy as np
import os
import signal
import socket
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto

from unitree_webrtc_connect.webrtc_driver import UnitreeWebRTCConnection, WebRTCConnectionMethod
from unitree_webrtc_connect.constants import RTC_TOPIC, SPORT_CMD
from aiortc import MediaStreamTrack

# Camara opcional: si cv2 no esta o no hay entorno grafico, se desactiva sola
# sin tumbar el bridge. El video se sigue consumiendo igual (keepalive de sesion).
try:
    import cv2
    _CV2_AVAILABLE = True
except Exception as _cv2_err:
    _CV2_AVAILABLE = False
    print("[CAMERA] cv2 no disponible (" + str(_cv2_err) + "); ventana desactivada", flush=True)

# Export de frames a YOLO por memoria compartida (proceso separado). Import
# protegido: si frame_ipc no esta, el bridge corre igual sin export.
try:
    from frame_ipc import FrameWriter
    _FRAME_IPC_AVAILABLE = True
except Exception as _ipc_err:
    _FRAME_IPC_AVAILABLE = False
    print("[YOLO] frame_ipc no disponible (" + str(_ipc_err) + "); export desactivado", flush=True)

# Fix ICE — crítico si hay ethernet conectado además del robot
import aioice.ice as _aioice_ice
_orig = _aioice_ice.get_host_addresses
_aioice_ice.get_host_addresses = lambda use_ipv4, use_ipv6: [
    ip for ip in _orig(use_ipv4, use_ipv6) if ip.startswith("192.168.12.")
]

# ─── Constantes ───────────────────────────────────────────────────────────────

# Switch de diagnostico. True = reactiva LiDAR/ODOM (necesario para SLAM/RViz).
# False = no se suscribe a sensores; aisla la locomocion para descartar que el
# procesamiento de LiDAR este ahogando el event loop. Flipear a True restaura todo.
ENABLE_SENSORS = True

# Camara: True muestra el streaming en una ventana (como teleop_video2.py).
# El stream se consume siempre (keepalive); este switch solo controla la ventana.
ENABLE_CAMERA = True

# Export de frames al proceso de YOLO via memoria compartida.
# False = master se comporta identico al auditado (esta ruta no se ejecuta).
# True = en run_display (hilo main) se copia el frame mas reciente a un buzon
# de memoria compartida que yolo_viewer.py lee desde otro proceso/venv.
ENABLE_YOLO_EXPORT = True

# Heartbeat de estado en consola cada N segundos (observabilidad/auditoria).
# Sube el valor para menos ruido; pon 0 para desactivarlo.
STATUS_INTERVAL_S = 5.0

LIDAR_UDP_HOST = "127.0.0.1"
LIDAR_UDP_PORT = 5005
ODOM_UDP_HOST  = "127.0.0.1"
ODOM_UDP_PORT  = 5006

IPC_SOCKET_PATH = "/tmp/go2_master.sock"

AES_KEY = os.environ.get("GO2_AES_KEY", None)

LIDAR_TIMEOUT_S    = 2.0
ODOM_TIMEOUT_S     = 2.0
MONITOR_INTERVAL_S = 1.0

SPORT_MOD_PUBLISH_HZ = 20
SPORT_MOD_INTERVAL   = 1.0 / SPORT_MOD_PUBLISH_HZ

# Throttle de repeticiones de Move (anti-saturacion del canal de comandos).
# True  = manda Move al INSTANTE cuando cambia tu tecla, y repite el MISMO
#         comando solo a CMD_REFRESH_HZ (no a 20Hz). Respuesta a la tecla sigue
#         siendo inmediata; solo se reduce la repeticion inutil. Trafico ~2.5x menor.
# False = comportamiento viejo (Move continuo a 20Hz). Para comparar A/B.
# StopMove y el freshness-watchdog NO cambian (siguen tal cual, son seguridad).
THROTTLE_REPEATS = True
CMD_REFRESH_HZ   = 8            # repeticion del MISMO comando; ajustable (6-12)
CMD_REFRESH_INTERVAL = 1.0 / CMD_REFRESH_HZ

CMD_QUEUE_MAXSIZE = 20
CMD_FRESHNESS_TIMEOUT_S = 0.5  # stale cmd → StopMove

_ZERO_CMD = {"vx": 0.0, "vy": 0.0, "wz": 0.0}
_stop_immediate = False

# ─── Perfilado de latencia (SOLO medicion; NO altera la logica de control) ────
# False = master identico al actual. True = acumula tiempos y los reporta en
# lineas [PROF] cada STATUS_INTERVAL_S (junto al heartbeat), sin floodear el log.
ENABLE_PROFILING = True
_prof = {"q_lag_ms": [], "send_ms": [], "loop_dt_ms": [], "lidar_cb_ms": [], "odom_cb_ms": []}
_prof_counts = {"move_sent": 0, "move_skipped": 0}
# Peso del LiDAR que llega por WiFi (proxy de carga del canal): puntos crudos por scan.
_lidar_pts_sum = 0
_lidar_scans = 0

def _prof_report() -> None:
    """Imprime min/avg/max de cada metrica acumulada y limpia. Lo llama status_loop."""
    if not ENABLE_PROFILING:
        return
    for k, vals in _prof.items():
        if vals:
            lo = min(vals); hi = max(vals); avg = sum(vals) / len(vals)
            log("PROF", f"{k:10s} n={len(vals):4d}  min={lo:6.1f}  avg={avg:6.1f}  max={hi:6.1f}  (ms)")
            vals.clear()
    if _prof_counts["move_sent"] or _prof_counts["move_skipped"]:
        log("PROF", f"moves      sent={_prof_counts['move_sent']:4d}  "
                    f"skipped={_prof_counts['move_skipped']:4d}  (throttle)")
        _prof_counts["move_sent"] = 0
        _prof_counts["move_skipped"] = 0
    global _lidar_pts_sum, _lidar_scans
    if _lidar_scans > 0:
        pts_scan = _lidar_pts_sum / _lidar_scans
        pts_s = _lidar_pts_sum / STATUS_INTERVAL_S
        kbs = pts_s * 12.0 / 1024.0   # proxy: 3 float32 (x,y,z) por punto, decodificado
        log("PROF", f"lidar_load pts/scan={pts_scan:7.0f}  pts/s={pts_s:8.0f}  ~{kbs:7.1f} KB/s (proxy)")
        _lidar_pts_sum = 0
        _lidar_scans = 0

# Buffer de un solo frame (siempre el mas reciente) para la ventana de camara.
# CLAVE (videofix): el event loop solo DEPOSITA el frame CRUDO (sin decodificar);
# la decodificacion pesada (to_ndarray) la hace el hilo main en run_display.
# Asi el decode NO le quita tiempo al lazo de control. Se descartan frames
# viejos => minima latencia y locomocion protegida.
_frame_lock = threading.Lock()
_raw_frame = [None]       # frame CRUDO (av.VideoFrame) mas reciente, sin decodificar
_display_seq = 0          # sube con cada frame nuevo (senal para run_display)
_frame_count = 0          # total recibidos (para fps en STATUS)

# ─── Lifecycle ────────────────────────────────────────────────────────────────

class LifecycleState(Enum):
    INITIALIZING = auto()
    CONNECTING   = auto()
    CONNECTED    = auto()
    DEGRADED     = auto()
    DISCONNECTED = auto()
    SHUTDOWN     = auto()

# ─── Estado ───────────────────────────────────────────────────────────────────

@dataclass
class PoseState:
    x:  float = 0.0
    y:  float = 0.0
    z:  float = 0.0
    qx: float = 0.0
    qy: float = 0.0
    qz: float = 0.0
    qw: float = 1.0

@dataclass
class RobotState:
    connection_state: LifecycleState = LifecycleState.INITIALIZING

    scan_seq:     int   = 0
    last_scan_ts: float = 0.0
    lidar_ok:     bool  = False

    odom_seq:     int   = 0
    last_odom_ts: float = 0.0
    last_pose:    PoseState = field(default_factory=PoseState)
    odom_ok:      bool  = False

    ipc_seq:      int   = 0

state = RobotState()

# ─── StructuredLogger ─────────────────────────────────────────────────────────

_last_error_log: dict[str, float] = {}

_RUN_T0 = time.monotonic()   # marca de inicio de corrida (para sello t=MM:SS)

def _elapsed_tag() -> str:
    s = int(time.monotonic() - _RUN_T0)
    return f"{s // 60:02d}:{s % 60:02d}"

def log(tag: str, msg: str) -> None:
    print(f"[t={_elapsed_tag()}] [{tag}] {msg}", flush=True)

def log_error_rate_limited(category: str, msg: str) -> None:
    now = time.monotonic()
    if now - _last_error_log.get(category, 0.0) >= 1.0:
        _last_error_log[category] = now
        log("ERROR", f"[{category}] {msg}")

def set_lifecycle(new_state: LifecycleState, reason: str = "") -> None:
    if state.connection_state == new_state:
        return
    state.connection_state = new_state
    suffix = f"  reason={reason}" if reason else ""
    log("LIFECYCLE", f"state={new_state.name}{suffix}")

# ─── UDP sockets ──────────────────────────────────────────────────────────────

_udp_lidar = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_udp_odom  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def udp_send(sock: socket.socket, data: bytes, host: str, port: int, label: str) -> None:
    try:
        sock.sendto(data, (host, port))
    except Exception as e:
        log_error_rate_limited(f"udp_{label}", str(e))

# ─── Callbacks WebRTC — trabajo MÍNIMO ────────────────────────────────────────

# Constantes LiDAR — mismas que lidar_sender.py validado
_LIDAR_RESOLUTION = 0.05
_LIDAR_Z_IDX_MIN  = 15
_LIDAR_Z_IDX_MAX  = 23
_LIDAR_ANGLE_MIN  = -math.pi
_LIDAR_ANGLE_MAX  =  math.pi
_LIDAR_RANGE_MIN  = 0.1
_LIDAR_RANGE_MAX  = 10.0
_LIDAR_NUM_BINS   = 360

def lidar_callback(message: dict) -> None:
    _t0_lidar = time.monotonic()
    try:
        d      = message["data"]
        origin = np.array(d["origin"], dtype=np.float32)
        pts    = d["data"]["positions"].reshape(-1, 3).astype(np.float32)
    except Exception as e:
        log_error_rate_limited("lidar_parse", str(e))
        return

    mask   = (pts[:, 2] >= _LIDAR_Z_IDX_MIN) & (pts[:, 2] <= _LIDAR_Z_IDX_MAX)
    pts_2d = pts[mask]
    if len(pts_2d) == 0:
        return

    # Peso del LiDAR (proxy de carga del canal WiFi): puntos crudos recibidos.
    global _lidar_pts_sum, _lidar_scans
    _lidar_pts_sum += pts.shape[0]
    _lidar_scans += 1

    xyz    = origin + pts_2d * _LIDAR_RESOLUTION
    x, y   = xyz[:, 0], xyz[:, 1]
    angles = np.arctan2(y, x)
    ranges = np.sqrt(x**2 + y**2)

    angle_res = (_LIDAR_ANGLE_MAX - _LIDAR_ANGLE_MIN) / _LIDAR_NUM_BINS
    bins = np.full(_LIDAR_NUM_BINS, np.inf, dtype=np.float32)
    # Vectorizado (reemplaza el for-loop Python que ahogaba el event loop).
    # Semantica identica al loop original: descarta fuera de rango, indexa por
    # angulo y guarda el minimo range por bin. Formato del paquete UDP intacto.
    valid = (ranges >= _LIDAR_RANGE_MIN) & (ranges <= _LIDAR_RANGE_MAX)
    idx = ((angles[valid] - _LIDAR_ANGLE_MIN) / angle_res).astype(np.int64)
    rng = ranges[valid]
    in_bins = (idx >= 0) & (idx < _LIDAR_NUM_BINS)
    np.minimum.at(bins, idx[in_bins], rng[in_bins])
    bins[bins == np.inf] = 0.0

    state.scan_seq    += 1
    state.last_scan_ts = time.monotonic()

    header  = struct.pack("IIffff", state.scan_seq, _LIDAR_NUM_BINS,
                          _LIDAR_ANGLE_MIN, _LIDAR_ANGLE_MAX,
                          _LIDAR_RANGE_MIN, _LIDAR_RANGE_MAX)
    payload = header + bins.tobytes()
    udp_send(_udp_lidar, payload, LIDAR_UDP_HOST, LIDAR_UDP_PORT, "lidar")
    if ENABLE_PROFILING:
        _prof["lidar_cb_ms"].append((time.monotonic() - _t0_lidar) * 1000.0)

def odom_callback(message: dict) -> None:
    _t0_odom = time.monotonic()
    try:
        d   = message["data"]
        hdr = d.get("header", {}).get("stamp", {})
        sec    = int(hdr.get("sec", 0))
        nanosec = int(hdr.get("nanosec", 0))
        pose = d.get("pose", {})
        pos  = pose.get("position", {})
        ori  = pose.get("orientation", {})
        x  = float(pos.get("x", 0.0))
        y  = float(pos.get("y", 0.0))
        z  = float(pos.get("z", 0.0))
        qx = float(ori.get("x", 0.0))
        qy = float(ori.get("y", 0.0))
        qz = float(ori.get("z", 0.0))
        qw = float(ori.get("w", 1.0))
    except Exception as e:
        log_error_rate_limited("odom_parse", str(e))
        return

    state.odom_seq    += 1
    state.last_odom_ts = time.monotonic()
    state.last_pose = PoseState(x=x, y=y, z=z, qx=qx, qy=qy, qz=qz, qw=qw)

    payload = struct.pack("<IIfffffff", sec, nanosec, x, y, z, qx, qy, qz, qw)
    udp_send(_udp_odom, payload, ODOM_UDP_HOST, ODOM_UDP_PORT, "odom")
    if ENABLE_PROFILING:
        _prof["odom_cb_ms"].append((time.monotonic() - _t0_odom) * 1000.0)

# ─── Video / Camara ──────────────────────────────────────────────────────────

async def _video_handler(track: MediaStreamTrack) -> None:
    """Consume el stream de video en el event loop. DOS funciones:
      1) Keepalive de la sesion WebRTC: drenar el track evita que el robot cierre
         la conexion por inactividad del canal de media. CRITICO (vs teleop_video2).
      2) Si ENABLE_CAMERA, decodifica a BGR y deja SOLO el frame mas reciente en el
         buffer; el dibujado lo hace run_display en el hilo main (GUI fuera del loop).
    Drena a maxima velocidad y descarta frames viejos => minima latencia."""
    global _frame_count, _display_seq
    while True:
        try:
            frame = await track.recv()
        except Exception:
            break  # track cerrado por reconexion o shutdown
        _frame_count += 1
        if not ENABLE_CAMERA:
            continue  # solo keepalive
        # videofix: NO decodificar aqui (eso ahogaba el lazo de control).
        # Solo dejamos el frame CRUDO; run_display (hilo main) lo decodifica.
        with _frame_lock:
            _raw_frame[0] = frame
            _display_seq += 1


def run_display() -> None:
    """Ventana de camara. Corre en el hilo MAIN (cv2/Qt lo exigen). El event loop
    vive en otro hilo, asi la GUI NO le quita tiempo a locomocion ni sensores.
    Muestra siempre el frame mas reciente del buffer (descarta atrasados)."""
    if not (ENABLE_CAMERA and _CV2_AVAILABLE):
        # Sin ventana: el hilo main solo espera el shutdown para no morir.
        while not _shutdown_event.is_set():
            time.sleep(0.2)
        return
    window = "Go2 Master - Camara"
    last_seq = -1
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    try:
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        cv2.imshow(window, blank)
        cv2.waitKey(1)
    except Exception as e:
        log("CAMERA", f"no se pudo abrir ventana ({e}); corriendo sin display")
        while not _shutdown_event.is_set():
            time.sleep(0.2)
        return
    log("CAMERA", "ventana de camara activa (hilo main) — ESC/q o cerrar para salir")
    yolo_writer = None
    if ENABLE_YOLO_EXPORT and _FRAME_IPC_AVAILABLE:
        try:
            yolo_writer = FrameWriter("go2_cam")
            log("CAMERA", "export a YOLO activo (memoria compartida 'go2_cam')")
        except Exception as e:
            log_error_rate_limited("yolo_export_init", str(e))
    shown = 0
    while not _shutdown_event.is_set():
        with _frame_lock:
            seq = _display_seq
            raw = _raw_frame[0]
        if raw is not None and seq != last_seq:
            # videofix: la decodificacion pesada ocurre AQUI (hilo main), no en el loop
            try:
                frame = raw.to_ndarray(format="bgr24")
            except Exception as e:
                log_error_rate_limited("video_decode", str(e))
                frame = None
            if frame is not None:
                try:
                    cv2.imshow(window, frame)
                    shown += 1
                    last_seq = seq
                except Exception as e:
                    log_error_rate_limited("camera_show", str(e))
                if yolo_writer is not None:
                    try:
                        yolo_writer.write(frame, seq)
                    except Exception as e:
                        log_error_rate_limited("yolo_export", str(e))
        key = cv2.waitKey(10) & 0xFF
        if key in (27, ord('q')):          # ESC o q
            _shutdown_event.set()
            break
        try:
            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                _shutdown_event.set()       # cerraron la ventana con la X
                break
        except Exception:
            break
    try:
        cv2.destroyAllWindows()
        cv2.waitKey(1)
    except Exception:
        pass
    if yolo_writer is not None:
        try:
            yolo_writer.close()
        except Exception:
            pass
    log("CAMERA", f"display terminado  frames_mostrados={shown}")


async def status_loop() -> None:
    """Heartbeat de estado en consola cada STATUS_INTERVAL_S segundos. Util para
    debug y auditoria: estado de conexion, tasa de LiDAR/ODOM y fps de camara."""
    if STATUS_INTERVAL_S <= 0:
        return
    prev_scan = prev_odom = prev_frames = 0
    while state.connection_state != LifecycleState.SHUTDOWN:
        await asyncio.sleep(STATUS_INTERVAL_S)
        scan_hz = (state.scan_seq  - prev_scan)   / STATUS_INTERVAL_S
        odom_hz = (state.odom_seq  - prev_odom)   / STATUS_INTERVAL_S
        cam_fps = (_frame_count    - prev_frames) / STATUS_INTERVAL_S
        prev_scan, prev_odom, prev_frames = state.scan_seq, state.odom_seq, _frame_count
        log("STATUS",
            f"conn={state.connection_state.name}  "
            f"lidar={scan_hz:4.1f}hz  odom={odom_hz:4.1f}hz  cam={cam_fps:4.1f}fps")
        _prof_report()

# ─── ConnectionMonitor ────────────────────────────────────────────────────────

class ConnectionMonitor:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._prev_scan_seq = 0
        self._prev_odom_seq = 0

    def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(self._loop())

    async def _loop(self) -> None:
        while state.connection_state not in (LifecycleState.SHUTDOWN,):
            await asyncio.sleep(MONITOR_INTERVAL_S)
            self.tick()

    def tick(self) -> None:
        if not ENABLE_SENSORS:
            return  # sin sensores no hay nada que monitorear
        now = time.monotonic()

        scan_delta = state.scan_seq - self._prev_scan_seq
        odom_delta = state.odom_seq - self._prev_odom_seq
        self._prev_scan_seq = state.scan_seq
        self._prev_odom_seq = state.odom_seq

        lidar_elapsed = now - state.last_scan_ts if state.last_scan_ts else float("inf")
        odom_elapsed  = now - state.last_odom_ts if state.last_odom_ts else float("inf")

        lidar_timeout = lidar_elapsed > LIDAR_TIMEOUT_S
        odom_timeout  = odom_elapsed  > ODOM_TIMEOUT_S

        state.lidar_ok = not lidar_timeout
        state.odom_ok  = not odom_timeout

        if lidar_timeout:
            log("MONITOR", f"lidar_timeout  elapsed={lidar_elapsed:.1f}s")
        if odom_timeout:
            log("MONITOR", f"odom_timeout   elapsed={odom_elapsed:.1f}s")

        # Logs periodicos LIDAR/ODOM silenciados para prueba de locomocion
        # if scan_delta > 0:
        #     log("LIDAR", f"seq={state.scan_seq}  hz_est={scan_delta / MONITOR_INTERVAL_S:.1f}")
        # if odom_delta > 0:
        #     log("ODOM",  f"seq={state.odom_seq}  hz_est={odom_delta / MONITOR_INTERVAL_S:.1f}")

        if state.connection_state == LifecycleState.CONNECTED:
            if lidar_timeout or odom_timeout:
                reason = "lidar_timeout" if lidar_timeout else "odom_timeout"
                set_lifecycle(LifecycleState.DEGRADED, reason)
        elif state.connection_state == LifecycleState.DEGRADED:
            if not lidar_timeout and not odom_timeout:
                set_lifecycle(LifecycleState.CONNECTED)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

# ─── IPC Server ───────────────────────────────────────────────────────────────

class IPCServer:
    def __init__(self, cmd_queue: asyncio.Queue) -> None:
        self._queue  = cmd_queue
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        if os.path.exists(IPC_SOCKET_PATH):
            os.unlink(IPC_SOCKET_PATH)
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=IPC_SOCKET_PATH
        )
        log("IPC", f"server listening  path={IPC_SOCKET_PATH}")

    async def _handle_client(self, reader: asyncio.StreamReader,
                              writer: asyncio.StreamWriter) -> None:
        state.ipc_seq += 1
        log("IPC", f"client_connected   seq={state.ipc_seq}")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                await self._dispatch(line.strip())
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as e:
            log_error_rate_limited("ipc_read", str(e))
        finally:
            try:
                writer.close()
            except Exception:
                pass
            log("IPC", "client_disconnected")

    async def _dispatch(self, raw: bytes) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as e:
            log_error_rate_limited("ipc_json", str(e))
            return

        msg_type = msg.get("type")

        if msg_type == "teleop_cmd":
            cmd = {
                "vx": float(msg.get("vx", 0.0)),
                "vy": float(msg.get("vy", 0.0)),
                "wz": float(msg.get("wz", 0.0)),
            }
            if ENABLE_PROFILING:
                cmd["_ts_in"] = time.monotonic()
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await self._queue.put(cmd)

        elif msg_type == "stop":
            global _stop_immediate
            _stop_immediate = True
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            await self._queue.put(_ZERO_CMD.copy())

        else:
            log("IPC", f"unknown_type  type={msg_type}")

    def stop(self) -> None:
        if self._server:
            self._server.close()
        if os.path.exists(IPC_SOCKET_PATH):
            try:
                os.unlink(IPC_SOCKET_PATH)
            except Exception:
                pass

# ─── SPORT_MOD publish loop ───────────────────────────────────────────────────

async def publish_loop(conn: UnitreeWebRTCConnection, cmd_queue: asyncio.Queue) -> None:
    log("SPORT", "publish_loop started  hz=20  mode=continuous")
    last_cmd = _ZERO_CMD.copy()
    was_moving = False
    last_cmd_ts = time.monotonic()  # freshness — master es dueño del clock
    _prof_loop_prev = time.monotonic()  # perfilado: marca de la vuelta anterior
    last_sent = _ZERO_CMD.copy()        # ultimo Move realmente enviado al robot
    last_sent_ts = 0.0                  # cuando se envio (para refresco lento)
    try:
        while state.connection_state not in (LifecycleState.SHUTDOWN,):
            global _stop_immediate
            if _stop_immediate:
                _stop_immediate = False
                try:
                    await conn.datachannel.pub_sub.publish_request_new(
                        RTC_TOPIC["SPORT_MOD"],
                        {"api_id": SPORT_CMD["StopMove"]},
                    )
                    last_cmd = _ZERO_CMD.copy()
                    last_cmd_ts = time.monotonic()
                    was_moving = False
                except Exception as e:
                    log_error_rate_limited("sport_stop_imm", str(e))
                await asyncio.sleep(0)
                continue
            await asyncio.sleep(SPORT_MOD_INTERVAL)
            if ENABLE_PROFILING:
                _now = time.monotonic()
                _prof["loop_dt_ms"].append((_now - _prof_loop_prev) * 1000.0)
                _prof_loop_prev = _now
            while not cmd_queue.empty():
                try:
                    last_cmd = cmd_queue.get_nowait()
                    last_cmd_ts = time.monotonic()  # cmd fresco — actualizar timestamp
                    if ENABLE_PROFILING and "_ts_in" in last_cmd:
                        _prof["q_lag_ms"].append((last_cmd_ts - last_cmd["_ts_in"]) * 1000.0)
                except asyncio.QueueEmpty:
                    break
            moving = not (last_cmd["vx"] == 0.0 and last_cmd["vy"] == 0.0 and last_cmd["wz"] == 0.0)
            # Freshness watchdog — master es dueño del clock
            if moving and (time.monotonic() - last_cmd_ts) > CMD_FRESHNESS_TIMEOUT_S:
                if was_moving:
                    try:
                        elapsed = time.monotonic() - last_cmd_ts
                        await conn.datachannel.pub_sub.publish_request_new(
                            RTC_TOPIC["SPORT_MOD"],
                            {"api_id": SPORT_CMD["StopMove"]},
                        )
                        log("SPORT", f"freshness_timeout elapsed={elapsed:.2f}s — StopMove enviado")
                    except Exception as e:
                        log_error_rate_limited("sport_fresh_stop", str(e))
                last_cmd = _ZERO_CMD.copy()
                was_moving = False
                continue
            if moving:
                # Throttle anti-saturacion: manda al INSTANTE si el comando cambio,
                # y si es el MISMO, solo lo repite cada CMD_REFRESH_INTERVAL (no 20Hz).
                changed = (last_cmd["vx"] != last_sent["vx"] or
                           last_cmd["vy"] != last_sent["vy"] or
                           last_cmd["wz"] != last_sent["wz"])
                due = (time.monotonic() - last_sent_ts) >= CMD_REFRESH_INTERVAL
                if (not THROTTLE_REPEATS) or changed or due:
                    try:
                        _t_send = time.monotonic()
                        await conn.datachannel.pub_sub.publish_request_new(
                            RTC_TOPIC["SPORT_MOD"],
                            {
                                "api_id": SPORT_CMD["Move"],
                                "parameter": {
                                    "x": last_cmd["vx"],
                                    "y": last_cmd["vy"],
                                    "z": last_cmd["wz"],
                                },
                            },
                        )
                        last_sent = {"vx": last_cmd["vx"], "vy": last_cmd["vy"], "wz": last_cmd["wz"]}
                        last_sent_ts = time.monotonic()
                        if ENABLE_PROFILING:
                            _prof["send_ms"].append((time.monotonic() - _t_send) * 1000.0)
                            _prof_counts["move_sent"] += 1
                    except Exception as e:
                        log_error_rate_limited("sport_pub", str(e))
                elif ENABLE_PROFILING:
                    _prof_counts["move_skipped"] += 1
            elif was_moving and not moving:
                # StopMove solo en transicion movimiento -> cero
                try:
                    await conn.datachannel.pub_sub.publish_request_new(
                        RTC_TOPIC["SPORT_MOD"],
                        {"api_id": SPORT_CMD["StopMove"]},
                    )
                except Exception as e:
                    log_error_rate_limited("sport_stop", str(e))
            was_moving = moving
    except asyncio.CancelledError:
        pass
    finally:
        log("SPORT", "publish_loop terminado")
async def send_stop_move(conn: UnitreeWebRTCConnection) -> None:
    try:
        await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"],
            {"api_id": SPORT_CMD["StopMove"]},
        )
    except Exception as e:
        log_error_rate_limited("sport_stop", str(e))

# ─── Shutdown ─────────────────────────────────────────────────────────────────

_monitor: ConnectionMonitor | None = None
_ipc: IPCServer | None = None
_shutdown_event = threading.Event()

def handle_sigint(sig, frame) -> None:
    print("\n", flush=True)
    _shutdown_event.set()

async def shutdown(conn: UnitreeWebRTCConnection) -> None:
    set_lifecycle(LifecycleState.SHUTDOWN)

    await send_stop_move(conn)
    log("SHUTDOWN", "StopMove enviado")

    if _ipc:
        _ipc.stop()
        log("SHUTDOWN", "IPC socket cerrado")

    if _monitor:
        await _monitor.stop()
        log("SHUTDOWN", "ConnectionMonitor detenido")

    try:
        _udp_lidar.close()
        _udp_odom.close()
        log("SHUTDOWN", "UDP sockets cerrados")
    except Exception:
        pass

    log("LIFECYCLE", "state=SHUTDOWN")

# ─── Main ─────────────────────────────────────────────────────────────────────

RECONNECT_DELAY_S = 15.0  # espera entre reconexiones

async def main() -> None:
    global _monitor, _ipc

    # IPC y monitor se crean una sola vez
    cmd_queue: asyncio.Queue = asyncio.Queue(maxsize=CMD_QUEUE_MAXSIZE)
    _ipc = IPCServer(cmd_queue)
    await _ipc.start()

    _monitor = ConnectionMonitor()
    _monitor.start()

    # Observabilidad: heartbeat de estado (la camara corre en el hilo main)
    asyncio.create_task(status_loop())
    _cam_state = "ON" if (ENABLE_CAMERA and _CV2_AVAILABLE) else "OFF"
    _sen_state = "ON" if ENABLE_SENSORS else "OFF"
    log("INIT", "camara=" + _cam_state + "  sensores=" + _sen_state)

    while not _shutdown_event.is_set():
        set_lifecycle(LifecycleState.CONNECTING)
        conn = UnitreeWebRTCConnection(
            WebRTCConnectionMethod.LocalAP,
            aes_128_key=AES_KEY,
        )
        try:
            await conn.connect()
        except Exception as e:
            log_error_rate_limited("webrtc_connect", str(e))
            log("RECONNECT", f"reintentando en {RECONNECT_DELAY_S}s")
            await asyncio.sleep(RECONNECT_DELAY_S)
            continue

        set_lifecycle(LifecycleState.CONNECTED)
        if ENABLE_SENSORS:
            conn.datachannel.pub_sub.subscribe(RTC_TOPIC["ULIDAR_ARRAY"], lidar_callback)
            conn.datachannel.pub_sub.subscribe(RTC_TOPIC["ROBOTODOM"],    odom_callback)
            log("INIT", "subscribed  topics=[ULIDAR_ARRAY, ROBOTODOM]")
        else:
            log("INIT", "SENSORS OFF (ENABLE_SENSORS=False) — sin LiDAR/ODOM, modo diagnostico")
        # Video / Camara — el baseline teleop_video2.py mantiene la sesion viva
        # consumiendo el stream RTP. Sin esto la sesion WebRTC se cierra sola.
        try:
            conn.video.switchVideoChannel(True)
            conn.video.add_track_callback(_video_handler)
            modo = "streaming+keepalive" if ENABLE_CAMERA else "keepalive"
            log("INIT", "video activo (" + modo + ")")
        except Exception as e:
            log_error_rate_limited("video", str(e))
        # BalanceStand — activa gait controller antes de aceptar comandos
        await asyncio.sleep(1.0)
        try:
            await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["SPORT_MOD"],
                {"api_id": SPORT_CMD["BalanceStand"]},
            )
            log("INIT", "BalanceStand enviado")
            await asyncio.sleep(2.0)
        except Exception as e:
            log_error_rate_limited("balance_stand", str(e))

        # Vaciar queue stale antes de arrancar nuevo publish_loop
        while not cmd_queue.empty():
            try:
                cmd_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        log("RECONNECT", "cmd_queue vaciada")
        pub_task = asyncio.get_event_loop().create_task(publish_loop(conn, cmd_queue))

        # Esperar hasta que la conexión caiga o shutdown
        while not _shutdown_event.is_set():
            if state.connection_state == LifecycleState.DISCONNECTED:
                break
            # Detectar caída por peer connection closed
            try:
                pc_state = conn.pc.connectionState
            except Exception:
                pc_state = "closed"
            if pc_state == "closed" or pc_state == "failed":
                log("RECONNECT", f"conexion caida (state={pc_state}) — reconectando en {RECONNECT_DELAY_S}s")
                break
            await asyncio.sleep(1.0)

        pub_task.cancel()
        try:
            await pub_task
        except asyncio.CancelledError:
            pass
        try:
            await conn.close()
        except Exception:
            pass

        if _shutdown_event.is_set():
            break

        await asyncio.sleep(RECONNECT_DELAY_S)

    await shutdown_final()

async def shutdown_final() -> None:
    set_lifecycle(LifecycleState.SHUTDOWN)
    if _ipc:
        _ipc.stop()
        log("SHUTDOWN", "IPC socket cerrado")
    if _monitor:
        await _monitor.stop()
        log("SHUTDOWN", "ConnectionMonitor detenido")
    try:
        _udp_lidar.close()
        _udp_odom.close()
        log("SHUTDOWN", "UDP sockets cerrados")
    except Exception:
        pass
    log("LIFECYCLE", "state=SHUTDOWN")

def _run_bridge() -> None:
    """Corre el bridge asyncio (WebRTC + IPC + sensores + locomocion) en un hilo
    aparte, dejando el hilo MAIN libre para la GUI de cv2."""
    worker_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(worker_loop)
    try:
        worker_loop.run_until_complete(main())
    except Exception as e:
        log_error_rate_limited("bridge", str(e))
    finally:
        try:
            pendientes = asyncio.all_tasks(worker_loop)
            for t in pendientes:
                t.cancel()
            if pendientes:
                worker_loop.run_until_complete(
                    asyncio.gather(*pendientes, return_exceptions=True))
        except Exception:
            pass
        worker_loop.close()


if __name__ == "__main__":
    # SIGINT y cv2 deben vivir en el hilo MAIN. El bridge va en un hilo worker.
    signal.signal(signal.SIGINT, handle_sigint)
    _bridge = threading.Thread(target=_run_bridge, name="go2-bridge", daemon=True)
    _bridge.start()
    try:
        run_display()              # bloquea en el hilo main hasta el shutdown
    except KeyboardInterrupt:
        _shutdown_event.set()
    finally:
        _shutdown_event.set()
        _bridge.join(timeout=6.0)
