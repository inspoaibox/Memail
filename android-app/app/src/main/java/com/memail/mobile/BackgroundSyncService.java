package com.memail.mobile;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class BackgroundSyncService extends JobService {
    private static final int JOB_ID = 22051;
    private static final long PERIODIC_MS = 15 * 60 * 1000L;
    private static final long FLEX_MS = 5 * 60 * 1000L;
    private static final String PREFS = "memail_mobile";
    private static final String CHANNEL_MAIL = "memail_mail";

    private final ExecutorService worker = Executors.newSingleThreadExecutor();

    static void schedule(Context context) {
        if (context == null) return;
        JobScheduler scheduler = (JobScheduler) context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (scheduler == null) return;
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
        if (scheduler != null) scheduler.cancel(JOB_ID);
    }

    @Override
    public boolean onStartJob(JobParameters params) {
        worker.submit(() -> {
            boolean retry = false;
            try {
                runBackgroundSync();
            } catch (Exception ignored) {
                retry = true;
            }
            boolean finalRetry = retry;
            runOnMain(() -> jobFinished(params, finalRetry));
        });
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return true;
    }

    @Override
    public void onDestroy() {
        worker.shutdownNow();
        super.onDestroy();
    }

    private void runBackgroundSync() throws Exception {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        String token = prefs.getString("token", "");
        String server = prefs.getString("server", "");
        if (token == null || token.isEmpty() || server == null || server.isEmpty()) return;

        ApiClient api = new ApiClient();
        api.configure(server, token);
        LocalStore store = new LocalStore(this);
        try {
            List<Models.Account> accounts = fetchAccounts(api);
            if (!accounts.isEmpty()) {
                int oldUnread = prefs.getInt("last_unread_total", 0);
                store.replaceAccounts(accounts);
                cacheInboxPages(api, store, accounts);
                prefs.edit()
                    .putLong("last_bootstrap_refresh_at", System.currentTimeMillis())
                    .putInt("last_unread_total", totalUnread(accounts))
                    .apply();
                notifyIfNeeded(prefs, accounts, oldUnread);
            }
        } finally {
            store.close();
        }
    }

    private List<Models.Account> fetchAccounts(ApiClient api) throws Exception {
        List<Models.Account> result = new ArrayList<>();
        JSONObject local = api.get("/api/mailboxes");
        JSONArray localBoxes = Json.array(local, "mailboxes");
        for (int i = 0; i < localBoxes.length(); i++) {
            JSONObject item = localBoxes.optJSONObject(i);
            if (item == null) continue;
            Models.Account account = new Models.Account();
            account.type = "local";
            account.id = Json.str(item, "address");
            account.email = account.id;
            account.name = Json.anyStr(item, "display_name", "displayName");
            account.group = Json.str(item, "group");
            result.add(account);
        }

        JSONObject external = api.get("/imap/api/accounts");
        JSONArray externalArray = external.optJSONArray("accounts");
        if (externalArray == null) externalArray = external.optJSONArray("items");
        if (externalArray == null) externalArray = external.optJSONArray("data");
        if (externalArray == null) externalArray = new JSONArray();
        for (int i = 0; i < externalArray.length(); i++) {
            JSONObject item = externalArray.optJSONObject(i);
            if (item == null) continue;
            Models.Account account = new Models.Account();
            account.type = "external";
            account.id = String.valueOf(item.opt("id"));
            account.email = Json.str(item, "email");
            account.name = Json.anyStr(item, "displayName", "display_name", "name");
            account.sendName = Json.anyStr(item, "sendName", "send_name");
            account.group = Json.str(item, "group");
            account.unread = Json.obj(item, "syncStatus").optInt("unseen", 0);
            result.add(account);
        }
        return result;
    }

    private void cacheInboxPages(ApiClient api, LocalStore store, List<Models.Account> accounts) {
        for (Models.Account account : accounts) {
            try {
                List<Models.Mail> page = new ArrayList<>();
                if ("external".equals(account.type)) {
                    JSONObject data = api.get("/imap/api/accounts/" + encode(account.id) + "/mails?folder=INBOX&count=30&page=1&cacheOnly=1");
                    JSONArray arr = Json.array(data, "mails");
                    for (int i = 0; i < arr.length(); i++) {
                        page.add(Models.Mail.fromExternal(arr.optJSONObject(i), account.id, "INBOX"));
                    }
                } else if ("local".equals(account.type)) {
                    JSONObject data = api.post("/api/inbox/query", new JSONObject()
                        .put("email", account.email)
                        .put("offset", 0)
                        .put("limit", 30)
                        .put("unread_only", false));
                    JSONArray arr = Json.array(data, "messages");
                    for (int i = 0; i < arr.length(); i++) {
                        page.add(Models.Mail.fromLocal(arr.optJSONObject(i), account.id, "inbox"));
                    }
                }
                store.upsertMails(page);
            } catch (Exception ignored) {
                // One mailbox failing must not block the rest of the background cache.
            }
        }
    }

    private void notifyIfNeeded(SharedPreferences prefs, List<Models.Account> accounts, int oldUnread) {
        if (!prefs.getBoolean("notify", true)) return;
        int total = 0;
        Set<String> enabledGroups = notificationGroups(prefs);
        for (Models.Account account : accounts) {
            String group = account.group == null || account.group.isEmpty() ? "未分组" : account.group;
            if (enabledGroups.isEmpty() || enabledGroups.contains(group)) total += Math.max(0, account.unread);
        }
        if (total > oldUnread && oldUnread > 0) notifyMail("Memail 新邮件", "未读邮件增加到 " + total + " 封");
    }

    private Set<String> notificationGroups(SharedPreferences prefs) {
        String saved = prefs.getString("notify_groups", "");
        Set<String> groups = new HashSet<>();
        if (saved == null || saved.isEmpty()) return groups;
        String[] parts = saved.split("\\|", -1);
        for (String part : parts) if (!part.isEmpty()) groups.add(part);
        return groups;
    }

    private int totalUnread(List<Models.Account> accounts) {
        int total = 0;
        for (Models.Account account : accounts) total += Math.max(0, account.unread);
        return total;
    }

    private void notifyMail(String title, String text) {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        createNotificationChannel();
        Intent intent = new Intent(this, MainActivity.class);
        intent.setAction("com.memail.mobile.OPEN_MAIL");
        intent.putExtra("open", "mail");
        intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pendingIntent = PendingIntent.getActivity(this, 1002, intent, flags);
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
            ? new Notification.Builder(this, CHANNEL_MAIL)
            : new Notification.Builder(this);
        Notification notification = builder
            .setSmallIcon(android.R.drawable.ic_dialog_email)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .build();
        ((NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE)).notify(1002, notification);
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationChannel channel = new NotificationChannel(CHANNEL_MAIL, "Memail 新邮件", NotificationManager.IMPORTANCE_DEFAULT);
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }

    private void runOnMain(Runnable runnable) {
        new android.os.Handler(getMainLooper()).post(runnable);
    }

    private static String encode(String value) {
        return URLEncoder.encode(value == null ? "" : value, StandardCharsets.UTF_8).replace("+", "%20");
    }
}
