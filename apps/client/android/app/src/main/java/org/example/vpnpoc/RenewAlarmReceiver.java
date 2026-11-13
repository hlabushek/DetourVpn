package org.example.vpnpoc;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Doze-safe wake-up for {@link NativeRenewer}; not exported, carries no data. */
public final class RenewAlarmReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        if (intent == null) return;
        if (!(context.getApplicationContext() instanceof ClientApplication)) return;
        ClientApplication app = (ClientApplication) context.getApplicationContext();
        if (NativeRenewer.ACTION.equals(intent.getAction())) {
            PendingResult pending = goAsync();
            app.renewer.wake(pending::finish);
        } else if (ClientApplication.WAKE_RESTORE.equals(intent.getAction())) {
            PendingResult pending = goAsync();
            app.wakeRestore(pending::finish); // ADR-121
        } else if (ClientApplication.RECOVER.equals(intent.getAction())) {
            PendingResult pending = goAsync();
            app.wakeRecovery(pending::finish); // ADR-125
        }
    }
}
