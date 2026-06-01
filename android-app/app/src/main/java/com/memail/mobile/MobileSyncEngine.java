package com.memail.mobile;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
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

final class MobileSyncEngine {
    interface StopSignal {
        boolean shouldStop();
    }

    static final String PREFS = "memail_mobile";
    static final String CHANNEL_MAIL = "memail_mail";
    private static final int NOTIFICATION_ID = 1002;
    private static final int CACHE_PAGE_SIZE = 30;
    private static final int MAX_BACKGROUND_FOLDERS_PER_ACCOUNT = 2;
    private static final int MAX_INCREMENTAL_PAGES_PER_FOLDER = 3;
    private static final int MAX_BACKFILL_PAGES_PER_FOLDER = 1;
    private static final int MAX_DETAIL_PREFETCH_PER_SYNC = 500;

    private MobileSyncEngine() {}

    static SyncResult sync(Context context, StopSignal stopSignal) throws Exception {
        StopSignal stop = stopSignal == null ? () -> false : stopSignal;
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String token = prefs.getString("token", "");
        String server = prefs.getString("server", "");
        SyncResult result = new SyncResult();
        if (token == null || token.isEmpty() || server == null || server.isEmpty()) return result;

        ApiClient api = new ApiClient();
        api.configure(server, token);
        LocalStore store = new LocalStore(context);
        try {
            List<Models.Account> accounts = fetchAccounts(api);
            if (stop.shouldStop()) return result;
            result.accountCount = accounts.size();
            if (!accounts.isEmpty()) {
                int oldUnread = prefs.getInt("last_unread_total", 0);
                DetailBudget detailBudget = new DetailBudget(MAX_DETAIL_PREFETCH_PER_SYNC);
                cacheRecentMailboxPages(api, store, accounts, stop, detailBudget);
                cacheStoredMissingDetails(api, store, stop, detailBudget);
                result.bodyPrefetchBudgetExhausted = detailBudget.remaining <= 0;
                result.missingBodyCount = result.bodyPrefetchBudgetExhausted ? store.countMailsMissingBody() : 0;
                if (stop.shouldStop()) return result;
                store.replaceAccounts(accounts);
                result.unreadTotal = totalUnread(accounts);
                prefs.edit()
                    .putLong("last_bootstrap_refresh_at", System.currentTimeMillis())
                    .putInt("last_unread_total", result.unreadTotal)
                    .apply();
                result.notified = notifyIfNeeded(context, prefs, accounts, oldUnread);
                result.synced = true;
            }
            return result;
        } finally {
            store.close();
        }
    }

