# INFORME MAESTRO — Proyecto Go2 Pro: Locomoción + Video + YOLO + (SLAM pendiente)

> **Propósito de este documento.** Esta es la **llave-memoria** del proyecto. Está escrito para que un **chat nuevo** (sin acceso al historial anterior) pueda leerlo y continuar **exactamente** donde lo dejamos, sin repetir trabajo ni reabrir cosas ya resueltas. Si eres un asistente leyendo esto: **lee el documento completo antes de proponer nada**, respeta las decisiones cerradas, y fíjate sobre todo en la sección **"§12 — TEST PENDIENTE"** (es lo siguiente que hay que hacer) y en **"§15 — Cómo trabajar con el usuario"**.

> **Estado en una frase:** locomoción + video + YOLO funcionan **estables ~11 min** con el robot (mejor punto del proyecto), tras descubrir y arreglar que **decodificar el video dentro del event loop** ahogaba el control. Queda **pendiente correr** una corrida de medición (master con sellos de tiempo + sonda WiFi) para atacar el último problema: **al prender los topics de LiDAR para ROS/RViz, el alcance útil se reduce a la mitad**.

---

## Índice
1. Objetivo del proyecto
2. Hardware y entorno
3. Arquitectura y decisiones cerradas
4. Inventario de repos y archivos
5. Seguridad: la GO2_AES_KEY (pendiente)
6. Métricas de instrumentación (qué significa cada número)
7. Diagnóstico del colapso de locomoción (medido)
8. Historial de arreglos: qué se intentó, qué falló, qué funcionó
9. Hallazgo grande de la última sesión: el video decodificándose en el loop
10. El problema actual: el LiDAR y el alcance (la "pelea por el WiFi")
11. Investigación: ¿se puede aligerar el LiDAR? (web)
12. **TEST PENDIENTE** (lo siguiente a correr)
13. Lista de posibles: qué falta medir y qué se puede optimizar
14. Comandos recurrentes
15. Cómo trabajar con el usuario (importante)
16. Flujo seguro de cambios
17. Decisiones abiertas
18. Archivos entregados en /outputs

---

## 1. Objetivo del proyecto

Que el **Unitree Go2 Pro** mantenga **simultáneamente y al máximo alcance posible**:
- **Streaming de video fluido**,
- **Detección YOLO en vivo**,
- **Mapa SLAM persistente** (que NO se borre al avanzar/alejarse del punto de partida),

todo **optimizado y caracterizado con datos** para una auditoría tipo **MIT/NASA** (debe permitir escribir un paper: no basta con "funciona", hay que decir *cuánto*, *por qué*, y *con qué límites*).

El usuario también tiene un **G1** con framework determinista en VM con Isaac Lab (contexto, no es el foco de este proyecto).

---

## 2. Hardware y entorno

- **Laptop:** Acer Nitro AN515-51. CPU **i5-7300HQ** (4 cores / 4 threads). **15 GB RAM**. GPU **GTX 1050 Mobile 4GB** (Pascal, sm_61). Driver NVIDIA 580.
- **SO:** Ubuntu 22.04.5. **Python 3.10.12**.
- **Robot:** Unitree **Go2 Pro** (NO trae Jetson; eso es el EDU). De fábrica solo trae **WebRTC/WiFi** (no CycloneDDS/Ethernet sin mod de firmware).
- **Conexión real:** WiFi directo PC↔robot, IP del robot **192.168.12.1**. Ping confirmado sano: **0% pérdida, 3-6 ms**. (El mensaje "Robot Connection Mode: 4G" en el log es una **etiqueta heredada del repo, NO refleja la conexión real** — descartar como sospechoso.)
- **Ubicación:** Tijuana, MX.
- **GPU/PyTorch:** PyTorch **cu126** (Pascal fue dropeado en PyTorch 2.8+). Benchmark **YOLO11n ≈ 9.5 ms/frame (~105 fps)** → **YOLO NO es cuello de botella** (confirmado con dato).

---

## 3. Arquitectura y decisiones cerradas

> Estas decisiones ya están tomadas y validadas. **No reabrir** salvo evidencia nueva.

