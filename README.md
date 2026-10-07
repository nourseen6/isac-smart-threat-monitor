# ISAC Smart Threat Monitor

An AIoT monitoring system for the Internet of Things course (CCS329) at AlRyada University. The Raspberry Pi collects PIR motion, ultrasonic distance, and camera frames. The laptop runs YOLOv8 inference and the monitoring dashboard.

## Hardware

![Assembled node: Raspberry Pi 4, PIR, ultrasonic sensor, and camera](docs/figures/09-hardware-assembly.jpeg)

| Component | Wiring | Role |
| --- | --- | --- |
| Raspberry Pi 4 Model B (4GB) | Ethernet to the local network | Sensor node |
| HC-SR501 PIR | Signal → GPIO 17 (pin 11), VCC → 5V (pin 2), GND → pin 6 | Motion detection |
| HC-SR04 ultrasonic | TRIG → GPIO 23 (pin 16), ECHO → GPIO 24 (pin 18) | Distance measurement |
| Raspberry Pi Camera Module v2 | CSI port | 640×480 frames for YOLOv8 |
| Breadboard and jumper wires | — | GPIO wiring |

The HC-SR04 ECHO pin outputs 5V, while a Raspberry Pi GPIO input is rated for 3.3V. The report uses a voltage divider: a 1 kΩ resistor in series with ECHO, and a 2 kΩ resistor from GPIO 24 to ground. TRIG is driven directly by the 3.3V GPIO output.

The wiring diagram and the rest of the architecture are in this figure:

![System architecture and GPIO wiring](docs/figures/04-system-architecture.jpeg)

Other figures from the report:

- [Use case](docs/figures/01-use-case-diagram.jpeg)
- [Sequence diagram](docs/figures/02-sequence-diagram.jpeg)
- [DFD](docs/figures/03-dfd-level1.jpeg)
- [Login](docs/figures/05-dashboard-login.jpeg)
- [HIGH threat](docs/figures/06-dashboard-high-threat.jpeg)
- [UNKNOWN during startup](docs/figures/07-dashboard-unknown.jpeg)
- [Statistics](docs/figures/08-dashboard-statistics.jpeg)

## Project layout

```
IOT/                 Flask dashboard, sensor fusion, and YOLOv8 (laptop)
raspberry_pi/        Raspberry Pi code (PIR, ultrasonic, camera)
docs/figures/        Hardware photos and diagrams from the report
```

## How to run

The MQTT broker listens on port 1883. Laptop scripts default to `Nourseen.local`. The Raspberry Pi script defaults to `192.168.1.100`, as in the report. Set `MQTT_BROKER` if the network address is different.

On the laptop, open three terminals:

```powershell
cd IOT
pip install -r ..\requirements.txt
python detect.py
python backend.py
python app.py
```

The dashboard is at `http://localhost:5000`.

On the Raspberry Pi:

```bash
sudo apt update
sudo apt install -y python3-rpi.gpio python3-picamera2
pip3 install -r requirements.txt
export MQTT_BROKER=192.168.1.100
chmod +x run_all.sh
./run_all.sh
```

`sensor_node.py` publishes:

| Topic | Payload |
| --- | --- |
| `isac/pir` | `0` or `1` |
| `isac/distance` | Distance in centimetres |
| `camera/frame` and `isa/camera/frame` | Base64-encoded JPEG frame |
| `yolo/detection` | `person` or `none` (published by `detect.py` on the laptop) |

## Firebase

Cloud logging is optional. Place the service-account file in `IOT/`, or set its path:

```powershell
$env:FIREBASE_CREDENTIALS = "C:\path\to\firebase-adminsdk.json"
$env:FIREBASE_DATABASE_URL = "https://isac-7e65e-default-rtdb.firebaseio.com/"
```

The service-account key is not in this repository.
