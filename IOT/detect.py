"""Laptop-side YOLOv8 person detection for the ISAC Smart Threat Monitor.

Subscribes to camera frames from the Raspberry Pi and publishes:
  yolo/detection          "person" or "none"  (dashboard backend)
  isa/detection/result    JSON detection payload (report topic)
"""

import base64
import json
import os

import cv2
import numpy as np
import paho.mqtt.client as mqtt
from ultralytics import YOLO

PERSON_CLASS_ID = 0
CONFIDENCE_THRESHOLD = 0.45
BROKER = os.environ.get("MQTT_BROKER", "Nourseen.local")
BROKER_PORT = int(os.environ.get("MQTT_PORT", "1883"))

model = YOLO(os.environ.get("YOLO_MODEL", "yolov8n.pt"))


def on_frame_received(client, userdata, message):
    data = json.loads(message.payload.decode())
    img_data = base64.b64decode(data["frame"])
    frame = cv2.imdecode(np.frombuffer(img_data, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return

    results = model(frame, classes=[PERSON_CLASS_ID], verbose=False)
    detections = []
    for box in results[0].boxes:
        conf = float(box.conf[0])
        if conf >= CONFIDENCE_THRESHOLD:
            detections.append(
                {"conf": round(conf, 3), "bbox": box.xyxy[0].tolist()}
            )

    detected = len(detections) > 0
    client.publish(
        "isa/detection/result",
        json.dumps(
            {
                "detected": detected,
                "count": len(detections),
                "max_conf": max((item["conf"] for item in detections), default=0.0),
                "detections": detections,
                "ts": data.get("ts"),
            }
        ),
        qos=1,
    )
    client.publish("yolo/detection", "person" if detected else "none", qos=1)
    print("person" if detected else "none")


def main():
    client = mqtt.Client()
    client.on_message = on_frame_received
    client.connect(BROKER, BROKER_PORT, 60)
    client.subscribe("isa/camera/frame")
    client.subscribe("camera/frame")
    print(f"YOLOv8 detector listening on {BROKER}:{BROKER_PORT}")
    client.loop_forever()


if __name__ == "__main__":
    main()