- **[DECISIÓN] YOLO corre aislado** en su propio venv `~/go2-yolo`, como **proceso separado**, NO dentro del bridge del robot (aislamiento exigido por la auditoría). El puente robot↔YOLO es **memoria compartida** (`multiprocessing.shared_memory`, buffer `go2_cam`).
- **[DECISIÓN] Un solo proceso maestro** (`go2_master.py`) mantiene **una única conexión WebRTC**. Suscribe LiDAR (`ULIDAR_ARRAY`) y odometría (`ROBOTODOM`), publica por UDP a ROS, mantiene el video vivo, sirve IPC para teleop, y corre el `publish_loop` de comandos SPORT.
- **[ANÁLISIS] Conexión = WebRTC/WiFi de fábrica** → tiene un **techo de latencia/ancho de banda**. La alternativa **Ethernet + CycloneDDS requiere firmware modificado** (comunidad theroboverse). Esa decisión sigue **abierta** (ver §17).
- **Repo de referencia** (usar como guía de configs probadas, NO sustituir el bridge propio): `github.com/abizovnuralem/go2_ros2_sdk`. Driver WebRTC por debajo: `legion1581/unitree_webrtc_connect` (caja negra; **el SDK NO tolera que se cancelen envíos async** — ver §8).

---

## 4. Inventario de repos y archivos

- **Repo maestro local:** `~/go2-ros2-webrtc-bridge`
- **GitHub:** `github.com/jorgerpg1213-mitech/go2-ros2-webrtc-bridge`
- **Scripts** (`~/go2-ros2-webrtc-bridge/scripts/`):
  - `go2_master.py` (~30KB): bridge principal. Corre en venv `~/go2_legacy_env`. Contiene: conexión WebRTC única, callbacks LiDAR/ODOM, envío UDP a puertos **5005 (lidar)** / **5006 (odom)**, video keepalive + ventana cámara, server IPC (socket `/tmp/go2_master.sock`), `publish_loop` (comandos SPORT_MOD Move/StopMove), reconexión, watchdog de frescura (0.5s). **Es el archivo que hemos ido modificando.**
  - `teleop_client.py`: cliente de teleoperación (pynput). Manda vx/vy/wz por el socket IPC mientras hay tecla apretada; watchdog 0.5s; SPACE=stop, ESC=salir. **Corre aparte** (no lo lanza el launcher).
  - `lidar_ros_publisher.py` / `odom_ros_publisher.py`: lado Docker. Patrón **hilo-recibe-UDP / timer-publica-ROS**. Sanos.
  - `lidar_sender.py` / `odom_sender.py`: **CÓDIGO MUERTO** (ya fusionados en master). **NO correr** (causaría 3 conexiones WebRTC).
  - `frame_ipc.py`, `yolo_viewer.py`, `yolo11n.pt`, `teleop_video2.py` (baseline histórico).
  - `go2_launch.sh`: un Enter levanta Docker (`osrf/ros:humble-desktop`, `--network host`, efímero, `--name go2_ros`) + lidar + odom + tf (`base_link→laser`) + rviz + master + yolo. **NO incluye teleop.** Cierre limpio con Ctrl+C. Logs por componente en `runs/<fecha>/`. Lee `GO2_AES_KEY` del entorno. Switches `ENABLE_RVIZ` / `ENABLE_YOLO` (default 1). **No tiene switch de cámara** (la cámara la controla el master con `ENABLE_CAMERA`).
  - `rviz_go2.rviz`: config RViz. Fixed Frame=`odom`, displays LaserScan (`/scan`, Best Effort, amarillo) + Odometry (`/odom`) + TF + Grid. **Corregido esta sesión:** `Keep: 100 → 1` (ver §8).
  - `wifi_probe.py`: **NUEVO esta sesión.** Sonda de WiFi independiente (ver §6 y §12).
  - Respaldos `.bak` en la máquina del usuario (ver §8 y §16).

---

## 5. Seguridad: la GO2_AES_KEY (PENDIENTE)

- Valor actual (en texto plano, usado para lanzar): `${GO2_AES_KEY}`
- Se exporta a mano por terminal: `export GO2_AES_KEY="${GO2_AES_KEY}"` (vive solo en esa terminal).
- **[PENDIENTE / acción del usuario]:** cambiar la key en el robot, sacarla de cualquier doc/repo público (GitHub), y leerla desde un archivo `.env` **no subido a git** en vez de tenerla en texto plano. **Riesgo de seguridad si queda expuesta.**

---

## 6. Métricas de instrumentación (qué significa cada número)

