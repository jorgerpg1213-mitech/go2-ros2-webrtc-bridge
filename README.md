# Go2 Pro — ROS2 WebRTC Bridge
## Unitree Go2 Pro :: Deterministic Runtime Engineering Platform

Real-time teleoperation and sensor pipeline for the Unitree Go2 Pro quadruped robot.  
Single WebRTC session — LiDAR + Odometry + Teleop over one connection.

Built with runtime engineering discipline: lifecycle-correct asyncio, deterministic teardown,
freshness policy, and clean reconnect semantics.

---

## Architecture

```
HOST
┌─────────────────────────────────────────────┐
│  go2_master.py                              │
│                                             │
│  WebRTC (única sesión — LocalAP)            │
│  ├── ULIDAR_ARRAY → UDP 5005 → /scan        │
│  ├── ROBOTODOM   → UDP 5006 → /odom + TF    │
│                                             │
│  ConnectionMonitor (1Hz)                    │
│  ├── lidar timeout detection >2s            │
│  ├── odom timeout detection >2s             │
│  └── lifecycle state management             │
│                                             │
│  IPC Unix Socket /tmp/go2_master.sock       │
│  └── JSON command receiver                  │
│                                             │
│  publish_loop 20Hz                          │
│  ├── cmd_queue consumer                     │
│  ├── _stop_immediate flag                   │
│  └── SPORT_MOD → Move / StopMove            │
│                                             │
│  ReconnectManager — auto 15s delay          │
│  └── stale queue flush on each reconnect    │
└─────────────────────────────────────────────┘
          ↑ JSON IPC
┌─────────────────────────────────────────────┐
│  teleop_client.py                           │
│  pynput → key press/release → JSON → socket │
│  deadman watchdog 0.5s                      │
└─────────────────────────────────────────────┘

DOCKER (go2_ros2)
├── lidar_ros_publisher.py   UDP 5005 → /scan
├── odom_ros_publisher.py    UDP 5006 → /odom + TF
└── static_transform_publisher  base_link → laser
```

---

## Runtime Stack

### T1 — LiDAR ROS Publisher
```bash
docker run --rm -it --name go2_ros2 --network host \
  -v ~/go2-ros2-webrtc-bridge/scripts:/scripts \
  osrf/ros:humble-desktop bash -c \
  "source /opt/ros/humble/setup.bash && python3 /scripts/lidar_ros_publisher.py"
```

### T2 — Odometry ROS Publisher
```bash
docker exec -it go2_ros2 bash -c \
  "source /opt/ros/humble/setup.bash && python3 /scripts/odom_ros_publisher.py"
```

### T3 — Static Transform Publisher
```bash
docker exec -it go2_ros2 bash -c \
  "source /opt/ros/humble/setup.bash && \
   ros2 run tf2_ros static_transform_publisher \
   --frame-id base_link --child-frame-id laser"
```

### T4 — go2_master.py (Host)
```bash
source ~/go2_legacy_env/bin/activate && \
export GO2_AES_KEY="5a22d44799557573192d8c2b54da0c1a" && \
python3 ~/go2-ros2-webrtc-bridge/scripts/go2_master.py
```

### T5 — teleop_client.py (Host — esperar BalanceStand en T4)
```bash
source ~/go2_legacy_env/bin/activate && \
python3 ~/go2-ros2-webrtc-bridge/scripts/teleop_client.py
```

### T6 — RViz
```bash
xhost +local:docker && \
docker exec -it -e DISPLAY=$DISPLAY go2_ros2 bash -c \
  "source /opt/ros/humble/setup.bash && rviz2"
```

**RViz config:**
- Fixed Frame → `odom`
- Add → LaserScan → topic `/scan`
- Add → TF

---

## Teleop Controls

| Key   | Action         | Speed      |
|-------|----------------|------------|
| W / ↑ | Forward        | 0.5 m/s    |
| S / ↓ | Backward       | -0.4 m/s   |
| A / ← | Turn left      | 1.2 rad/s  |
| D / → | Turn right     | -1.2 rad/s |
| Q     | Strafe left    | 0.3 m/s    |
| E     | Strafe right   | -0.3 m/s   |
| SPACE | Emergency stop | —          |
| ESC   | Exit           | —          |

Deadman watchdog: 0.5s sin input → StopMove automático.

---

## Lifecycle States

