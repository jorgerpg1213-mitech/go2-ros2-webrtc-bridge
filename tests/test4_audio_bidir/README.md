# Test 4 — Audio bidireccional + video + teleop + YOLO

Telepresencia: el operador habla desde la PC al robot y escucha el micro del
robot, mientras conduce con video en vivo y detección YOLO. Graba el audio
crudo del micrófono del robot.

## Qué hace
- PC -> robot: voz del operador por el micrófono de la PC.
- Robot -> PC: audio del micrófono del robot (reproducido en la PC).
- Captura: WAV crudo del mic del robot en `runs/audio_captures/`.
- Video + teleop + YOLO, igual que test1.

## Requisitos
- Venv master: `~/go2_legacy_env`
- Venv YOLO: `~/go2-yolo`
- AES key del robot exportada.
- **Auriculares en la PC** (evita el feedback acústico que degrada el audio).

## Cómo correr
```bash
export GO2_AES_KEY="tu_key"
./tests/test4_audio_bidir/go2_run_audio.sh
```
Teleop en otra terminal:
```bash
source ~/go2_legacy_env/bin/activate && python3 scripts/teleop_client.py
```
Cierre: Ctrl+C en la terminal del launcher (mata master + yolo limpio).

## Notas
- Sin YOLO: `ENABLE_YOLO=0 ./tests/test4_audio_bidir/go2_run_audio.sh`
- El WAV queda en `runs/<fecha>_audio/` y en `runs/audio_captures/`.
- LiDAR y ODOM apagados por default (no se necesitan para telepresencia).
