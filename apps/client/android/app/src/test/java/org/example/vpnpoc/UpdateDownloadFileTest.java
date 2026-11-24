package org.example.vpnpoc;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.Test;
import static org.junit.Assert.*;

/** ADR-193: bounded copy, cancel and partial-file promotion of the update download. */
public class UpdateDownloadFileTest {
    private static byte[] bytes(int n) {
        byte[] value = new byte[n];
        for (int i = 0; i < n; i++) value[i] = (byte) i;
        return value;
    }

    @Test public void exactSizeIsOkAndProgressCountsRealBytes() throws IOException {
        byte[] body = bytes(200_000);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        List<Long> seen = new ArrayList<>();
        String result = UpdateDownloadFile.copyBounded(new ByteArrayInputStream(body), out,
                body.length, () -> false, seen::add);
        assertEquals(UpdateDownloadFile.OK, result);
        assertArrayEquals(body, out.toByteArray());
        assertFalse(seen.isEmpty());
        assertEquals(Long.valueOf(body.length), seen.get(seen.size() - 1));
        for (int i = 1; i < seen.size(); i++) assertTrue(seen.get(i) > seen.get(i - 1));
    }

    @Test public void moreThanExpectedStopsBeforeWritingTheExtra() throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        String result = UpdateDownloadFile.copyBounded(new ByteArrayInputStream(bytes(1000)), out,
                999, () -> false, n -> { });
        assertEquals(UpdateDownloadFile.SIZE_MISMATCH, result);
        assertEquals(0, out.size());
    }

    @Test public void shortBodyIsASizeMismatch() throws IOException {
        assertEquals(UpdateDownloadFile.SIZE_MISMATCH, UpdateDownloadFile.copyBounded(
                new ByteArrayInputStream(bytes(10)), new ByteArrayOutputStream(), 11,
                () -> false, n -> { }));
        assertEquals(UpdateDownloadFile.SIZE_MISMATCH, UpdateDownloadFile.copyBounded(
                new ByteArrayInputStream(bytes(0)), new ByteArrayOutputStream(), 0,
                () -> false, n -> { }));
    }

    @Test public void cancelStopsMidStream() throws IOException {
        AtomicBoolean cancel = new AtomicBoolean(false);
        // One 64 KiB chunk per read, so the cancel lands between chunks.
        InputStream slow = new ByteArrayInputStream(bytes(300_000)) {
            @Override public synchronized int read(byte[] b, int off, int len) {
                return super.read(b, off, Math.min(len, 64 * 1024));
            }
        };
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        String result = UpdateDownloadFile.copyBounded(slow, out, 300_000, cancel::get,
                n -> { if (n >= 64 * 1024) cancel.set(true); });
        assertEquals(UpdateDownloadFile.CANCELLED, result);
        assertEquals(64 * 1024, out.size());
    }

    @Test public void cancelAfterTheLastByteStillCancels() throws IOException {
        AtomicBoolean cancel = new AtomicBoolean(false);
        String result = UpdateDownloadFile.copyBounded(new ByteArrayInputStream(bytes(10)),
                new ByteArrayOutputStream(), 10, cancel::get, n -> cancel.set(true));
        assertEquals(UpdateDownloadFile.CANCELLED, result);
    }

    @Test public void promoteReplacesTheServedFileAndLeavesNoPartial() throws IOException {
        File dir = Files.createTempDirectory("upd").toFile();
        File partial = new File(dir, UpdateDownloadFile.PARTIAL_NAME);
        File target = new File(dir, "update.apk");
        try (FileOutputStream out = new FileOutputStream(partial)) { out.write(bytes(5)); }
        try (FileOutputStream out = new FileOutputStream(target)) { out.write(bytes(9)); }
        assertTrue(UpdateDownloadFile.promote(partial, target));
        assertFalse(partial.exists());
        assertEquals(5, target.length());
        UpdateDownloadFile.deleteQuietly(target);
        assertTrue(dir.delete());
    }

    @Test public void promoteWithoutAPartialServesNothing() throws IOException {
        File dir = Files.createTempDirectory("upd").toFile();
        File target = new File(dir, "update.apk");
        assertFalse(UpdateDownloadFile.promote(new File(dir, UpdateDownloadFile.PARTIAL_NAME), target));
        assertFalse(target.exists());
        assertFalse(UpdateDownloadFile.promote(null, target));
        assertTrue(dir.delete());
    }

    @Test public void partialNameIsNotTheServedName() {
        assertNotEquals(AppUpdateInstaller.FILE_NAME, UpdateDownloadFile.PARTIAL_NAME);
    }
}
