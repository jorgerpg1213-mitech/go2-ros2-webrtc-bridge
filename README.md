# go2-ros2-webrtc-bridge

Puente entre un robot **Unitree Go2 Pro** y **ROS 2 (Humble)** sobre el canal **WebRTC**
del robot, con teleoperación, video con detección **YOLO**, y dos modalidades de mapeo
externo (**SLAM 2D** y **nube de puntos voxel 3D**).

Todo corre desde una laptop sobre la red local del robot (LocalAP, `192.168.12.1`),
sin Jetson, sin Expansion Dock y sin LiDAR externo.

---

## 1. Plataforma

| Componente | Detalle |
|---|---|
| Robot | Unitree Go2 **Pro** (LiDAR L1 interno, sin Expansion Dock) |
| Enlace | WebRTC directo (LocalAP `192.168.12.1`) |
| Host | Laptop Ubuntu 22.04, Python 3.10 |
| ROS 2 | Humble, dentro de contenedor Docker `osrf/ros:humble-desktop` (`--network host`) |
| Venv maestro | `~/go2_legacy_env` (conexión + master) |
| Venv YOLO | `~/go2-yolo` (detección aislada) |

La clave de cifrado (`GO2_AES_KEY`) **nunca** se versiona: se lee del entorno y está
bloqueada por `.gitignore`.

---

## 2. Arquitectura

### 2.1 Una sola conexión, un solo event loop

El robot Go2 admite **una única sesión WebRTC simultánea**. Toda la telemetría
(video, LiDAR, odometría) y todos los comandos (teleop) viajan por **ese mismo canal**,
procesados por **un solo event loop asyncio en un solo hilo**.

```
                 +-----------------------------+
   Go2 Pro  <----+   go2_master.py (1 hilo)     +----> UDP 5005  scan 2D   -> ROS2
  (WebRTC)  ---->+   - recibe video/lidar/odom  +----> UDP 5006  odom+TF   -> ROS2
                 |   - manda comandos teleop    +----> UDP 5007  nube 3D   -> ROS2
                 |   - escribe frame a IPC      +----> shared mem -> YOLO (otro venv)
                 +-----------------------------+
```

Consecuencia de diseño: **no hay paralelismo real dentro de la conexión**. Si una tarea
pesada ocupa el event loop, todo lo demás (incluido el teleop) espera. Este hecho domina
todo lo que sigue.

### 2.2 Procesos

- **`go2_master.py`** (host, venv maestro): dueño único de la conexión WebRTC. Recibe los
  streams, los reenvía por UDP a los nodos ROS 2, escribe el frame de video a memoria
  compartida para YOLO, y aplica los comandos de teleop que recibe por su socket Unix.
- **`yolo_viewer.py`** (host, venv YOLO): lee el frame por IPC y corre la detección,
  **aislado** del proceso maestro para no contaminar el event loop.
- **Nodos ROS 2** (dentro de Docker): `lidar_ros_publisher.py`, `odom_ros_publisher.py`,
  `cloud_ros_publisher.py`, `slam_toolbox`, `rviz2`.
- **`teleop_client.py`** (host): proceso aparte; manda comandos al maestro por
  `/tmp/go2_master.sock`.

---

## 3. El cuello del teleop (hallazgo central)

Al activar el LiDAR, el teleop se degradaba progresivamente hasta morir. La causa **no**
es falta de CPU.

### 3.1 Diagnóstico

El robot publica la nube comprimida del LiDAR en el topic
`rt/utlidar/voxel_map_compressed` (constante `ULIDAR_ARRAY`). La librería la **descomprime
con un decoder WASM (`libvoxel`)** dentro del *handler de recepción del data channel*
(`webrtc_datachannel.py` → `deal_array_buffer_for_lidar`), es decir **antes** de que el dato
llegue a nuestro callback, y **dentro del event loop**.

Medición clave (perfilado del maestro):

```
loop_dt_ms ... max=14001.7   <- el event loop se bloqueó hasta 14 s
lidar_cb_ms ... avg=8.5      <- nuestro callback es trivial (<10 ms)
pts/scan = 280000+           <- la nube crece al explorar
```

