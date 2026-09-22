#include "sensors.h"
#include "config.h"
#include "core.h"
#ifdef QY_WOKWI_SIM
#include "sim_profile.h"
#endif

bool i2cPresent(uint8_t address) {
#ifdef QY_WOKWI_SIM
    return qy_sim::i2cPresent(address);
#else
    Wire.beginTransmission(address);
    return Wire.endTransmission() == 0;
#endif
}

static bool readReg(uint8_t address, uint8_t reg, uint8_t *data, size_t length) {
    Wire.beginTransmission(address);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom(address, length, true) != length) return false;
    for (size_t i = 0; i < length; ++i) data[i] = Wire.read();
    return true;
}

static bool writeReg(uint8_t address, uint8_t reg, const uint8_t *data, size_t length) {
    Wire.beginTransmission(address);
    Wire.write(reg);
    if (Wire.write(data, length) != length) { Wire.endTransmission(); return false; }
    return Wire.endTransmission() == 0;
}

bool readAds(uint8_t address, uint8_t channel, int16_t &value) {
#ifdef QY_WOKWI_SIM
    return qy_sim::readAds(address, channel, value);
#else
    if (channel > 3) return false;
    // OS=1, single-ended MUX, PGA +/-4.096V, single-shot, 128 SPS, comparator off.
    uint16_t config = 0x8000 | ((4 + channel) << 12) | 0x0200 | 0x0100 | 0x0080 | 0x0003;
    uint8_t command[] = {static_cast<uint8_t>(config >> 8), static_cast<uint8_t>(config)};
    if (!writeReg(address, 1, command, 2)) return false;
    const uint32_t began = millis();
    uint8_t bytes[2];
    while (millis() - began < 30) {
        delay(2);
        if (!readReg(address, 1, bytes, 2)) return false;
        if (bytes[0] & 0x80) {
            if (!readReg(address, 0, bytes, 2)) return false;
            value = static_cast<int16_t>((bytes[0] << 8) | bytes[1]);
            return true;
        }
    }
    return false;
#endif
}

bool readSht(uint8_t address, float &temperature, float &humidity) {
#ifdef QY_WOKWI_SIM
    return qy_sim::readSht(address, temperature, humidity);
#else
    Wire.beginTransmission(address);
    Wire.write(0x24); Wire.write(0x00); // High repeatability, clock stretching disabled.
    if (Wire.endTransmission() != 0) return false;
    delay(16);
    uint8_t bytes[6];
    if (Wire.requestFrom(address, static_cast<size_t>(6), true) != 6) return false;
    for (int i = 0; i < 6; ++i) bytes[i] = Wire.read();
    if (qy::crc8(bytes, 2, 0x31, 0xFF) != bytes[2] || qy::crc8(bytes + 3, 2, 0x31, 0xFF) != bytes[5]) return false;
    temperature = -45.0f + 175.0f * ((bytes[0] << 8) | bytes[1]) / 65535.0f;
    humidity = 100.0f * ((bytes[3] << 8) | bytes[4]) / 65535.0f;
    return true;
#endif
}

bool readLeaf(float &temperature) {
#ifdef QY_WOKWI_SIM
    return qy_sim::readLeaf(temperature);
#else
    uint8_t bytes[3];
    if (!readReg(cfg::Mlx, 0x07, bytes, 3)) return false;
    const uint8_t packet[] = {static_cast<uint8_t>(cfg::Mlx << 1), 0x07,
        static_cast<uint8_t>((cfg::Mlx << 1) | 1), bytes[0], bytes[1]};
    if (qy::crc8(packet, 5, 0x07, 0) != bytes[2]) return false;
    const uint16_t raw = (bytes[1] << 8) | bytes[0];
    if (raw & 0x8000) return false; // Device error flag.
    temperature = raw * 0.02f - 273.15f;
    return isfinite(temperature);
#endif
}

bool readHx(int32_t &raw) {
    if (digitalRead(cfg::HxData) != LOW) return false;
    static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
    uint32_t bits = 0;
    // Prevent task/interrupt preemption extending SCK high beyond the power-down threshold.
    portENTER_CRITICAL(&mux);
    for (int i = 0; i < 24; ++i) {
        digitalWrite(cfg::HxClock, HIGH);
        delayMicroseconds(1);
        bits = (bits << 1) | digitalRead(cfg::HxData);
        digitalWrite(cfg::HxClock, LOW);
        delayMicroseconds(1);
    }
    digitalWrite(cfg::HxClock, HIGH); // Pulse 25 selects channel A, gain 128.
    delayMicroseconds(1);
    digitalWrite(cfg::HxClock, LOW);
    portEXIT_CRITICAL(&mux);
    raw = qy::signed24(bits);
    return digitalRead(cfg::HxData) == HIGH && raw != -8388608 && raw != 8388607;
}

// Bosch's default Wire adapter does not reject a short read. Use strict callbacks.
static int8_t bmeRead(uint8_t reg, uint8_t *data, uint32_t count, void *) {
    return readReg(cfg::Bme, reg, data, count) ? BME68X_OK : BME68X_E_COM_FAIL;
}
static int8_t bmeWrite(uint8_t reg, const uint8_t *data, uint32_t count, void *) {
    return writeReg(cfg::Bme, reg, data, count) ? BME68X_OK : BME68X_E_COM_FAIL;
}
static void bmeDelay(uint32_t us, void *) {
    if (us >= 1000) delay(us / 1000);
    if (us % 1000) delayMicroseconds(us % 1000);
}

bool readBme(float &gasOhm) {
#ifdef QY_WOKWI_SIM
    return qy_sim::readBme(gasOhm);
#else
    static Bme68x sensor;
    static bool ready = false, attempted = false;
    static uint32_t lastAttempt = 0;
    if (!ready) {
        if (attempted && millis() - lastAttempt < 5000) return false;
        attempted = true; lastAttempt = millis();
        uint8_t variant = 0;
        if (!readReg(cfg::Bme, 0xF0, &variant, 1) || variant != 1) return false; // BME688 gas-high variant.
        sensor.begin(BME68X_I2C_INTF, bmeRead, bmeWrite, bmeDelay, nullptr);
        if (sensor.status != BME68X_OK) return false;
        sensor.setTPH(BME68X_OS_2X, BME68X_OS_1X, BME68X_OS_2X);
        if (sensor.status != BME68X_OK) return false;
        sensor.setHeaterProf(cfg::BmeHeaterC, cfg::BmeHeaterMs);
        if (sensor.status != BME68X_OK) return false;
        ready = true;
    }
    sensor.setOpMode(BME68X_FORCED_MODE);
    if (sensor.status != BME68X_OK) { ready = false; return false; }
    delay((sensor.getMeasDur() + 999) / 1000 + cfg::BmeHeaterMs + 2);
    if (!sensor.fetchData() || sensor.status != BME68X_OK) { ready = false; return false; }
    bme68xData data{};
    sensor.getData(data);
    const uint8_t required = BME68X_NEW_DATA_MSK | BME68X_GASM_VALID_MSK | BME68X_HEAT_STAB_MSK;
    if ((data.status & required) != required || !isfinite(data.gas_resistance) || data.gas_resistance <= 0) return false;
    gasOhm = data.gas_resistance;
    return true;
#endif
}