| State         | Description                         |
|---------------|-------------------------------------|
| INITIALIZING  | Process startup                     |
| CONNECTING    | WebRTC negotiation                  |
| CONNECTED     | Data flowing                        |
| DEGRADED      | LiDAR or ODOM timeout detected      |
| DISCONNECTED  | WebRTC dropped — awaiting reconnect |
| SHUTDOWN      | Clean deterministic teardown        |

---

## Runtime Engineering — Fixes Aplicados

Esta sección documenta el trabajo de auditoría y hardening del runtime asyncio.
El sistema pasó de "funcional" a "determinísticamente robusto" mediante fixes quirúrgicos
aplicados bajo metodología: síntomas → auditoría → diagnóstico → fix dirigido → validación.

### Problema A — Lifecycle asyncio incompleto

**Fix 1 — CRÍTICO: pub_task lifecycle completo**

Antes:
```python
pub_task.cancel()
await conn.close()  # race condition — SCTP flush sobre transport muerto
```

Después:
```python
pub_task.cancel()
try:
    await pub_task          # esperar terminación limpia
except asyncio.CancelledError:
    pass
try:
    await conn.close()      # transport libre — sin race condition
except Exception:
    pass
```

`publish_loop` ahora tiene `try/except CancelledError/finally` completo:
```python
try:
    while ...:
        ...
except asyncio.CancelledError:
    pass
finally:
    log("SPORT", "publish_loop terminado")
```

**Evidencia directa antes del fix:** `Task exception was never retrieved` + `Cannot send data, not connected` en logs de runtime.

---

**Fix 2 — ALTO: ConnectionMonitor.stop() con await**

Antes:
```python
def stop(self) -> None:
    if self._task:
        self._task.cancel()  # cancel sin await — task zombie garantizado
```

Después:
```python
async def stop(self) -> None:
    if self._task:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
```

Call sites actualizados a `await _monitor.stop()` en shutdown.

---

### Problema B — Locomotion Freshness inexistente

**Fix 3 — ALTO: Vaciar cmd_queue entre reconnects**

La queue se crea una sola vez y sobrevive los reconnects. Comandos del ciclo anterior
causaban locomotion residual inmediata al reconectar.

```python
# Antes de crear pub_task en cada ciclo de reconnect:
while not cmd_queue.empty():
    try:
        cmd_queue.get_nowait()
    except asyncio.QueueEmpty:
        break
log("RECONNECT", "cmd_queue vaciada")
```

---

### Pending — Fix 6 (bloqueado hasta validación runtime)

Freshness watchdog en `publish_loop` — independiente del cliente.
`last_cmd` actualmente se republica indefinidamente si el cliente muere.
Fix 6 NO se aplica hasta que Fix 1 esté validado con el robot en runtime.

---

## Interacción entre problemas (documentada)

```
reconnect sucio (Problema A)
    → comandos stale en queue (Problema B)
    → locomotion residual post-reconnect
    → complica siguiente reconnect
    → degradación progresiva observable
```

---

## Validated Runtime (Fase 1)

| Criterio                    | Resultado              |
|-----------------------------|------------------------|
| Single WebRTC session       | ✅ Confirmado — LocalAP |
| /scan publishing            | ✅ ~6-8 Hz estable      |
| /odom publishing            | ✅ ~17-22 Hz estable    |
| TF odom→base_link           | ✅ Confirmado           |
| RViz visualization          | ✅ Confirmado visual    |
| Teleop IPC                  | ✅ Funcional            |
| Robot locomotion WASD       | ✅ Operacional          |
| Auto-reconnect              | ✅ 15s delay            |
| Asyncio lifecycle fixes     | 🔄 Aplicados — pendiente validación runtime con robot |

---

## Stack

| Component           | Technology                       |
|---------------------|----------------------------------|
| Robot SDK           | unitree_webrtc_connect (LocalAP) |
| ROS2                | Humble — Docker osrf/ros:humble  |
| Host Python         | 3.10 + go2_legacy_env            |
| LiDAR transport     | UDP 127.0.0.1:5005               |
| Odometry transport  | UDP 127.0.0.1:5006               |
| Teleop IPC          | Unix Socket /tmp/go2_master.sock |
| Visualization       | RViz2 (Docker)                   |

---

## Firmware Quirks — Go2 Pro

