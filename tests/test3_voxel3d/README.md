# Test 3 — Voxel 3D + Teleop

Acumulación de **nube de puntos 3D** (voxel) en coordenadas del mundo, visualizada en RViz.
Sin cámara. La salida 3D se activa con `ENABLE_CLOUD3D=1` (lo fija el lanzador).

## Qué entrega

- Nube de puntos **3D** (`sensor_msgs/PointCloud2`) que se acumula en RViz, anclada al
  frame `odom`.
- Teleoperación en paralelo.

## Arquitectura del flujo

```
Go2 (WebRTC) --nube LiDAR--> go2_master.py
   (xyz en coords del mundo, ya calculados en el callback)
        |  downsample a CLOUD_MAX_PTS = 5000
        v
   UDP 5007 --> cloud_ros_publisher.py (Docker) --acumula--> /cloud (PointCloud2, frame=odom)
   UDP 5006 --> odom_ros_publisher.py  (Docker) --> /odom + TF
                                                        |
                                                      RViz (rviz_voxel3d.rviz)
```

El maestro ya calcula los puntos en coordenadas del mundo para el pipeline 2D; el parche
`patch_cloud3d.py` reaprovecha esos mismos `xyz`, los **downsamplea** a 5000 puntos (para
mantenerlos ligeros y dentro de un datagrama UDP) y los manda al puerto 5007. El publisher
los acumula y los publica como nube 3D. No usa `slam_toolbox`.

## Archivos que usa

- `scripts/go2_launch_voxel3d.sh` — lanzador dedicado (Docker + odom + tf + cloud + RViz).
- `scripts/go2_master.py` — manda la nube 3D por UDP 5007 cuando `ENABLE_CLOUD3D=1`.
- `scripts/cloud_ros_publisher.py` — UDP 5007 → PointCloud2 `/cloud`.
- `scripts/odom_ros_publisher.py` — `/odom` + TF.
- `scripts/rviz_voxel3d.rviz` — vista RViz (Fixed Frame `odom`, display PointCloud2).
- `scripts/teleop_client.py` — teleoperación.

## Cómo lanzarla

**Terminal 1 — stack voxel 3D:**
```bash
export GO2_AES_KEY="<tu_key>"
export LIDAR_DECODE_EVERY_N=4   # opcional, más ligero (requiere patch_skip_decode aplicado)
bash ~/go2-ros2-webrtc-bridge/scripts/go2_launch_voxel3d.sh
```

**Terminal 2 — teleop:**
```bash
source ~/go2_legacy_env/bin/activate
python3 ~/go2-ros2-webrtc-bridge/scripts/teleop_client.py
```

## Qué observar

- La nube 3D se acumula en RViz con el robot como referencia (Fixed Frame `odom`).
- La densidad está limitada a propósito (`CLOUD_MAX_PTS = 5000`) para que sea ligera; es una
  vista "poco densa pero decente", no una reconstrucción de máxima resolución.

## Parámetros relevantes

| Variable | Dónde | Efecto |
|---|---|---|
| `ENABLE_CLOUD3D=1` | lo fija el lanzador | habilita el envío de la nube 3D a UDP 5007 |
| `CLOUD_MAX_PTS=5000` | `go2_master.py` | tope de puntos por frame (downsample) |
| `LIDAR_DECODE_EVERY_N=4` | entorno (opcional) | decodea 1 de cada 4 frames del LiDAR |

## Limitación

Voxel 3D **con cámara** encendida satura el event loop y degrada el teleop; por eso este
test corre sin cámara. El cuello es arquitectónico (canal único), no de CPU.

## Limpieza previa

```bash
pkill -9 -f "go2_master.py" ; pkill -9 -f "teleop_client" ; pkill -9 -f "go2_launch" ; \
docker rm -f go2_ros 2>/dev/null ; rm -f /tmp/go2_master.sock ; sleep 2 ; echo "LIMPIO"
```
