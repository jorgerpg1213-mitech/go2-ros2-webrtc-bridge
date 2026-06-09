"""
cloud_ros_publisher.py — Go2 Pro :: Mapa voxel
DOCKER side: UDP (5007) -> acumula voxeles -> sensor_msgs/PointCloud2 en /cloud

La nube llega YA en coordenadas del mundo (frame odom): el master manda
xyz = origin_global + indice*resolucion. Por eso se publica directamente en
frame 'odom' y RViz NO le aplica ninguna TF -> los puntos quedan clavados en
el mundo y el mapa se ACUMULA (no se arrastra con el robot).

Acumulacion: se cuantizan los puntos a una rejilla (VOXEL_SIZE) y se guardan
las celdas ocupadas en un set (dedup). Cada celda = un voxel. Se publica el
mapa completo acumulado a PUBLISH_HZ. Tope MAX_VOXELS para no crecer infinito.

Formato UDP (little-endian): header "<II" (seq, num_points) + num_points*3 float32.
"""

import socket
import struct
import threading
import numpy as np

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import PointCloud2, PointField
from std_msgs.msg import Header

UDP_HOST   = "0.0.0.0"
UDP_PORT   = 5007

VOXEL_SIZE = 0.05      # m — tamaño de celda para dedup/voxelizado (0.05 = fino, 0.10 = mas solido/ligero)
PUBLISH_HZ = 2.0       # Hz — cada cuanto se republica el mapa acumulado (conservador para 1a prueba)
MAX_VOXELS = 150000    # tope de celdas acumuladas (proteccion de memoria; conservador para 1a prueba)
FRAME_ID   = "odom"    # los puntos ya vienen en coords del mundo
# Empaque de celda (ix,iy,iz) -> clave int64 unica. _BIAS hace no-negativas las
# coords; _BASE separa los tres ejes sin colision (rango +-_BIAS celdas por eje).
_BIAS = 100000
_BASE = 2 * _BIAS + 1


class CloudPublisher(Node):

    def __init__(self):
        super().__init__('go2_cloud')
        self.pub = self.create_publisher(PointCloud2, '/cloud', 5)

        # Acumulador INCREMENTAL: set de claves de voxel empacadas (int) + lista de
        # coords solo de celdas NUEVAS. Costo por paquete ~ O(puntos del paquete),
        # INDEPENDIENTE del tamaño del mapa (no re-unique global cada vez).
        self._keys   = set()          # claves de celda ocupada (int64 empacado)
        self._coords = []             # lista de arrays (k,3) float32 — solo celdas nuevas
        self._count  = 0
        self._cache  = None           # array publicable, se reconstruye solo si _dirty
        self._lock   = threading.Lock()
        self._dirty  = False

        self._running = True
        self._thread  = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

        self.create_timer(1.0 / PUBLISH_HZ, self._publish)
        self.get_logger().info(
            f"Escuchando UDP {UDP_HOST}:{UDP_PORT} — /cloud frame={FRAME_ID} "
            f"voxel={VOXEL_SIZE}m pub={PUBLISH_HZ}Hz")

    def _recv_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((UDP_HOST, UDP_PORT))
        sock.settimeout(1.0)
        hdr = struct.calcsize("<II")
        while self._running:
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception as e:
                self.get_logger().error(f"UDP: {e}")
                continue
            if len(data) < hdr:
                continue
            seq, n = struct.unpack("<II", data[:hdr])
            body = data[hdr:]
            if len(body) != n * 12:
                # datagrama truncado/raro — descartar (no fabricar datos)
                continue
            pts = np.frombuffer(body, dtype=np.float32).reshape(-1, 3)
            if pts.shape[0] == 0:
                continue
            # cuantizar a celdas (int32), empacar a clave int64 (vectorizado) y dedup
            # DENTRO del paquete con np.unique (operacion chica, solo este paquete).
            cells = np.round(pts / VOXEL_SIZE).astype(np.int64)
            packed = ((cells[:, 0] + _BIAS) * _BASE + (cells[:, 1] + _BIAS)) * _BASE \
                     + (cells[:, 2] + _BIAS)
            upacked, first_idx = np.unique(packed, return_index=True)
            with self._lock:
                if self._count < MAX_VOXELS:
                    # membership contra el set (incremental, ~O(celdas del paquete))
                    new_mask = np.fromiter(
                        (k not in self._keys for k in upacked.tolist()),
                        dtype=bool, count=upacked.shape[0])
                    if new_mask.any():
                        new_packed = upacked[new_mask]
                        new_cells  = cells[first_idx][new_mask]
                        # PM #3: respetar el tope ESTRICTAMENTE — recortar a remaining
                        remaining = MAX_VOXELS - self._count
                        if new_packed.shape[0] > remaining:
                            new_packed = new_packed[:remaining]
                            new_cells  = new_cells[:remaining]
                        new_coords = new_cells.astype(np.float32) * VOXEL_SIZE
                        self._keys.update(new_packed.tolist())
                        self._coords.append(new_coords)
                        self._count += new_coords.shape[0]
                        self._dirty = True
        sock.close()

    def _publish(self):
        with self._lock:
            if self._count == 0:
                return
            if self._dirty:
                # reconstruir el array publicable solo cuando hubo celdas nuevas
                self._cache = np.concatenate(self._coords, axis=0)
                self._coords = [self._cache]   # compactar la lista
                self._dirty = False
            coords = self._cache

        msg = PointCloud2()
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = FRAME_ID
        msg.height = 1
        msg.width  = coords.shape[0]
        msg.fields = [
            PointField(name='x', offset=0,  datatype=PointField.FLOAT32, count=1),
            PointField(name='y', offset=4,  datatype=PointField.FLOAT32, count=1),
            PointField(name='z', offset=8,  datatype=PointField.FLOAT32, count=1),
        ]
        msg.is_bigendian = False
        msg.point_step   = 12
        msg.row_step     = 12 * coords.shape[0]
        msg.is_dense     = True
        msg.data         = coords.astype(np.float32).tobytes()
        self.pub.publish(msg)

    def destroy_node(self):
        self._running = False
        super().destroy_node()


def main():
    rclpy.init()
    node = CloudPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