El master tiene un sistema de perfilado (`ENABLE_PROFILING`) que **acumula en memoria y reporta cada 5 s** junto al `STATUS` (no floodea por vuelta). Desde la última sesión, **cada línea del log lleva un sello de tiempo `[t=MM:SS]`** relativo al inicio de la corrida.

Campos PROF:
- **`q_lag_ms`**: lag desde que se aprieta la tecla hasta que el comando entra a la cola. (≈0 = el código reacciona rápido.)
- **`send_ms`**: cuánto tarda el `await publish_request_new` en mandar el comando al robot. **Métrica clave.** Sano ~5-50 ms; degradado salta a cientos/miles de ms.
- **`loop_dt_ms`**: cuánto tarda una vuelta del `publish_loop` (objetivo 50 ms). **Si esto se dispara a segundos, el control está congelado.**
- **`lidar_cb_ms`** / **`odom_cb_ms`**: cuánto tardan los callbacks de sensores. (Confirmado baratos: lidar 2-8 ms, odom ~0.1 ms → NO ahogan el loop.)
- **`moves sent/skipped`**: efecto del throttle (cuántos Move se mandaron vs se saltaron).
- **`lidar_load pts/scan ... pts/s ... KB/s (proxy)`**: **NUEVO.** Cuántos puntos crudos manda el LiDAR por scan → proxy de cuánta data mete el LiDAR al canal WiFi. (El KB/s es aproximado: 3 float32 por punto decodificado; NO es el tamaño exacto codificado en el WiFi, pero sirve como tendencia.)
- En el `STATUS`: `conn`, tasas `lidar=Xhz odom=Yhz cam=Zfps`.

**`wifi_probe.py`** (sonda aparte, no toca el master). Cada segundo imprime, con sello `t=MM:SS`:
- **RSSI (dBm)**: fuerza de señal. ~-50 excelente, ~-70 flojo, <-80 crítico.
- **bitrate (Mbps)**: velocidad que negocia el WiFi (cae cuando la señal baja = "el tubo se angosta").
- **rx/tx (KB/s)**: datos reales que entran/salen por la interfaz (rx = lo que llega del robot).

---

## 7. Diagnóstico del colapso de locomoción (medido)

**Síntoma original:** la locomoción iba bien ~10-15 s y **colapsaba**; el robot no frenaba al soltar la tecla.

**Hallazgos MEDIDOS (confirmados, ya cerrados):**
1. **YOLO no afecta** (corridas con/sin idénticas). DESCARTADO.
2. **Sensores baratos:** `lidar_cb` 2-8 ms, `odom_cb` ~0.1 ms. **No ahogan el event loop.** DESCARTADO con dato.
3. **Conexión estable:** **0 reconexiones** en corridas largas. El colapso **NO** es caída de conexión.
4. **CAUSA RAÍZ (original):** el `await publish_request_new` (`send_ms`) se degrada en **cascada** al moverse: 5 ms → 24 → 102 → 200 → 609 → 2123 ms, y el `loop_dt` llegaba a **~20800 ms (21 s congelado)**. `q_lag` se mantenía ~0 (el código reacciona rápido; el atasco es **el envío**). **Mecanismo: bufferbloat / head-of-line blocking** del canal WebRTC/SCTP saturado (video + lidar + odom + comandos compitiendo). El video llegaba clavado ~14fps y corrupto ("non-existing PPS 0 / no frame") → el techo es el **canal/robot**, no la PC.
5. El bug **NO lo causó el launcher** (diff confirmó solo líneas de instrumentación). El envío continuo a 20 Hz siempre fue frágil; la instrumentación solo lo hizo visible.

---

## 8. Historial de arreglos: qué se intentó, qué falló, qué funcionó

### ✅ ARREGLO 1 — THROTTLE (ÉXITO, vigente como base)
- `THROTTLE_REPEATS=True`, `CMD_REFRESH_HZ=8` (ajustable 6-12).
- Manda Move **al instante** cuando el comando cambia (respuesta inmediata a la tecla), y si es el **mismo** comando, lo repite solo a **8 Hz** (no a 20). StopMove y watchdog intactos.
- **Resultado medido + sentido:** la locomoción pasó de colapsar en ~10-15 s a funcionar **7 minutos continuos**, streaming bien, 0 reconexiones. Recorta ~40% de los envíos. Fue el primer gran salto.
- **Pero:** el `send_ms` aún trepaba en picos; el throttle ayuda pero no elimina la saturación estructural del canal.

