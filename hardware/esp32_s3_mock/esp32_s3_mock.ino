#include <Arduino.h>

// 青韵智控 MVP-v0.1：ESP32-S3 模拟数据发送端
// 用于在真实传感器到货前联调上位机、串口协议和数据落盘。
// 输出协议：USB Serial + JSON Lines，1 Hz。

static uint32_t seqNo = 0;
static uint32_t lastSendMs = 0;

float wave(float base, float amp, float periodSec, float phase = 0.0f) {
  float t = millis() / 1000.0f;
  return base + amp * sinf((2.0f * PI * t / periodSec) + phase);
}

void setup() {
  Serial.begin(115200);
  delay(1200);
  Serial.println("{\"type\":\"status\",\"message\":\"qingyun-mvp mock firmware ready\",\"version\":\"0.1.0\"}");
}

void loop() {
  uint32_t now = millis();
  if (now - lastSendMs < 1000) {
    delay(5);
    return;
  }
  lastSendMs = now;

  // 构造可重复但有缓慢变化的模拟信号。
  int gas1 = (int)wave(15100, 700, 90, 0.0f);
  int gas2 = (int)wave(18800, 950, 120, 0.6f);
  int gas3 = (int)wave(13900, 520, 75, 1.2f);
  int gas4 = (int)wave(22000, 1100, 150, 1.8f);

  float chamberT = wave(27.2f, 0.35f, 180);
  float chamberRH = wave(71.0f, 2.0f, 140, 0.5f);
  float ambientT = wave(26.8f, 0.25f, 240, 0.7f);
  float ambientRH = wave(69.0f, 1.4f, 200, 1.1f);
  float leafT = wave(26.5f, 0.28f, 190, 0.2f);
  float massG = 4000.0f - (millis() / 1000.0f) * 0.012f;
  int bmeGas = (int)wave(183000, 12000, 110, 1.0f);

  float gasV1 = gas1 * 4.096f / 32767.0f;
  float gasV2 = gas2 * 4.096f / 32767.0f;
  float gasV3 = gas3 * 4.096f / 32767.0f;
  float gasV4 = gas4 * 4.096f / 32767.0f;

  Serial.print("{\"type\":\"sensor\",\"seq\":");
  Serial.print(seqNo++);
  Serial.print(",\"t_ms\":");
  Serial.print(now);
  Serial.print(",\"gas_adc\":[");
  Serial.print(gas1); Serial.print(',');
  Serial.print(gas2); Serial.print(',');
  Serial.print(gas3); Serial.print(',');
  Serial.print(gas4); Serial.print(']');
  Serial.print(",\"gas_v\":[");
  Serial.print(gasV1, 4); Serial.print(',');
  Serial.print(gasV2, 4); Serial.print(',');
  Serial.print(gasV3, 4); Serial.print(',');
  Serial.print(gasV4, 4); Serial.print(']');
  Serial.print(",\"bme688_gas_ohm\":"); Serial.print(bmeGas);
  Serial.print(",\"chamber_t_c\":"); Serial.print(chamberT, 2);
  Serial.print(",\"chamber_rh_pct\":"); Serial.print(chamberRH, 2);
  Serial.print(",\"ambient_t_c\":"); Serial.print(ambientT, 2);
  Serial.print(",\"ambient_rh_pct\":"); Serial.print(ambientRH, 2);
  Serial.print(",\"leaf_t_c\":"); Serial.print(leafT, 2);
  Serial.print(",\"mass_g\":"); Serial.print(massG, 2);
  Serial.print(",\"pump\":1,\"valve_sample\":1,\"valve_purge\":0}");
  Serial.println();
}
