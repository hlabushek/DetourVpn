package org.example.vpnpoc;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;

/**
 * ADR-147 (3.4): a periodic 15-minute backstop for OEM task killers that drop alarms. It uses the
 * platform JobScheduler (the scheduler WorkManager itself uses on API 23+), so no new dependency
 * is added. A run only asks {@link ClientApplication#backstop} to send {@code
 * freshSignal("backstop")} when the VPN is wanted, no tunnel is up and nothing is in flight; the
 * supervisor's normal rules (owner, network, 30 s gap) still apply. Not exported; bound only by
 * the system ({@code BIND_JOB_SERVICE}).
 */
public final class BackstopJobService extends JobService {
    static final int JOB_ID = 0x0de7_0001;

    @Override public boolean onStartJob(JobParameters params) {
        ((ClientApplication) getApplication()).backstop(() -> jobFinished(params, false));
        return true; // Work continues on the application worker thread.
    }

    @Override public boolean onStopJob(JobParameters params) {
        return false; // Periodic: the next period runs anyway.
    }

    /** Schedules the periodic job if it is not scheduled yet. Never throws. */
    static void ensureScheduled(Context context) {
        try {
            JobScheduler scheduler = context.getSystemService(JobScheduler.class);
            if (scheduler == null || scheduler.getPendingJob(JOB_ID) != null) return;
            scheduler.schedule(new JobInfo.Builder(JOB_ID,
                    new ComponentName(context, BackstopJobService.class))
                    .setPeriodic(DozeSurvival.BACKSTOP_PERIOD_MS)
                    .setPersisted(true) // survives a reboot (RECEIVE_BOOT_COMPLETED is declared)
                    .build());
        } catch (RuntimeException unavailable) {
            // Alarms, callbacks and the app itself still drive recovery.
        }
    }

    /** Cancels the periodic job (the VPN is no longer wanted). Never throws. */
    static void cancel(Context context) {
        try {
            JobScheduler scheduler = context.getSystemService(JobScheduler.class);
            if (scheduler != null) scheduler.cancel(JOB_ID);
        } catch (RuntimeException ignored) { /* Nothing scheduled. */ }
    }
}
