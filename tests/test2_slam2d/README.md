# Test 2 — SLAM 2D + Teleop

Mapa 2D (slam_toolbox) acumulado en RViz. Sin camara.

## Terminal 1
    export GO2_AES_KEY="<tu_key>"
    ENABLE_CAMERA=0 ENABLE_YOLO=0 ENABLE_LIDAR=1 ENABLE_ODOM=1 \
      bash ~/go2-ros2-webrtc-bridge/scripts/go2_launch.sh

## Terminal 2 (teleop)
    source ~/go2_legacy_env/bin/activate
    python3 ~/go2-ros2-webrtc-bridge/scripts/teleop_client.py

Archivos: scripts/go2_launch.sh, go2_master.py, lidar_ros_publisher.py, odom_ros_publisher.py, slam_params.yaml, rviz_go2.rviz, teleop_client.py
