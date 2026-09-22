#pragma once
#include "motor_core.h"

void motorBegin();
void motorMainAlive();
bool motorStart(float rpm, int direction, uint32_t duration, const char* session);
void motorStop();
bool motorReset();
void motorLease(const char* session);
qy::MotorPolicy motorSnapshot();
bool motorReady();
bool motorInterlocked();
