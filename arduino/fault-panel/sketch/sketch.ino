// Fault panel: the microcontroller half of the UNO Q app (unchanged from the CV-003 positioner it started as).
//
//  - LED matrix (8 x 13): the panel's display. The Python side draws each frame and sends it here.
//  - RGB LED 3: status light. Python sets the status code; the blinking happens here.
//    ST_OK green = done / nothing broken, ST_ALARM red blink = fault, ST_ISOLATED amber = job in progress,
//    ST_TESTING blue pulse = claiming, otherwise purple slow blink = no link to the server.
//  - Button, two ways (either works): touch the A0 header contact with a fingertip or a key/coin, or short
//    D13 to the GND pin next to it. Tap = acknowledge (the job starts), hold 1.2 s = take the newest fault.
//    Each gesture is sent to Python, which tells the server.

#include <Arduino_LED_Matrix.h>
#include <Arduino_RouterBridge.h>
#include <vector>
#include <zephyr/kernel.h>

Arduino_LED_Matrix matrix;

const int BUTTON_PIN = D13;  // next to GND on the UNO header, so one paperclip bridges the two
const unsigned long DEBOUNCE_MS = 30;
const unsigned long HOLD_MS = 1200;
const int FRAME_LEN = 8 * 13;

// Bridge providers run on their own thread: they only copy data in, loop() does all the drawing.
K_MUTEX_DEFINE(frame_mtx);
static uint8_t frame[FRAME_LEN];
static volatile bool frame_dirty = false;

enum Status { ST_OK = 0, ST_ALARM = 1, ST_ISOLATED = 2, ST_TESTING = 3, ST_NO_LINK = 4 };
static volatile int status_code = ST_NO_LINK;

void draw(std::vector<uint8_t> f) {
  if ((int)f.size() < FRAME_LEN) return;
  k_mutex_lock(&frame_mtx, K_FOREVER);
  memcpy(frame, f.data(), FRAME_LEN);
  frame_dirty = true;
  k_mutex_unlock(&frame_mtx);
}

void set_status(int s) { status_code = s; }

void led(int r, int g, int b) {  // LED 3 is PWM: 0 = off, 255 = full
  analogWrite(LED3_R, r);
  analogWrite(LED3_G, g);
  analogWrite(LED3_B, b);
}

void update_led(unsigned long now) {
  bool blink = (now / 250) % 2;        // 2 Hz
  bool slow = (now / 600) % 2;
  int tri = now % 1200;  // triangle wave 0..600..0 over 1.2 s (no libm on this core)
  float pulse = (tri < 600 ? tri : 1200 - tri) / 600.0f;
  switch (status_code) {
    case ST_OK:       led(0, 120, 0); break;
    case ST_ALARM:    blink ? led(255, 0, 0) : led(0, 0, 0); break;
    case ST_ISOLATED: led(255, 90, 0); break;
    case ST_TESTING:  led(0, 0, (int)(40 + 215 * pulse)); break;
    default:          slow ? led(120, 0, 120) : led(0, 0, 0); break;  // no link to the server
  }
}

// Touch without any parts: a fingertip (or a key/coin held in the fingers) on the A0 header contact.
// The body picks up mains hum, so the pin's peak-to-peak swing over one mains period jumps when touched.
// The untouched level is learned continuously. "touch_level" goes to Python 10x per second: it is streamed
// with the readings, so the viewer shows the A0 value live.
const int TOUCH_PIN = A0;
const float TOUCH_FACTOR = 3.0;   // touched when the swing is this many times the learned untouched swing...
const int TOUCH_MIN = 60;         // ...and at least this many ADC counts above it
static float touch_base = -1;
static bool touched = false;
static unsigned long last_touch_report = 0;

void update_touch(unsigned long now) {
  int lo = 4095, hi = 0;
  for (int i = 0; i < 40; i++) {  // 40 samples over 20 ms = one 50 Hz period
    int v = analogRead(TOUCH_PIN);
    lo = min(lo, v);
    hi = max(hi, v);
    delayMicroseconds(500);
  }
  int p2p = hi - lo;
  if (touch_base < 0) touch_base = p2p;
  touched = p2p > touch_base * TOUCH_FACTOR && p2p > touch_base + TOUCH_MIN;
  if (!touched) touch_base += (p2p - touch_base) * 0.02f;  // learn the untouched level slowly
  if (now - last_touch_report >= 100) {
    last_touch_report = now;
    Bridge.notify("touch_level", p2p, (int)touch_base, touched ? 1 : 0);
  }
}

// Tap / hold detection: pressed = D13 bridged to GND, or A0 touched.
static bool last_raw = HIGH, stable = HIGH, hold_sent = false;
static unsigned long last_change = 0, press_start = 0;

void update_button(unsigned long now) {
  bool raw = (digitalRead(BUTTON_PIN) == LOW || touched) ? LOW : HIGH;
  if (raw != last_raw) { last_change = now; last_raw = raw; }
  if (now - last_change < DEBOUNCE_MS || raw == stable) {
    if (stable == LOW && !hold_sent && now - press_start >= HOLD_MS) {
      hold_sent = true;
      Bridge.notify("button", 2);  // hold
    }
    return;
  }
  stable = raw;
  if (stable == LOW) {
    press_start = now;
    hold_sent = false;
  } else if (!hold_sent) {
    Bridge.notify("button", 1);  // tap
  }
}

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(3);  // frame values 0..7
  matrix.clear();
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  led(0, 0, 0);

  Bridge.begin();
  Bridge.provide("draw", draw);
  Bridge.provide("set_status", set_status);
}

void loop() {
  unsigned long now = millis();
  update_touch(now);
  update_button(now);
  update_led(now);
  if (frame_dirty) {
    k_mutex_lock(&frame_mtx, K_FOREVER);
    matrix.draw(frame);
    frame_dirty = false;
    k_mutex_unlock(&frame_mtx);
  }
}
