#!/usr/bin/env python3
"""
patch_cloud3d.py — Agrega salida de nube 3D (voxel) al master, ADITIVO.

Activa con ENABLE_CLOUD3D=1 (default off => no afecta los otros tests).
Manda los puntos xyz (ya calculados en lidar_callback) al puerto UDP 5007,
donde cloud_ros_publisher.py los acumula y publica como PointCloud2 /cloud.

Incluye downsample (CLOUD_MAX_PTS) para que sea ligero y quepa en un datagrama UDP.

SEGURIDAD:
  - Respalda go2_master.py a .precloud3d.bak antes de tocar.
  - Inserciones con anclas unicas; si alguna no coincide, aborta sin cambios.
  - Idempotente: si ya esta parcheado, no hace nada.
  - Verifica que compila; si no, restaura solo.

Uso:
  python3 patch_cloud3d.py
  python3 patch_cloud3d.py --revert
"""
import sys, os, shutil, py_compile

P = os.path.expanduser("~/go2-ros2-webrtc-bridge/scripts/go2_master.py")
BAK = P + ".precloud3d.bak"
MARKER = "# CLOUD3D PATCH"

# --- Ancla 1: env var (despues de ENABLE_ODOM) ---
A1 = 'ENABLE_ODOM  = os.environ.get("ENABLE_ODOM", "0") != "0"'
A1_NEW = A1 + '\n' + \
    '# CLOUD3D PATCH: salida de nube 3D al puerto 5007 (cloud_ros_publisher).\n' + \
    '# Default off => no afecta los tests de camara/2D.\n' + \
    'ENABLE_CLOUD3D = os.environ.get("ENABLE_CLOUD3D", "0") != "0"'

# --- Ancla 2: puertos UDP (despues de ODOM_UDP_PORT) ---
A2 = 'ODOM_UDP_PORT  = 5006'
A2_NEW = A2 + '\n' + \
    'CLOUD_UDP_HOST = "127.0.0.1"   # CLOUD3D PATCH\n' + \
    'CLOUD_UDP_PORT = 5007          # CLOUD3D PATCH\n' + \
    'CLOUD_MAX_PTS  = 5000          # CLOUD3D PATCH: cap para UDP (<64KB) y carga ligera'

# --- Ancla 3: socket (despues de _udp_odom) ---
A3 = '_udp_odom  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)'
A3_NEW = A3 + '\n' + \
    '_udp_cloud = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # CLOUD3D PATCH'

# --- Ancla 4: envio de la nube (despues de calcular xyz) ---
A4 = '    xyz    = origin + pts_2d * _LIDAR_RESOLUTION'
A4_NEW = A4 + '\n' + \
    '    # CLOUD3D PATCH: mandar puntos 3D (mundo) al publisher de nube, con downsample.\n' + \
    '    if ENABLE_CLOUD3D:\n' + \
    '        _pts3d = xyz\n' + \
    '        if _pts3d.shape[0] > CLOUD_MAX_PTS:\n' + \
    '            _stride = _pts3d.shape[0] // CLOUD_MAX_PTS + 1\n' + \
    '            _pts3d = _pts3d[::_stride]\n' + \
    '        _ncloud = int(_pts3d.shape[0])\n' + \
    '        _cloud_payload = struct.pack("<II", state.scan_seq, _ncloud) + \\\n' + \
    '            _pts3d.astype(np.float32).tobytes()\n' + \
    '        udp_send(_udp_cloud, _cloud_payload, CLOUD_UDP_HOST, CLOUD_UDP_PORT, "cloud")'


def revert():
    if not os.path.exists(BAK):
        print(f"ERROR: no hay backup en {BAK}"); sys.exit(1)
    shutil.copy2(BAK, P)
    py_compile.compile(P, doraise=True)
    print(f"Restaurado original desde {BAK}. Compila OK.")


def apply():
    if not os.path.exists(P):
        print(f"ERROR: no existe {P}"); sys.exit(1)
    s = open(P).read()
    if MARKER in s:
        print("Ya esta parcheado. Nada que hacer.")
        print("Revertir: python3 patch_cloud3d.py --revert")
        return
    # verificar anclas unicas
    for name, anchor in [("env", A1), ("ports", A2), ("socket", A3), ("xyz", A4)]:
        c = s.count(anchor)
        if c != 1:
            print(f"ERROR: ancla '{name}' aparece {c} veces (esperaba 1). Abortando sin cambios.")
            sys.exit(1)
    # backup
    if not os.path.exists(BAK):
        shutil.copy2(P, BAK); print(f"Backup creado: {BAK}")
    else:
        print(f"Backup ya existia: {BAK}")
    # aplicar
    s = s.replace(A1, A1_NEW)
    s = s.replace(A2, A2_NEW)
    s = s.replace(A3, A3_NEW)
    s = s.replace(A4, A4_NEW)
    open(P, "w").write(s)
    print("Inserciones aplicadas.")
    try:
        py_compile.compile(P, doraise=True)
        print("Verificado: compila OK.")
    except py_compile.PyCompileError as e:
        shutil.copy2(BAK, P)
        print("ERROR: no compila. Restaurado original.")
        print(e); sys.exit(1)
    print("")
    print("LISTO. Comportamiento:")
    print("  - Sin ENABLE_CLOUD3D       => igual que antes (tests 1 y 2 intactos).")
    print("  - ENABLE_CLOUD3D=1         => manda nube 3D downsampleada a UDP 5007.")
    print("Revertir: python3 patch_cloud3d.py --revert")


if __name__ == "__main__":
    if "--revert" in sys.argv:
        revert()
    else:
        apply()
