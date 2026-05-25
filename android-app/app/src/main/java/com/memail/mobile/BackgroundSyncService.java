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
import android.net.Uri;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class BackgroundSyncService extends JobService {
    private static final int JOB_ID = 22051;
    private static final int JOB_ID_NOW = 22052;
    private static final long PERIODIC_MS = 15 * 60 * 1000L;
    private static final long FLEX_MS = 5 * 60 * 1000L;
    private static final String PREFS = "memail_mobile";
    private static final String CHANNEL_MAIL = "memail_mail";
    private static final int CACHE_PAGE_SIZE = 100;
    private static final int MAX_EMPTY_PAGES = 2;

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
            scheduler.schedule(new JobInfo.Builder(JOB_ID_NOW, new ComponentName(context, BackgroundSyncService.class))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setOverrideDeadline(0)
                .build());
        } catch (Exception ignored) {
            // Scheduling is a cache optimization; app startup must never depend on it.
        }
    }

    static void cancel(Context context) {
        if (context == null) return;
        JobScheduler scheduler = (JobScheduler) context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (scheduler != null) {
            scheduler.cancel(JOB_ID);
            scheduler.cancel(JOB_ID_NOW);
        }
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
                cacheAllMailboxPages(api, store, accounts);
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

    private void cacheAllMailboxPages(ApiClient api, LocalStore store, List<Models.Account> accounts) {
        for (Models.Account account : accounts) {
            try {
                if ("external".equals(account.type)) {
                    cacheExternalAccount(api, store, account);
                } else if ("local".equals(account.type)) {
                    cacheLocalAccount(api, store, account);
                }
            } catch (Exception ignored) {
                // One mailbox failing must not block the rest of the background cache.
            }
        }
    }

    private void cacheExternalAccount(ApiClient api, LocalStore store, Models.Account account) throws Exception {
        JSONObject folderData = api.get("/imap/api/accounts/" + encode(account.id) + "/folders");
        JSONArray folders = Json.arrayAny(folderData, "items", "folders", "data");
        List<String> folderPaths = new ArrayList<>();
        if (folders.length() == 0) folderPaths.add("INBOX");
        for (int i = 0; i < folders.length(); i++) {
            JSONObject item = folders.optJSONObject(i);
            if (item == null || item.optBoolean("noselect", false)) continue;
            String path = Json.anyStr(item, "path", "name");
            if (!path.isEmpty()) folderPaths.add(path);
        }
        for (String folder : folderPaths) {
            int page = 1;
            int emptyPages = 0;
            boolean hasMore = true;
            while (hasMore && emptyPages < MAX_EMPTY_PAGES) {
                JSONObject data = api.get("/imap/api/accounts/" + encode(account.id)
                    + "/mails?folder=" + encode(folder)
                    + "&count=" + CACHE_PAGE_SIZE
                    + "&page=" + page
                    + "&cacheOnly=1");
                JSONArray arr = Json.array(data, "mails");
                List<Models.Mail> mails = new ArrayList<>();
                for (int i = 0; i < arr.length(); i++) {
                    mails.add(Models.Mail.fromExternal(arr.optJSONObject(i), account.id, folder));
                }
                store.upsertMails(mails);
                cacheMailDetails(api, store, mails);
                emptyPages = mails.isEmpty() ? emptyPages + 1 : 0;
                hasMore = data.optBoolean("hasMore", false) || page * CACHE_PAGE_SIZE < data.optInt("total", 0);
                page++;
            }
        }
    }

    private void cacheLocalAccount(ApiClient api, LocalStore store, Models.Account account) throws Exception {
        cacheLocalFolder(api, store, account, "inbox", false);
        cacheLocalFolder(api, store, account, "sent", false);
    }

    private void cacheLocalFolder(ApiClient api, LocalStore store, Models.Account account, String folder, boolean unreadOnly) throws Exception {
        int offset = 0;
        int emptyPages = 0;
        boolean hasMore = true;
        while (hasMore && emptyPages < MAX_EMPTY_PAGES) {
            JSONObject body = new JSONObject()
                .put("email", account.email)
                .put("offset", offset)
                .put("limit", CACHE_PAGE_SIZE)
                .put("unread_only", unreadOnly);
            JSONObject data = "sent".equals(folder)
                ? api.post("/api/sent/query", body)
                : api.post("/api/inbox/query", body);
            JSONArray arr = Json.array(data, "messages");
            List<Models.Mail> mails = new ArrayList<>();
            for (int i = 0; i < arr.length(); i++) {
                mails.add(Models.Mail.fromLocal(arr.optJSONObject(i), account.id, folder));
            }
            store.upsertMails(mails);
            cacheMailDetails(api, store, mails);
            emptyPages = mails.isEmpty() ? emptyPages + 1 : 0;
            int total = data.optInt("total", offset + mails.size());
            offset += CACHE_PAGE_SIZE;
            hasMore = offset < total;
        }
    }

    private void cacheMailDetails(ApiClient api, LocalStore store, List<Models.Mail> page) {
        if (page == null || page.isEmpty()) return;
        for (Models.Mail mail : page) {
            if (mail == null || mail.id == null || mail.id.isEmpty()) continue;
            try {
                JSONObject data;
                if ("external".equals(mail.accountType)) {
                    data = api.get("/imap/api/accounts/" + encode(mail.accountId) + "/mails/" + encode(mail.id) + "?folder=" + encode(mail.folder) + "&markSeen=0");
                } else if ("sent".equals(mail.folder)) {
                    data = api.post("/api/sent/detail", new JSONObject()
                        .put("email", mail.accountId)
                        .put("message_id", mail.id));
                } else {
                    data = api.post("/api/inbox/detail", new JSONObject()
                        .put("email", mail.accountId)
                        .put("message_id", mail.id));
                }
                Models.Mail detail = mergeDetail(mail, data);
                store.upsertMailDetail(detail);
            } catch (Exception ignored) {
                // Detail caching is best-effort; list cache remains usable.
            }
        }
    }

    private Models.Mail mergeDetail(Models.Mail base, JSONObject data) {
        JSONObject detail = data.optJSONObject("detail");
        JSONObject source = detail == null ? data : detail;
        Models.Mail mail = new Models.Mail();
        mail.accountType = base.accountType;
        mail.accountId = base.accountId;
        mail.folder = base.folder;
        mail.id = base.id;
        mail.sender = nonEmpty(base.sender, Json.anyStr(source, "from", "from_address"));
        mail.subject = nonEmpty(base.subject, Json.str(source, "subject"));
        mail.preview = nonEmpty(base.preview, Json.anyStr(source, "intro", "text"));
        mail.date = nonEmpty(base.date, Json.anyStr(source, "date", "createdAt", "created_at"));
        mail.kind = base.kind;
        mail.to = Json.anyStr(source, "to", "to_address");
        mail.text = Json.str(source, "text");
        mail.html = Json.str(source, "html");
        mail.error = nonEmpty(base.error, Json.str(source, "error"));
        mail.seen = base.seen;
        mail.favorite = base.favorite || source.optBoolean("flagged", false) || Json.obj(source, "meta").optBoolean("favorite", false);
        return mail;
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
        return Uri.encode(value == null ? "" : value);
    }

    private static String nonEmpty(String value, String fallback) {
        return value == null || value.isEmpty() ? fallback : value;
    }
}
