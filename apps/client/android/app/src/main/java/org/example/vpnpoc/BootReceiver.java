package org.example.vpnpoc;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.net.VpnService;
import org.amnezia.awg.backend.GoBackend;

/**
 * ADR-098: after a reboot, restores the VPN only if the user left it connected (no manual
 * disconnect) and VPN consent is still ours. The service then runs the same restore path as an
 * Always-on start. Users with system Always-on get this from Android anyway; a duplicate start is
 * ignored by the restore path.
 */
public final class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        if (intent == null || !Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        if (!ClientApplication.wantsConnected(context)) return;
        if (VpnService.prepare(context) != null) {
            ClientApplication.setWantsConnected(context, false); // Consent revoked while off.
            return;
        }
        try {
            Intent restore = new Intent(context, GoBackend.VpnService.class)
                    .setAction(GoBackend.VpnService.RESTORE_ACTION);
            if (android.os.Build.VERSION.SDK_INT >= 26) context.startForegroundService(restore);
            else context.startService(restore);
        } catch (RuntimeException refused) {
            // Background start refused by the OS; the user can reconnect from the app.
        }
    }
}
