package org.example.vpnpoc;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.os.Bundle;
import org.amnezia.awg.crypto.KeyPair;

/** Real-device native/parser checks. Synthetic profile never starts a service or sends traffic. */
public final class PocDeviceTests extends Instrumentation {
    private boolean permissionTest;
    private boolean networkTest;
    private boolean lifecycleTest;
    private boolean leaseExpiryTest;
    private boolean agentRestartTest;
    private boolean localStartupTest;
    private boolean freshEgressTest;
    private boolean controlEventTest;
    @Override public void onCreate(Bundle args) {
        permissionTest = args != null && "true".equals(args.getString("permission"));
        networkTest = args != null && "true".equals(args.getString("network"));
        lifecycleTest = args != null && "true".equals(args.getString("lifecycle"));
        leaseExpiryTest = args != null && "true".equals(args.getString("leaseExpiry"));
        agentRestartTest = args != null && "true".equals(args.getString("agentRestart"));
        localStartupTest = args != null && "true".equals(args.getString("localStartup"));
        freshEgressTest = args != null && "true".equals(args.getString("freshEgress"));
        controlEventTest = args != null && "true".equals(args.getString("controlEvent"));
        super.onCreate(args); start();
    }
    @Override public void onStart() {
        if (controlEventTest) { new PocLifecycleTests(this, false, false).runControlEventCheck(); return; }
        if (freshEgressTest) { new PocLifecycleTests(this, false, false).runFreshEgressCheck(); return; }
        if (localStartupTest) { new PocLifecycleTests(this, false, false).runLocalStartupCheck(); return; }
        if (lifecycleTest) { new PocLifecycleTests(this, leaseExpiryTest, agentRestartTest).run(); return; }
        if (networkTest) { new PocNetworkTests(this).run(); return; }
        // Instrumentation.onCreate precedes Application.onCreate; wait for its main-loop completion.
        waitForIdleSync();
        Bundle result = new Bundle();
        try {
            result.putString("stage", "application");
            PocApplication app = (PocApplication) getTargetContext().getApplicationContext();
            result.putString("native_initialization_failure", app.initializationFailure);
            result.putString("stage", "native_core");
            check(app.provider != null && app.provider.capabilities().contains("awg3"));
            check(!app.provider.isUp());
            result.putBoolean("native_core_version_and_down_state", true);
            result.putString("stage", "profile");
            String profile = "[Interface]\nAddress = 10.203.71.2/30\nDNS = 192.0.2.53\n"
                + "Jc = 4\nJmin = 40\nJmax = 70\nS1 = 20\nS2 = 30\n"
                + "H1 = 1001\nH2 = 1002\nH3 = 1003\nH4 = 1004\n"
                + "[Peer]\nPublicKey = " + new KeyPair().getPublicKey().toBase64()
                + "\nEndpoint = 192.0.2.1:51820\nAllowedIPs = 0.0.0.0/0\n";
            var parsed = Profile.parse(profile, app.key);
            check(parsed.getInterface().getKeyPair().getPublicKey().equals(app.key.getPublicKey()));
            check(parsed.toAwgUserspaceString().contains("jc=4"));
            result.putBoolean("device_key_and_awg_config_parse", true);
            result.putString("stage", "negative_profiles");
            rejects(profile.replace("Address =", "PrivateKey = ignored\nAddress ="), app);
            rejects(profile.replace("0.0.0.0/0", "10.0.0.0/8"), app);
            rejects(profile.replace("192.0.2.1:51820", "node.example:51820"), app);
            rejects(profile.replace("Jc = 4", "UnknownAWG = 4"), app);
            rejects(profile.replace("192.0.2.53", "2001:db8::53"), app);
            result.putBoolean("negative_profiles_rejected", true);
            result.putString("stage", "manifest");
            PackageInfo info = getTargetContext().getPackageManager().getPackageInfo(
                getTargetContext().getPackageName(), PackageManager.GET_SERVICES);
            check((info.applicationInfo.flags & ApplicationInfo.FLAG_ALLOW_BACKUP) == 0);
            boolean protectedService = false;
            for (var service : info.services)
                if (service.name.equals("org.amnezia.awg.backend.GoBackend$VpnService"))
                    protectedService = !service.exported && "android.permission.BIND_VPN_SERVICE".equals(service.permission);
            check(protectedService);
            result.putBoolean("backup_disabled_and_service_protected", true);
            if (permissionTest) {
                result.putString("stage", "permission_denial");
                result.putBoolean("permission_required", android.net.VpnService.prepare(getTargetContext()) != null);
                check(android.net.VpnService.prepare(getTargetContext()) != null);
                MainActivity activity = (MainActivity) startActivitySync(new android.content.Intent(
                    getTargetContext(), MainActivity.class).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK));
                waitForIdleSync();
                java.lang.reflect.Field pending = MainActivity.class.getDeclaredField("pending");
                pending.setAccessible(true);
                runOnMainSync(() -> {
                    try { pending.set(activity, parsed); }
                    catch (Exception failure) { throw new AssertionError(); }
                    android.widget.LinearLayout layout = (android.widget.LinearLayout)
                        ((android.view.ViewGroup) activity.findViewById(android.R.id.content)).getChildAt(0);
                    for (int i = 0; i < layout.getChildCount(); i++)
                        if (layout.getChildAt(i) instanceof android.widget.Button button
                                && button.getText().toString().equals("Подключить")) button.performClick();
                });
                waitForIdleSync();
                result.putString("stage", "permission_dialog");
                boolean dialog = false;
                for (int i = 0; i < 30; i++) {
                    var root = getUiAutomation().getRootInActiveWindow();
                    if (root != null && root.getPackageName() != null)
                        result.putString("permission_window_package", root.getPackageName().toString());
                    if (root != null && "com.android.vpndialogs".contentEquals(root.getPackageName())) {
                        dialog = true; break;
                    }
                    android.os.SystemClock.sleep(100);
                }
                check(dialog);
                result.putString("stage", "permission_result");
                sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK);
                waitForIdleSync();
                for (int i = 0; i < 30 && !app.status.equals("Разрешение VPN не выдано"); i++)
                    android.os.SystemClock.sleep(100);
                check(app.status.equals("Разрешение VPN не выдано"));
                check(!app.provider.isUp() && android.net.VpnService.prepare(getTargetContext()) != null);
                runOnMainSync(activity::finish);
                result.putBoolean("system_permission_denial_no_native_tunnel", true);
            }
            result.putBoolean("tunnel_or_network_test", false);
            result.putString("stage", "complete");
            finish(Activity.RESULT_OK, result);
        } catch (Throwable ignored) {
            result.putString("failure_class", ignored.getClass().getSimpleName());
            result.putString("error", "device_check_failed");
            finish(Activity.RESULT_CANCELED, result);
        }
    }
    private static void check(boolean condition) { if (!condition) throw new AssertionError(); }
    private static void rejects(String profile, PocApplication app) throws Exception {
        boolean rejected = false;
        try { Profile.parse(profile, app.key); } catch (Exception expected) { rejected = true; }
        check(rejected);
    }
}
