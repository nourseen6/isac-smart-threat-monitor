import csv
import importlib
import io
import os
import sys
import threading
import time
import zipfile
from collections import deque
from datetime import datetime
from xml.sax.saxutils import escape

from flask import Flask, Response, jsonify, render_template, request, send_file

def _firebase_credentials_path():
    configured = os.environ.get("FIREBASE_CREDENTIALS", "").strip()
    if configured:
        return configured

    here = os.path.dirname(os.path.abspath(__file__))
    for name in os.listdir(here):
        if "firebase-adminsdk" in name and name.endswith(".json"):
            return os.path.join(here, name)
    return ""


# Firebase — optional cloud logging (safe, non-blocking).
# The service-account JSON stays on the machine and is not part of the repo.
try:
    import firebase_logger

    _credentials = _firebase_credentials_path()
    if _credentials:
        firebase_logger.init_firebase(
            credentials_path=_credentials,
            database_url=os.environ.get(
                "FIREBASE_DATABASE_URL",
                "https://isac-7e65e-default-rtdb.firebaseio.com/",
            ),
        )
except Exception:
    pass  # Firebase missing entirely — system runs normally


app = Flask(__name__)

APP_STARTED_AT = time.time()
HEALTH_WINDOW_SECONDS = 15

alerts = deque(maxlen=500)
events = deque(maxlen=1000)
last_topic_seen = {}
stats = {
    "yolo_person_detections": 0,
}

state_lock = threading.RLock()
backend_error = None
backend_thread = None


def local_timestamp():
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def append_event(kind, message, source="backend"):
    with state_lock:
        events.append(
            {
                "time": local_timestamp(),
                "epoch": time.time(),
                "kind": kind,
                "source": source,
                "message": message,
            }
        )


def parse_backend_decision(lines):
    values = {}

    for line in lines:
        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        values[key.strip().lower()] = value.strip()

    if "threat level" not in values and "message" not in values:
        return

    alert = {
        "time": local_timestamp(),
        "epoch": time.time(),
        "pir": values.get("pir", ""),
        "distance": values.get("distance", ""),
        "yolo": values.get("yolo", ""),
        "threat": values.get("threat level", "UNKNOWN"),
        "message": values.get("message", "Awaiting backend decision"),
    }

    with state_lock:
        alerts.append(alert)

        events.append(
            {
                "time": alert["time"],
                "epoch": alert["epoch"],
                "kind": "decision",
                "source": "sensor-fusion",
                "message": f"{alert['threat']} - {alert['message']}",
            }
        )

    # Firebase cloud logging
    try:
        firebase_logger.log_alert(alert)

    except Exception:
        pass

def parse_backend_line(line):
    clean = line.strip()

    if not clean:
        return

    if clean.startswith("[") and "] -> " in clean:
        topic, payload = clean[1:].split("] -> ", 1)

        with state_lock:
            last_topic_seen[topic] = time.time()

            if topic == "yolo/detection" and payload.strip().lower() == "person":
                stats["yolo_person_detections"] += 1

            events.append(
                {
                    "time": local_timestamp(),
                    "epoch": time.time(),
                    "kind": "sensor",
                    "source": topic,
                    "message": payload.strip(),
                }
            )

        return

    if "ISAC Backend Started" in clean:
        append_event("system", "ISAC Backend Started", "backend")


class BackendStdoutCapture:
    def __init__(self, original_stdout):
        self.original_stdout = original_stdout
        self.buffer = ""
        self.capture_lock = threading.RLock()
        self.capturing_decision = False
        self.decision_lines = []

    def write(self, text):
        try:
            self.original_stdout.write(text)
        except UnicodeEncodeError:
            if hasattr(self.original_stdout, "buffer"):
                self.original_stdout.buffer.write(text.encode("utf-8"))
            else:
                encoding = getattr(self.original_stdout, "encoding", "utf-8")
                self.original_stdout.write(text.encode(encoding, "replace").decode(encoding))

        with self.capture_lock:
            self.buffer += text

            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                self._process_complete_line(line.rstrip("\r"))

    def flush(self):
        self.original_stdout.flush()

    def isatty(self):
        return self.original_stdout.isatty()

    @property
    def encoding(self):
        return getattr(self.original_stdout, "encoding", "utf-8")

    def __getattr__(self, name):
        return getattr(self.original_stdout, name)

    def _process_complete_line(self, line):
        clean = line.strip()

        if clean == "========== ISAC DECISION ==========":
            self.capturing_decision = True
            self.decision_lines = [clean]
            return

        if self.capturing_decision:
            self.decision_lines.append(clean)

            if clean == "===================================":
                parse_backend_decision(self.decision_lines)
                self.capturing_decision = False
                self.decision_lines = []

            return

        parse_backend_line(clean)


def get_backend_module():
    return sys.modules.get("backend")


def import_backend_forever():
    global backend_error

    try:
        importlib.import_module("backend")
    except Exception as exc:
        backend_error = f"{type(exc).__name__}: {exc}"
        append_event("error", backend_error, "backend-import")


def start_backend_thread():
    global backend_thread

    if backend_thread and backend_thread.is_alive():
        return

    if not isinstance(sys.stdout, BackendStdoutCapture):
        sys.stdout = BackendStdoutCapture(sys.stdout)

    backend_thread = threading.Thread(target=import_backend_forever, daemon=True)
    backend_thread.start()


def safe_bool(value):
    try:
        return bool(value())
    except Exception:
        return False


def health_from_seen(topic_names, online_label, waiting_label, stale_label):
    now = time.time()

    with state_lock:
        seen_times = [last_topic_seen.get(topic) for topic in topic_names]

    seen_times = [seen for seen in seen_times if seen is not None]

    if not seen_times:
        return {"ok": False, "state": waiting_label}

    if now - max(seen_times) <= HEALTH_WINDOW_SECONDS:
        return {"ok": True, "state": online_label}

    return {"ok": False, "state": stale_label}


