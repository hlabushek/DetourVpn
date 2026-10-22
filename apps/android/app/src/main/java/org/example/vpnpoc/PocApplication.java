package org.example.vpnpoc;

import android.app.Application;
import org.amnezia.awg.backend.Tunnel;
import org.amnezia.awg.crypto.KeyPair;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** One ephemeral key and one serialized backend per process; no disk storage. */
public final class PocApplication extends Application implements Tunnel {
    final ExecutorService worker = Executors.newSingleThreadExecutor();
    final ExecutorService importWorker = Executors.newSingleThreadExecutor();
    final KeyPair key = new KeyPair();
    TunnelProviderV1 provider;
    volatile String status = "Отключено";
    String initializationFailure = "none";
    @Override public void onCreate() {
        super.onCreate();
        try { provider = new AwgProviderV1(this, this); }
        catch (RuntimeException | LinkageError ignored) {
            initializationFailure = ignored.getClass().getSimpleName();
            status = "Native SDK недоступен или версия несовместима";
        }
    }
    @Override public String getName() { return "i011"; }
    @Override public void onStateChange(State state) {
        if (state == State.DOWN) status = "Отключено";
    }
}
