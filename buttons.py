"""One action per press/chord; rearm after all inputs are released stably."""
import time

DEBOUNCE_MS = 30


class ButtonLatch:
    def __init__(self):
        self.armed = True
        self.released_since = None
        self.last_event = None

    def block(self):
        self.armed = False
        self.released_since = None
        self.last_event = "stop"

    def press(self, event):
        self.released_since = None
        # Stop must remain available while another key is held. It too fires
        # only once, then blocks the chord until a qualified release.
        if not self.armed and not (event == "stop" and self.last_event != "stop"):
            return False
        self.armed = False
        self.last_event = event
        return True

    def released(self, now):
        if self.armed:
            return
        if self.released_since is None:
            self.released_since = now
        elif time.ticks_diff(now, self.released_since) >= DEBOUNCE_MS:
            self.armed = True
            self.released_since = None