    private static List<Models.Account> fetchAccounts(ApiClient api) throws Exception {
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

    private static void cacheRecentMailboxPages(ApiClient api, LocalStore store, List<Models.Account> accounts, StopSignal stop, DetailBudget detailBudget) {
        for (Models.Account account : accounts) {
            if (stop.shouldStop()) return;
            try {
                if ("external".equals(account.type)) {
                    cacheExternalAccount(api, store, account, stop, detailBudget);
                } else if ("local".equals(account.type)) {
                    cacheLocalAccount(api, store, account, stop, detailBudget);
                }
            } catch (Exception ignored) {
                // One mailbox failing must not block the rest of the mobile cache refresh.
            }
        }
    }

    private static void cacheExternalAccount(ApiClient api, LocalStore store, Models.Account account, StopSignal stop, DetailBudget detailBudget) throws Exception {
        JSONObject folderData = api.get("/imap/api/accounts/" + encode(account.id) + "/folders");
        JSONArray folders = Json.arrayAny(folderData, "items", "folders", "data");
        List<String> folderPaths = backgroundFolderPaths(folders);
        if (folderPaths.isEmpty()) folderPaths.add("INBOX");
        int cachedFolders = 0;
        for (String folder : folderPaths) {
            if (stop.shouldStop() || cachedFolders >= MAX_BACKGROUND_FOLDERS_PER_ACCOUNT) return;
            cacheExternalFolderIncrementally(api, store, account, folder, stop, detailBudget);
            cachedFolders++;
        }
    }

    private static List<String> backgroundFolderPaths(JSONArray folders) {
        List<String> paths = new ArrayList<>();
        for (int i = 0; i < folders.length(); i++) {
            JSONObject item = folders.optJSONObject(i);
            if (item == null || item.optBoolean("noselect", false)) continue;
            String path = Json.anyStr(item, "path", "name");
            if (path.isEmpty() || paths.contains(path)) continue;
            if ("INBOX".equalsIgnoreCase(path)) paths.add(0, path);
            else paths.add(path);
        }
        return paths;
    }

    private static void cacheExternalFolderIncrementally(ApiClient api, LocalStore store, Models.Account account, String folder, StopSignal stop, DetailBudget detailBudget) throws Exception {
        int page = 1;
        int pagesRead = 0;
        boolean hitExisting = false;
        boolean hasMore = true;
        while (hasMore && pagesRead < MAX_INCREMENTAL_PAGES_PER_FOLDER && !hitExisting) {
            if (stop.shouldStop()) return;
            JSONObject data = api.get("/imap/api/accounts/" + encode(account.id)
                + "/mails?folder=" + encode(folder)
                + "&count=" + CACHE_PAGE_SIZE
                + "&page=" + page
                + "&cacheOnly=1");
            JSONArray arr = Json.array(data, "mails");
            if (arr.length() == 0) break;
            List<Models.Mail> pageMails = new ArrayList<>();
            for (int i = 0; i < arr.length(); i++) {
                Models.Mail mail = Models.Mail.fromExternal(arr.optJSONObject(i), account.id, folder);
                pageMails.add(mail);
                if (store.hasMail(mail)) {
                    hitExisting = true;
                    break;
                }
            }
            store.upsertMails(pageMails);
            cacheMissingMailDetails(api, store, pageMails, stop, detailBudget);
            pagesRead++;
            hasMore = data.optBoolean("hasMore", false) || page * CACHE_PAGE_SIZE < data.optInt("total", 0);
            page++;
        }
        if (!hitExisting) backfillExternalFolderPage(api, store, account, folder, stop, detailBudget);
    }

    private static void backfillExternalFolderPage(ApiClient api, LocalStore store, Models.Account account, String folder, StopSignal stop, DetailBudget detailBudget) throws Exception {
        int cached = store.countMails("external", account.id, folder);
        int page = Math.max(1, (cached / CACHE_PAGE_SIZE) + 1);
        for (int i = 0; i < MAX_BACKFILL_PAGES_PER_FOLDER; i++) {
            if (stop.shouldStop()) return;
            JSONObject data = api.get("/imap/api/accounts/" + encode(account.id)
                + "/mails?folder=" + encode(folder)
                + "&count=" + CACHE_PAGE_SIZE
                + "&page=" + page
                + "&cacheOnly=1");
            JSONArray arr = Json.array(data, "mails");
            if (arr.length() == 0) return;
            List<Models.Mail> pageMails = new ArrayList<>();
            for (int j = 0; j < arr.length(); j++) {
                pageMails.add(Models.Mail.fromExternal(arr.optJSONObject(j), account.id, folder));
            }
            store.upsertMails(pageMails);
            cacheMissingMailDetails(api, store, pageMails, stop, detailBudget);
            if (!(data.optBoolean("hasMore", false) || page * CACHE_PAGE_SIZE < data.optInt("total", 0))) return;
            page++;
        }
    }

    private static void cacheLocalAccount(ApiClient api, LocalStore store, Models.Account account, StopSignal stop, DetailBudget detailBudget) throws Exception {
        cacheLocalFolderIncrementally(api, store, account, "inbox", false, stop, detailBudget);
        if (stop.shouldStop()) return;
        cacheLocalFolderIncrementally(api, store, account, "sent", false, stop, detailBudget);
        if (stop.shouldStop()) return;
        cacheLocalUnreadCount(api, account);
    }

    private static void cacheLocalFolderIncrementally(ApiClient api, LocalStore store, Models.Account account, String folder, boolean unreadOnly, StopSignal stop, DetailBudget detailBudget) throws Exception {
        int offset = 0;
        int pagesRead = 0;
        boolean hitExisting = false;
        boolean hasMore = true;
        while (hasMore && pagesRead < MAX_INCREMENTAL_PAGES_PER_FOLDER && !hitExisting) {
            if (stop.shouldStop()) return;
            JSONObject data = queryLocalFolder(api, account, folder, unreadOnly, CACHE_PAGE_SIZE, offset);
            JSONArray arr = Json.array(data, "messages");
            if (arr.length() == 0) break;
            List<Models.Mail> pageMails = new ArrayList<>();
            for (int i = 0; i < arr.length(); i++) {
                Models.Mail mail = Models.Mail.fromLocal(arr.optJSONObject(i), account.id, folder);
                pageMails.add(mail);
                if (store.hasMail(mail)) {
                    hitExisting = true;
                    break;
                }
            }
            store.upsertMails(pageMails);
            cacheMissingMailDetails(api, store, pageMails, stop, detailBudget);
            pagesRead++;
            offset += CACHE_PAGE_SIZE;
            hasMore = offset < data.optInt("total", offset);
        }
        if (!hitExisting) backfillLocalFolderPage(api, store, account, folder, unreadOnly, stop, detailBudget);
    }

    private static void backfillLocalFolderPage(ApiClient api, LocalStore store, Models.Account account, String folder, boolean unreadOnly, StopSignal stop, DetailBudget detailBudget) throws Exception {
        int offset = store.countMails("local", account.id, folder);
        for (int i = 0; i < MAX_BACKFILL_PAGES_PER_FOLDER; i++) {
            if (stop.shouldStop()) return;
            JSONObject data = queryLocalFolder(api, account, folder, unreadOnly, CACHE_PAGE_SIZE, offset);
            JSONArray arr = Json.array(data, "messages");
            if (arr.length() == 0) return;
            List<Models.Mail> pageMails = new ArrayList<>();
            for (int j = 0; j < arr.length(); j++) {
                pageMails.add(Models.Mail.fromLocal(arr.optJSONObject(j), account.id, folder));
            }
            store.upsertMails(pageMails);
            cacheMissingMailDetails(api, store, pageMails, stop, detailBudget);
            offset += CACHE_PAGE_SIZE;
            if (offset >= data.optInt("total", offset)) return;
        }
    }

    private static void cacheLocalUnreadCount(ApiClient api, Models.Account account) throws Exception {
        JSONObject data = queryLocalFolder(api, account, "inbox", true, 1, 0);
        int total = data.optInt("total", -1);
        if (total >= 0) account.unread = total;
    }

    private static JSONObject queryLocalFolder(ApiClient api, Models.Account account, String folder, boolean unreadOnly, int limit, int offset) throws Exception {
        JSONObject body = new JSONObject()
            .put("email", account.email)
            .put("offset", Math.max(0, offset))
            .put("limit", Math.max(1, limit))
            .put("unread_only", unreadOnly);
        return "sent".equals(folder)
            ? api.post("/api/sent/query", body)
            : api.post("/api/inbox/query", body);
    }

    private static void cacheStoredMissingDetails(ApiClient api, LocalStore store, StopSignal stop, DetailBudget detailBudget) {
        if (detailBudget.remaining <= 0 || stop.shouldStop()) return;
        cacheMissingMailDetails(api, store, store.readMailsMissingBody(detailBudget.remaining), stop, detailBudget);
    }

    private static void cacheMissingMailDetails(ApiClient api, LocalStore store, List<Models.Mail> mails, StopSignal stop, DetailBudget detailBudget) {
        if (mails == null || mails.isEmpty()) return;
        for (Models.Mail mail : mails) {
            if (stop.shouldStop() || detailBudget.remaining <= 0) return;
            if (mail == null || store.hasFullBody(mail)) continue;
            try {
                store.upsertMailDetail(fetchMailDetail(api, mail));
                detailBudget.remaining--;
            } catch (Exception ignored) {
                // Detail prefetch is best-effort; list cache must remain fast and usable.
            }
        }
    }

    private static Models.Mail fetchMailDetail(ApiClient api, Models.Mail mail) throws Exception {
        JSONObject data;
        if ("external".equals(mail.accountType)) {
            data = api.get("/imap/api/accounts/" + encode(mail.accountId) + "/mails/" + encode(mail.id) + "?folder=" + encode(mail.folder) + "&markSeen=0");
        } else if ("sent".equals(mail.folder)) {
            data = api.post("/api/sent/detail", new JSONObject().put("email", mail.accountId).put("message_id", mail.id));
        } else {
            data = api.post("/api/inbox/detail/cache", new JSONObject()
                .put("email", mail.accountId)
                .put("message_id", mail.id));
        }
        return mergeDetail(mail, data);
    }

    private static Models.Mail mergeDetail(Models.Mail base, JSONObject data) {
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
        mail.to = nonEmpty(Json.anyStr(source, "to", "to_address"), base.to);
        mail.text = nonEmpty(Json.str(source, "text"), base.text);
        mail.html = nonEmpty(Json.str(source, "html"), base.html);
        mail.error = nonEmpty(base.error, Json.str(source, "error"));
        mail.seen = base.seen;
        mail.favorite = base.favorite || source.optBoolean("flagged", false) || Json.obj(source, "meta").optBoolean("favorite", false);
        return mail;
    }

    private static boolean notifyIfNeeded(Context context, SharedPreferences prefs, List<Models.Account> accounts, int oldUnread) {
        if (!prefs.getBoolean("notify", true)) return false;
        int total = 0;
        Set<String> enabledGroups = notificationGroups(prefs);
        for (Models.Account account : accounts) {
            String group = account.group == null || account.group.isEmpty() ? "未分组" : account.group;
            if (enabledGroups.isEmpty() || enabledGroups.contains(group)) total += Math.max(0, account.unread);
        }
        if (total > oldUnread && oldUnread > 0) {
            notifyMail(context, "Memail 新邮件", "未读邮件增加到 " + total + " 封");
            return true;
        }
        return false;
    }

    private static Set<String> notificationGroups(SharedPreferences prefs) {
        String saved = prefs.getString("notify_groups", "");
        Set<String> groups = new HashSet<>();
        if (saved == null || saved.isEmpty()) return groups;
        String[] parts = saved.split("\\|", -1);
        for (String part : parts) if (!part.isEmpty()) groups.add(part);
        return groups;
    }

    private static int totalUnread(List<Models.Account> accounts) {
        int total = 0;
        for (Models.Account account : accounts) total += Math.max(0, account.unread);
        return total;
    }

    private static String nonEmpty(String value, String fallback) {
        return value == null || value.isEmpty() ? fallback : value;
    }

    static void createMailNotificationChannel(Context context) {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) return;
        NotificationChannel channel = new NotificationChannel(CHANNEL_MAIL, "Memail 新邮件", NotificationManager.IMPORTANCE_DEFAULT);
        manager.createNotificationChannel(channel);
    }

    static void notifyMail(Context context, String title, String text) {
        if (Build.VERSION.SDK_INT >= 33 && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        createMailNotificationChannel(context);
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) return;
        Intent intent = new Intent(context, MainActivity.class);
        intent.setAction("com.memail.mobile.OPEN_MAIL");
        intent.putExtra("open", "mail");
        intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pendingIntent = PendingIntent.getActivity(context, NOTIFICATION_ID, intent, flags);
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
            ? new Notification.Builder(context, CHANNEL_MAIL)
            : new Notification.Builder(context);
        Notification notification = builder
            .setSmallIcon(android.R.drawable.ic_dialog_email)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .build();
        manager.notify(NOTIFICATION_ID, notification);
    }

    private static String encode(String value) {
        return Uri.encode(value == null ? "" : value);
    }

    static final class SyncResult {
        boolean synced;
        boolean notified;
        int accountCount;
        int unreadTotal;
        int missingBodyCount;
        boolean bodyPrefetchBudgetExhausted;
    }

    private static final class DetailBudget {
        int remaining;

        DetailBudget(int remaining) {
            this.remaining = remaining;
        }
    }
}
