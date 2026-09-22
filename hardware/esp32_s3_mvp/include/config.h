#pragma once
#include <stdint.h>

// Hardware contract HW-v0.2: ESP32-S3-DevKitC-1 / WROOM-1 N8R8.
namespace cfg {
constexpr char Hardware[] = "QY-HW-0.2-N8R8";
constexpr char Firmware[] = "QY-FW-0.2.0";
constexpr int Sda = 8, Scl = 9;
constexpr int HxData = 4, HxClock = 5;
constexpr int AirEnable = 6; // LOW only when physical enable switch is closed.
constexpr int Pump = 16, SampleValve = 17, PurgeValve = 18;
constexpr uint32_t I2cHz = 100000;
constexpr uint16_t I2cTimeoutMs = 25;
constexpr uint8_t AdsGas = 0x48, AdsAux = 0x49;
constexpr uint8_t ShtAmbient = 0x44, ShtChamber = 0x45;
constexpr uint8_t Mlx = 0x5A, Bme = 0x76;
constexpr bool GasEnabled[4] = {true, true, true, false};
constexpr bool AuxEnabled = false; // ADS #2 provisioned, not required for MVP.
constexpr float AdsLsbV = 4.096f / 32768.0f;
constexpr float GasDivider = 2.0f; // Buffered 10k/10k divider, see wiring document.
constexpr uint32_t FrameMs = 1000;
constexpr uint32_t WarmupMs = 20UL * 60 * 1000; // Engineering marker, NOT proof of conditioning.
constexpr uint16_t BmeHeaterC = 320, BmeHeaterMs = 150;
constexpr uint32_t HxFreshMs = 1500;
constexpr uint32_t CalibrationSamples = 20, CalibrationTimeoutMs = 10000;
constexpr uint32_t MaxAirMs = 120000, ValveSettleMs = 200;
#ifdef QY_ENABLE_AIR
constexpr bool AirCompiled = QY_ENABLE_AIR != 0;
#else
constexpr bool AirCompiled = false;
#endif
}
