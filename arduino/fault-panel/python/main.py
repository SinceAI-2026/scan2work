"""Fault panel: the Linux (Python) half of the UNO Q app.

The board shows the first broken thing in the Scan2Work twin until its job is done:
  - on start it claims the first item of the server's inspection queue (whatever data the server runs on);
  - once a second it sends a heartbeat (with its A0 touch pad level) and gets back what to show:
      fault        red blink, matrix: the worst signal's two bars, normal (dim) vs now (bright)
      in_progress  amber, matrix: one column per checklist step, lit when done
      done / clear green, matrix: a tick. A done task stays done until the board restarts (or a hold).
  - touch A0 (or bridge D13 to GND) briefly = acknowledge (the job starts), hold = claim the newest fault now.

Settings: PLANT_API (the server's address, e.g. https://scan2work.example.com or http://<laptop IP>:8765) and
LIVE_TOKEN (if the server sets one), from the environment or from board.env next to this file (see board.env.example).
"""
import json
import os
import queue
import time
import urllib.request
from pathlib import Path

from arduino.app_utils import App, Bridge


def _settings():
    vals = {}
    path = Path(__file__).with_name("board.env")
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                vals[key.strip()] = value.strip().strip('"').strip("'")
    get = lambda k, default="": os.environ.get(k) or vals.get(k) or default
    return get("PLANT_API", "http://10.60.2.242:8765").rstrip("/"), get("LIVE_TOKEN")


PLANT_API, LIVE_TOKEN = _settings()
TICK_S = 0.2
BEAT_S = 1.0

# --- LED matrix frames (8 rows x 13 columns, brightness 0..7, row-major) ------------------------------
ROWS, COLS = 8, 13
TICK = ["............#", "...........#.", "..........#..", "#........#...", ".#......#....", "..#....#.....", "...#..#......", "....##......."]
# sketch status codes -> RGB LED 3: 0 green, 1 red blink, 2 amber, 3 blue pulse, 4 purple slow blink (no link)
LED = {"fault": 1, "in_progress": 2, "done": 0, "clear": 0, "claiming": 3}
NO_LINK = 4


def bar(px, row, pct, level):
    for c in range(round(max(0.0, min(100.0, pct)) / 100 * COLS)):
        px[row * COLS + c] = level


def render(st, t):
    px = [0] * (ROWS * COLS)
    status, task = st.get("status"), st.get("task") or {}
    if status in ("done", "clear"):
        for r, line in enumerate(TICK):
            for c, ch in enumerate(line):
                if ch == "#":
                    px[r * COLS + c] = 6
    elif status == "in_progress":
        total, done = task.get("checks_total") or 0, task.get("checks_done") or 0
        for k in range(min(total, COLS)):
            for r in range(2, 6):
                px[r * COLS + k] = 7 if k < done else 1
    elif status == "fault":
        sig = task.get("signal")
        if sig:
            for r in (0, 1, 2):
                bar(px, r, sig["normal_pct"], 2)   # normal (3-day mean)
            for r in (4, 5, 6):
                bar(px, r, sig["now_pct"], 7)      # now
        for c in range(COLS):                       # bottom row: alarm ticker
            px[7 * COLS + c] = 7 if (c + int(t * 8)) % 4 == 0 else 0
    return bytes(px)


# --- server -----------------------------------------------------------------------------------------
def post(path, body):
    headers = {"content-type": "application/json"}
    if LIVE_TOKEN:
        headers["x-live-token"] = LIVE_TOKEN
    req = urllib.request.Request(PLANT_API + path, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=4) as res:
        return json.loads(res.read())


state = {"status": "claiming", "task": None}
link = {"ok": False, "claimed": False, "beat": 0.0}
actions: "queue.Queue[str]" = queue.Queue()
touch = {"swing": None, "base": None, "touched": False, "printed": 0.0}


def on_button(kind):
    """From the sketch: 1 = tap, 2 = hold. Runs on the Bridge thread, so just queue it."""
    actions.put("ack" if int(kind) == 1 else "claim")


def on_touch_level(p2p, base, touched):
    """From the sketch 10x per second: A0 swing vs the learned untouched level (sent with every heartbeat)."""
    was = touch["touched"]
    touch.update(swing=int(p2p), base=int(base), touched=bool(touched))
    if bool(touched) != was or time.time() - touch["printed"] > 2:
        touch["printed"] = time.time()
        print(f"touch A0: swing {p2p}, untouched {base}{'  TOUCHED' if touched else ''}")


def talk(path, body):
    try:
        res = post(path, body)
        state.update(status=res["status"], task=res.get("task"))
        if not link["ok"]:
            print(f"linked to {PLANT_API}: {res['status']} {(res.get('task') or {}).get('tag', '')}")
        link["ok"] = True
        return True
    except Exception as e:  # noqa: BLE001
        if link["ok"]:
            print("server unreachable:", e)
        link["ok"] = False
        return False


def loop():
    now = time.time()
    if not link["claimed"]:   # on start: take the newest fault (retried until the server answers)
        link["claimed"] = talk("/api/live/board/claim", {"by": "board"})
        link["beat"] = now
    while not actions.empty():
        talk("/api/live/board/action", {"action": actions.get(), "by": "board"})
    if link["claimed"] and now - link["beat"] >= BEAT_S:
        link["beat"] = now
        talk("/api/live/board/heartbeat", {"touch": {k: touch[k] for k in ("swing", "base", "touched")}})
    Bridge.call("set_status", LED.get(state["status"], 0) if link["ok"] else NO_LINK)
    Bridge.call("draw", render(state, now))
    time.sleep(TICK_S)


Bridge.provide("button", on_button)
Bridge.provide("touch_level", on_touch_level)
print(f"Fault panel -> {PLANT_API}")
App.run(user_loop=loop)