### ❌ ARREGLO 2 — TIMEOUT + StopMove ráfaga (FALLÓ, REVERTIDO, DESCARTADO)
- Se intentó: `SEND_TIMEOUT_ENABLE` con `asyncio.wait_for(timeout=0.5s)` en los envíos, vía helper `_sport_send`, + `StopMove` en ráfaga (`STOP_BURST_N=3`) vía `_sport_stop_burst`.
- **RESULTADO: ROMPIÓ el robot.** No dio ni 3 pasos. Log: `asyncio.exceptions.InvalidStateError: invalid state` + `[sport_stop_timeout] envio > 0.5s abandonado` en cascada.
- **CAUSA:** el SDK `unitree_webrtc_connect` (caja negra) **NO tolera que se cancele el `publish_request_new` a medias**. `wait_for` cancela la corrutina al expirar → canal WebRTC corrupto → **peor** que sin timeout.
- **Revertido** con respaldo. **APRENDIZAJE PARA EL PAPER:** *el SDK no tolera cancelación de envíos asíncronos; el timeout vía `wait_for` corrompe el canal.* Esto **descarta toda esa familia de soluciones**. La rama válida sería **fire-and-forget** (no cancelar el envío, sino **no esperarlo** en el lazo de control) — **NO intentado aún, y con riesgo** (el SDK es impredecible).

### ✅ ARREGLO 3 — RViz `Keep: 100 → 1` (config visual, ÉXITO)
- La "flecha verde que se quedaba grabada/regada" que el usuario reportó muchas veces era culpa de la **propia config de RViz** (`Odometry` con `Keep:100` acumula 100 flechas). Cambiado a `Keep:1` (solo la flecha actual).
- Es **config visual pura, no toca el control, cero riesgo.** Resuelto.

### 🚫 BUG ODOM TIMESTAMP — DECISIÓN: NO TOCAR
- `odom_ros_publisher.py` sella con el reloj de Docker (`get_clock().now()`), no con el `sec/nanosec` real del robot, **a pesar de que el comentario dice lo contrario** (el comentario miente).
- **PERO NO debe cambiarse ahora:** el LiDAR (`/scan`) tampoco trae timestamp del robot (el master lo manda sin tiempo), así que `lidar_ros_publisher` también sella con reloj Docker. Hoy **ambos (`/scan` y `/odom`) están en el MISMO reloj = consistentes.** "Arreglar" odom a usar el reloj del robot los pondría en relojes distintos → rompería el emparejamiento temporal que SLAM necesita → **mapa peor**. El arreglo correcto es **no cambiar el código ahora**; los relojes se resuelven holísticamente en la fase SLAM. Solo el comentario es cosmético/mentiroso.

### 🗺️ EL "MAPA QUE SE BORRA" — NO ES BUG, ES FALTA DE SLAM
- El usuario reporta que el mapa se mueve con el robot y desaparece al alejarse del inicio. Eso es **exactamente lo esperado SIN SLAM**: RViz solo muestra el scan del momento pegado a la pose actual; no hay nada que acumule/guarde el mapa.
- El mapa persistente que quieren los superiores **solo existe con SLAM** (`slam_toolbox` crea el frame `map→odom→base_link` y acumula scans en un mapa global fijo). **No es un bug que arreglar — es la pieza que falta montar.** La base ya está sana para montarlo.

---

## 9. Hallazgo grande de la última sesión: el video decodificándose en el loop

Este fue el descubrimiento mayor de la sesión. Secuencia:

**(a) Deriva lenta detectada.** Analizando una corrida de ~7-8 min con video, el `loop_dt` en reposo **subía solo** con el tiempo de sesión: media del primer tercio **~100.7 ms** → idle tardío **~136 ms** (1.4x), y al final **colapso a 9254 ms**. La deriva ocurría **incluso sin moverse** → no era el throttle ni los comandos; **algo se acumulaba en el canal con el tiempo**.

**(b) Test video OFF.** Se apagó `ENABLE_CAMERA` y se corrió ~11 min:
- `loop_dt` primer tercio **97.6 ms**, último tercio **118.7 ms**, **máximo 178 ms**. **Sin colapso.** Reconexión de teleop sin problema.
- **Comparativa dura:** pico peor **sin video = 178 ms** vs **con video = 9254 ms** → **52x peor con video.**

