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
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
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

    // ADR-188: progress and cancel for the one running download. In-memory only: a new process
    // starts idle, and its activity drops any file left behind (discardStaleAtStart).
    private static final AtomicBoolean CANCEL = new AtomicBoolean(false);
    private static final AtomicLong RECEIVED = new AtomicLong(0);
    private static volatile long expectedTotal = 0;
    /** "idle", "downloading", "verifying" or "ready" (a verified file is in the cache). */
    private static volatile String phase = "idle";
    /** Outcome code of the last finished run, "" before the first one. */
    private static volatile String lastOutcome = "";
    private static volatile HttpsURLConnection current;
    /** SHA-256 and version of the file that passed every check; null when there is none. */
    private static volatile String readySha256;
    private static volatile long readyVersionCode;

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

    /** ADR-193: where the bytes arrive. Never served by {@link UpdateApkProvider}. */
    static File partialFile(Context context) {
        return new File(new File(context.getCacheDir(), DIRECTORY), UpdateDownloadFile.PARTIAL_NAME);
    }

    /**
     * Removes the files left by an earlier attempt (the served file and a partial download).
     * Called before each download and after a failure or cancel.
     */
    static void discardStale(Context context) {
        UpdateDownloadFile.deleteQuietly(partialFile(context));
        UpdateDownloadFile.deleteQuietly(apkFile(context));
    }

    /**
     * ADR-188: a fresh activity drops a stale file, but not one this process is still writing or
     * has just verified (the activity can be recreated while the process and download live on).
     */
    static void discardStaleAtStart(Context context) {
        if (RUNNING.get() || readySha256 != null) return;
        discardStale(context);
    }

    /** ADR-188: a snapshot for the UI. Numbers only; never the URL, hash or file path. */
    static Map<String, Object> progress() {
        Map<String, Object> value = new HashMap<>();
        value.put("phase", phase);
        value.put("received", RECEIVED.get());
        value.put("total", expectedTotal);
        value.put("outcome", lastOutcome);
        return value;
    }

    /**
     * ADR-188: asks the running download to stop. The read loop checks the flag; the connection
     * is also closed off the calling thread so a stalled read ends at once. The download's own
     * callback then answers "cancelled" and the partial file is deleted.
     */
    static void cancel() {
        if (!RUNNING.get()) return;
        CANCEL.set(true);
        HttpsURLConnection connection = current;
        if (connection != null) {
            Thread closer = new Thread(() -> {
                try { connection.disconnect(); } catch (RuntimeException ignored) { /* Closing. */ }
            }, "update-cancel");
            closer.setDaemon(true);
            closer.start();
        }
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
     * "not_newer", "download_failed", "size_mismatch", "hash_mismatch", "package_mismatch",
     * "cancelled" (ADR-188). A file this process already verified for the same SHA-256 and
     * version is re-checked and reused instead of downloaded again.
     */
    static void download(Context context, String url, String sha256, long sizeBytes,
            long manifestVersionCode, long minSupportedVersionCode, Callback callback) {
        Context app = context.getApplicationContext();
        if (!RUNNING.compareAndSet(false, true)) { callback.done("busy"); return; }
        CANCEL.set(false);
        RECEIVED.set(0);
        expectedTotal = sizeBytes > 0 && sizeBytes <= 512L * 1024 * 1024 ? sizeBytes : 0;
        phase = "downloading";
        DOWNLOADS.execute(() -> {
            String outcome;
            try {
                outcome = downloadAndVerify(app, url, sha256, sizeBytes, manifestVersionCode,
                        minSupportedVersionCode);
            } catch (Exception | LinkageError failure) {
                outcome = "download_failed";
            }
            // A cancel wins over any other result, including a file that just verified.
            if (CANCEL.get()) outcome = "cancelled";
            boolean verified = "verified".equals(outcome);
            try {
                if (verified) {
                    readyVersionCode = manifestVersionCode;
                    readySha256 = sha256;
                } else {
                    readySha256 = null;
                    discardStale(app);
                }
            } catch (RuntimeException deleteFailed) {
                // The next download deletes it first anyway.
            } finally {
                current = null;
                lastOutcome = outcome;
                phase = verified ? "ready" : "idle";
                // Released last, so a new download cannot start while this one deletes its file.
                RUNNING.set(false);
            }
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
        File file = apkFile(app);
        // ADR-188: the same verified build again (the user closed the system installer): every
        // check below runs again on the cached file, so nothing unverified is ever handed over.
        if (sha256.equals(readySha256) && readyVersionCode == manifestVersionCode && file.isFile()) {
            RECEIVED.set(sizeBytes);
            phase = "verifying";
            if (file.length() == sizeBytes
                    && "verified".equals(verifyFile(app, file, sha256, manifestVersionCode, installed)))
                return "verified";
        }
        readySha256 = null;
        discardStale(app);
        // ADR-193: bytes go to a partial file the provider never serves; only a file that passed
        // every check below is renamed to the served name.
        File partial = partialFile(app);
        File dir = file.getParentFile();
        if (dir == null || (!dir.isDirectory() && !dir.mkdirs())) return "download_failed";
        if (CANCEL.get()) return "cancelled";
        HttpsURLConnection connection = (HttpsURLConnection) new URL(url).openConnection();
        current = connection;
        try {
            connection.setInstanceFollowRedirects(false); // No redirect to another host or scheme.
            connection.setConnectTimeout(10_000);
            connection.setReadTimeout(20_000);
            connection.setUseCaches(false);
            connection.setRequestProperty("Accept-Encoding", "identity");
            if (connection.getResponseCode() != 200) return "download_failed";
            long declared = connection.getContentLengthLong();
            if (declared >= 0 && declared != sizeBytes) return "size_mismatch";
            String copied;
            try (InputStream in = connection.getInputStream();
                    OutputStream out = new FileOutputStream(partial)) {
                copied = UpdateDownloadFile.copyBounded(in, out, sizeBytes, CANCEL::get, RECEIVED::set);
            }
            if (!UpdateDownloadFile.OK.equals(copied)) return copied;
        } finally {
            current = null;
            connection.disconnect();
        }
        if (CANCEL.get()) return "cancelled";
        phase = "verifying";
        String verdict = verifyFile(app, partial, sha256, manifestVersionCode, installed);
        if (!"verified".equals(verdict)) return verdict;
        if (CANCEL.get()) return "cancelled";
        return UpdateDownloadFile.promote(partial, file) ? "verified" : "download_failed";
    }

    private static String verifyFile(Context app, File file, String sha256, long manifestVersionCode,
            long installed) throws Exception {
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
