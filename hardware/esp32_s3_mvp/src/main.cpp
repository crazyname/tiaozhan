#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <esp_timer.h>
#include "config.h"
#include "core.h"
#include "sensors.h"
#include "motor.h"

namespace {
Preferences preferences;
struct Calibration {
    uint32_t magic = 0x51594332; // QYC2; one NVS blob prevents torn multi-key updates.
    int32_t offset = 0;
    float scale = 0;
    uint32_t check = 0;
} calibration;
bool storageReady = false, offsetKnown = false;
enum class CalMode { None, Tare, Reference };
CalMode calMode = CalMode::None;
uint32_t calStarted = 0, calCount = 0;
double calSum = 0, calGrams = 0;
int32_t calMin = 0, calMax = 0;
int64_t hxSum = 0;
uint32_t hxCount = 0, hxLast = 0, sequence = 0;
bool hxSeen = false;
qy::AirPolicy air;
qy::AirOutputs airOut;
portMUX_TYPE airMux = portMUX_INITIALIZER_UNLOCKED;
uint32_t mainHeartbeat = 0;
bool airTaskReady = false;

void emitJson(const JsonDocument &doc) {
    // One bounded USB write instead of a separate timeout for every character.
    static char tx[4096];
    if (measureJson(doc) + 1 >= sizeof(tx)) return;
    const size_t n = serializeJson(doc, tx, sizeof(tx) - 1);
    tx[n] = '\n';
    Serial.write(reinterpret_cast<const uint8_t *>(tx), n + 1);
}

void status(const char *message, bool ok = true) {
    StaticJsonDocument<384> doc;
    doc["type"] = "status"; doc["ok"] = ok; doc["message"] = message;
    emitJson(doc);
}
uint32_t calibrationCheck(const Calibration &record) {
    // CRC8 plus magic/finite checks detects accidental corruption; not an authentication code.
    return qy::crc8(reinterpret_cast<const uint8_t *>(&record), offsetof(Calibration, check), 0x31, 0xFF);
}
bool storeCalibration(Calibration record) {
    record.check = calibrationCheck(record);
    if (!storageReady || preferences.putBytes("record", &record, sizeof(record)) != sizeof(record)) return false;
    calibration = record; offsetKnown = true;
    return true;
}
void airTask(void *) {
    for (;;) {
        portENTER_CRITICAL(&airMux);
        bool enabled = cfg::AirCompiled && digitalRead(cfg::AirEnable) == LOW && millis() - mainHeartbeat < 1500;
        airOut = air.tick(millis(), enabled);
        // Stop pump before closing valves, open selected valve before starting pump.
        if (!airOut.pump) digitalWrite(cfg::Pump, LOW);
        digitalWrite(cfg::SampleValve, airOut.sample ? HIGH : LOW);
        digitalWrite(cfg::PurgeValve, airOut.purge ? HIGH : LOW);
        if (airOut.pump) digitalWrite(cfg::Pump, HIGH);
        portEXIT_CRITICAL(&airMux);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
void stopAir() {
    portENTER_CRITICAL(&airMux);
    air.stop();
    portEXIT_CRITICAL(&airMux);
}
void pollHx() {
    int32_t raw;
    if (readHx(raw)) {
        hxSum += raw; ++hxCount; hxLast = millis(); hxSeen = true;
        if (calMode != CalMode::None) {
            if (!calCount) calMin = calMax = raw;
            calMin = min(calMin, raw); calMax = max(calMax, raw);
            calSum += raw; ++calCount;
        }
    }
    if (calMode == CalMode::None) return;
    if (millis() - calStarted >= cfg::CalibrationTimeoutMs) {
        calMode = CalMode::None; status("calibration timeout; previous calibration retained", false); return;
    }
    if (calCount < cfg::CalibrationSamples) return;
    Calibration next = calibration;
    const double average = calSum / calCount;
    // Reject gross movement; final accuracy still requires a multi-point bench calibration.
    bool valid = static_cast<int64_t>(calMax) - calMin <= 5000;
    if (calMode == CalMode::Tare) next.offset = static_cast<int32_t>(llround(average));
    else valid = valid && qy::scaleFromReference(average, calibration.offset, calGrams, next.scale);
    const bool saved = valid && storeCalibration(next);
    calMode = CalMode::None;
    hxSum = 0; hxCount = 0; // No averaging across different calibrations.
    status(saved ? "calibration saved in NVS" : "calibration rejected or NVS write failed; previous value retained", saved);
}
void startCalibration(CalMode mode, double grams = 0) {
    if (motorSnapshot().running) { status("calibration rejected: motor active", false); return; }
    if (calMode != CalMode::None) { status("calibration already running", false); return; }
    if (!storageReady) { status("NVS unavailable", false); return; }
    if (mode == CalMode::Reference && (!offsetKnown || !isfinite(grams) || grams <= 0 || grams > 20000)) {
        status("tare first; reference grams must be >0 and <=20000", false); return;
    }
    stopAir(); calMode = mode; calGrams = grams; calCount = 0; calSum = 0; calStarted = millis();
    status("collecting 20 fresh HX711 readings; keep platform stationary");
}
void command(const char *line) {
    StaticJsonDocument<384> input;
    auto error = deserializeJson(input, line);
    if (error || !input.is<JsonObject>() || !input["cmd"].is<const char *>()) {
        status("invalid JSON command", false); return;
    }
    const char *cmd = input["cmd"];
    if (!strcmp(cmd, "motor_heartbeat")) {
        if (input["session_id"].is<const char*>()) motorLease(input["session_id"]);
        return; // No command response; this is a lease renewal, never a start command.
    }
    if (!strcmp(cmd, "motor") || !strcmp(cmd, "motor_stop") || !strcmp(cmd, "motor_reset")) {
        StaticJsonDocument<384> out;
        out["type"] = "status"; out["cmd"] = cmd; out["request_id"] = input["request_id"];
        bool accepted = false;
        if (!strcmp(cmd, "motor_stop")) { motorStop(); accepted = motorReady(); }
        else if (!strcmp(cmd, "motor_reset")) accepted = motorReset();
        else if (calMode == CalMode::None && input["rpm"].is<float>() && input["direction"].is<int>() &&
                 input["duration_ms"].is<uint32_t>() && input["session_id"].is<const char*>()) {
            accepted = motorStart(input["rpm"].as<float>(), input["direction"].as<int>(), input["duration_ms"].as<uint32_t>(), input["session_id"]);
        }
        out["ok"] = accepted;
        out["message"] = accepted ? "motor command accepted; inspect encoder telemetry" : "motor rejected: build, interlock, stationary, fault, calibration or range";
        emitJson(out); return;
    }
    if (!strcmp(cmd, "stop")) { stopAir(); status("air stopped"); }
    else if (!strcmp(cmd, "tare")) startCalibration(CalMode::Tare);
    else if (!strcmp(cmd, "calibrate")) {
        if (!input["grams"].is<double>()) { status("grams must be numeric", false); return; }
        startCalibration(CalMode::Reference, input["grams"].as<double>());
    } else if (!strcmp(cmd, "info")) {
        StaticJsonDocument<1536> out;
        out["type"] = "status"; out["message"] = "firmware configuration";
        out["firmware"] = cfg::Firmware; out["hardware"] = cfg::Hardware;
        out["source_mode"] = cfg::SourceMode; out["air_compiled"] = cfg::AirCompiled;
        out["air_enable"] = digitalRead(cfg::AirEnable) == LOW;
        out["motor_compiled"] = motorReady(); out["motor_interlock"] = motorInterlocked();
        out["offset_known"] = offsetKnown;
        out["hx_offset"] = calibration.offset; out["hx_counts_per_g"] = calibration.scale;
        out["device_id"] = String(static_cast<uint32_t>(ESP.getEfuseMac() >> 32), HEX) + String(static_cast<uint32_t>(ESP.getEfuseMac()), HEX);
        out["preflight_schema"] = "QY-PREFLIGHT-1";
        uint8_t mask = 0;
        for (uint8_t i = 0; i < 4; ++i) if (cfg::GasEnabled[i]) mask |= 1 << i;
        out["gas_enabled_mask"] = mask;
        // Probe only configured addresses. Unlike scan, info never changes outputs.
        auto devices = out.createNestedObject("i2c_present");
        devices["ads_gas"] = i2cPresent(cfg::AdsGas);
        devices["sht_ambient"] = i2cPresent(cfg::ShtAmbient);
        devices["sht_chamber"] = i2cPresent(cfg::ShtChamber);
        devices["mlx"] = i2cPresent(cfg::Mlx);
        devices["bme"] = i2cPresent(cfg::Bme);
        if (cfg::AuxEnabled) devices["ads_aux"] = i2cPresent(cfg::AdsAux);
        if (out.overflowed()) { status("info document overflow", false); return; }
        emitJson(out);
    } else if (!strcmp(cmd, "scan")) {
        stopAir();
        StaticJsonDocument<2048> out;
        out["type"] = "status"; out["message"] = "I2C scan (7-bit addresses in decimal)";
        JsonArray addresses = out.createNestedArray("addresses");
        for (uint8_t addr = 8; addr < 120; ++addr) if (i2cPresent(addr)) addresses.add(addr);
        emitJson(out);
    } else if (!strcmp(cmd, "air")) {
        if (!input["mode"].is<const char *>() || !input["duration_ms"].is<uint32_t>() || calMode != CalMode::None) {
            stopAir(); status("air requires mode, integer duration_ms and no calibration", false); return;
        }
        const char *mode = input["mode"];
        if (strcmp(mode, "sample") && strcmp(mode, "purge")) { stopAir(); status("unknown air mode", false); return; }
        uint32_t duration = input["duration_ms"];
        bool enabled = cfg::AirCompiled && airTaskReady && digitalRead(cfg::AirEnable) == LOW;
        portENTER_CRITICAL(&airMux);
        bool accepted = air.request(!strcmp(mode, "sample") ? qy::AirMode::Sample : qy::AirMode::Purge,
                                    duration, millis(), enabled);
        portEXIT_CRITICAL(&airMux);
        status(accepted ? "timed air command accepted" : "air disabled or duration outside 1000..120000 ms", accepted);
    } else status("unknown command; use info, scan, tare, calibrate, air or stop", false);
}
void pollCommands() {
    static char buffer[256]; static size_t used = 0; static bool overflow = false;
    int budget = 64;
    while (budget-- && Serial.available()) {
        char c = Serial.read();
        if (c == '\r') continue;
        if (c == '\n') {
            if (overflow) status("command line too long", false);
            else if (used) { buffer[used] = 0; command(buffer); }
            used = 0; overflow = false;
        } else if (used < sizeof(buffer) - 1) buffer[used++] = c;
        else overflow = true;
    }
}
void number(JsonVariant target, float value) {
    if (isfinite(value)) target.set(value); else target.set(nullptr);
}
void publish() {
    StaticJsonDocument<4096> out;
    out["type"] = "sensor"; out["seq"] = sequence++;
    out["t_ms"] = static_cast<uint64_t>(esp_timer_get_time() / 1000);
    out["firmware"] = cfg::Firmware; out["hardware"] = cfg::Hardware;
    out["protocol"] = "JSONL-v0.3"; out["source_mode"] = cfg::SourceMode;
    const String deviceId = String(static_cast<uint32_t>(ESP.getEfuseMac() >> 32), HEX) + String(static_cast<uint32_t>(ESP.getEfuseMac()), HEX);
    out["device_id"] = deviceId;
    auto errors = out.createNestedArray("errors");
    auto adc = out.createNestedArray("gas_adc"); auto volts = out.createNestedArray("gas_v");
    uint8_t mask = 0;
    for (uint8_t i = 0; i < 4; ++i) {
        int16_t raw = 0;
        if (!cfg::GasEnabled[i]) { adc.add(nullptr); volts.add(nullptr); continue; }
        mask |= 1 << i;
        if (!readAds(cfg::AdsGas, i, raw)) {
            adc.add(nullptr); volts.add(nullptr); errors.add(String("ADS_GAS_CH") + String(i + 1));
        } else {
            adc.add(raw); volts.add(raw * cfg::AdsLsbV);
            if (raw < 0 || raw * cfg::AdsLsbV > 2.65f) errors.add(String("ADC_RANGE_CH") + String(i + 1));
        }
    }
    out["gas_enabled_mask"] = mask;
    out["gas_divider_ratio"] = cfg::GasDivider; // gas_v is ADC-pin voltage, not pre-divider voltage.
    if (cfg::AuxEnabled) {
        auto aux = out.createNestedArray("aux_adc");
        for (uint8_t i = 0; i < 4; ++i) {
            int16_t raw;
            if (readAds(cfg::AdsAux, i, raw)) aux.add(raw);
            else { aux.add(nullptr); errors.add("ADS_AUX_ERROR"); }
        }
    }
    float t = NAN, rh = NAN, leaf = NAN, gas = NAN;
    if (!readSht(cfg::ShtAmbient, t, rh)) errors.add("SHT_AMBIENT_ERROR");
    number(out["ambient_t_c"], t); number(out["ambient_rh_pct"], rh);
    t = rh = NAN;
    if (!readSht(cfg::ShtChamber, t, rh)) errors.add("SHT_CHAMBER_ERROR");
    number(out["chamber_t_c"], t); number(out["chamber_rh_pct"], rh);
    if (!readLeaf(leaf)) errors.add("MLX_ERROR"); number(out["leaf_t_c"], leaf);
    if (!readBme(gas)) errors.add("BME688_ERROR_OR_UNSTABLE"); number(out["bme688_gas_ohm"], gas);
    const bool fresh = hxSeen && hxCount && millis() - hxLast <= cfg::HxFreshMs;
    const bool calibrated = offsetKnown && isfinite(calibration.scale) && fabs(calibration.scale) >= 0.001f;
    float rawMean = fresh ? static_cast<float>(static_cast<double>(hxSum) / hxCount) : NAN;
    number(out["hx_raw"], rawMean); out["hx_samples"] = hxCount;
    float mass = (fresh && calibrated && calMode == CalMode::None) ? (rawMean - calibration.offset) / calibration.scale : NAN;
    number(out["mass_g"], mass);
    out["hx_offset"] = calibration.offset; out["hx_counts_per_g"] = calibration.scale;
    if (!fresh) errors.add("HX711_NO_FRESH_DATA");
    if (isfinite(mass) && (mass < 0 || mass > 20000)) errors.add("MASS_OUT_OF_RANGE");
    hxSum = 0; hxCount = 0;
    portENTER_CRITICAL(&airMux);
    qy::AirOutputs outputs = airOut;
    portEXIT_CRITICAL(&airMux);
    out["pump"] = outputs.pump ? 1 : 0; out["valve_sample"] = outputs.sample ? 1 : 0; out["valve_purge"] = outputs.purge ? 1 : 0;
    out["actuator_state_kind"] = "commanded_not_feedback";
    const auto motor = motorSnapshot();
    out["motor_compiled"] = motorReady(); out["motor_interlock"] = motorInterlocked();
    out["motor_running"] = motor.running; out["motor_fault"] = motor.fault;
    out["shake_target_rpm"] = motor.target;
    number(out["shake_actual_rpm"], motorReady() && motor.feedbackValid ? motor.actual : NAN);
    out["shake_direction"] = motor.direction;
    out["shake_start_time"] = motor.started; out["shake_end_time"] = motor.ended;
    out["shake_time_kind"] = "mcu_uptime_ms";
    out["shake_duration_s"] = motor.duration/1000.0f;
    out["motor_pwm_duty"] = motor.duty;
    out["motor_current_a"] = nullptr; // CS is PWM-dependent and has not been calibrated.
    String quality;
    auto addFlag = [&](const char *flag) { if (quality.length()) quality += ';'; quality += flag; };
    if (errors.size()) addFlag("SENSOR_ERROR");
    if (esp_timer_get_time() / 1000 < cfg::WarmupMs) addFlag("WARMUP");
    if (!calibrated) addFlag("UNCALIBRATED");
    if (calMode != CalMode::None) addFlag("CALIBRATING");
    out["quality_flag"] = quality.length() ? quality : "OK";
    out["acquisition_ms"] = static_cast<uint64_t>(esp_timer_get_time() / 1000) - out["t_ms"].as<uint64_t>();
    if (out.overflowed()) { status("sensor document overflow", false); return; }
    emitJson(out);
}
}

void setup() {
    motorBegin();
    for (int pin : {cfg::Pump, cfg::SampleValve, cfg::PurgeValve, cfg::HxClock}) {
        digitalWrite(pin, LOW); pinMode(pin, OUTPUT);
    }
    pinMode(cfg::AirEnable, INPUT_PULLUP); pinMode(cfg::HxData, INPUT_PULLUP);
    mainHeartbeat = millis();
    airTaskReady = xTaskCreatePinnedToCore(airTask, "air-safety", 2048, nullptr, 3, nullptr, 0) == pdPASS;
    Serial.begin(115200);
    Serial.setTxTimeoutMs(20); // Native USB CDC: bounded wait if PC stops reading.
    Wire.begin(cfg::Sda, cfg::Scl, cfg::I2cHz); Wire.setTimeOut(cfg::I2cTimeoutMs);
    storageReady = preferences.begin("qy-scale", false);
    if (storageReady && preferences.getBytesLength("record") == sizeof(Calibration)) {
        Calibration saved;
        if (preferences.getBytes("record", &saved, sizeof(saved)) == sizeof(saved) && saved.magic == calibration.magic &&
            saved.check == calibrationCheck(saved) && isfinite(saved.scale) &&
            saved.offset > -8388608 && saved.offset < 8388607) {
            calibration = saved; offsetKnown = true;
        }
    }
    status(cfg::ReadyMessage);
    if (!airTaskReady) status("air safety task unavailable; air commands disabled", false);
    if (!storageReady) status("NVS unavailable; calibration disabled", false);
}

void loop() {
    motorMainAlive();
    portENTER_CRITICAL(&airMux); mainHeartbeat = millis(); portEXIT_CRITICAL(&airMux);
    pollCommands(); pollHx();
    static uint32_t lastFrame = millis();
    if (millis() - lastFrame >= cfg::FrameMs) {
        lastFrame = millis(); // Do not emit catch-up bursts after delays.
        publish();
    }
    delay(2);
}
