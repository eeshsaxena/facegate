from facegate.camera import Camera


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class BufferedCapture:
    """Hands back `buffered` old frames instantly, then live ones at 30 fps."""

    def __init__(self, clock, buffered):
        self.clock = clock
        self.buffered = buffered
        self.grabs = 0

    def grab(self):
        self.grabs += 1
        if self.buffered:
            self.buffered -= 1
            self.clock.t += 0.001
        else:
            self.clock.t += 1 / 30
        return True


def camera_with(cap):
    cam = Camera()
    cam._cap = cap
    return cam


def test_old_buffered_frames_are_all_dropped():
    clock = Clock()
    cap = BufferedCapture(clock, buffered=8)
    n = camera_with(cap).flush(clock=clock)
    assert cap.buffered == 0  # every stale frame is gone
    assert n > 8 and clock.t >= 0.3  # and we waited for live frames


def test_flush_keeps_going_for_the_minimum_time_even_if_live():
    clock = Clock()
    cap = BufferedCapture(clock, buffered=0)
    camera_with(cap).flush(min_seconds=0.3, clock=clock)
    assert clock.t >= 0.3


def test_flush_is_bounded_and_safe_when_closed():
    clock = Clock()
    cap = BufferedCapture(clock, buffered=10_000)
    assert camera_with(cap).flush(max_grabs=50, clock=clock) == 50
    assert Camera().flush() == 0
