# Go2 Pro — ROS2 WebRTC Bridge
## Unitree Go2 Pro :: Runtime Engineering Platform

Real-time teleoperation and sensor pipeline for the Unitree Go2 Pro quadruped robot.
Single WebRTC session — LiDAR + Odometry + Teleop over one connection.

---

## Architecture

```
HOST
┌─────────────────────────────────────────────┐
│  go2_master.py                              │
│  WebRTC (única sesión — LocalAP)            │
│  ├── ULIDAR_ARRAY → UDP 5005 → /scan        │
│  ├── ROBOTODOM   → UDP 5006 → /odom + TF    │
│  ConnectionMonitor (1Hz)                    │
│  IPC Unix Socket /tmp/go2_master.sock       │
│  publish_loop 20Hz → SPORT_MOD              │
│  ReconnectManager — auto 15s delay          │
└─────────────────────────────────────────────┘
↑ JSON IPC
┌─────────────────────────────────────────────┐
│  teleop_client.py                           │
│  pynput → key press/release → JSON → socket │
└─────────────────────────────────────────────┘
DOCKER (go2_ros2)
├── lidar_ros_publisher.py   UDP 5005 → /scan
├── odom_ros_publisher.py    UDP 5006 → /odom + TF
└── static_transform_publisher  base_link → laser
```

---

## Runtime Stack

T1 — LiDAR ROS Publisher
docker run --rm -it --name go2_ros2 --network host -v ~/go2-ros2-webrtc-bridge/scripts:/scripts osrf/ros:humble-desktop bash -c "source /opt/ros/humble/setup.bash && python3 /scripts/lidar_ros_publisher.py"

T2 — Odometry ROS Publisher
docker exec -it go2_ros2 bash -c "source /opt/ros/humble/setup.bash && python3 /scripts/odom_ros_publisher.py"

T3 — Static Transform Publisher
docker exec -it go2_ros2 bash -c "source /opt/ros/humble/setup.bash && ros2 run tf2_ros static_transform_publisher --frame-id base_link --child-frame-id laser"

T4 — go2_master.py (Host)
source ~/go2_legacy_env/bin/activate && export GO2_AES_KEY="5a22d44799557573192d8c2b54da0c1a" && python3 ~/go2-ros2-webrtc-bridge/scripts/go2_master.py

T5 — teleop_client.py (Host — esperar BalanceStand en T4)
source ~/go2_legacy_env/bin/activate && python3 ~/go2-ros2-webrtc-bridge/scripts/teleop_client.py

T6 — RViz
xhost +local:docker && docker exec -it -e DISPLAY=$DISPLAY go2_ros2 bash -c "source /opt/ros/humble/setup.bash && rviz2"

---

## Teleop Controls

| Key   | Action        | Speed      |
|-------|---------------|------------|
| W / ↑ | Forward       | 0.5 m/s    |
| S / ↓ | Backward      | -0.4 m/s   |
| A / ← | Turn left     | 1.2 rad/s  |
| D / → | Turn right    | -1.2 rad/s |
| Q     | Strafe left   | 0.3 m/s    |
| E     | Strafe right  | -0.3 m/s   |
| SPACE | Emergency stop| —          |
| ESC   | Exit          | —          |

Deadman watchdog: 0.5s sin input → stop automático.

---

## Lifecycle States

| State         | Description                          |
|---------------|--------------------------------------|
| INITIALIZING  | Process startup                      |
| CONNECTING    | WebRTC negotiation                   |
| CONNECTED     | Data flowing                         |
| DEGRADED      | LiDAR or ODOM timeout                |
| DISCONNECTED  | WebRTC dropped — awaiting reconnect  |
| SHUTDOWN      | Clean teardown                       |

---

## Stack

| Component          | Technology                          |
|--------------------|-------------------------------------|
| Robot SDK          | unitree_webrtc_connect (LocalAP)    |
| ROS2               | Humble — Docker osrf/ros:humble     |
| Host Python        | 3.10 + go2_legacy_env               |
| LiDAR transport    | UDP 127.0.0.1:5005                  |
| Odometry transport | UDP 127.0.0.1:5006                  |
| Teleop IPC         | Unix Socket /tmp/go2_master.sock    |
| Visualization      | RViz2 (Docker)                      |

---

## Firmware Quirks

| Quirk                        | Notes                              |
|------------------------------|------------------------------------|
| data2=3                      | AES key mandatory — firmware 1.1.x |
| BalanceStand required        | Without it robot moves torso only  |
| WebRTC session drops ~50-60s | Confirmed firmware behavior        |
| ICE multi-homing             | Filter to 192.168.12.* required    |
| LocalAP required             | LocalSTA fails                     |

---

## Project Status

| Area                  | Status                                        |
|-----------------------|-----------------------------------------------|
| WebRTC base           | ✅ Stable                                     |
| ROS2 bridge           | ✅ Stable                                     |
| LiDAR pipeline        | ✅ Stable                                     |
| Odometry pipeline     | ✅ Stable                                     |
| TF tree               | ✅ Stable                                     |
| Teleop IPC            | ✅ Functional                                 |
| Asyncio lifecycle     | 🔄 Fix 1/2/3 applied — pending runtime validation |
| Freshness watchdog    | ⏳ Fix 6 pending lifecycle validation         |
| Launch orchestration  | ⏳ Post runtime stabilization                 |

---

*Unitree Go2 Pro — Runtime Engineering*
*ROS2 Humble + WebRTC + asyncio*
