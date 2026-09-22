#include <Arduino.h>
#include <esp_timer.h>
#include <cstring>
#include "config.h"
#include "motor.h"

namespace {
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
qy::MotorPolicy policy;
volatile int32_t encoderCount = 0;
volatile uint8_t encoderPrevious = 0;
bool taskReady = false;
uint64_t mainTick = 0;
char leaseSession[33] = {};
constexpr int PwmChannel = 4;
uint64_t nowMs() { return esp_timer_get_time()/1000; }
bool enabled() { return cfg::MotorCompiled && digitalRead(cfg::MotorEnable) == LOW && digitalRead(cfg::MotorGuard) == LOW; }
bool healthy() { return digitalRead(cfg::MotorFault) == HIGH; }
void IRAM_ATTR encoderISR() {
    uint8_t state = (digitalRead(cfg::EncoderA) ? 2 : 0) | (digitalRead(cfg::EncoderB) ? 1 : 0);
    portENTER_CRITICAL_ISR(&mux);
    encoderCount += qy::encoderDelta(encoderPrevious, state);
    encoderPrevious = state;
    portEXIT_CRITICAL_ISR(&mux);
}
void applyOutput(const qy::MotorPolicy& output) {
    if (!output.running) {
        ledcWrite(PwmChannel, 0); digitalWrite(cfg::MotorSleep, LOW);
    } else {
        digitalWrite(cfg::MotorDir, output.direction > 0 ? HIGH : LOW);
        digitalWrite(cfg::MotorSleep, HIGH);
        ledcWrite(PwmChannel, static_cast<uint32_t>(output.duty*1023));
    }
}
void task(void*) {
    uint64_t lastMeasure = nowMs();
    float rpm = NAN;
    for (;;) {
        const auto now = nowMs();
        portENTER_CRITICAL(&mux);
        if (now-lastMeasure >= 100) {
            const int32_t ticks = encoderCount; encoderCount = 0;
            rpm = ticks * 60000.0f / (cfg::EncoderCountsPerOutputTurn * (now-lastMeasure));
            lastMeasure = now;
        }
        policy.tick(now, enabled(), healthy(), now-mainTick < 1500, rpm);
        const auto output = policy;
        portEXIT_CRITICAL(&mux);
        applyOutput(output);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
}

void motorBegin() {
    if (!cfg::MotorCompiled) return; // Ordinary acquisition firmware never touches reserved motor pins.
    for (int pin : {cfg::MotorPwm, cfg::MotorDir, cfg::MotorSleep}) {
        digitalWrite(pin, LOW); pinMode(pin, OUTPUT);
    }
    for (int pin : {cfg::MotorEnable, cfg::MotorGuard, cfg::MotorFault}) pinMode(pin, INPUT_PULLUP);
    pinMode(cfg::EncoderA, INPUT); pinMode(cfg::EncoderB, INPUT);
    encoderPrevious = (digitalRead(cfg::EncoderA) ? 2 : 0) | (digitalRead(cfg::EncoderB) ? 1 : 0);
    ledcSetup(PwmChannel, 20000, 10); ledcAttachPin(cfg::MotorPwm, PwmChannel); ledcWrite(PwmChannel, 0);
    attachInterrupt(digitalPinToInterrupt(cfg::EncoderA), encoderISR, CHANGE);
    attachInterrupt(digitalPinToInterrupt(cfg::EncoderB), encoderISR, CHANGE);
    mainTick = nowMs();
    taskReady = xTaskCreatePinnedToCore(task, "motor-watchdog", 3072, nullptr, 4, nullptr, 0) == pdPASS;
}
void motorMainAlive() {
    if (!cfg::MotorCompiled) return;
    portENTER_CRITICAL(&mux); mainTick = nowMs(); portEXIT_CRITICAL(&mux);
}
bool motorReady() { return cfg::MotorCompiled && taskReady; }
bool motorInterlocked() { return motorReady() && enabled(); }
bool motorStart(float rpm, int direction, uint32_t duration, const char* session) {
    if (!motorReady() || !session || strlen(session) != 32) return false;
    portENTER_CRITICAL(&mux);
    const bool accepted = policy.start(rpm, direction, duration, nowMs(), enabled(), healthy());
    if (accepted) memcpy(leaseSession, session, 33);
    portEXIT_CRITICAL(&mux);
    return accepted;
}
void motorStop() {
    if (!motorReady()) return;
    portENTER_CRITICAL(&mux); policy.stop(nowMs()); portEXIT_CRITICAL(&mux);
}
bool motorReset() {
    if (!motorReady()) return false;
    portENTER_CRITICAL(&mux); bool ok = policy.clear(nowMs(), enabled(), healthy()); portEXIT_CRITICAL(&mux);
    return ok;
}
void motorLease(const char* session) {
    if (!motorReady() || !session) return;
    portENTER_CRITICAL(&mux);
    if (policy.running && !strcmp(session, leaseSession)) policy.hostLease = nowMs();
    portEXIT_CRITICAL(&mux);
}
qy::MotorPolicy motorSnapshot() {
    portENTER_CRITICAL(&mux); auto snapshot = policy; portEXIT_CRITICAL(&mux); return snapshot;
}
