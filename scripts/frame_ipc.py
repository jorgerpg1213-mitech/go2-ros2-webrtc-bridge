"""
frame_ipc.py — Buzon de un solo frame sobre memoria compartida.

Un escritor (go2_master.py, hilo main de run_display) deja SIEMPRE el frame
mas reciente; uno o varios lectores (yolo_viewer.py) lo recogen a su propio
ritmo. No hay cola: el lector toma el ultimo frame disponible, igual que el
buffer interno del bridge. Eso evita acumular latencia.

Diseno:
  - Memoria compartida con nombre fijo (p.ej. "go2_cam"), via la libreria
    estandar (multiprocessing.shared_memory). Cero dependencias de terceros,
    por lo que NO entra ningun paquete nuevo al venv del robot.
  - Cabecera de tamano fijo + bytes del frame (BGR, uint8, contiguo).
  - Lecturas consistentes mediante seqlock (seq_begin / seq_end): si un frame
    se estaba escribiendo durante la lectura, el lector lo detecta y reintenta.
    Asi nunca se entrega un frame "a medio escribir".
  - El escritor es el dueno del bloque: lo crea y lo libera (unlink). El lector
    solo lo abre y lo cierra.

Aislamiento de fallos: si algo falla aqui, lanza excepcion y el llamador
(master) lo captura en su try/except con log rate-limited, sin afectar al robot.
"""

import struct
import time
from multiprocessing import shared_memory

# ─── Layout de la cabecera (little-endian) ──────────────────────────────────
MAGIC = b"GO2C"
VERSION = 1
HEADER_SIZE = 64  # bytes reservados para la cabecera (con padding)

_OFF_MAGIC = 0       # 4s
_OFF_VERSION = 4     # I  (uint32)
_OFF_SEQ_BEGIN = 8   # Q  (uint64) — se escribe ANTES de los datos
_OFF_SEQ_END = 16    # Q  (uint64) — se escribe DESPUES de los datos
_OFF_HEIGHT = 24     # I
_OFF_WIDTH = 28      # I
_OFF_CHANNELS = 32   # I
_OFF_ITEMSIZE = 36   # I  (bytes por elemento; 1 para uint8)
_OFF_TIMESTAMP = 40  # d  (double, time.time() del frame)
# 48..64 reservado


class FrameWriter:
    """Escribe el frame mas reciente en memoria compartida. Un solo escritor."""

    def __init__(self, name: str = "go2_cam"):
        self.name = name
        self._shm = None
        self._capacity = 0       # bytes de datos que caben (sin cabecera)
        self._seq = 0

    def _ensure(self, nbytes: int) -> None:
        """Crea (o recrea) el bloque si no existe o si cambio el tamano del frame."""
        if self._shm is not None and self._capacity >= nbytes:
            return
        # cerrar bloque previo si lo habia (cambio de resolucion)
        self._close_shm()
        size = HEADER_SIZE + nbytes
        try:
            self._shm = shared_memory.SharedMemory(name=self.name, create=True, size=size)
        except FileExistsError:
            # bloque viejo de una corrida anterior: lo reclamamos
            stale = shared_memory.SharedMemory(name=self.name, create=False)
            stale.close()
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
            self._shm = shared_memory.SharedMemory(name=self.name, create=True, size=size)
        self._capacity = nbytes
        # inicializar cabecera (seq en 0 => lector sabe que aun no hay frame valido)
        buf = self._shm.buf
        struct.pack_into("<4s", buf, _OFF_MAGIC, MAGIC)
        struct.pack_into("<I", buf, _OFF_VERSION, VERSION)
        struct.pack_into("<Q", buf, _OFF_SEQ_BEGIN, 0)
        struct.pack_into("<Q", buf, _OFF_SEQ_END, 0)

    def write(self, frame, seq: int = None) -> None:
        """Copia `frame` (ndarray BGR uint8, contiguo) al buzon como ultimo frame.

        `seq` es opcional; si no se pasa, se usa un contador interno. Conviene
        pasar el seq del bridge para que el lector sepa cuando hay frame nuevo.
        """
        # asegurar contiguidad y tipo sin copiar de mas
        h, w = frame.shape[0], frame.shape[1]
        c = frame.shape[2] if frame.ndim == 3 else 1
        itemsize = frame.itemsize
        if frame.flags["C_CONTIGUOUS"]:
            mv = memoryview(frame).cast("B")
        else:
            mv = memoryview(frame.tobytes())
        nbytes = mv.nbytes

        self._ensure(nbytes)
        if seq is None:
            self._seq += 1
            seq = self._seq

        buf = self._shm.buf
        # 1) dimensiones y metadatos
        struct.pack_into("<I", buf, _OFF_HEIGHT, h)
        struct.pack_into("<I", buf, _OFF_WIDTH, w)
        struct.pack_into("<I", buf, _OFF_CHANNELS, c)
        struct.pack_into("<I", buf, _OFF_ITEMSIZE, itemsize)
        struct.pack_into("<d", buf, _OFF_TIMESTAMP, time.time())
        # 2) marcar inicio de escritura
        struct.pack_into("<Q", buf, _OFF_SEQ_BEGIN, seq)
        # 3) copiar pixeles
        buf[HEADER_SIZE:HEADER_SIZE + nbytes] = mv
        # 4) marcar fin de escritura (commit). El lector compara begin==end.
        struct.pack_into("<Q", buf, _OFF_SEQ_END, seq)

    def _close_shm(self) -> None:
        if self._shm is not None:
            try:
                self._shm.close()
                self._shm.unlink()
            except FileNotFoundError:
                pass
            finally:
                self._shm = None
                self._capacity = 0

    def close(self) -> None:
        self._close_shm()