**(c) Matiz CLAVE.** Apagar `ENABLE_CAMERA` **NO quita el video del canal** — el robot lo sigue mandando y el master lo sigue recibiendo (keepalive, `track.recv()`); solo se deja de **decodificar** (`frame.to_ndarray`). Como el colapso desapareció igual, **el culpable NO era el ancho de banda del video, sino la DECODIFICACIÓN por frame corriendo DENTRO del event loop** (`_video_handler` es async y corría en el mismo hilo del control). A 14 fps, decodificar cada frame le robaba tiempo al lazo de control, y bajo congestión eso se compounde hasta el colapso.

**(d) ✅ VIDEOFIX (ÉXITO parcial, vigente).** Se movió la decodificación **fuera del event loop**: el handler ahora solo guarda el **frame crudo** (`_raw_frame`), y el **hilo de display** (`run_display`, hilo main) hace el `to_ndarray`. **No toca el envío de comandos.**
- **Resultado:** **11 min con video + YOLO + locomoción**, sin colapso terminal, reconexión de teleop OK, video y YOLO "se ven muy bien" (reporte del usuario). `loop_dt` arranca **84.2 ms** y termina **90.8 ms** → **deriva eliminada.**
- **PERO NO es victoria total:** quedan **picos sueltos** de varios segundos: **12 de 106 reportes (11%) con `loop_dt ≥ 500 ms`, máximo 3503 ms**, pero **recuperan solos** (ya no es cascada irreversible).
- **Interpretación:** el videofix **quitó la enfermedad que mataba la corrida con el tiempo** (deriva + colapso terminal), pero los picos residuales de 1-3 s **ya no son el decode** (lo sacamos) — son **el canal WebRTC/WiFi soltando ráfagas al congestionarse**, y muy probablemente **correlacionados con la distancia** (ver §10). En esos picos, `send_ms` salta a 1-2 s y el `lidar` cae de 7.8 a 4-5 Hz y rebota a 9-10 Hz (señal de llegada en lote tras pausa) → **transporte, no código del usuario.**

---

## 10. El problema actual: el LiDAR y el alcance (la "pelea por el WiFi")

**Observación del usuario (clave):** con teleop + video + YOLO **solo**, el robot llega **lejos**. En cuanto se **prenden los topics de LiDAR para ROS/RViz**, el **alcance útil se reduce a la mitad** (y al alejarse llega a perder señal por milisegundos).

**Explicación física (modelo del "tubo"):** el WiFi PC↔robot es un tubo de ancho fijo. Al alejarse, la señal baja y el WiFi **negocia velocidades más bajas** → el tubo **se angosta**. Video + LiDAR juntos **llenan mucho** el tubo; entonces el tubo angosto de la distancia **se satura antes** → se rompe a menos metros. No es que se pierda la señal físicamente antes; es que la señal débil ya no da abasto para tanta data junta.

**Aclaración técnica importante (corregida en la sesión):**
- Lo que el master **publica a ROS/RViz** (`/scan`) **SÍ es 2D** (un `LaserScan` aplanado en bins, ligero) y va por la **red local de la PC**, NO por el WiFi del robot.
- Lo **pesado** es lo que el **robot manda por WiFi**: una **nube voxelizada 3D** (`ULIDAR_ARRAY`, decodificada por `LibVoxelDecoder`). El master la recibe en 3D (`positions` x,y,z) y *luego* la aplana a 2D. **El peso en el WiFi es la nube 3D de entrada, antes de aplanar.** Por eso, justo al suscribir el LiDAR, el canal se llena y se pierde alcance.

---

## 11. Investigación: ¿se puede aligerar el LiDAR? (web, hecha esta sesión)

Resumen honesto de lo investigado:
- El LiDAR por WebRTC es un **push voxelizado del robot** a **~7 Hz** (coincide con el log y con notas del `go2_ros2_sdk`, que menciona que subieron el rate de ~2 Hz a ~7 Hz). Hubo incluso un PR "Fixing the low update rate of the LiDAR stream using the WebRTC method" → es un **cuello conocido**.
- **NO se encontró ninguna perilla** para pedirle al robot que mande **menos puntos / más lento / menor resolución**. La resolución y el rate los fija el **firmware**; el decoder (`LibVoxelDecoder`) es fijo.
- **Conclusión honesta:** **aligerar el LiDAR del lado del robot probablemente NO se puede** con un parámetro simple. (Tirar frames del lado de la PC NO ayuda al canal, solo a la CPU, y la CPU no es el cuello.)

