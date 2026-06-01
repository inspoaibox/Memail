package com.memail.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

public class RealtimeSyncService extends Service {
    private static final String CHANNEL_REALTIME = "memail_realtime";
    private static final int NOTIFICATION_ID = 22053;
    private static final long EVENT_RECONNECT_MS = 4_000L;
    private static final int EVENT_READ_TIMEOUT_MS = 65_000;
    private static final long SYNC_DEBOUNCE_MS = 1_200L;

    private final ExecutorService eventWorker = Executors.newSingleThreadExecutor();
    private final ExecutorService syncWorker = Executors.newSingleThreadExecutor();
    private final Object lock = new Object();
    private volatile boolean running = false;
    private boolean syncRequested = false;
    private boolean syncRunning = false;
    private volatile HttpURLConnection eventConnection;
    private Future<?> loopTask;

    static void start(Context context) {
        if (context == null) return;
        Intent intent = new Intent(context, RealtimeSyncService.class);
        try {
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent);
            else context.startService(intent);
        } catch (Exception ignored) {
            // The periodic JobScheduler sync remains as a fallback if the OS blocks a foreground start.
        }
    }

    static void stop(Context context) {
        if (context == null) return;
        context.stopService(new Intent(context, RealtimeSyncService.class));
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createRealtimeChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        running = true;
        Notification notification = buildNotification("后台持续接收邮件更新");
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
        startEventLoop();
        requestSync();
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        HttpURLConnection conn = eventConnection;
        if (conn != null) conn.disconnect();
        Future<?> task = loopTask;
        if (task != null) task.cancel(true);
        eventWorker.shutdownNow();
        syncWorker.shutdownNow();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void startEventLoop() {
        synchronized (lock) {
            if (loopTask != null && !loopTask.isDone()) return;
            loopTask = eventWorker.submit(this::eventLoop);
        }
    }

    private void eventLoop() {
        while (running) {
            HttpURLConnection conn = null;
            try {
                SharedPreferences prefs = getSharedPreferences(MobileSyncEngine.PREFS, MODE_PRIVATE);
                String token = prefs.getString("token", "");
                String server = prefs.getString("server", "");
                if (token == null || token.isEmpty() || server == null || server.isEmpty()) {
                    stopSelf();
                    return;
                }

                int since = prefs.getInt("sync_seq", 0);
                URL url = new URL(ApiClient.normalizeBaseUrl(server) + "/api/mobile/events?since=" + since);
                conn = (HttpURLConnection) url.openConnection();
                eventConnection = conn;
                conn.setConnectTimeout(12000);
                conn.setReadTimeout(EVENT_READ_TIMEOUT_MS);
                conn.setRequestProperty("Accept", "text/event-stream");
                conn.setRequestProperty("Authorization", "Bearer " + token);
                int code = conn.getResponseCode();
                if (code >= 400) throw new Exception("events HTTP " + code);
                readEventStream(conn.getInputStream());
            } catch (Exception ignored) {
                if (!sleepQuietly(EVENT_RECONNECT_MS)) return;
            } finally {
                if (conn != null) conn.disconnect();
                if (eventConnection == conn) eventConnection = null;
            }
        }
    }

    private void readEventStream(InputStream input) throws Exception {
        StringBuilder data = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8))) {
            String line;
            while (running && (line = reader.readLine()) != null) {
                if (line.isEmpty()) {
                    if (data.length() > 0) {
                        String raw = data.toString();
                        data.setLength(0);
                        try {
                            handleServerEvent(new JSONObject(raw));
                        } catch (Exception ignored) {
                            // Ignore malformed SSE payloads and keep the connection alive.
                        }
                    }
                    continue;
                }
                if (line.startsWith("data:")) {
                    if (data.length() > 0) data.append('\n');
                    data.append(line.substring(5).trim());
                }
            }
        }
    }

    private void handleServerEvent(JSONObject event) {
        int seq = event.optInt("seq", 0);
        SharedPreferences prefs = getSharedPreferences(MobileSyncEngine.PREFS, MODE_PRIVATE);
        if (seq > prefs.getInt("sync_seq", 0)) prefs.edit().putInt("sync_seq", seq).apply();
        String type = Json.str(event, "type");
        if (type.startsWith("mail.")
            || type.startsWith("draft.")
            || type.startsWith("outbox.")
            || type.startsWith("message.")
            || type.startsWith("keyword_rule.")
            || type.startsWith("mailbox.")
            || type.startsWith("device.token.")) {
            requestSync();
        }
    }

    private void requestSync() {
        if (!running) return;
        synchronized (lock) {
            syncRequested = true;
            if (syncRunning) return;
            syncRunning = true;
        }
        syncWorker.submit(this::drainSyncRequests);
    }

    private void drainSyncRequests() {
        try {
            while (running) {
                if (!sleepQuietly(SYNC_DEBOUNCE_MS) || !running) return;
                synchronized (lock) {
                    if (!syncRequested) return;
                    syncRequested = false;
                }
                try {
                    updateNotification("正在缓存完整正文");
                    MobileSyncEngine.SyncResult result = MobileSyncEngine.sync(this, () -> !running || Thread.currentThread().isInterrupted());
                    if (result.bodyPrefetchBudgetExhausted && result.missingBodyCount > 0) {
                        updateNotification("继续缓存剩余正文 " + result.missingBodyCount + " 封");
                        synchronized (lock) {
                            syncRequested = true;
                        }
                    } else {
                        updateNotification("后台持续接收邮件更新");
                    }
                } catch (Exception ignored) {
                    updateNotification("等待下一次服务端更新");
                    if (!sleepQuietly(EVENT_RECONNECT_MS) || !running) return;
                    synchronized (lock) {
                        syncRequested = true;
                    }
                }
                synchronized (lock) {
                    if (!syncRequested) return;
                }
            }
        } finally {
            boolean restart;
            synchronized (lock) {
                syncRunning = false;
                restart = running && syncRequested;
                if (restart) syncRunning = true;
            }
            if (restart) syncWorker.submit(this::drainSyncRequests);
        }
    }

    private boolean sleepQuietly(long millis) {
        try {
            Thread.sleep(millis);
            return true;
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    private void updateNotification(String text) {
        NotificationManager manager = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) manager.notify(NOTIFICATION_ID, buildNotification(text));
    }

    private Notification buildNotification(String text) {
        Intent intent = new Intent(this, MainActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pendingIntent = PendingIntent.getActivity(this, NOTIFICATION_ID, intent, flags);
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
            ? new Notification.Builder(this, CHANNEL_REALTIME)
            : new Notification.Builder(this);
        return builder
            .setSmallIcon(android.R.drawable.ic_dialog_email)
            .setContentTitle("Memail 实时同步")
            .setContentText(text)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .setShowWhen(false)
            .build();
    }

    private void createRealtimeChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) return;
        NotificationChannel channel = new NotificationChannel(CHANNEL_REALTIME, "Memail 实时同步", NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("保持后台邮件实时同步");
        manager.createNotificationChannel(channel);
    }
}
