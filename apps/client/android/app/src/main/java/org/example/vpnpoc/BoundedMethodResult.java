package org.example.vpnpoc;

import android.os.Handler;
import io.flutter.plugin.common.MethodChannel;
import java.util.concurrent.atomic.AtomicBoolean;

/** A stuck JNI operation may not keep a channel request pending indefinitely. */
final class BoundedMethodResult implements MethodChannel.Result {
    private final AtomicBoolean completed = new AtomicBoolean();
    private final MethodChannel.Result target;
    private final Handler handler;
    private final Runnable timeout;
    BoundedMethodResult(MethodChannel.Result target, Handler handler, Runnable cancel) {
        this.target = target;
        this.handler = handler;
        timeout = () -> {
            if (completed.compareAndSet(false, true)) {
                cancel.run();
                target.error("native_operation_timeout", "Операция VPN остановлена по таймауту", null);
            }
        };
        handler.postDelayed(timeout, 15_000);
    }
    private boolean finish() {
        if (!completed.compareAndSet(false, true)) return false;
        handler.removeCallbacks(timeout);
        return true;
    }
    @Override public void success(Object value) { if (finish()) target.success(value); }
    @Override public void error(String code, String message, Object details) {
        if (finish()) target.error(code, message, details);
    }
    @Override public void notImplemented() { if (finish()) target.notImplemented(); }
}
