#include "core.h"
#include <assert.h>
#include <stdio.h>
#include <limits>

int main() {
    const uint8_t sht[] = {0xBE, 0xEF};
    assert(qy::crc8(sht, 2, 0x31, 0xFF) == 0x92); // Sensirion published check vector.
    const uint8_t standard[] = {'1','2','3','4','5','6','7','8','9'};
    assert(qy::crc8(standard, 9, 0x07, 0) == 0xF4); // CRC-8/SMBUS check vector.
    assert(qy::signed24(0) == 0);
    assert(qy::signed24(0x7FFFFF) == 8388607);
    assert(qy::signed24(0x800000) == -8388608);
    assert(qy::signed24(0xFFFFFF) == -1);
    float scale = 0;
    assert(qy::scaleFromReference(11000, 1000, 100, scale) && scale == 100);
    assert(qy::scaleFromReference(-9000, 1000, 100, scale) && scale == -100);
    assert(!qy::scaleFromReference(1000, 1000, 100, scale));
    assert(!qy::scaleFromReference(11000, 1000, 0, scale));
    assert(!qy::scaleFromReference(11000, 1000, std::numeric_limits<double>::infinity(), scale));

    qy::AirPolicy air;
    assert(!air.tick(0, true).pump);
    assert(!air.request(qy::AirMode::Sample, 1000, 0, false));
    assert(!air.request(qy::AirMode::Sample, 120001, 0, true));
    assert(!air.request(qy::AirMode::Sample, 999, 0, true));
    assert(air.request(qy::AirMode::Sample, 1000, 0, true));
    assert(!air.tick(199, true).sample);
    assert(air.tick(200, true).sample && !air.tick(200, true).pump);
    assert(air.tick(400, true).pump && !air.tick(400, true).purge);
    assert(!air.tick(1000, true).pump);
    assert(air.mode == qy::AirMode::Off);
    assert(air.request(qy::AirMode::Sample, 2000, 1000, true));
    assert(air.tick(1400, true).pump);
    assert(air.request(qy::AirMode::Purge, 2000, 1500, true));
    auto out = air.tick(1501, true);
    assert(!out.pump && !out.sample && !out.purge);
    out = air.tick(1900, true);
    assert(out.pump && out.purge && !out.sample);
    assert(!air.tick(1950, false).pump);
    assert(!air.tick(2000, true).pump); // Interlock closure must not restart an old command.
    assert(air.request(qy::AirMode::Sample, 1000, 0xFFFFFF00, true));
    assert(air.tick(0x90, true).pump); // 400 ms elapsed across uint32 wrap.
    assert(!air.tick(0x2E8, true).pump); // Exactly 1000 ms.
    assert(air.request(qy::AirMode::Sample, 1000, 0, true));
    air.stop(); assert(!air.tick(500, true).pump);
    puts("PASS: CRC, signed ADC, calibration, air interlock, valve sequencing, timeout and timer wrap");
}