Un decode tarda **< 1 s**, pero el loop llegó a bloquearse **14 s**. Eso **no es costo de
cómputo**: es **congestión del carril único**. El decode pesado, ejecutándose en el mismo
hilo que el teleop, mete a todos los demás eventos en una fila que se desborda. Y empeora
con el tiempo porque la nube crece (215k → 280k+ puntos) conforme el robot explora.

### 3.2 Conclusión sobre hardware

Comprar una CPU con más núcleos **no resuelve** este cuello: el límite es el **diseño de un
solo event loop**, no la potencia. Núcleos extra quedarían ociosos. Lo único que ayudaría
a fondo sería sacar el decode a **otro proceso** (multiprocessing real), lo cual exige
modificar cómo la librería entrega los datos. No se adquirió hardware por esta razón.

---

## 4. Mitigaciones implementadas

### 4.1 `patch_skip_decode.py` — saltar el decode pesado

Parche a `webrtc_datachannel.py` que decodea **1 de cada N** frames del LiDAR. Los frames
saltados **reusan la última nube decodeada** (nunca devuelven `None`, así no rompen los
callbacks de aguas abajo).

- Controlado por la variable de entorno **`LIDAR_DECODE_EVERY_N`** (default `1` = decodea
  todo = comportamiento idéntico al original).
- Respaldo automático (`.skipdecode.bak`); reversible con `--revert`.
- Aborta sin tocar nada si el bloque original no coincide; restaura solo si no compila.

Detalle de por qué funciona: en `pub_sub.py`, `subscribe`/`unsubscribe` mandan mensajes
`SUBSCRIBE`/`UNSUBSCRIBE` reales al robot (no son solo registros locales de callback). El
flujo del LiDAR se controla de raíz; el decode solo ocurre sobre lo que el robot manda.

```bash
python3 scripts/patch_skip_decode.py          # aplicar
python3 scripts/patch_skip_decode.py --revert  # revertir
```

### 4.2 `patch_cloud3d.py` — salida de nube voxel 3D

Parche **aditivo** al maestro. Los puntos 3D en coordenadas del mundo (`xyz`, ya calculados
en el callback del LiDAR) se mandan por **UDP 5007** a `cloud_ros_publisher.py`, que los
acumula y publica como `sensor_msgs/PointCloud2` en `/cloud` (frame `odom`).

- Controlado por **`ENABLE_CLOUD3D`** (default `0` = apagado, no afecta a las otras pruebas).
- **Downsample** a `CLOUD_MAX_PTS = 5000` puntos: mantiene la nube ligera y dentro del
  límite de un datagrama UDP.
- Anclas únicas; respaldo `.precloud3d.bak`; verifica compilación y restaura si falla.

```bash
python3 scripts/patch_cloud3d.py          # aplicar
python3 scripts/patch_cloud3d.py --revert  # revertir
```

---

## 5. SLAM interno del robot: descartado

Se investigó a fondo activar el SLAM **interno** del Go2 (topics `rt/uslam/...`,
`rt/mapping/grid_map`). Hallazgo: el servicio `uslam` que activa el mapeo **solo existe en
la variante EDU con Expansion Dock + LiDAR externo vía Ethernet/DDS**. En el **Pro** sobre
WebRTC, el `server_log` hace eco del comando pero **ningún topic de mapa publica jamás** —
no hay servicio escuchando. Ningún proyecto WebRTC público lo ha logrado en un Pro.

Por eso **todo el mapeo de este repo es externo**: se toma la nube cruda del LiDAR y se
construye el mapa en el host (slam_toolbox para 2D, acumulación de nube para 3D).

Los diagnósticos de esa investigación quedan archivados en `scripts/diag_slam_*.py` como
evidencia, sin uso operativo.

---

## 6. Las tres pruebas

