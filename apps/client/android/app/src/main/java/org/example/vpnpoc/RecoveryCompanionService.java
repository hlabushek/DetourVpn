package org.example.vpnpoc;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

/**
 * ADR-147 (3.3): a companion foreground service that runs only in a recovery gap — the user wants
 * the VPN, but no tunnel is up. Its only job is to keep the process out of the cached state so
 * wake and network callbacks reach {@link ClientApplication} at once. It starts nothing, holds no
 * wake lock, opens no socket and carries no data; {@link ClientApplication} stops it as soon as
 * the VPN service is up or the intent ends. Not exported.
 */
public final class RecoveryCompanionService extends Service {
    static final String CHANNEL = "detourvpn.recovery";
    static final int NOTIFICATION_ID = 12;

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        try {
            Notification notification = notification();
            if (Build.VERSION.SDK_INT >= DozeSurvival.FGS_TYPE_SDK) {
                startForeground(NOTIFICATION_ID, notification,
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_SYSTEM_EXEMPTED);
            } else {
                startForeground(NOTIFICATION_ID, notification);
            }
        } catch (RuntimeException refused) {
            // The system refused the foreground state (type or background-start rules). Recovery
            // still runs through alarms, the backstop job and callbacks; only this aid is lost.
            ((ClientApplication) getApplication()).companionRefused("start_foreground");
            stopSelf();
            return START_NOT_STICKY;
        }
        ((ClientApplication) getApplication()).companionStarted();
        // Not sticky: after a process death native recovery decides afresh whether a gap exists.
        return START_NOT_STICKY;
    }

    private Notification notification() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26 && manager != null) {
            manager.createNotificationChannel(new NotificationChannel(CHANNEL,
                    "Восстановление VPN", NotificationManager.IMPORTANCE_LOW));
        }
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        int icon = getResources().getIdentifier("ic_stat_vpn", "drawable", getPackageName());
        builder.setContentTitle("DetourVPN восстанавливает подключение")
                .setContentText("Нажмите, чтобы открыть приложение")
                .setSmallIcon(icon != 0 ? icon : android.R.drawable.ic_lock_lock)
                .setShowWhen(false).setOngoing(true);
        Intent launch = getPackageManager().getLaunchIntentForPackage(getPackageName());
        if (launch != null)
            builder.setContentIntent(PendingIntent.getActivity(this, 0, launch,
                    PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
        return builder.build();
    }

    @Override public void onDestroy() {
        ((ClientApplication) getApplication()).companionStopped();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
