package org.example.vpnpoc;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Doze-safe wake-up for {@link NativeRenewer}; not exported, carries no data. */
public final class RenewAlarmReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        if (intent == null || !NativeRenewer.ACTION.equals(intent.getAction())) return;
        if (!(context.getApplicationContext() instanceof ClientApplication)) return;
        ClientApplication app = (ClientApplication) context.getApplicationContext();
        PendingResult pending = goAsync();
        app.renewer.wake(pending::finish);
    }
}
