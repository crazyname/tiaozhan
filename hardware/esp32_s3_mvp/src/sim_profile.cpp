#include <Arduino.h>
#include <math.h>
#include "config.h"
#include "sim_profile.h"

#ifdef QY_WOKWI_SIM
namespace qy_sim {
namespace {
float clampf(float value, float low, float high) {
    if (value < low) return low;
    if (value > high) return high;
    return value;
}

float seconds() {
    return millis() / 1000.0f;
}

float cycle01() {
    return fmodf(seconds(), 120.0f) / 120.0f;
}

float wave(float scale, float period, float phase = 0.0f) {
    return scale * sinf((seconds() / period) * 2.0f * PI + phase);
}
}

bool i2cPresent(uint8_t address) {
    return address == cfg::AdsGas || address == cfg::ShtAmbient ||
           address == cfg::ShtChamber || address == cfg::Mlx ||
           address == cfg::Bme || (cfg::AuxEnabled && address == cfg::AdsAux);
}

bool readAds(uint8_t address, uint8_t channel, int16_t &value) {
    if (address != cfg::AdsGas || channel > 3) return false;
    if (!cfg::GasEnabled[channel]) return false;

    const float p = cycle01();
    const float bases[3] = {0.72f, 0.93f, 1.10f};
    const float gains[3] = {0.52f, 0.38f, 0.61f};
    float volts = bases[channel] + gains[channel] * p +
                  wave(0.025f + channel * 0.006f, 7.0f + channel * 1.7f, channel * 0.8f);
    volts = clampf(volts, 0.05f, 2.55f);
    long raw = lroundf(volts / cfg::AdsLsbV);
    if (raw < 0) raw = 0;
    if (raw > 32767) raw = 32767;
    value = static_cast<int16_t>(raw);
    return true;
}

bool readSht(uint8_t address, float &temperature, float &humidity) {
    const float p = cycle01();
    if (address == cfg::ShtAmbient) {
        temperature = 24.2f + wave(0.35f, 31.0f);
        humidity = 68.0f + wave(1.8f, 27.0f, 0.5f);
        return true;
    }
    if (address == cfg::ShtChamber) {
        temperature = 24.8f + 2.8f * p + wave(0.22f, 13.0f);
        humidity = 86.0f - 17.0f * p + wave(1.2f, 11.0f, 0.7f);
        return true;
    }
    return false;
}

bool readLeaf(float &temperature) {
    const float p = cycle01();
    temperature = 24.5f + 2.1f * p + wave(0.18f, 17.0f, 0.4f);
    return true;
}

bool readBme(float &gasOhm) {
    const float p = cycle01();
    gasOhm = 85000.0f + 175000.0f * p + wave(12000.0f, 19.0f, 0.9f);
    if (gasOhm < 1000.0f) gasOhm = 1000.0f;
    return true;
}

float motorRpm(float duty, int direction, uint64_t nowMs) {
    static float rpm = 0.0f;
    static uint64_t previous = 0;
    if (!previous) previous = nowMs;
    float dt = (nowMs - previous) / 1000.0f;
    previous = nowMs;
    if (dt < 0.0f) dt = 0.0f;
    if (dt > 0.25f) dt = 0.25f;

    float target = duty < 0.005f ? 0.0f : direction * duty * 50.0f;
    float alpha = clampf(dt / 0.30f, 0.0f, 1.0f);
    rpm += (target - rpm) * alpha;
    if (fabsf(rpm) < 0.03f && target == 0.0f) rpm = 0.0f;
    return rpm;
}
}
#endif
