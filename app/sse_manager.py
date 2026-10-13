import json
import queue
import threading
import time

_subscribers = {}
_lock = threading.Lock()


def subscribe():
    sid = str(time.time_ns())
    q = queue.Queue(maxsize=100)
    with _lock:
        _subscribers[sid] = q
    return sid, q


def unsubscribe(sid):
    with _lock:
        _subscribers.pop(sid, None)


def broadcast(event_data):
    payload = 'data: ' + json.dumps(event_data) + '\n\n'
    with _lock:
        subscribers = list(_subscribers.values())
    for q in subscribers:
        try:
            q.put_nowait(payload)
        except Exception:
            pass
