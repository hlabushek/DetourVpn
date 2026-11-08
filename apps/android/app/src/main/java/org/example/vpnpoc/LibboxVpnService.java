package org.example.vpnpoc;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.ConnectivityManager;
import android.net.IpPrefix;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.VpnService;
import android.os.Build;
import android.os.IBinder;
import android.os.ParcelFileDescriptor;
import android.system.OsConstants;
import io.nekohasekai.libbox.BoxService;
import io.nekohasekai.libbox.InterfaceUpdateListener;
import io.nekohasekai.libbox.Libbox;
import io.nekohasekai.libbox.NetworkInterfaceIterator;
import io.nekohasekai.libbox.PlatformInterface;
import io.nekohasekai.libbox.RoutePrefix;
import io.nekohasekai.libbox.RoutePrefixIterator;
import io.nekohasekai.libbox.SetupOptions;
import io.nekohasekai.libbox.StringIterator;
import io.nekohasekai.libbox.StringBox;
import io.nekohasekai.libbox.TunOptions;
import io.nekohasekai.libbox.WIFIState;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.NetworkInterface;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/** Sole Android VPN service and libbox platform adapter for the beta client. */
public final class LibboxVpnService extends VpnService implements PlatformInterface {
    static final String ACTION_START = "org.example.vpnpoc.action.LIBBOX_START";
    private static final String CHANNEL = "detourvpn_vpn";
    private static final int NOTIFICATION_ID = 7301;
    private static volatile boolean libboxSetup;

    private final LocalBinder binder = new LocalBinder();
    private final AtomicLong networkCallbackSequence = new AtomicLong();
    private final Map<InterfaceUpdateListener, ConnectivityManager.NetworkCallback> monitors =
            new ConcurrentHashMap<>();
    private volatile BoxService box;
    private volatile ParcelFileDescriptor tunDescriptor;

    @Override public void onCreate() {
        super.onCreate();
        startInForeground();
    }