| Test | Entrega | Lanzador | Carpeta |
|---|---|---|---|
| 1 | Cámara + YOLO + teleop | `scripts/go2_run.sh` | `tests/test1_camara_yolo/` |
| 2 | SLAM 2D + teleop | `scripts/go2_launch.sh` | `tests/test2_slam2d/` |
| 3 | Voxel 3D + teleop | `scripts/go2_launch_voxel3d.sh` | `tests/test3_voxel3d/` |
| 4 | Audio bidireccional + YOLO + teleop | `tests/test4_audio_bidir/go2_run_audio.sh` | `tests/test4_audio_bidir/` |

Cada carpeta `tests/` tiene su propio README con comandos exactos. Resumen también en
`pruebas_go2.docx`.

---

## 7. Interruptores del maestro (variables de entorno)

| Variable | Default | Efecto |
|---|---|---|
| `ENABLE_CAMERA` | `1` | Procesa/Muestra el video (y alimenta YOLO). |
| `ENABLE_LIDAR` | `0` | Suscribe la nube del LiDAR y publica scan 2D (UDP 5005). |
| `ENABLE_ODOM` | `0` | Suscribe odometría y publica `/odom` + TF (UDP 5006). |
| `ENABLE_CLOUD3D` | `0` | Manda la nube 3D al publisher (UDP 5007). |
| `LIDAR_DECODE_EVERY_N` | `1` | Decodea 1 de cada N frames del LiDAR (requiere patch). |

Los lanzadores fijan la combinación correcta de cada prueba; no hace falta exportarlas a mano.

---

## 8. Limitación conocida

**Voxel 3D + cámara simultáneos saturan el event loop** y degradan el teleop (video y nube
pesada compiten por el carril único). Por eso las pruebas 2 y 3 corren **sin cámara**.
El cuello es **arquitectónico** (un solo canal/event loop), no de CPU.

---

## 9. Limpieza entre corridas

El robot admite una sola sesión WebRTC; tras muchas conexiones seguidas el enlace puede
quedar en `CONNECTING`. Antes de cada prueba:

```bash
pkill -9 -f "go2_master.py" ; pkill -9 -f "teleop_client" ; pkill -9 -f "yolo" ; \
pkill -9 -f "go2_launch" ; pkill -9 -f "go2_run" ; \
docker rm -f go2_ros 2>/dev/null ; rm -f /tmp/go2_master.sock ; sleep 2 ; echo "LIMPIO"
```

Si el maestro se queda en `CONNECTING`, reiniciar el robot físicamente y verificar con una
conexión mínima antes de relanzar el stack completo.

---

## 10. Estructura del repositorio

```
scripts/
  go2_master.py              Proceso maestro (conexión WebRTC única)
  teleop_client.py           Cliente de teleoperación
  yolo_viewer.py             Detección YOLO (venv aislado)
  frame_ipc.py               IPC de video master -> YOLO
  go2_run.sh                 Lanzador Test 1 (cámara + YOLO)
  go2_launch.sh              Lanzador Test 2 (SLAM 2D)
  go2_launch_voxel3d.sh      Lanzador Test 3 (voxel 3D)
  lidar_ros_publisher.py     UDP 5005 -> LaserScan (Docker)
  odom_ros_publisher.py      UDP 5006 -> /odom + TF (Docker)
  cloud_ros_publisher.py     UDP 5007 -> PointCloud2 /cloud (Docker)
  slam_params.yaml           Parámetros de slam_toolbox
  rviz_go2.rviz              Vista RViz para SLAM 2D
  rviz_voxel3d.rviz          Vista RViz para nube 3D
  patch_skip_decode.py       Mitigación del decode (1 de cada N)
  patch_cloud3d.py           Salida de nube 3D (aditivo)
  diag_slam_*.py             Diagnósticos del SLAM interno (archivados)
tests/
  test1_camara_yolo/         Lanzador + README del Test 1
  test2_slam2d/              Lanzador + README del Test 2
  test3_voxel3d/             Lanzador + config RViz + README del Test 3
  test4_audio_bidir/         Lanzador + README del Test 4 (audio bidireccional)
```
