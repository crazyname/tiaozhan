#include "motor_core.h"
#include <assert.h>
#include <cstring>
#include <stdio.h>

qy::MotorPolicy ready() {
    qy::MotorPolicy policy;
    policy.tick(0, true, true, true, 0);
    policy.tick(1100, true, true, true, 0);
    return policy;
}

int main() {
    auto p = ready();
    assert(!p.start(4, 1, 1000, 1100, true, true));
    assert(!p.start(31, 1, 1000, 1100, true, true));
    assert(!p.start(NAN, 1, 1000, 1100, true, true));
    assert(!p.start(10, 0, 1000, 1100, true, true));
    assert(!p.start(10, 1, 300001, 1100, true, true));
    assert(!p.start(10, 1, 1000, 1100, false, true));
    assert(p.start(10, 1, 1000, 1100, true, true));
    assert(!p.start(20, -1, 1000, 1110, true, true)); // No in-flight reversal or restart.
    p.tick(1200, true, true, true, 5);
    assert(p.running && p.duty > 0 && p.duty <= .051f);
    p.hostLease = 2000;
    p.tick(2100, true, true, true, 10);
    assert(!p.running && p.duty == 0 && p.ended == 2100 && !p.latched);
    assert(!p.start(10, -1, 1000, 2200, true, true)); // Must stop physically before reversing.

    for (int fault = 0; fault < 7; ++fault) {
        p = ready(); assert(p.start(20, 1, 300000, 1100, true, true));
        if (fault == 0) p.tick(1200, false, true, true, 10);
        if (fault == 1) p.tick(1200, true, false, true, 10);
        if (fault == 2) p.tick(1200, true, true, false, 10);
        if (fault == 3) p.tick(2300, true, true, true, 10);
        if (fault == 4) p.tick(1200, true, true, true, 46);
        if (fault == 5) p.tick(1200, true, true, true, -3);
        if (fault == 6) { p.hostLease = 2100; p.tick(2100, true, true, true, 0); }
        assert(!p.running && p.duty == 0 && p.latched && strcmp(p.fault, "NONE"));
        p.tick(3000, true, true, true, 0);
        p.tick(4100, true, true, true, 0);
        assert(!p.start(10, 1, 1000, 4100, true, true)); // Restored interlocks never clear faults.
        assert(!p.clear(4100, false, true));
        assert(p.clear(4100, true, true));
        assert(p.start(10, 1, 1000, 4100, true, true));
        p.stop(4200); assert(!p.running && p.duty == 0);
    }
    p = ready(); assert(p.start(10, -1, 1000, 1100, true, true));
    p.tick(1200, true, true, true, -10); assert(p.running);
    p.tick(1300, true, true, true, NAN); assert(!p.running && p.latched);
    assert(qy::encoderDelta(0, 2)+qy::encoderDelta(2, 3)+qy::encoderDelta(3, 1)+qy::encoderDelta(1, 0) == 4);
    assert(qy::encoderDelta(0, 1)+qy::encoderDelta(1, 3)+qy::encoderDelta(3, 2)+qy::encoderDelta(2, 0) == -4);
    assert(qy::encoderDelta(0, 3) == 0); // Impossible two-bit jump doesn't fabricate a count.
    puts("motor policy tests passed");
}
