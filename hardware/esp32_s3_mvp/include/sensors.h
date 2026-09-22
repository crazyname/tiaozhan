#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <bme68xLibrary.h>

bool i2cPresent(uint8_t address);
bool readAds(uint8_t address, uint8_t channel, int16_t &value);
bool readSht(uint8_t address, float &temperature, float &humidity);
bool readLeaf(float &temperature);
bool readHx(int32_t &raw);
bool readBme(float &gasOhm);
