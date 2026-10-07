#!/usr/bin/env python3
"""Raspberry Pi edge node for the ISAC Smart Threat Monitor.

Pin map from the project report (BCM numbering):
  GPIO 17 (physical pin 11)  HC-SR501 PIR signal, input
  GPIO 23 (physical pin 16)  HC-SR04 TRIG, output
  GPIO 24 (physical pin 18)  HC-SR04 ECHO, input
  5V pin 2 / GND pin 6       sensor power
  CSI                        Raspberry Pi Camera Module v2

The HC-SR04 ECHO pin is 5V. The report uses a resistor divider
(1 kΩ series, 2 kΩ to ground) before GPIO 24.
"""

import base64
import json
import os
import signal
import sys
import time

import cv2
import paho.mqtt.client as mqtt

try:
    import RPi.GPIO as GPIO
    from picamera2 import Picamera2
except ImportError as exc:
    sys.exit(
        "Run this on the Raspberry Pi with RPi.GPIO and picamera2 installed.\n"
        f"Import failed: {exc}"
    )

PIR_PIN = 17
TRIG_PIN = 23
ECHO_PIN = 24

BROKER = os.environ.get("MQTT_BROKER", "192.168.1.100")
BROKER_PORT = int(os.environ.get("MQTT_PORT", "1883"))
SENSOR_INTERVAL = 1.0
FRAME_INTERVAL = 0.5


def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(PIR_PIN, GPIO.IN)
    GPIO.setup(TRIG_PIN, GPIO.OUT)
    GPIO.setup(ECHO_PIN, GPIO.IN)
    GPIO.output(TRIG_PIN, False)
    time.sleep(0.05)


def measure_distance():
    """HC-SR04 distance in centimetres, or -1 on timeout."""
    GPIO.output(TRIG_PIN, True)
    time.sleep(0.000012)  # 12 microseconds; raised from 10 us after echo timeouts
    GPIO.output(TRIG_PIN, False)

    pulse_start = time.time()
    timeout = pulse_start + 0.05
    while GPIO.input(ECHO_PIN) == 0:
        pulse_start = time.time()
        if pulse_start > timeout:
            return -1

    pulse_end = time.time()
    timeout = pulse_end + 0.05
    while GPIO.input(ECHO_PIN) == 1:
        pulse_end = time.time()
        if pulse_end > timeout:
            return -1

    duration = pulse_end - pulse_start
    distance = (duration * 34300) / 2
    return round(distance, 2)


def setup_camera():
    picam2 = Picamera2()
    picam2.configure(picam2.create_still_configuration(main={"size": (640, 480)}))
    picam2.start()
    time.sleep(1)
    return picam2


def publish_camera_frame(client, picam2):
    frame = picam2.capture_array()
    if frame is not None and frame.ndim == 3 and frame.shape[2] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
    elif frame is not None and frame.ndim == 3 and frame.shape[2] == 3:
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if not ok:
        return

    payload = json.dumps(
        {
            "frame": base64.b64encode(buffer).decode("utf-8"),
            "ts": time.time(),
        }
    )
    client.publish("isa/camera/frame", payload, qos=1)
    client.publish("camera/frame", payload, qos=1)


def publish_sensors(client, pir, distance):
    client.publish("isac/pir", str(int(pir)), qos=0)
    client.publish("isa/sensors/pir", json.dumps({"value": int(pir)}), qos=0)
    if distance >= 0:
        client.publish("isac/distance", str(distance), qos=0)
    client.publish(
        "isa/sensors/ultrasonic",
        json.dumps({"distance_cm": distance}),
        qos=0,
    )


def main():
    setup_gpio()
    picam2 = setup_camera()

    client = mqtt.Client()
    client.connect(BROKER, BROKER_PORT, 60)
    client.loop_start()
    print(f"ISAC sensor node connected to {BROKER}:{BROKER_PORT}")

    def shutdown(*_args):
        print("Stopping sensor node...")
        client.loop_stop()
        client.disconnect()
        picam2.stop()
        GPIO.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    last_sensor = 0.0
    last_frame = 0.0
    while True:
        now = time.time()
        if now - last_sensor >= SENSOR_INTERVAL:
            pir = GPIO.input(PIR_PIN)
            distance = measure_distance()
            publish_sensors(client, pir, distance)
            print(f"PIR={pir} distance={distance} cm")
            last_sensor = now
        if now - last_frame >= FRAME_INTERVAL:
            publish_camera_frame(client, picam2)
            last_frame = now
        time.sleep(0.02)


if __name__ == "__main__":
    main()
