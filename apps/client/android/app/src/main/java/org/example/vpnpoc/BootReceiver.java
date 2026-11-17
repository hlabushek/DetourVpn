package org.example.vpnpoc;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.net.VpnService;
import org.amnezia.awg.backend.GoBackend;

/**
 * ADR-098/149: after a reboot ({@code BOOT_COMPLETED}) or an app update
 * ({@code MY_PACKAGE_REPLACED}, ADR-149 6.3), restores the VPN only if the user left it connected
 * (no manual disconnect) and VPN consent is still ours ({@link RestoreTrigger}). The service then
 * runs the same restore path as an Always-on start. Users with system Always-on get this from
 * Android anyway; a duplicate start is ignored by the restore path.
 *
 * <p>ADR-149 6.2(a): the app is not direct-boot aware, so {@code BOOT_COMPLETED} arrives only after
 * the first unlock; until then Lockdown (if the user set it) blocks traffic. No
 * {@code LOCKED_BOOT_COMPLETED}, no device-protected credentials.
 */
public final class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        String action = intent == null ? null : intent.getAction();
        if (!RestoreTrigger.restoreAction(action)) return;
        boolean wanted = ClientApplication.wantsConnected(context);
        // prepare() is asked only when the user wants the VPN, as before.
        boolean consent = wanted && VpnService.prepare(context) == null;
        RestoreTrigger.Decision decision = RestoreTrigger.decide(action, wanted, consent);
        log(context, decision, action);
        switch (decision) {
            case CLEAR_INTENT:
                ClientApplication.setWantsConnected(context, false); // Consent revoked while off.
                return;
            case RESTORE:
                break;
            default:
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

    /** ADR-144 event log, codes only: which broadcast and what was decided. */
    private static void log(Context context, RestoreTrigger.Decision decision, String action) {
        try {
            Context app = context.getApplicationContext();
            if (app instanceof ClientApplication)
                ((ClientApplication) app).log(RestoreTrigger.MY_PACKAGE_REPLACED.equals(action)
                        ? "restore_update" : "restore_boot",
                        decision.name().toLowerCase(java.util.Locale.ROOT));
        } catch (RuntimeException ignored) { /* Diagnostics only. */ }
    }
}