**Implicación → tres caminos para recuperar alcance (cada uno ataca una causa distinta):**
1. **Bajar bitrate/resolución del VIDEO** → liberar tubo para el LiDAR a distancia (si el SDK lo permite). Software, medible.
2. **Router / antena WiFi 6** → solo se justifica **si la medición** dice que el cuello es la **radio** (señal), no la carga.
3. **Ethernet + CycloneDDS por firmware** (theroboverse) → **saca el LiDAR del WiFi de raíz**. Es la solución de fondo, pero es la **decisión grande** (tocar firmware).

---

## 12. ⏳ TEST PENDIENTE (lo siguiente a correr) — NO SE HA CORRIDO

> **ESTO ES LO QUE QUEDÓ PENDIENTE.** Los dos scripts ya están desplegados y compilando OK en la máquina del usuario, pero **la corrida de medición NO se ha ejecutado todavía.**

**Qué está desplegado (estado actual del master):**
- `go2_master.py` = **videofix + throttle + instrumentación completa + sellos de tiempo `[t=MM:SS]` + métrica `lidar_load`**. Cámara **ON**. (Confirmado por diff y compila OK.)
- `wifi_probe.py` = sonda WiFi independiente, desplegada y compilando OK.
- Respaldo del estado previo: `go2_master.tstampprev.bak` (y otros `.bak`, ver §16).

**Objetivo del test:** convertir "se corta a la mitad" en **números**, y determinar si el cuello al alejarse es **la carga del LiDAR llenando el tubo** o **la señal WiFi cayendo** — para decidir entre bajar video / comprar router / ir a Ethernet, **sin adivinar**.

**Cómo correrlo (3 terminales):**
```bash
# T1 — master
export GO2_AES_KEY="${GO2_AES_KEY}" && ~/go2-ros2-webrtc-bridge/scripts/go2_launch.sh

# T2 — sonda WiFi (cuando el master esté arriba; 1 línea/seg)
python3 ~/go2-ros2-webrtc-bridge/scripts/wifi_probe.py
#   si no detecta interfaz: correr 'iw dev', ver el nombre (wlp.../wlan0) y pasarlo:
#   python3 ~/go2-ros2-webrtc-bridge/scripts/wifi_probe.py <iface>

# T3 — teleop
source ~/go2_legacy_env/bin/activate && python3 ~/go2-ros2-webrtc-bridge/scripts/teleop_client.py
```

**Protocolo de la caminata:**
1. Arrancar **cerca** de la PC, moverse normal ~2 min.
2. **Alejarse poco a poco** hasta donde empiece a fallar.
3. **Anotar a qué minuto (`t=MM:SS`) se alejó y cuándo sintió que se iba la señal.**

**Qué capturar al terminar (Ctrl+C en las 3):**
```bash
# PROF del master (trae [t=MM:SS], lidar_load, send_ms, loop_dt)
grep "PROF\|STATUS" "$(ls -dt ~/go2-ros2-webrtc-bridge/runs/*/ | head -1)"master.log
```
- Y **el log completo de la terminal del wifi_probe** (cópialo tal cual).

**Qué se aprende (cómo se cruza):** con los sellos de tiempo en ambos, se correlaciona algo como: *"al min 5 te alejaste → RSSI cayó -55→-75 → bitrate 300→50 Mbps → ahí el `send_ms` saltó y el `lidar_load`/lidar Hz cambió"*. Eso dice **a qué señal/distancia se rompe** y **si el culpable es carga o radio**.

---

## 13. Lista de posibles — para contemplar DESPUÉS del test pendiente

> Estas son hipótesis/ideas a evaluar **una vez tengamos los datos del §12**. **No todo dará fruto, y algunos tienen riesgo.** Cada uno se **mide o se prueba con switch+respaldo** antes de creerle (lección del timeout).

### A) Lo que falta MEDIR (para dejar de adivinar)
1. **Peso real de cada flujo en el WiFi**: video vs LiDAR vs comandos, en bytes/seg reales (hoy solo proxies). El `wifi_probe` da el total rx/tx; falta desglose por flujo.
2. **Señal (RSSI) y bitrate vs distancia** — la curva de "a cuántos metros se rompe". (Es justo lo del §12.)
3. **Cuánto pesa el decode del LiDAR** (`LibVoxelDecoder`) dentro del SDK — si bloquea el loop como bloqueaba el video.
4. **Latencia real ida-y-vuelta** de un comando (tecla → robot se mueve), no solo el `send_ms`.
5. **Si el robot deja de mandar video** cuando nadie lo mira (probar no suscribir el track y medir cuánto libera el canal).
6. **Corrida MUY larga (30+ min)**: ¿fugas de memoria o degradación lenta no vista en 11 min?
7. **CPU/RAM por componente** durante la corrida (master, docker, yolo) — ver si algo se satura en la PC al alejarse.

