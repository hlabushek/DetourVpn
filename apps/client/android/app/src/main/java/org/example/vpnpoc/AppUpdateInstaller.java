package org.example.vpnpoc;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.net.ssl.HttpsURLConnection;

/**
 * In-app update download and hand-off (ADR-107). The APK is downloaded over HTTPS only into
 * app-private cache, checked for exact size, SHA-256, package name and version code, and only
 * then handed to the Android system installer through {@link UpdateApkProvider}. Android itself
 * still requires the same signing key and the user's confirmation. No logging of URLs or bodies.
 */
final class AppUpdateInstaller {
    static final String DIRECTORY = "updates";
    static final String FILE_NAME = "update.apk";
    static final String MIME = "application/vnd.android.package-archive";
    private static final ExecutorService DOWNLOADS = Executors.newSingleThreadExecutor();
    private static final AtomicBoolean RUNNING = new AtomicBoolean(false);

    interface Callback { void done(String outcome); }

    private AppUpdateInstaller() {}

    static long installedVersionCode(Context context) throws PackageManager.NameNotFoundException {
        PackageInfo info = context.getPackageManager().getPackageInfo(context.getPackageName(), 0);
        return versionCode(info);
    }

    /** The installed versionName (for display only; decisions use the version code). */
    static String installedVersionName(Context context) throws PackageManager.NameNotFoundException {
        return context.getPackageManager().getPackageInfo(context.getPackageName(), 0).versionName;
    }

    @SuppressWarnings("deprecation")
    private static long versionCode(PackageInfo info) {
        return Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
    }

    static File apkFile(Context context) {
        return new File(new File(context.getCacheDir(), DIRECTORY), FILE_NAME);
    }

    /** Removes a file left by an earlier attempt. Called on start and before each download. */
    static void discardStale(Context context) {
        File file = apkFile(context);
        if (file.exists() && !file.delete()) file.deleteOnExit();
    }

    /** False on Android 8+ when the user has not allowed "install unknown apps" for DetourVPN. */
    static boolean canInstall(Context context) {
        return Build.VERSION.SDK_INT < 26 || context.getPackageManager().canRequestPackageInstalls();
    }

    static Intent unknownSourcesSettings(Context context) {
        return new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:" + context.getPackageName()))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
    }

    /**
     * Downloads and verifies on a background thread; {@code callback} receives a stable outcome
     * code: "verified" (file ready, caller starts the installer), "busy", "invalid_manifest",
     * "not_newer", "download_failed", "size_mismatch", "hash_mismatch", "package_mismatch".
     */
    static void download(Context context, String url, String sha256, long sizeBytes,
            long manifestVersionCode, long minSupportedVersionCode, Callback callback) {
        Context app = context.getApplicationContext();
        if (!RUNNING.compareAndSet(false, true)) { callback.done("busy"); return; }
        DOWNLOADS.execute(() -> {
            String outcome;
            try {
                outcome = downloadAndVerify(app, url, sha256, sizeBytes, manifestVersionCode,
                        minSupportedVersionCode);
            } catch (Exception | LinkageError failure) {
                outcome = "download_failed";
            } finally {
                RUNNING.set(false);
            }
            if (!"verified".equals(outcome)) discardStale(app);
            callback.done(outcome);
        });
    }

    private static String downloadAndVerify(Context app, String url, String sha256, long sizeBytes,
            long manifestVersionCode, long minSupportedVersionCode) throws Exception {
        if (!UpdatePolicy.isAcceptableUrl(url) || sha256 == null || !sha256.matches("[0-9a-f]{64}")
                || sizeBytes < 1 || sizeBytes > 512L * 1024 * 1024
                || manifestVersionCode < 1 || manifestVersionCode > Integer.MAX_VALUE
                || minSupportedVersionCode < 1 || minSupportedVersionCode > manifestVersionCode)
            return "invalid_manifest";
        long installed = installedVersionCode(app);
        if (installed > Integer.MAX_VALUE) return "not_newer";
        // Downgrade and same-version manifests never download anything.
        if (UpdatePolicy.decide((int) installed, (int) manifestVersionCode,
                (int) minSupportedVersionCode) == UpdatePolicy.Decision.NONE) return "not_newer";
        discardStale(app);
        File file = apkFile(app);
        File dir = file.getParentFile();
        if (dir == null || (!dir.isDirectory() && !dir.mkdirs())) return "download_failed";
        HttpsURLConnection connection = (HttpsURLConnection) new URL(url).openConnection();
        try {
            connection.setInstanceFollowRedirects(false); // No redirect to another host or scheme.
            connection.setConnectTimeout(10_000);
            connection.setReadTimeout(20_000);
            connection.setUseCaches(false);
            connection.setRequestProperty("Accept-Encoding", "identity");
            if (connection.getResponseCode() != 200) return "download_failed";
            long declared = connection.getContentLengthLong();
            if (declared >= 0 && declared != sizeBytes) return "size_mismatch";
            long total = 0;
            try (InputStream in = connection.getInputStream();
                    OutputStream out = new FileOutputStream(file)) {
                byte[] buffer = new byte[64 * 1024];
                int read;
                while ((read = in.read(buffer)) != -1) {
                    total += read;
                    if (total > sizeBytes) return "size_mismatch";
                    out.write(buffer, 0, read);
                }
            }
            if (total != sizeBytes) return "size_mismatch";
        } finally {
            connection.disconnect();
        }
        if (!UpdatePolicy.sha256Matches(file, sha256)) return "hash_mismatch";
        // Second downgrade guard on the verified file itself: same package, exact manifest version.
        PackageInfo archive = app.getPackageManager().getPackageArchiveInfo(file.getPath(), 0);
        if (archive == null || !app.getPackageName().equals(archive.packageName)
                || versionCode(archive) != manifestVersionCode || versionCode(archive) <= installed)
            return "package_mismatch";
        return "verified";
    }

    /** Opens the system installer for the verified file. The file stays until the next start. */
    static Intent installIntent(Context context) {
        Uri uri = new Uri.Builder().scheme("content")
                .authority(context.getPackageName() + UpdateApkProvider.AUTHORITY_SUFFIX)
                .path("/" + FILE_NAME).build();
        return new Intent(Intent.ACTION_VIEW).setDataAndType(uri, MIME)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
    }
}
