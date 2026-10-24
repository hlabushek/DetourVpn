package org.example.vpnpoc;
import org.junit.Test;
import static org.junit.Assert.*;

public class WindowDeadlineTest {
    @Test public void renewalKeepsBindingAndReplayDoesNotExtend() {
        var lease = new WindowDeadline();
        lease.start("connection", "runtime", 1, 80000, 0);
        lease.advance("connection", "runtime", 2, 110000, 30000);
        lease.advance("connection", "runtime", 2, 120000, 40000);
        assertEquals(110000, lease.deadline);
    }
    @Test public void wrongConnectionRuntimeAndGenerationFail() {
        var lease = new WindowDeadline(); lease.start("a", "r", 1, 80000, 0);
        assertThrows(IllegalStateException.class, () -> lease.advance("b", "r", 2, 100000, 30000));
        assertThrows(IllegalStateException.class, () -> lease.advance("a", "s", 2, 100000, 30000));
        assertThrows(IllegalStateException.class, () -> lease.advance("a", "r", 3, 100000, 30000));
    }
    @Test public void expiredOrStoppedCannotBeRevived() {
        var lease = new WindowDeadline(); lease.start("a", "r", 1, 80000, 0);
        assertThrows(IllegalStateException.class, () -> lease.advance("a", "r", 2, 160000, 80000));
        lease.clear();
        assertThrows(IllegalStateException.class, () -> lease.advance("a", "r", 2, 100000, 30000));
    }
    @Test public void boundsAndNonIncreasingDeadlineFail() {
        var lease = new WindowDeadline();
        assertThrows(IllegalStateException.class, () -> lease.start("a", "r", 1, 90001, 0));
        lease.start("a", "r", 1, 80000, 0);
        assertThrows(IllegalStateException.class, () -> lease.advance("a", "r", 2, 80000, 30000));
    }
}
