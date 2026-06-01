package com.memail.mobile;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

public class BackgroundSyncService extends JobService {
    private static final int JOB_ID = 22051;
    private static final int LEGACY_IMMEDIATE_JOB_ID = 22052;
    private static final long PERIODIC_MS = 15 * 60 * 1000L;
    private static final long FLEX_MS = 5 * 60 * 1000L;

    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Object jobLock = new Object();
    private Future<?> currentTask;
    private int jobGeneration = 0;
    private volatile boolean stopRequested = false;

    static void schedule(Context context) {
        if (context == null) return;
        JobScheduler scheduler = (JobScheduler) context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (scheduler == null) return;
        scheduler.cancel(LEGACY_IMMEDIATE_JOB_ID);
        JobInfo info = new JobInfo.Builder(JOB_ID, new ComponentName(context, BackgroundSyncService.class))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPeriodic(PERIODIC_MS, FLEX_MS)
            .setPersisted(true)
            .build();
        try {
            scheduler.schedule(info);
        } catch (Exception ignored) {
            // Scheduling is a cache optimization; app startup must never depend on it.
        }
    }

    static void cancel(Context context) {
        if (context == null) return;
        JobScheduler scheduler = (JobScheduler) context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (scheduler != null) {
            scheduler.cancel(JOB_ID);
            scheduler.cancel(LEGACY_IMMEDIATE_JOB_ID);
        }
    }

    @Override
    public boolean onStartJob(JobParameters params) {
        final int generation;
        synchronized (jobLock) {
            stopRequested = false;
            jobGeneration++;
            generation = jobGeneration;
        }
        Future<?> task = worker.submit(() -> {
            boolean retry = false;
            try {
                runBackgroundSync(generation);
            } catch (Exception ignored) {
                retry = true;
            }
            boolean finalRetry = retry;
            boolean shouldFinish;
            synchronized (jobLock) {
                shouldFinish = generation == jobGeneration && !stopRequested;
                if (generation == jobGeneration) currentTask = null;
            }
            if (shouldFinish) runOnMain(() -> jobFinished(params, finalRetry));
        });
        synchronized (jobLock) {
            currentTask = task;
        }
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        Future<?> task;
        synchronized (jobLock) {
            stopRequested = true;
            jobGeneration++;
            task = currentTask;
            currentTask = null;
        }
        if (task != null) task.cancel(true);
        return true;
    }

    @Override
    public void onDestroy() {
        worker.shutdownNow();
        super.onDestroy();
    }

    private void runBackgroundSync(int generation) throws Exception {
        MobileSyncEngine.sync(this, () -> shouldStop(generation));
    }

    private boolean shouldStop(int generation) {
        return stopRequested || generation != jobGeneration || Thread.currentThread().isInterrupted();
    }

    private void runOnMain(Runnable runnable) {
        new android.os.Handler(getMainLooper()).post(runnable);
    }
}
