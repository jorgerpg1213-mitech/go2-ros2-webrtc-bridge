# Go2 ROS2 WebRTC Bridge

ROS2 integration pipeline for Unitree Go2 using WebRTC transport.

## Current Capabilities

- WebRTC connection to Unitree Go2
- UDP bridge architecture
- ROS2 LaserScan publishing
- ROS2 Odometry publishing
- TF tree integration:
  - odom → base_link
  - base_link → laser
- RViz visualization
- Runtime audit tools for topic inspection and timing analysis

## Architecture

HOST:
- WebRTC client
- Unitree topic subscription
- UDP forwarding

ROS2 Container:
- UDP receivers
- ROS2 publishers
- TF broadcasters
- RViz visualization

## Important Findings

The topic:

rt/utlidar/voxel_map_compressed

does not currently behave like a realtime sensor-frame LiDAR scan.
Evidence suggests it represents a spatially integrated or world-stabilized voxel map.

Infrastructure validation is complete, but semantic LiDAR topic auditing remains ongoing.

## Repository Structure

scripts/
- ROS2 publishers
- WebRTC senders

audits/
- Runtime audit tools
- Topic inspection utilities

docs/
- Historical notes and original references

## Status

Experimental research repository.
ROS2 infrastructure operational.
Realtime LiDAR semantics under investigation.

