# Test 3 — Voxel 3D + Teleop

Nube de puntos 3D acumulada en RViz (Fixed Frame odom). Sin camara.
Salida 3D activada con ENABLE_CLOUD3D=1 (lo fija el lanzador).

## Terminal 1
    export GO2_AES_KEY="<tu_key>"
    export LIDAR_DECODE_EVERY_N=4   # opcional, mas ligero
    bash ~/go2-ros2-webrtc-bridge/scripts/go2_launch_voxel3d.sh

## Terminal 2 (teleop)
    source ~/go2_legacy_env/bin/activate
    python3 ~/go2-ros2-webrtc-bridge/scripts/teleop_client.py

Archivos: scripts/go2_launch_voxel3d.sh, rviz_voxel3d.rviz, go2_master.py (ENABLE_CLOUD3D), cloud_ros_publisher.py, odom_ros_publisher.py, teleop_client.py
