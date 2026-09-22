#pragma once
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include "config.h"

namespace qy {
inline uint8_t crc8(const uint8_t *data, size_t count, uint8_t polynomial, uint8_t initial) {
    uint8_t crc = initial;
    while (count--) {
        crc ^= *data++;
        for (int i = 0; i < 8; ++i)
            crc = (crc & 0x80) ? static_cast<uint8_t>((crc << 1) ^ polynomial) : static_cast<uint8_t>(crc << 1);
    }
    return crc;
}
inline int32_t signed24(uint32_t raw) {
    raw &= 0xFFFFFF;
    return (raw & 0x800000) ? static_cast<int32_t>(raw) - 0x1000000 : static_cast<int32_t>(raw);
}
inline bool scaleFromReference(double raw, double offset, double grams, float &scale) {
    if (!isfinite(raw) || !isfinite(offset) || !isfinite(grams) || grams <= 0 || grams > 20000 || fabs(raw - offset) < 100)
        return false;
    scale = static_cast<float>((raw - offset) / grams);
    return isfinite(scale) && fabs(scale) >= 0.001;
}
enum class AirMode { Off, Sample, Purge };
struct AirOutputs { bool pump = false, sample = false, purge = false; };
class AirPolicy {
public:
    AirMode mode = AirMode::Off;
    uint32_t started = 0, duration = 0;
    void stop() { mode = AirMode::Off; }
    bool request(AirMode desired, uint32_t ms, uint32_t now, bool enabled) {
        if (desired == AirMode::Off) { stop(); return true; }
        if (!enabled || ms < 1000 || ms > cfg::MaxAirMs ||
            (desired != AirMode::Sample && desired != AirMode::Purge)) { stop(); return false; }
        mode = desired;
        started = now;
        duration = ms;
        return true;
    }
    AirOutputs tick(uint32_t now, bool enabled) {
        const uint32_t elapsed = now - started; // wrap-safe for bounded intervals
        if (!enabled || elapsed >= duration) stop();
        AirOutputs out;
        if (mode == AirMode::Off) return out;
        // All outputs off before opening the new valve; pump starts another 200 ms later.
        if (elapsed < cfg::ValveSettleMs) return out;
        out.sample = mode == AirMode::Sample;
        out.purge = mode == AirMode::Purge;
        out.pump = elapsed >= 2 * cfg::ValveSettleMs;
        return out;
    }
};
}
