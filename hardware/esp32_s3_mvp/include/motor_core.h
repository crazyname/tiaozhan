#pragma once
#include <stdint.h>
#include <math.h>
#include <cstring>

namespace qy {
// Bench design limits, not validated process settings or a safety-rated controller.
struct MotorPolicy {
    bool running = false, latched = false;
    float target = 0, actual = 0, duty = 0, integral = 0;
    int direction = 1;
    uint64_t started = 0, ended = 0, hostLease = 0, previousTick = 0, stillSince = 0, noMotionSince = 0;
    uint32_t duration = 0;
    const char* fault = "NONE";
    bool feedbackValid = false, stationary = false;

    void stop(uint64_t now, const char* reason = "NONE") {
        if (running) ended = now;
        running = false; duty = integral = 0;
        if (strcmp(reason, "NONE")) { fault = reason; latched = true; }
    }
    bool clear(uint64_t now, bool enabled, bool driverHealthy) {
        if (running || !enabled || !driverHealthy || !feedbackValid || !stationary || now-stillSince < 1000) return false;
        fault = "NONE"; latched = false; return true;
    }
    bool start(float rpm, int dir, uint32_t milliseconds, uint64_t now, bool enabled, bool driverHealthy) {
        if (running || !enabled || !driverHealthy || latched || !feedbackValid || !stationary || now-stillSince < 1000 ||
            !isfinite(rpm) || rpm < 5 || rpm > 30 || (dir != 1 && dir != -1) || milliseconds < 1000 || milliseconds > 300000) return false;
        target = rpm; direction = dir; duration = milliseconds; started = hostLease = previousTick = noMotionSince = now;
        ended = 0; duty = integral = 0; running = true; return true;
    }
    void tick(uint64_t now, bool enabled, bool driverHealthy, bool mainAlive, float measured) {
        actual = measured; feedbackValid = isfinite(measured);
        if (feedbackValid && fabsf(measured) < 1) {
            if (!stationary) stillSince = now;
            stationary = true;
        } else stationary = false;
        if (!running) { duty = 0; previousTick = now; return; }
        if (!enabled) { stop(now, "INTERLOCK"); return; }
        if (!driverHealthy) { stop(now, "DRIVER_FAULT"); return; }
        if (!mainAlive) { stop(now, "MCU_WATCHDOG"); return; }
        if (now-hostLease >= 1200) { stop(now, "HOST_TIMEOUT"); return; }
        if (!feedbackValid) { stop(now, "ENCODER_INVALID"); return; }
        if (fabsf(measured) > 45) { stop(now, "OVERSPEED"); return; }
        if (measured*direction < -2) { stop(now, "DIRECTION_MISMATCH"); return; }
        if (now-started >= duration) { stop(now); return; }
        if (fabsf(measured) >= 1) noMotionSince = now;
        if (now-noMotionSince >= 1000) { stop(now, "NO_ENCODER_MOTION"); return; }
        const float dt = (now-previousTick)/1000.0f;
        previousTick = now;
        const float error = target - measured*direction;
        integral = fmaxf(-.2f, fminf(.4f, integral + .004f*error*dt));
        const float desired = fmaxf(0, fminf(.60f, target/100.0f + .012f*error + integral));
        // Slew limit limits starting steps; timeout/interlock bypass ramp and disable immediately.
        duty = fminf(desired, duty + .5f*dt);
    }
};

inline int8_t encoderDelta(uint8_t previous, uint8_t current) {
    static const int8_t lut[16] = {0,-1,1,0,1,0,0,-1,-1,0,0,1,0,1,-1,0};
    return lut[((previous & 3) << 2) | (current & 3)];
}
}
