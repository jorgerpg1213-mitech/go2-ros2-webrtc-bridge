#!/usr/bin/env python3
"""
patch_skip_decode.py — Aplica skip-decode a la librería unitree_webrtc_connect.

Modifica deal_array_buffer_for_lidar en webrtc_datachannel.py para decodear
solo 1 de cada N frames del lidar (N = env var LIDAR_DECODE_EVERY_N).

SEGURIDAD:
  - Respalda el archivo original a .skipdecode.bak antes de tocar nada.
  - Default N=1 => decodea TODO => comportamiento IDÉNTICO al actual.
  - Solo con LIDAR_DECODE_EVERY_N=4 (u otro) empieza a saltar frames.
  - En frames saltados reusa la última nube (nunca devuelve None => no rompe callbacks).
  - Idempotente: si ya está parcheado, no vuelve a parchear.

Uso:
  python3 patch_skip_decode.py            # aplica patch
  python3 patch_skip_decode.py --revert   # restaura original desde backup
"""
import sys, os, shutil, py_compile

LIB = os.path.expanduser(
    "~/go2_legacy_env/lib/python3.10/site-packages/"
    "unitree_webrtc_connect/webrtc_datachannel.py"
)
BAK = LIB + ".skipdecode.bak"

MARKER = "# SKIP-DECODE PATCH"

# Bloque original exacto (dentro de deal_array_buffer_for_lidar)
ORIG = (
    "        decoded_data = self.decoder.decode(binary_data, decoded_json['data'])\n"
    "\n"
    "        decoded_json['data']['data'] = decoded_data\n"
    "        return decoded_json"
)

# Bloque nuevo: contador + decodear 1 de cada N, reusar última nube si se salta
NEW = (
    "        # SKIP-DECODE PATCH: decodear solo 1 de cada N frames del lidar\n"
    "        # para no saturar el event loop. N = env LIDAR_DECODE_EVERY_N (default 1).\n"
    "        import os as _os\n"
    "        _n = 1\n"
    "        try:\n"
    "            _n = int(_os.environ.get('LIDAR_DECODE_EVERY_N', '1'))\n"
    "        except Exception:\n"
    "            _n = 1\n"
    "        self._lidar_fc = getattr(self, '_lidar_fc', 0) + 1\n"
    "        if _n <= 1 or (self._lidar_fc % _n) == 1:\n"
    "            decoded_data = self.decoder.decode(binary_data, decoded_json['data'])\n"
    "            self._last_cloud = decoded_data\n"
    "            decoded_json['data']['data'] = decoded_data\n"
    "        else:\n"
    "            # frame saltado: reusar última nube (nunca None) => no rompe callbacks\n"
    "            decoded_json['data']['data'] = getattr(self, '_last_cloud', None)\n"
    "        return decoded_json"
)


def revert():
    if not os.path.exists(BAK):
        print(f"ERROR: no hay backup en {BAK}")
        sys.exit(1)
    shutil.copy2(BAK, LIB)
    print(f"Restaurado original desde {BAK}")
    py_compile.compile(LIB, doraise=True)
    print("Verificado: compila OK.")


def apply():
    if not os.path.exists(LIB):
        print(f"ERROR: no existe {LIB}")
        sys.exit(1)

    src = open(LIB).read()

    if MARKER in src:
        print("Ya está parcheado. No hago nada.")
        print("Para revertir: python3 patch_skip_decode.py --revert")
        return

    if ORIG not in src:
        print("ERROR: no encontré el bloque original esperado.")
        print("La librería puede tener otra versión. Abortando sin tocar nada.")
        sys.exit(1)

    # contar ocurrencias para asegurar match único
    if src.count(ORIG) != 1:
        print(f"ERROR: el bloque aparece {src.count(ORIG)} veces (esperaba 1). Abortando.")
        sys.exit(1)

    # backup
    if not os.path.exists(BAK):
        shutil.copy2(LIB, BAK)
        print(f"Backup creado: {BAK}")
    else:
        print(f"Backup ya existía: {BAK} (no lo sobreescribo)")

    # aplicar
    patched = src.replace(ORIG, NEW)
    open(LIB, "w").write(patched)
    print("Patch aplicado.")

    # verificar que compila
    try:
        py_compile.compile(LIB, doraise=True)
        print("Verificado: compila OK.")
    except py_compile.PyCompileError as e:
        print("ERROR: el archivo parcheado NO compila. Restaurando original...")
        shutil.copy2(BAK, LIB)
        print("Original restaurado.")
        print(e)
        sys.exit(1)

    print("")
    print("LISTO. Comportamiento:")
    print("  - Sin variable de entorno  => decodea TODO (igual que antes).")
    print("  - LIDAR_DECODE_EVERY_N=4   => decodea 1 de cada 4 frames.")
    print("")
    print("Para revertir: python3 patch_skip_decode.py --revert")


if __name__ == "__main__":
    if "--revert" in sys.argv:
        revert()
    else:
        apply()
