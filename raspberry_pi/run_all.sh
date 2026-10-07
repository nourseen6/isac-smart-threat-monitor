#!/bin/bash
# Starts the Raspberry Pi edge node: PIR, ultrasonic, camera, and MQTT publish.
cd "$(dirname "$0")"
exec python3 sensor_node.py