    private void startInForeground() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(new NotificationChannel(CHANNEL,
                    "VPN connection", NotificationManager.IMPORTANCE_LOW));
        }
        Notification notification = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL)
                    .setSmallIcon(org.example.vpnclient.R.drawable.ic_vpn)
                    .setContentTitle("DetourVpn")
                    .setContentText("Защищённое соединение")
                    .setOngoing(true).build()
                : new Notification.Builder(this)
                    .setSmallIcon(org.example.vpnclient.R.drawable.ic_vpn)
                    .setContentTitle("DetourVpn")
                    .setContentText("Защищённое соединение")
                    .setOngoing(true).build();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SYSTEM_EXEMPTED);
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, 0);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if ((intent == null || !ACTION_START.equals(intent.getAction()))
                && intent != null && VpnService.SERVICE_INTERFACE.equals(intent.getAction())) {
            ((ClientApplication) getApplication()).startAlwaysOnRecovery(this);
        } else if (intent == null) {
            ((ClientApplication) getApplication()).startAlwaysOnRecovery(this);
        }
        return START_STICKY;
    }

    @Override public IBinder onBind(Intent intent) {
        if (VpnService.SERVICE_INTERFACE.equals(intent.getAction())) return super.onBind(intent);
        return binder;
    }

    @Override public void onRevoke() {
        stopBox();
        super.onRevoke();
    }

    @Override public void onDestroy() {
        stopBox();
        for (var callback : monitors.values()) {
            try { getConnectivity().unregisterNetworkCallback(callback); }
            catch (RuntimeException ignored) { }
        }
        monitors.clear();
        ((ClientApplication) getApplication()).stopAlwaysOnService(this);
        super.onDestroy();
    }

    private ConnectivityManager getConnectivity() {
        return (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
    }

    final class LocalBinder extends android.os.Binder {
        void connect(String configuration) throws Exception { startBox(configuration); }
        void disconnect() { stopBox(); }
        boolean isUp() { return box != null; }
        String peerStats() throws Exception {
            BoxService service = box;
            if (service == null) throw new IllegalStateException("vpn_not_active");
            return service.peerStats();
        }
    }

    private synchronized void startBox(String configuration) throws Exception {
        if (configuration == null || configuration.length() > 65536 || prepare(this) != null)
            throw new IllegalArgumentException("vpn_configuration_invalid");
        stopBox();
        if (!libboxSetup) {
            SetupOptions setup = new SetupOptions();
            setup.setBasePath(getFilesDir().getAbsolutePath());
            setup.setWorkingPath(getFilesDir().getAbsolutePath());
            setup.setTempPath(getCacheDir().getAbsolutePath());
            setup.setFixAndroidStack(Build.VERSION.SDK_INT <= Build.VERSION_CODES.N_MR1);
            try {
                Libbox.setup(setup);
            } catch (Exception | LinkageError failure) {
                logStartFailure("libbox_setup", failure);
                throw failure;
            }
            libboxSetup = true;
        }
        final BoxService service;
        try {
            trace("libbox_new_service_begin");
            service = Libbox.newService(configuration, this);
            trace("libbox_new_service_returned");
        } catch (Exception | LinkageError failure) {
            logStartFailure("libbox_config", failure);
            throw failure;
        }
        try {
            trace("libbox_service_start_begin");
            service.start();
            trace("libbox_service_start_returned");
            box = service;
        } catch (Exception | LinkageError failure) {
            logStartFailure("libbox_service_start", failure);
            try { service.close(); } catch (Exception ignored) { }
            closeTunDescriptor();
            throw failure;
        }
    }

    private static void trace(String event) {
        android.util.Log.i("DetourVpnTrace", "event=" + event);
    }

    private static void logStartFailure(String stage, Throwable failure) {
        var seen = java.util.Collections.newSetFromMap(
                new java.util.IdentityHashMap<Throwable, Boolean>());
        var types = new java.util.ArrayList<String>();
        Throwable current = failure;
        while (current != null && types.size() < 4 && seen.add(current)) {
            String name = current.getClass().getSimpleName();
            types.add(name.matches("[A-Za-z0-9_]{1,48}") ? name : "Other");
            current = current.getCause();
        }
        String safeCategory = classifyStartFailure(failure);
        android.util.Log.e("DetourVpnNativeCall", "start_stage=" + stage
                + " failure_types=" + String.join(",", types)
                + (safeCategory == null ? "" : " failure_category=" + safeCategory));
    }

    private static String classifyStartFailure(Throwable failure) {
        var seen = java.util.Collections.newSetFromMap(
                new java.util.IdentityHashMap<Throwable, Boolean>());
        Throwable current = failure;
        for (int depth = 0; current != null && depth < 4 && seen.add(current); depth++) {
            String message = current.getMessage();
            if (message != null && message.length() <= 4096) {
                String normalized = message.toLowerCase(java.util.Locale.ROOT);
                String nativeCategory = LibboxStartErrorCategory.classify(message);
                if (nativeCategory != null) return nativeCategory;
                if (normalized.contains("set ipc config")) return "awg_uapi_config";
                if (normalized.contains("tun start")) return "awg_tun_start";
                if (normalized.contains("awg device up")) return "awg_device_up";
                if (normalized.contains("start outbound/awg[awg]")) return "awg_endpoint_start";
                if (normalized.contains("start inbound/tun[tun-in]")) return "tun_inbound_start";
                if (normalized.contains("socket_protection_failed")) return "socket_protection";
                if (normalized.contains("vpn_permission_missing")) return "vpn_permission";
                if (normalized.contains("vpn_establish_failed")) return "android_tun_establish";
                if (normalized.contains("open tun")) return "android_tun_open";
                if (normalized.contains("create tunnel")) return "awg_tun_create";
                if (normalized.contains("start tunnel")) return "awg_tun_start";
            }
            current = current.getCause();
        }
        return null;
    }

    private static void logEngineFailureCategory(String message) {
        if (message == null || message.length() > 4096) return;
        String normalized = message.toLowerCase(java.util.Locale.ROOT);
        String category = LibboxStartErrorCategory.classifyEngineBindFailure(message);
        if (category == null) {
            if (normalized.contains("start outbound/awg[awg]")) category = "awg_endpoint_start";
            else if (normalized.contains("start inbound/tun[tun-in]")) category = "tun_inbound_start";
            else if (normalized.contains("open tun")) category = "tun_open";
            else if (normalized.contains("interface monitor")) category = "interface_monitor";
        }
        if (category != null) {
            android.util.Log.e("DetourVpnNativeCall", "engine_failure_category=" + category);
        }
    }

    private synchronized void stopBox() {
        BoxService service = box;
        box = null;
        if (service != null) {
            try { service.close(); } catch (Exception ignored) { }
        }
        closeTunDescriptor();
    }

    private void closeTunDescriptor() {
        ParcelFileDescriptor descriptor = tunDescriptor;
        tunDescriptor = null;
        if (descriptor != null) {
            try { descriptor.close(); } catch (java.io.IOException ignored) { }
        }
    }

    @Override public boolean usePlatformAutoDetectInterfaceControl() { return true; }
    @Override public void autoDetectInterfaceControl(int fd) throws Exception {
        try {
            if (!protect(fd)) throw new IllegalStateException("socket_protection_failed");
        } catch (Exception | LinkageError failure) {
            logStartFailure("android_protect_socket", failure);
            throw failure;
        }
    }
    @Override public int openTun(TunOptions options) throws Exception {
        android.util.Log.i("DetourVpnNativeCall", "platform_stage=android_open_tun_enter");
        try {
            int fd = openAndroidTun(options);
            android.util.Log.i("DetourVpnNativeCall", "platform_stage=android_open_tun_success");
            return fd;
        } catch (Exception | LinkageError failure) {
            logStartFailure("android_open_tun", failure);
            throw failure;
        }
    }

    private int openAndroidTun(TunOptions options) throws Exception {
        if (prepare(this) != null) throw new IllegalStateException("vpn_permission_missing");
        VpnService.Builder builder = new VpnService.Builder().setSession("DetourVpn")
                .setMtu(options.getMTU());
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) builder.setMetered(false);
        addAddresses(builder, options.getInet4Address());
        addAddresses(builder, options.getInet6Address());
        if (options.getAutoRoute()) {
            StringBox dns = options.getDNSServerAddress();
            if (dns != null && dns.getValue() != null && !dns.getValue().isBlank())
                builder.addDnsServer(dns.getValue());
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                addRoutes(builder, options.getInet4RouteAddress());
                addRoutes(builder, options.getInet6RouteAddress());
                addExcludedRoutes(builder, options.getInet4RouteExcludeAddress());
                addExcludedRoutes(builder, options.getInet6RouteExcludeAddress());
            } else {
                addRoutes(builder, options.getInet4RouteRange());
                addRoutes(builder, options.getInet6RouteRange());
            }
            // App/domain split policy is evaluated by libbox route rules. Applying the
            // package list again at Android's TUN boundary would prevent domain rules
            // from matching traffic from apps outside the package list.
        }
        try { builder.addDisallowedApplication(getPackageName()); }
        catch (android.content.pm.PackageManager.NameNotFoundException impossible) {
            throw new IllegalStateException("app_package_unavailable");
        }
        ParcelFileDescriptor established = builder.establish();
        if (established == null) throw new IllegalStateException("vpn_establish_failed");
        closeTunDescriptor();
        tunDescriptor = established;
        return established.getFd();
    }

    private static void addAddresses(VpnService.Builder builder, RoutePrefixIterator addresses)
            throws Exception {
        while (addresses.hasNext()) {
            RoutePrefix address = addresses.next();
            builder.addAddress(address.address(), address.prefix());
        }
    }

    private static void addRoutes(VpnService.Builder builder, RoutePrefixIterator routes)
            throws Exception {
        while (routes.hasNext()) {
            RoutePrefix route = routes.next();
            builder.addRoute(InetAddress.getByName(route.address()), route.prefix());
        }
    }

    private static void addExcludedRoutes(VpnService.Builder builder, RoutePrefixIterator routes)
            throws Exception {
        while (routes.hasNext()) {
            RoutePrefix route = routes.next();
            builder.excludeRoute(new IpPrefix(InetAddress.getByName(route.address()), route.prefix()));
        }
    }

    @Override public void writeLog(String message) { logEngineFailureCategory(message); }
    @Override public boolean useProcFS() { return false; }
    @Override public int findConnectionOwner(int protocol, String sourceAddress, int sourcePort,
            String destinationAddress, int destinationPort) throws Exception {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) throw new IllegalStateException("owner_lookup_unavailable");
        int uid = getConnectivity().getConnectionOwnerUid(protocol,
                new InetSocketAddress(sourceAddress, sourcePort),
                new InetSocketAddress(destinationAddress, destinationPort));
        if (uid < 0) throw new IllegalStateException("owner_not_found");
        return uid;
    }
    @Override public String packageNameByUid(int uid) throws Exception {
        String[] names = getPackageManager().getPackagesForUid(uid);
        if (names == null || names.length == 0) throw new IllegalStateException("owner_not_found");
        return names[0];
    }
    @Override public int uidByPackageName(String packageName) throws Exception {
        return getPackageManager().getApplicationInfo(packageName, 0).uid;
    }

    @Override public void startDefaultInterfaceMonitor(InterfaceUpdateListener listener) throws Exception {
        ConnectivityManager.NetworkCallback callback = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network network) {
                notifyInterface(listener, network, "available");
            }
            @Override public void onCapabilitiesChanged(Network network, NetworkCapabilities caps) {
                notifyInterface(listener, network, "capabilities_changed");
            }
            @Override public void onLinkPropertiesChanged(Network network, LinkProperties properties) {
                notifyInterface(listener, network, "link_properties_changed");
            }
        };
        monitors.put(listener, callback);
        try {
            getConnectivity().registerDefaultNetworkCallback(callback);
        } catch (Exception | LinkageError failure) {
            monitors.remove(listener);
            logStartFailure("android_interface_monitor", failure);
            throw failure;
        }
    }
    @Override public void closeDefaultInterfaceMonitor(InterfaceUpdateListener listener) {
        ConnectivityManager.NetworkCallback callback = monitors.remove(listener);
        if (callback != null) getConnectivity().unregisterNetworkCallback(callback);
    }
    private void notifyInterface(InterfaceUpdateListener listener, Network network, String source) {
        long sequence = networkCallbackSequence.incrementAndGet();
        trace("network_callback_" + source + "_begin_" + sequence);
        LinkProperties link = getConnectivity().getLinkProperties(network);
        NetworkCapabilities caps = getConnectivity().getNetworkCapabilities(network);
        if (link == null || link.getInterfaceName() == null) {
            trace("network_callback_snapshot_unavailable_" + sequence);
            return;
        }
        try {
            NetworkInterface system = NetworkInterface.getByName(link.getInterfaceName());
            if (system == null) {
                trace("network_callback_interface_unavailable_" + sequence);
                return;
            }
            trace("network_callback_update_begin_" + sequence);
            listener.updateDefaultInterface(link.getInterfaceName(), system.getIndex(),
                    caps != null && !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED), false);
            trace("network_callback_update_returned_" + sequence);
        } catch (Exception ignored) {
            trace("network_callback_update_failed_" + sequence);
        }
    }

    @Override public NetworkInterfaceIterator getInterfaces() throws Exception {
        List<io.nekohasekai.libbox.NetworkInterface> found = new ArrayList<>();
        for (Network network : getConnectivity().getAllNetworks()) {
            LinkProperties link = getConnectivity().getLinkProperties(network);
            NetworkCapabilities caps = getConnectivity().getNetworkCapabilities(network);
            if (link == null || caps == null || link.getInterfaceName() == null
                    || caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue;
            NetworkInterface system = NetworkInterface.getByName(link.getInterfaceName());
            if (system == null || system.isLoopback()) continue;
            var item = new io.nekohasekai.libbox.NetworkInterface();
            item.setName(system.getName()); item.setIndex(system.getIndex());
            item.setMTU(system.getMTU());
            item.setType(caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ? Libbox.InterfaceTypeWIFI
                    : caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) ? Libbox.InterfaceTypeCellular
                    : caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) ? Libbox.InterfaceTypeEthernet
                    : Libbox.InterfaceTypeOther);
            item.setMetered(!caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED));
            item.setFlags(OsConstants.IFF_UP | OsConstants.IFF_RUNNING);
            List<String> addresses = new ArrayList<>();
            for (var address : system.getInterfaceAddresses())
                addresses.add(address.getAddress().getHostAddress() + "/" + address.getNetworkPrefixLength());
            item.setAddresses(new ArrayStringIterator(addresses));
            List<String> dns = new ArrayList<>();
            for (InetAddress address : link.getDnsServers()) dns.add(address.getHostAddress());
            item.setDNSServer(new ArrayStringIterator(dns));
            found.add(item);
        }
        return new NetworkInterfaceIterator() {
            private int index;
            @Override public boolean hasNext() { return index < found.size(); }
            @Override public io.nekohasekai.libbox.NetworkInterface next() { return found.get(index++); }
        };
    }
    @Override public boolean underNetworkExtension() { return false; }
    @Override public boolean includeAllNetworks() { return false; }
    @Override public WIFIState readWIFIState() { return null; }
    @Override public StringIterator systemCertificates() { return new ArrayStringIterator(List.of()); }
    @Override public void clearDNSCache() { }
    @Override public void sendNotification(io.nekohasekai.libbox.Notification notification) { }
    @Override public io.nekohasekai.libbox.LocalDNSTransport localDNSTransport() { return null; }

    private static final class ArrayStringIterator implements StringIterator {
        private final List<String> values;
        private int index;
        ArrayStringIterator(List<String> values) { this.values = values; }
        @Override public boolean hasNext() { return index < values.size(); }
        @Override public int len() { return values.size(); }
        @Override public String next() { return values.get(index++); }
    }
}
