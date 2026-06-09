# Test 2 — SLAM 2D + Teleop

Mapeo **2D externo** con `slam_toolbox`, construido a partir del LaserScan derivado de la
nube del LiDAR. Sin cámara (se apaga para liberar el event loop).

## Qué entrega

- Mapa de ocupación **2D** que se acumula en tiempo real en RViz conforme se explora.
- Teleoperación en paralelo.
- TF `odom -> base_link` viva, de modo que el mapa queda anclado al mundo.

## Arquitectura del flujo

```
Go2 (WebRTC) --nube LiDAR--> go2_master.py --proyección 2D + binning angular-->
   UDP 5005 --> lidar_ros_publisher.py (Docker) --> /scan (LaserScan)
   UDP 5006 --> odom_ros_publisher.py  (Docker) --> /odom + TF
                                                       |
                                          slam_toolbox --> mapa 2D --> RViz
```

El maestro toma los puntos del LiDAR, los expresa en el frame del robot, los proyecta a 2D
y los reduce a *bins* angulares (mínimo rango por bin) antes de mandarlos como LaserScan.
Esto descarga trabajo del lado de ROS 2. `slam_toolbox` hace el scan-matching y arma el mapa.

## Archivos que usa

- `scripts/go2_launch.sh` — lanzador (Docker + nodos ROS 2 + RViz).
- `scripts/go2_master.py` — manda LaserScan (UDP 5005) y odom (UDP 5006).
- `scripts/lidar_ros_publisher.py`, `scripts/odom_ros_publisher.py` — nodos ROS 2.
- `scripts/slam_params.yaml` — parámetros de slam_toolbox.
- `scripts/rviz_go2.rviz` — vista RViz (mapa 2D).
- `scripts/teleop_client.py` — teleoperación.

## Cómo lanzarla

**Terminal 1 — stack (cámara y YOLO apagados):**
```bash
export GO2_AES_KEY="<tu_key>"
ENABLE_CAMERA=0 ENABLE_YOLO=0 ENABLE_LIDAR=1 ENABLE_ODOM=1 \
  bash ~/go2-ros2-webrtc-bridge/scripts/go2_launch.sh
```

**Terminal 2 — teleop:**
```bash
source ~/go2_legacy_env/bin/activate
python3 ~/go2-ros2-webrtc-bridge/scripts/teleop_client.py
```

## Parámetro opcional

Si el patch `patch_skip_decode.py` está aplicado, puede añadirse
`LIDAR_DECODE_EVERY_N=4` al inicio del comando de la Terminal 1 para aligerar el decode.

## Qué observar

- El mapa 2D se dibuja y cierra paredes en RViz a medida que avanzas.
- Avanzar **despacio** mejora el scan-matching y el cierre del mapa.

## Limpieza previa

```bash
pkill -9 -f "go2_master.py" ; pkill -9 -f "teleop_client" ; pkill -9 -f "go2_launch" ; \
docker rm -f go2_ros 2>/dev/null ; rm -f /tmp/go2_master.sock ; sleep 2 ; echo "LIMPIO"
```
