package org.example.vpnpoc;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.function.BooleanSupplier;
import java.util.function.LongConsumer;

/**
 * ADR-193: pure file and stream rules of the update download, kept free of Android types so they
 * run under plain JUnit. The bytes are written to a partial file that {@link UpdateApkProvider}
 * never serves; only a file that passed every check is moved to the served name.
 */
final class UpdateDownloadFile {
    /** Partial download. Same directory as the served file so the final move is a rename. */
    static final String PARTIAL_NAME = "update-partial.apk";

    static final String OK = "ok";
    static final String SIZE_MISMATCH = "size_mismatch";
    static final String CANCELLED = "cancelled";

    private UpdateDownloadFile() {}

    /**
     * Copies {@code in} to {@code out}, never more than {@code expected} bytes. Answers
     * {@link #OK} only when exactly {@code expected} bytes arrived. {@code cancelled} is checked
     * before every write, and {@code progress} receives the running byte count after it.
     */
    static String copyBounded(InputStream in, OutputStream out, long expected,
            BooleanSupplier cancelled, LongConsumer progress) throws IOException {
        if (expected < 1) return SIZE_MISMATCH;
        byte[] buffer = new byte[64 * 1024];
        long total = 0;
        int read;
        while ((read = in.read(buffer)) != -1) {
            if (cancelled.getAsBoolean()) return CANCELLED;
            total += read;
            if (total > expected) return SIZE_MISMATCH;
            out.write(buffer, 0, read);
            progress.accept(total);
        }
        if (cancelled.getAsBoolean()) return CANCELLED;
        return total == expected ? OK : SIZE_MISMATCH;
    }

    /**
     * Moves a verified partial file to the served name. On any failure both files are removed,
     * so nothing half-moved can ever be served.
     */
    static boolean promote(File partial, File target) {
        if (partial == null || target == null || !partial.isFile()) return false;
        deleteQuietly(target);
        if (target.exists() || !partial.renameTo(target)) {
            deleteQuietly(partial);
            deleteQuietly(target);
            return false;
        }
        return true;
    }

    static void deleteQuietly(File file) {
        if (file == null) return;
        try {
            if (file.exists() && !file.delete()) file.deleteOnExit();
        } catch (SecurityException ignored) {
            // App-private cache: not expected. The next attempt deletes it first anyway.
        }
    }
}
