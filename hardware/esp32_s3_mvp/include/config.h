#pragma once
#include <stdint.h>

// Hardware contract HW-v0.2: ESP32-S3-DevKitC-1 / WROOM-1 N8R8.
namespace cfg {
#ifdef QY_WOKWI_SIM
constexpr char Hardware[] = "QY-WOKWI-SIM";
constexpr char SourceMode[] = "simulation";
constexpr char ReadyMessage[] = "QY-FW-0.4.0 Wokwi functional simulation ready; outputs initially OFF";
#else
constexpr char Hardware[] = "QY-HW-0.2-N8R8";
constexpr char SourceMode[] = "hardware";
constexpr char ReadyMessage[] = "QY-FW-0.4.0 hardware acquisition ready; outputs initially OFF";
#endif
constexpr char Firmware[] = "QY-FW-0.4.0";
constexpr int Sda = 8, Scl = 9;
constexpr int HxData = 4, HxClock = 5;
constexpr int AirEnable = 6; // LOW only when physical enable switch is closed.
constexpr int Pump = 16, SampleValve = 17, PurgeValve = 18;
constexpr int MotorEnable = 7, MotorGuard = 21;
constexpr int MotorPwm = 10, MotorDir = 11, MotorSleep = 12, MotorFault = 15;
constexpr int EncoderA = 13, EncoderB = 14;
constexpr float EncoderCountsPerOutputTurn = 64.0f * (25.0f*30*28*28*30)/(10.0f*10*12*12*12);
#ifdef QY_ENABLE_MOTOR
constexpr bool MotorCompiled = QY_ENABLE_MOTOR != 0;
#else
constexpr bool MotorCompiled = false;
#endif
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
#ifdef QY_WOKWI_SIM
constexpr uint32_t WarmupMs = 5000; // Simulation-only: keep demos short; not a hardware claim.
#else
constexpr uint32_t WarmupMs = 20UL * 60 * 1000; // Engineering marker, NOT proof of conditioning.
#endif
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
