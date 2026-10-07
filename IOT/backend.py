import os
import time

import paho.mqtt.client as mqtt

# =========================
# GLOBAL VALUES
# =========================
pir_status = 0
distance = 999
yolo_result = "none"

last_alert_time = 0
alert_cooldown = 5  # seconds

# =========================
# MQTT CALLBACK
# =========================
def on_message(client, userdata, msg):

    global pir_status
    global distance
    global yolo_result
    global last_alert_time

    topic = msg.topic
    payload = msg.payload.decode()

    print(f"[{topic}] -> {payload}")

    # =========================
    # PIR DATA
    # =========================
    if topic == "isac/pir":

        pir_status = int(payload)

    # =========================
    # ULTRASONIC DATA
    # =========================
    elif topic == "isac/distance":

        distance = float(payload)

    # =========================
    # YOLO DATA
    # =========================
    elif topic == "yolo/detection":

        yolo_result = payload

    # =========================
    # SENSOR FUSION
    # =========================
    threat = "LOW"
    message = "System normal"

    # =========================
    # PIR ONLY
    # =========================
    if pir_status == 1:

        threat = "MEDIUM"
        message = "Motion detected"

    # =========================
    # YOLO ONLY
    # =========================
    if yolo_result == "person":

        threat = "MEDIUM"
        message = "Person detected"

    # =========================
    # MOTION + CLOSE OBJECT
    # =========================
    if pir_status == 1 and distance < 200:

        threat = "MEDIUM"
        message = "Object detected nearby"

    # =========================
    # FULL HUMAN DETECTION
    # =========================
    if pir_status == 1 and distance < 200 and yolo_result == "person":

        threat = "HIGH"
        message = "High confidence human detection"

    # =========================
    # FALSE ALARM
    # =========================
    if pir_status == 1 and yolo_result != "person":

        threat = "MEDIUM"
        message = "False alarm / unknown movement"

    # =========================
    # SMART ALERT FILTER
    # =========================
    current_time = time.time()

    if current_time - last_alert_time > alert_cooldown:

        print("\n========== ISAC DECISION ==========")
        print("PIR:", pir_status)
        print("Distance:", distance)
        print("YOLO:", yolo_result)
        print("Threat Level:", threat)
        print("Message:", message)
        print("===================================\n")

        last_alert_time = current_time

# =========================
# MQTT SETUP
# =========================
client = mqtt.Client()

MQTT_BROKER = os.environ.get("MQTT_BROKER", "Nourseen.local")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
client.connect(MQTT_BROKER, MQTT_PORT, 60)

# =========================
# SUBSCRIBE TO TOPICS
# =========================
client.subscribe("isac/pir")
client.subscribe("isac/distance")
client.subscribe("yolo/detection")

client.on_message = on_message

print("🚀 ISAC Backend Started...")

client.loop_forever()