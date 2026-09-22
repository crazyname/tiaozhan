#pragma once
#include <stdint.h>

#ifdef QY_WOKWI_SIM
namespace qy_sim {
bool i2cPresent(uint8_t address);
bool readAds(uint8_t address, uint8_t channel, int16_t &value);
bool readSht(uint8_t address, float &temperature, float &humidity);
bool readLeaf(float &temperature);
bool readBme(float &gasOhm);
float motorRpm(float duty, int direction, uint64_t nowMs);
}
#endif