| Quirk                        | Observación                                        |
|------------------------------|----------------------------------------------------|
| data2=3                      | AES key mandatory — firmware 1.1.x diverge del README oficial |
| BalanceStand requerido       | Sin él el robot mueve torso pero no levanta patas  |
| WebRTC session drops ~50-60s | Comportamiento confirmado — también en scripts históricos |
| ICE sensible a multi-homing  | Sin filtro 192.168.12.* todos los candidate pairs fallan |
| LocalAP requerido            | LocalSTA falla — robot no expone IP en ese modo    |
| pynput listener timing       | Iniciar DESPUÉS de conn.connect() — antes bloquea ICE |
| Move es setpoint continuo    | No es comando discreto — requiere loop 20Hz        |
| CycloneDDS/Ethernet          | No confirmado en Pro — funciona en EDU con Jetson interno |

---

## Architectural Decisions (ADRs)

**ADR-001 — Filtro ICE como monkey patch local**  
Filtrar candidatos ICE en cada script individualmente. No contaminar la librería instalada.

**ADR-002 — pynput sobre cv2.waitKey**  
cv2.waitKey no detecta key release real. Sin key release el robot no para al soltar tecla.

**ADR-003 — Estado continuo 20Hz sobre modelo pulse**  
Move es velocity setpoint continuo. Pulse model producía comportamiento errático confirmado.

**ADR-004 — listener.start() después de conn.connect()**  
Evidencia experimental directa — listener antes de connect bloqueaba ICE negotiation.

**ADR-005 — AES key como variable de entorno**  
Repo público — no exponer credenciales de dispositivo.

**ADR-006 — Single WebRTC session como master bridge**  
Go2 Pro acepta máximo 2 conexiones WebRTC. Arquitectura bridge evita conflictos de slots.

**ADR-007 — Docker naming fijo --name go2_ros2**  
`$(docker ps -q)` es frágil con múltiples containers. Nombre fijo elimina ambigüedad.

---

## Repository Structure

```
scripts/    — runtime principal
              go2_master.py        WebRTC bridge maestro
              teleop_client.py     Teleop IPC pynput
              lidar_ros_publisher.py  UDP 5005 → /scan
              odom_ros_publisher.py   UDP 5006 → /odom + TF
              lidar_sender.py      Legacy — referencia histórica
              odom_sender.py       Legacy — referencia histórica

audits/     — herramientas de diagnóstico
              lidar_audit.py       Inspección de topics LiDAR
              lidar_inspect.py     Análisis de payload voxel
              pose_audit.py        Auditoría de odometría

docs/       — referencia histórica
```

---

## Project Status

| Área                   | Estado                                              |
|------------------------|-----------------------------------------------------|
| WebRTC base            | ✅ Estable                                          |
| ROS2 bridge            | ✅ Estable                                          |
| LiDAR pipeline         | ✅ Estable (~6-8 Hz — límite firmware)              |
| Odometry pipeline      | ✅ Estable (~17-22 Hz)                              |
| TF tree                | ✅ Estable                                          |
| Teleop IPC             | ✅ Funcional                                        |
| Asyncio lifecycle      | 🔄 Fix 1/2/3 aplicados — pendiente validación robot |
| Freshness watchdog     | ⏳ Fix 6 pendiente validación lifecycle             |
| Launch orchestration   | ⏳ Post runtime stabilization                       |
| Jetson edge node       | 🔭 Roadmap futuro                                  |
| Zenoh remote bridge    | 🔭 Roadmap futuro                                  |
| SLAM / Nav2            | 🔭 Roadmap futuro                                  |

---

## Roadmap

| Etapa | Descripción | Estado |
|-------|-------------|--------|
| 1 | Pipeline sensorial base — LiDAR + ODOM | ✅ Completada |
| 2 | Integración ROS2 + RViz | ✅ Completada |
| 3 | Arquitectura teleoperación IPC | ✅ Completada |
| 4 | Auditoría runtime asyncio/WebRTC | ✅ Completada |
| 5 | Runtime stabilization — fixes lifecycle + freshness | 🔄 En progreso |
| 6 | Teleop operacional estable — sesiones largas sin degradación | ⏳ Pendiente |
| 7 | Integración navegación ROS2 — cmd_vel bridge, Nav2, SLAM | ⏳ Futuro |
| 8 | Plataforma experimental avanzada — Jetson edge, Zenoh, Isaac | ⏳ Futuro |

---

*Unitree Go2 Pro — Deterministic Runtime Engineering*  
*ROS2 Humble + WebRTC + asyncio*  
*Single-Session WebRTC Bridge for Real-Time ROS2 Teleoperation and Sensor Fusion on Quadruped Robots*
