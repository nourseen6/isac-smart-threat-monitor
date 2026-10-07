# firebase_logger.py
# Safe, isolated Firebase integration — errors never propagate to main system

import threading
import time

_firebase_enabled = False
_db = None

def init_firebase(credentials_path, database_url):
    """
    Call this once at startup. If it fails, Firebase is silently disabled.
    """
    global _firebase_enabled, _db

    try:
        import firebase_admin
        from firebase_admin import credentials, db

        cred = credentials.Certificate(credentials_path)
        firebase_admin.initialize_app(cred, {"databaseURL": database_url})
        _db = db
        _firebase_enabled = True
        print("[Firebase] Cloud logging enabled.")

    except Exception as exc:
        print(f"[Firebase] Disabled — could not initialize: {exc}")
        _firebase_enabled = False


def log_alert(alert: dict):
    """
    Push a copy of an alert to Firebase in a background thread.
    Completely non-blocking. If it fails, nothing breaks.
    """
    if not _firebase_enabled:
        return

    def _push():
        try:
            ref = _db.reference("isac/alerts")
            ref.push({
                "time":     alert.get("time", ""),
                "threat":   alert.get("threat", "UNKNOWN"),
                "message":  alert.get("message", ""),
                "pir":      alert.get("pir", ""),
                "distance": alert.get("distance", ""),
                "yolo":     alert.get("yolo", ""),
                "epoch":    alert.get("epoch", time.time()),
            })
        except Exception as exc:
            print(f"[Firebase] Alert push failed (non-critical): {exc}")

    threading.Thread(target=_push, daemon=True).start()


def log_event(kind: str, message: str, source: str = "system"):
    """
    Push a system event to Firebase. Non-blocking, fail-safe.
    """
    if not _firebase_enabled:
        return

    def _push():
        try:
            ref = _db.reference("isac/events")
            ref.push({
                "time":    time.strftime("%Y-%m-%d %H:%M:%S"),
                "kind":    kind,
                "source":  source,
                "message": message,
                "epoch":   time.time(),
            })
        except Exception as exc:
            print(f"[Firebase] Event push failed (non-critical): {exc}")

    threading.Thread(target=_push, daemon=True).start()