### B) Lo que se podría OPTIMIZAR (confirmar con medición antes)
1. **Bajar bitrate/resolución del video** → liberar tubo para el LiDAR a distancia (si el SDK lo permite).
2. **Mover/aligerar más cosas fuera del event loop**, como se hizo con el video (odom, decode LiDAR).
3. **Throttle del LiDAR hacia ROS** (no se necesitan 7 Hz para SLAM; con 3-4 Hz quizá basta y se aligera CPU/canal local).
4. **Punto dulce de `CMD_REFRESH_HZ`** (barrer 6/8/10/12) — nunca se hizo el barrido.
5. **Fire-and-forget en el envío** (la rama pendiente tras el fracaso del timeout) — **con mucho cuidado**, el SDK es impredecible.
6. **Ethernet + CycloneDDS por firmware** → saca el LiDAR del WiFi de raíz (solución de fondo, decisión grande).
7. **Router / antena WiFi 6** → solo si la medición dice que el cuello es la radio.
8. **Reducir el video a B/N o menos FPS solo para YOLO** (YOLO no necesita color bonito) → menos carga.

---

## 14. Comandos recurrentes

```bash
# Lanzar stack completo (master + lidar + odom + tf + rviz + yolo). NO incluye teleop.
export GO2_AES_KEY="${GO2_AES_KEY}" && ~/go2-ros2-webrtc-bridge/scripts/go2_launch.sh

# Teleop (aparte)
source ~/go2_legacy_env/bin/activate && python3 ~/go2-ros2-webrtc-bridge/scripts/teleop_client.py

# Sonda WiFi (aparte)
python3 ~/go2-ros2-webrtc-bridge/scripts/wifi_probe.py

# Ping al robot (confirmar conexión)
ping -c 4 192.168.12.1

# PROF de la última corrida
grep "PROF\|STATUS" "$(ls -dt ~/go2-ros2-webrtc-bridge/runs/*/ | head -1)"master.log

# Eventos no-PROF (reconexiones, errores)
grep -v "PROF\|STATUS\|h264\|aiortc\|QFontDatabase\|Qt no longer" "$(ls -dt ~/go2-ros2-webrtc-bridge/runs/*/ | head -1)"master.log | tail -50

# Confirmar qué master está puesto (debe salir THROTTLE_REPEATS; NO debe salir SEND_TIMEOUT_ENABLE)
grep -n "SEND_TIMEOUT_ENABLE\|THROTTLE_REPEATS\|_sport_stop_burst\|^ENABLE_CAMERA\|_raw_frame" ~/go2-ros2-webrtc-bridge/scripts/go2_master.py

# Limpieza total (matar todo y limpiar sockets/docker)
pkill -9 -f "go2_master.py" ; pkill -9 -f "yolo_viewer.py" ; pkill -9 -f "teleop_client.py" ; pkill -9 -f "lidar_ros_publisher" ; pkill -9 -f "odom_ros_publisher" ; pkill -9 -f "static_transform_publisher" ; docker rm -f go2_ros 2>/dev/null ; docker ps -aq | xargs -r docker rm -f 2>/dev/null ; rm -f /tmp/go2_master.sock ; xhost -local:docker 2>/dev/null ; echo "TODO LIMPIO"
```

---

## 15. Cómo trabajar con el usuario (IMPORTANTE para el próximo chat)

