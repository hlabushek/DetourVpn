package org.example.vpnpoc;

import android.app.Activity;
import android.content.Intent;
import android.net.VpnService;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import org.amnezia.awg.config.Config;

public final class MainActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private PocApplication app;
    private TextView status;
    private Config pending;
    private int importGeneration;
    private final Runnable poll = new Runnable() {
        @Override public void run() {
            app.worker.execute(() -> {
                try {
                    boolean up = app.provider != null && app.provider.isUp();
                    if (up) app.status = TunnelStatus.describe(true,
                        app.provider.lastHandshakeSeconds(), System.currentTimeMillis() / 1000);
                } catch (Exception ignored) { app.status = "Ошибка статуса · отключите туннель"; }
                main.post(() -> status.setText(app.status));
            });
            main.postDelayed(this, 2000);
        }
    };
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        app = (PocApplication) getApplication();
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(32, 32, 32, 32);
        setContentView(layout);
        TextView intro = new TextView(this);
        intro.setText(R.string.intro);
        layout.addView(intro);
        TextView publicKey = new TextView(this);
        publicKey.setText(app.key.getPublicKey().toBase64());
        publicKey.setTextIsSelectable(true);
        layout.addView(publicKey);
        status = new TextView(this);
        layout.addView(status);
        Button load = new Button(this);
        load.setText(R.string.load_profile);
        load.setOnClickListener(v -> startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT)
            .setType("*/*").addCategory(Intent.CATEGORY_OPENABLE), 10));
        layout.addView(load);
        Button connect = new Button(this);
        connect.setText(R.string.connect);
        connect.setOnClickListener(v -> {
            if (app.provider == null) return;
            if (pending == null) { app.status = "Сначала выберите корректный профиль"; return; }
            Intent permission = VpnService.prepare(this);
            if (permission == null) connect();
            else startActivityForResult(permission, 11);
        });
        layout.addView(connect);
        Button disconnect = new Button(this);
        disconnect.setText(R.string.disconnect);
        disconnect.setOnClickListener(v -> app.worker.execute(() -> {
            try { if (app.provider != null) app.provider.disconnect(); app.status = "Отключено"; }
            catch (Exception ignored) { app.status = "Ошибка отключения · проверьте системный VPN"; }
        }));
        layout.addView(disconnect);
    }
    private void connect() {
        final Config config = pending;
        if (config == null) return;
        app.status = "Создание туннеля…";
        app.worker.execute(() -> {
            try {
                // Never replace a live peer implicitly.
                if (app.provider == null || app.provider.isUp()) return;
                app.provider.connect(config);
                app.status = TunnelStatus.describe(true, 0, 0);
            } catch (Exception ignored) {
                try { if (app.provider != null) app.provider.disconnect(); }
                catch (Exception cleanupIgnored) { /* System settings remain available. */ }
                app.status = "Подключение не удалось · проверьте разрешение и staging profile";
            }
        });
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == 11) {
            if (result == RESULT_OK) connect();
            else app.status = "Разрешение VPN не выдано";
        } else if (request == 10 && result == RESULT_OK && data != null && data.getData() != null) {
            pending = null;
            final int generation = ++importGeneration;
            final android.net.Uri uri = data.getData();
            // A slow document provider must never block native disconnect/status operations.
            app.importWorker.execute(() -> {
                try (InputStream in = getContentResolver().openInputStream(uri)) {
                    if (in == null) throw new IllegalArgumentException();
                    ByteArrayOutputStream buffer = new ByteArrayOutputStream();
                    byte[] chunk = new byte[1024];
                    int count;
                    while (buffer.size() <= 16384 && (count = in.read(chunk)) != -1)
                        buffer.write(chunk, 0, count);
                    byte[] bytes = buffer.toByteArray();
                    if (bytes.length > 16384) throw new IllegalArgumentException();
                    Config config = Profile.parse(new String(bytes, StandardCharsets.UTF_8), app.key);
                    main.post(() -> {
                        if (generation != importGeneration) return;
                        pending = config;
                        app.status = "Профиль проверен · перед сменой профиля отключите VPN";
                    });
                } catch (Exception ignored) {
                    main.post(() -> {
                        if (generation == importGeneration)
                            app.status = "Профиль отклонён · неверный формат/политика";
                    });
                }
            });
        }
    }
    @Override protected void onResume() { super.onResume(); main.post(poll); }
    @Override protected void onPause() { main.removeCallbacks(poll); super.onPause(); }
}
