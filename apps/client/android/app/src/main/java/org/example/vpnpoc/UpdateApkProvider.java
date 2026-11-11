package org.example.vpnpoc;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

/**
 * Minimal FileProvider replacement for the in-app updater (ADR-107). The app has no androidx
 * dependency, so instead of androidx.core.content.FileProvider this non-exported provider serves
 * exactly one read-only file, the verified update APK, to apps granted a one-off read permission
 * (the system package installer). No other path, no write, no listing.
 */
public final class UpdateApkProvider extends ContentProvider {
    static final String AUTHORITY_SUFFIX = ".updates";
    private static final String PATH = "/" + AppUpdateInstaller.FILE_NAME;

    @Override public boolean onCreate() { return true; }

    private File fileFor(Uri uri) throws FileNotFoundException {
        if (getContext() == null || uri == null || !PATH.equals(uri.getPath())
                || !(getContext().getPackageName() + AUTHORITY_SUFFIX).equals(uri.getAuthority()))
            throw new FileNotFoundException();
        File file = AppUpdateInstaller.apkFile(getContext());
        if (!file.isFile()) throw new FileNotFoundException();
        return file;
    }

    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) throw new FileNotFoundException();
        return ParcelFileDescriptor.open(fileFor(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override public String getType(Uri uri) {
        return PATH.equals(uri.getPath()) ? AppUpdateInstaller.MIME : null;
    }

    @Override public Cursor query(Uri uri, String[] projection, String selection,
            String[] selectionArgs, String sortOrder) {
        File file;
        try { file = fileFor(uri); } catch (FileNotFoundException missing) { return null; }
        MatrixCursor cursor = new MatrixCursor(
                new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}, 1);
        cursor.addRow(new Object[] {"DetourVPN.apk", file.length()});
        return cursor;
    }

    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException();
    }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException();
    }
}