class FrameReader:
    """Lee el frame mas reciente del buzon. Soporta que el escritor aun no exista."""

    def __init__(self, name: str = "go2_cam"):
        self.name = name
        self._shm = None

    def available(self) -> bool:
        """Intenta enganchar el bloque. True si el escritor ya lo creo."""
        if self._shm is not None:
            return True
        try:
            self._shm = shared_memory.SharedMemory(name=self.name, create=False)
            # El lector NO es dueno del bloque. Por un comportamiento conocido de
            # multiprocessing, el resource_tracker del lector intentaria liberar
            # (unlink) el bloque al salir, lo que borraria el buzon del escritor.
            # Lo desregistramos para que solo el escritor sea dueno.
            try:
                from multiprocessing import resource_tracker
                resource_tracker.unregister(self._shm._name, "shared_memory")
            except Exception:
                pass
            return True
        except FileNotFoundError:
            return False

    def read(self, max_retries: int = 5):
        """Devuelve (frame_ndarray_BGR, seq, timestamp) o None si no hay frame.

        Usa seqlock: si detecta que el frame se estaba escribiendo, reintenta.
        Importa numpy aqui (no a nivel de modulo) para no imponerselo a quien
        solo escribe; el lector (yolo_viewer) si tiene numpy.
        """
        import numpy as np
        if not self.available():
            return None
        buf = self._shm.buf

        # validar cabecera
        magic = bytes(buf[_OFF_MAGIC:_OFF_MAGIC + 4])
        if magic != MAGIC:
            return None

        for _ in range(max_retries):
            seq_end = struct.unpack_from("<Q", buf, _OFF_SEQ_END)[0]
            if seq_end == 0:
                return None  # aun no se ha escrito ningun frame
            h = struct.unpack_from("<I", buf, _OFF_HEIGHT)[0]
            w = struct.unpack_from("<I", buf, _OFF_WIDTH)[0]
            c = struct.unpack_from("<I", buf, _OFF_CHANNELS)[0]
            ts = struct.unpack_from("<d", buf, _OFF_TIMESTAMP)[0]
            nbytes = h * w * c  # uint8 => itemsize 1
            if HEADER_SIZE + nbytes > len(buf):
                return None  # tamanos inconsistentes (bloque en recreacion)
            raw = bytes(buf[HEADER_SIZE:HEADER_SIZE + nbytes])
            seq_begin = struct.unpack_from("<Q", buf, _OFF_SEQ_BEGIN)[0]
            if seq_begin == seq_end:
                # lectura consistente: no hubo escritura a mitad
                frame = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, c))
                return frame, seq_end, ts
            # se estaba escribiendo: reintentar
        return None  # no se logro lectura consistente este ciclo

    def close(self) -> None:
        if self._shm is not None:
            try:
                self._shm.close()  # el lector NO hace unlink (no es dueno)
            except Exception:
                pass
            finally:
                self._shm = None
