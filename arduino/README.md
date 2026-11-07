# Fault panel (Arduino UNO Q)

The board shows the **first broken item** in the Field Atlas twin until its job is done. It knows nothing about the
plant: whatever data the server was built from, the board takes the top of the inspection queue (the viewer's
"To inspect" list, worst first).

| Board | Meaning | In the app |
|---|---|---|
| Red blinking LED; matrix: two bars, normal (dim) and now (bright), alarm ticker on the bottom row | Fault, no job yet. The bars are the asset's signal furthest from its 3-day mean | The **Arduino panel** card shows the tag, issue and signal. Snowflake event `FAULT_DETECTED` |
| **Tap** (touch A0 briefly) → amber LED; matrix: one column per checklist step | Acknowledged: the technician is on site, so the job starts | The job opens in "To inspect", like starting it from the panel or the voice assistant. Event `ACKNOWLEDGED` |
| Green LED, tick on the matrix | The job was closed as fixed in the app (panel or voice assistant), so the item left the queue | Event `RESTORED`. The board stays green |
| **Hold** 1.2 s, or restart the board | Take the newest first fault now | – |
| Purple slow blink | No connection to the server | Card says "Board offline" |

A done task stays done. The board takes the newest fault the next time it starts, so after a fix you restart it
(or hold) to get the next one.

## Connecting the board

The board only needs to reach the server over HTTP(S). It sends a request once a second, and the server never
connects to the board, so no port forwarding is needed.

1. Copy `fault-panel/python/board.env.example` to `fault-panel/python/board.env` (git-ignored) and set:
   - `PLANT_API`: the server's address.
     - **Deployed:** the public HTTPS address, e.g. `https://field-atlas.example.com`. The board can be on any
       network with internet, such as a phone hotspot.
     - **Laptop:** `http://<laptop IP>:8765`, with the server started as `.venv/bin/python server.py 8765 --lan` and
       the board on the same network. Find the IP with `ipconfig getifaddr en0` (macOS).
   - `LIVE_TOKEN`: the same value as `LIVE_TOKEN` in the server's `live/.env`. With a token set, only the board can
     claim and send heartbeats, so nobody else can fake "Board online". Set one whenever the server is public.
2. Put the app on the board: open `fault-panel` in **Arduino App Lab** and press **Run**. Or, with the board on USB-C,
   run `arduino/deploy.sh` (add `--logs` to follow the console). It copies the app, including `board.env`, over adb.
3. Within a few seconds the viewer's **Arduino panel** card (left panel) says **Board online** and shows the fault.

The matrix shows the Arduino logo for the first ~20–30 s after power-on: boot the board before the demo.

The touch pad: touch the **A0** contact on the header with a fingertip, or with a key or coin held in your fingers
(your body picks up mains hum, which the board detects). Or bridge **D13** and the **GND** pin next to it with a
paperclip. The App Lab console prints `touch A0: swing …, untouched …`. If touches are missed or fire on their own,
adjust `TOUCH_FACTOR` / `TOUCH_MIN` in the sketch.

## Tiger Data and Snowflake (optional)

Only the panel uses them (`live/`); the rest of Field Atlas never touches them, and the panel works without them.

- **Tiger Data:** one row per heartbeat in `board_heartbeats`: which fault the board shows, its status and its A0
  touch pad level.
- **Snowflake:** the panel's event log in `DEVICE_EVENTS` (claimed, acknowledged, done).

Copy `live/.env.example` to `live/.env`, fill it in, then once from the repo root:

```sh
.venv/bin/python -m pip install -r live/requirements.txt
.venv/bin/python -m live.setup_tiger        # board_heartbeats
.venv/bin/python -m live.setup_snowflake    # DEVICE_EVENTS
```

## Demo script

1. Start the board. It turns red and shows the first fault's worst signal; the card shows the same.
2. **Tap**: amber. The job is open in "To inspect".
3. Work the job in the app (tick the checks, close it as fixed), or let the voice assistant do it. The board turns green.
4. **Hold**, or restart the board: it takes the next fault.

The card's **Acknowledge** and **Show newest** buttons do the same as tap and hold.

## API (server.py → live/board.py)

- `GET /api/live/board`: state (status, task with tag, issue and signal, next fault, board online, store status)
- `POST /api/live/board/claim`: take the first broken item as the task (the board calls it on start). Token
- `POST /api/live/board/heartbeat` `{"touch": {"swing", "base", "touched"}}`: once a second, returns the state. Token
- `POST /api/live/board/action` `{"action": "ack" | "claim"}`: tap and hold, also the card's buttons
- `GET /api/live/board/events`: the panel's event log

## Files

- `fault-panel/sketch/sketch.ino`: microcontroller. Draws the frames on the matrix, drives RGB LED 3, reads A0 touch
  and D13 (tap/hold) and tells Python over the Bridge.
- `fault-panel/python/main.py`: Linux side. Claims the fault, sends heartbeats and button actions, renders the frames.
- `deploy.sh`: copy and start the app over adb, without App Lab.