def current_status_payload():
    module = get_backend_module()
    client = getattr(module, "client", None) if module else None

    with state_lock:
        latest_alert = alerts[-1].copy() if alerts else None

    mqtt_connected = safe_bool(client.is_connected) if client else False

    if backend_error:
        system_state = "BACKEND ERROR"
    elif mqtt_connected:
        system_state = "MONITORING"
    elif module:
        system_state = "BACKEND STARTING"
    else:
        system_state = "BOOTING"

    sensors = {
        "pir": getattr(module, "pir_status", None) if module else None,
        "distance": getattr(module, "distance", None) if module else None,
        "yolo": getattr(module, "yolo_result", None) if module else None,
    }

    decision = latest_alert or {
        "time": None,
        "pir": "",
        "distance": "",
        "yolo": "",
        "threat": "UNKNOWN",
        "message": "Awaiting backend decision",
    }

    return {
        "timestamp": local_timestamp(),
        "system_state": system_state,
        "backend": {
            "loaded": module is not None,
            "thread_alive": backend_thread.is_alive() if backend_thread else False,
            "error": backend_error,
        },
        "sensors": sensors,
        "decision": decision,
        "health": {
            "mqtt": {
                "ok": mqtt_connected,
                "state": "CONNECTED" if mqtt_connected else "DISCONNECTED",
            },
            "raspberry_pi": health_from_seen(
                ["isac/pir", "isac/distance"],
                "ONLINE",
                "WAITING FOR SENSOR DATA",
                "STALE SENSOR DATA",
            ),
            "camera": health_from_seen(
                ["yolo/detection"],
                "ACTIVE",
                "WAITING FOR CAMERA DATA",
                "NO RECENT CAMERA DATA",
            ),
            "yolo": health_from_seen(
                ["yolo/detection"],
                "RUNNING",
                "WAITING FOR YOLO DATA",
                "NO RECENT YOLO DATA",
            ),
        },
    }


def format_duration(seconds):
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m {secs}s"

    if minutes:
        return f"{minutes}m {secs}s"

    return f"{secs}s"


def statistics_payload():
    with state_lock:
        alert_snapshot = list(alerts)
        false_alarms = sum(
            1 for alert in alert_snapshot if "false alarm" in alert["message"].lower()
        )
        yolo_person_detections = stats["yolo_person_detections"]

    return {
        "total_detections": len(alert_snapshot),
        "false_alarms": false_alarms,
        "system_uptime": format_duration(time.time() - APP_STARTED_AT),
        "detection_count": yolo_person_detections,
    }


def rows_for_dataset(dataset):
    normalized = (dataset or "alerts").lower()

    with state_lock:
        alert_snapshot = list(alerts)
        event_snapshot = list(events)

    if normalized in {"logs", "sensor-logs", "fusion-logs"}:
        headers = ["Time", "Type", "Source", "Message"]
        rows = [
            [event["time"], event["kind"], event["source"], event["message"]]
            for event in event_snapshot
        ]
        filename = "sensor-fusion-logs"
        sheet_name = "Fusion Logs"
    else:
        headers = ["Time", "PIR", "Distance", "YOLO", "Threat", "Message"]
        rows = [
            [
                alert["time"],
                alert["pir"],
                alert["distance"],
                alert["yolo"],
                alert["threat"],
                alert["message"],
            ]
            for alert in alert_snapshot
        ]
        filename = "live-alerts"
        sheet_name = "Live Alerts"

    return headers, rows, filename, sheet_name


def column_name(index):
    name = ""

    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name

    return name


def safe_xml_text(value):
    text = "" if value is None else str(value)
    text = "".join(char for char in text if char in "\t\n\r" or ord(char) >= 32)
    return escape(text)


def build_xlsx(headers, rows, sheet_name):
    workbook = io.BytesIO()
    all_rows = [headers] + rows

    sheet_rows = []
    for row_number, row in enumerate(all_rows, start=1):
        cells = []

        for column_number, value in enumerate(row, start=1):
            cell_ref = f"{column_name(column_number)}{row_number}"
            cells.append(
                f'<c r="{cell_ref}" t="inlineStr"><is><t>{safe_xml_text(value)}</t></is></c>'
            )

        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData>"
        "</worksheet>"
    )

    safe_sheet_name = safe_xml_text(sheet_name[:31])

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        f'<sheet name="{safe_sheet_name}" sheetId="1" r:id="rId1"/>'
        "</sheets>"
        "</workbook>"
    )

    with zipfile.ZipFile(workbook, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    workbook.seek(0)
    return workbook


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/status")
def status():
    return jsonify(current_status_payload())


@app.route("/alerts")
def alert_history():
    with state_lock:
        alert_snapshot = list(alerts)
        event_snapshot = list(events)

    return jsonify(
        {
            "alerts": list(reversed(alert_snapshot)),
            "events": list(reversed(event_snapshot[-100:])),
        }
    )


@app.route("/statistics")
def statistics():
    return jsonify(statistics_payload())


@app.route("/export-csv")
def export_csv():
    headers, rows, filename, _sheet_name = rows_for_dataset(request.args.get("dataset"))
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}.csv",
            "Cache-Control": "no-store",
        },
    )


@app.route("/export-excel")
def export_excel():
    headers, rows, filename, sheet_name = rows_for_dataset(request.args.get("dataset"))
    workbook = build_xlsx(headers, rows, sheet_name)

    return send_file(
        workbook,
        as_attachment=True,
        download_name=f"{filename}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        max_age=0,
    )


start_backend_thread()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