- **NO empujar a "cerrar la sesión".** El usuario se molestó varias veces por eso. **Seguir su ritmo**; él dice cuándo se cierra.
- **Respuestas en seco, breves, sin relleno.** Le molesta el texto largo. Pocas palabras, directo. Cuando pide "se breve", de verdad ser breve.
- **Análisis con poder computacional real**, no relleno: cuando hay datos, **calcular** (parsear logs, sacar medias/picos) en vez de estimar a ojo. Lo valora explícitamente.
- **No clavarse en una sola cosa.** Evaluar el panorama: si vamos a tocar código, aprovechar para mirar más de una cosa, y proponer **mediciones** antes que parches a ciegas.
- **Honestidad brutal sobre fallos.** Cuando algo es error propio (ej. el timeout), **admitirlo sin rodeos**, sin auto-flagelarse pero sin excusas.
- **No prometer de más** con el SDK (es caja negra). Decir las incógnitas de frente.
- **Sin bullets al declinar.** Cálido pero directo.
- Tiene **Opus 4.x en effort MAX**. Quiere rigor de auditoría MIT/NASA.
- **Explicar en palabras sencillas** cuando lo pide (analogías como "el tubo" del WiFi funcionaron bien).

---

## 16. Flujo seguro de cambios (SIEMPRE)

Cualquier cambio al master sigue este flujo (nos salvó del fallo del timeout):
1. **Respaldo** `.bak` del master actual.
2. **Mover** el archivo nuevo a `scripts/`.
3. **Diff** contra el respaldo correcto → verificar que **solo** difieran las líneas esperadas.
4. **Reemplazar** y `python3 -c "import ast; ast.parse(...)"` (o `py_compile`).
5. **Correr con el robot en piso seguro**, cerca del apagado, switch + respaldo listos para revertir.

El usuario aplica en SU máquina (el asistente no la toca). El asistente prepara/valida en su contenedor (`/home/claude/instr/`) y entrega el archivo + el diff esperado.

**Respaldos conocidos en la máquina del usuario** (de más viejo a más nuevo, aprox.): `go2_master.py.bak`, `go2_master.prethrottle.bak`, `go2_master.t1prev.bak` (throttle bueno pre-timeout), `go2_master.precam.bak` (throttle+cámara ON, base del videofix), `go2_master.videoprev.bak` (cámara OFF, de la prueba sin video), `go2_master.tstampprev.bak` (estado justo antes del master con sellos de tiempo).

---

## 17. Decisiones abiertas

- **Firmware Ethernet/DDS** vs seguir en WebRTC/WiFi (la decisión de fondo para el alcance/SLAM).
- **Comprar router/antena WiFi 6** — solo si la medición confirma que el cuello es la señal.
- **Valor final de `CMD_REFRESH_HZ`** (sin barrer aún; está en 8).
- **Explorar fire-and-forget** como rediseño del envío (con cautela; el timeout ya nos mordió).
- **Montaje de SLAM** (`slam_toolbox` en Docker, conectar a `/scan`, resolver frames `map→odom→base_link→laser`) — fase grande pendiente; la base ya está sana para empezar, idealmente **después** de cerrar el tema de alcance/LiDAR.

---

## 18. Archivos entregados en /outputs (esta sesión y previas)

- `INFORME_MAESTRO_GO2.md` — **este documento** (llave-memoria).
- `go2_master_tstamp.py` — master vigente: **videofix + throttle + instrumentación + sellos de tiempo + lidar_load**. (Ya desplegado como `go2_master.py`.)
- `wifi_probe.py` — sonda WiFi independiente. (Ya desplegado.)
- `go2_master_videofix.py` — versión con el decode de video fuera del loop (sin los sellos de tiempo; superada por `tstamp`).
- `go2_master_throttle.py` — solo throttle (histórico).
- `go2_master_test1.py` — **timeout (DESCARTADO, NO usar).**
- `go2_master_instrumentado.py` — instrumentación PROF base (histórico).
- `rviz_go2.rviz` — config RViz con `Keep:1` (flechas corregidas).
- `AUDITORIA_publish_loop.md` — auditoría técnica nivel MIT del `publish_loop` (cascada `send_ms`, bufferbloat, hallazgos P0/P1/P2, rediseño propuesto). Nota: el P0 "timeout" resultó inviable por el SDK; el camino válido es fire-and-forget.

---

### TL;DR para el próximo chat
1. Lee todo esto.
2. Lo siguiente es **correr el TEST PENDIENTE del §12** (master con sellos de tiempo + `wifi_probe`, haciendo la caminata de alejarse) y pedir al usuario los dos logs.
3. Con esos datos, decidir entre los caminos del §11 (bajar video / router / Ethernet) y contemplar la **lista de posibles del §13**.
4. No reabrir lo cerrado (throttle bueno, timeout descartado, odom no se toca, mapa = SLAM pendiente).
5. Trabaja según el §15 (breve, en seco, honesto, mide antes de tocar, no empujes a cerrar).
