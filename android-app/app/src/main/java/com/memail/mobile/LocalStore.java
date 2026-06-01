package com.memail.mobile;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import java.util.ArrayList;
import java.util.List;

final class LocalStore extends SQLiteOpenHelper {
    private static final String DB_NAME = "memail_mobile_cache.db";
    private static final int DB_VERSION = 2;
    private static final String[] MAIL_LIST_COLUMNS = new String[]{
        "account_type",
        "account_id",
        "folder",
        "id",
        "sender",
        "subject",
        "preview",
        "date_text",
        "kind",
        "to_text",
        "error",
        "seen",
        "favorite"
    };
    private static final String[] MAIL_MERGE_COLUMNS = new String[]{
        "preview",
        "to_text",
        "text_body",
        "html_body"
    };

    LocalStore(Context context) {
        super(context, DB_NAME, null, DB_VERSION);
        setWriteAheadLoggingEnabled(true);
    }

    @Override
    public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE accounts ("
            + "type TEXT NOT NULL,"
            + "id TEXT NOT NULL,"
            + "email TEXT,"
            + "name TEXT,"
            + "send_name TEXT,"
            + "group_name TEXT,"
            + "unread INTEGER DEFAULT 0,"
            + "position INTEGER DEFAULT 0,"
            + "updated_at INTEGER DEFAULT 0,"
            + "PRIMARY KEY(type, id))");
        db.execSQL("CREATE TABLE folders ("
            + "account_type TEXT NOT NULL,"
            + "account_id TEXT NOT NULL,"
            + "path TEXT NOT NULL,"
            + "name TEXT,"
            + "count INTEGER DEFAULT 0,"
            + "position INTEGER DEFAULT 0,"
            + "updated_at INTEGER DEFAULT 0,"
            + "PRIMARY KEY(account_type, account_id, path))");
        db.execSQL("CREATE TABLE mails ("
            + "account_type TEXT NOT NULL,"
            + "account_id TEXT NOT NULL,"
            + "folder TEXT NOT NULL,"
            + "id TEXT NOT NULL,"
            + "sender TEXT,"
            + "subject TEXT,"
            + "preview TEXT,"
            + "date_text TEXT,"
            + "kind TEXT,"
            + "to_text TEXT,"
            + "text_body TEXT,"
            + "html_body TEXT,"
            + "error TEXT,"
            + "seen INTEGER DEFAULT 1,"
            + "favorite INTEGER DEFAULT 0,"
            + "updated_at INTEGER DEFAULT 0,"
            + "PRIMARY KEY(account_type, account_id, folder, id))");
        db.execSQL("CREATE INDEX idx_mails_scope_date ON mails(account_type, account_id, folder, date_text DESC)");
        db.execSQL("CREATE INDEX idx_mails_date ON mails(date_text DESC)");
        createTranslationTable(db);
    }

    @Override
    public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        if (oldVersion < 2) {
            createTranslationTable(db);
            return;
        }
        db.execSQL("DROP TABLE IF EXISTS mails");
        db.execSQL("DROP TABLE IF EXISTS translations");
        db.execSQL("DROP TABLE IF EXISTS folders");
        db.execSQL("DROP TABLE IF EXISTS accounts");
        onCreate(db);
    }

    private static void createTranslationTable(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE IF NOT EXISTS translations ("
            + "account_type TEXT NOT NULL,"
            + "account_id TEXT NOT NULL,"
            + "folder TEXT NOT NULL,"
            + "id TEXT NOT NULL,"
            + "source_hash TEXT NOT NULL,"
            + "translation TEXT,"
            + "format TEXT,"
            + "engine TEXT,"
            + "provider TEXT,"
            + "model TEXT,"
            + "updated_at INTEGER DEFAULT 0,"
            + "PRIMARY KEY(account_type, account_id, folder, id, source_hash))");
    }

    void replaceAccounts(List<Models.Account> accounts) {
        SQLiteDatabase db = getWritableDatabase();
        db.beginTransaction();
        try {
            db.delete("accounts", null, null);
            long now = System.currentTimeMillis();
            for (int i = 0; i < accounts.size(); i++) {
                Models.Account account = accounts.get(i);
                ContentValues values = new ContentValues();
                values.put("type", safe(account.type));
                values.put("id", safe(account.id));
                values.put("email", safe(account.email));
                values.put("name", safe(account.name));
                values.put("send_name", safe(account.sendName));
                values.put("group_name", safe(account.group));
                values.put("unread", account.unread);
                values.put("position", i);
                values.put("updated_at", now);
                db.insertWithOnConflict("accounts", null, values, SQLiteDatabase.CONFLICT_REPLACE);
            }
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
    }

    List<Models.Account> readAccounts() {
        List<Models.Account> items = new ArrayList<>();
        SQLiteDatabase db = getReadableDatabase();
        try (Cursor cursor = db.query("accounts", null, null, null, null, null, "position ASC, email ASC")) {
            while (cursor.moveToNext()) {
                Models.Account account = new Models.Account();
                account.type = get(cursor, "type");
                account.id = get(cursor, "id");
                account.email = get(cursor, "email");
                account.name = get(cursor, "name");
                account.sendName = get(cursor, "send_name");
                account.group = get(cursor, "group_name");
                account.unread = cursor.getInt(cursor.getColumnIndexOrThrow("unread"));
                items.add(account);
            }
        }
        return items;
    }

    void replaceFolders(Models.Account account, List<Models.Folder> folders) {
        if (account == null) return;
        SQLiteDatabase db = getWritableDatabase();
        db.beginTransaction();
        try {
            db.delete("folders", "account_type=? AND account_id=?", new String[]{safe(account.type), safe(account.id)});
            long now = System.currentTimeMillis();
            for (int i = 0; i < folders.size(); i++) {
                Models.Folder folder = folders.get(i);
                ContentValues values = new ContentValues();
                values.put("account_type", safe(folder.accountType));
                values.put("account_id", safe(folder.accountId));
                values.put("path", safe(folder.path));
                values.put("name", safe(folder.name));
                values.put("count", folder.count);
                values.put("position", i);
                values.put("updated_at", now);
                db.insertWithOnConflict("folders", null, values, SQLiteDatabase.CONFLICT_REPLACE);
            }
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
    }

    List<Models.Folder> readFolders(String accountType, String accountId) {
        List<Models.Folder> items = new ArrayList<>();
        SQLiteDatabase db = getReadableDatabase();
        try (Cursor cursor = db.query(
            "folders",
            null,
            "account_type=? AND account_id=?",
            new String[]{safe(accountType), safe(accountId)},
            null,
            null,
            "position ASC, name ASC"
        )) {
            while (cursor.moveToNext()) {
                items.add(readFolder(cursor));
            }
        }
        return items;
    }

    void upsertMails(List<Models.Mail> mails) {
        if (mails == null || mails.isEmpty()) return;
        SQLiteDatabase db = getWritableDatabase();
        db.beginTransaction();
        try {
            long now = System.currentTimeMillis();
            for (Models.Mail mail : mails) {
                if (isEmpty(mail.accountType) || isEmpty(mail.accountId) || isEmpty(mail.folder)) continue;
                Models.Mail existing = readMailMergeFields(db, mail.accountType, mail.accountId, mail.folder, mailId(mail));
                ContentValues values = new ContentValues();
                values.put("account_type", safe(mail.accountType));
                values.put("account_id", safe(mail.accountId));
                values.put("folder", safe(mail.folder));
                values.put("id", mailId(mail));
                values.put("sender", safe(mail.sender));
                values.put("subject", safe(mail.subject));
                values.put("preview", safe(nonEmpty(mail.preview, existing == null ? "" : existing.preview)));
                values.put("date_text", safe(mail.date));
                values.put("kind", safe(mail.kind));
                values.put("to_text", safe(nonEmpty(mail.to, existing == null ? "" : existing.to)));
                values.put("text_body", safe(nonEmpty(mail.text, existing == null ? "" : existing.text)));
                values.put("html_body", safe(nonEmpty(mail.html, existing == null ? "" : existing.html)));
                values.put("error", safe(mail.error));
                values.put("seen", mail.seen ? 1 : 0);
                values.put("favorite", mail.favorite ? 1 : 0);
                values.put("updated_at", now);
                db.insertWithOnConflict("mails", null, values, SQLiteDatabase.CONFLICT_REPLACE);
            }
            db.setTransactionSuccessful();
        } finally {
            db.endTransaction();
        }
    }

    void upsertMailDetail(Models.Mail mail) {
        if (mail == null) return;
        List<Models.Mail> one = new ArrayList<>();
        one.add(mail);
        upsertMails(one);
    }

    Models.Mail readMailDetail(Models.Mail mail) {
        if (mail == null) return null;
        SQLiteDatabase db = getReadableDatabase();
        try (Cursor cursor = db.query(
            "mails",
            null,
            "account_type=? AND account_id=? AND folder=? AND id=?",
            new String[]{safe(mail.accountType), safe(mail.accountId), safe(mail.folder), mailId(mail)},
            null,
            null,
            null,
            "1"
        )) {
            return cursor.moveToFirst() ? readMail(cursor) : null;
        }
    }

    boolean hasMail(Models.Mail mail) {
        if (mail == null || isEmpty(mail.accountType) || isEmpty(mail.accountId) || isEmpty(mail.folder)) return false;
        SQLiteDatabase db = getReadableDatabase();
        try (Cursor cursor = db.query(
            "mails",
            new String[]{"id"},
            "account_type=? AND account_id=? AND folder=? AND id=?",
            new String[]{safe(mail.accountType), safe(mail.accountId), safe(mail.folder), mailId(mail)},
            null,
            null,
            null,
            "1"
        )) {
            return cursor.moveToFirst();
        }
    }

    boolean hasFullBody(Models.Mail mail) {
        if (mail == null || isEmpty(mail.accountType) || isEmpty(mail.accountId) || isEmpty(mail.folder)) return false;
        SQLiteDatabase db = getReadableDatabase();
        try (Cursor cursor = db.query(
            "mails",
            new String[]{"text_body", "html_body"},
            "account_type=? AND account_id=? AND folder=? AND id=?",
            new String[]{safe(mail.accountType), safe(mail.accountId), safe(mail.folder), mailId(mail)},
            null,
            null,
            null,
            "1"
        )) {
            if (!cursor.moveToFirst()) return false;
            return !get(cursor, "text_body").isEmpty() || !get(cursor, "html_body").isEmpty();
        }
    }

    int countMails(String accountType, String accountId, String folder) {
        SQLiteDatabase db = getReadableDatabase();
        try (Cursor cursor = db.rawQuery(
            "SELECT COUNT(*) FROM mails WHERE account_type=? AND account_id=? AND folder=?",
            new String[]{safe(accountType), safe(accountId), safe(folder)}
        )) {
            return cursor.moveToFirst() ? cursor.getInt(0) : 0;
        }
    }

    List<Models.Mail> readMails(
        String accountType,
        String accountId,
        String folder,
        String query,
        int limit,
        int offset,
        boolean unreadOnly
    ) {
        List<String> args = new ArrayList<>();
        StringBuilder where = new StringBuilder("account_type=? AND account_id=?");
        args.add(safe(accountType));
        args.add(safe(accountId));
        if (unreadOnly && "unread".equals(folder)) {
            where.append(" AND folder NOT IN ('drafts','outbox')");
        } else {
            where.append(" AND folder=?");
            args.add(safe(folder));
        }
        appendFilters(where, args, query, unreadOnly);
        return queryMails(where.toString(), args, limit, offset);
    }

    List<Models.Mail> readMailsMissingBody(int limit) {
        List<Models.Mail> items = new ArrayList<>();
        SQLiteDatabase db = getReadableDatabase();
        try (Cursor cursor = db.query(
            "mails",
            MAIL_LIST_COLUMNS,
            "folder NOT IN ('drafts','outbox') AND (text_body IS NULL OR text_body='') AND (html_body IS NULL OR html_body='')",
            null,
            null,
            null,
            "date_text DESC, updated_at DESC",
            String.valueOf(Math.max(1, limit))
        )) {
            while (cursor.moveToNext()) {
                items.add(readMail(cursor));
            }
        }
        return items;
    }

    int countMailsMissingBody() {
        SQLiteDatabase db = getReadableDatabase();
        try (Cursor cursor = db.rawQuery(
            "SELECT COUNT(*) FROM mails WHERE folder NOT IN ('drafts','outbox') AND (text_body IS NULL OR text_body='') AND (html_body IS NULL OR html_body='')",
            null
        )) {
            return cursor.moveToFirst() ? cursor.getInt(0) : 0;
        }
    }

    void deleteMail(Models.Mail mail) {
        if (mail == null) return;
        SQLiteDatabase db = getWritableDatabase();
        db.delete(
            "mails",
            "account_type=? AND account_id=? AND folder=? AND id=?",
            new String[]{safe(mail.accountType), safe(mail.accountId), safe(mail.folder), mailId(mail)}
        );
        db.delete(
            "translations",
            "account_type=? AND account_id=? AND folder=? AND id=?",
            new String[]{safe(mail.accountType), safe(mail.accountId), safe(mail.folder), mailId(mail)}
        );
    }

    TranslationCache readTranslation(Models.Mail mail, String sourceHash) {
        if (mail == null || isEmpty(sourceHash)) return null;
        SQLiteDatabase db = getReadableDatabase();
        try (Cursor cursor = db.query(
            "translations",
            null,
            "account_type=? AND account_id=? AND folder=? AND id=? AND source_hash=?",
            new String[]{safe(mail.accountType), safe(mail.accountId), safe(mail.folder), mailId(mail), sourceHash},
            null,
            null,
            "updated_at DESC",
            "1"
        )) {
            if (!cursor.moveToFirst()) return null;
            TranslationCache cache = new TranslationCache();
            cache.translation = get(cursor, "translation");
            cache.format = get(cursor, "format");
            cache.engine = get(cursor, "engine");
            cache.provider = get(cursor, "provider");
            cache.model = get(cursor, "model");
            return cache.translation.isEmpty() ? null : cache;
        }
    }

    void saveTranslation(
        Models.Mail mail,
        String sourceHash,
        String translation,
        String format,
        String engine,
        String provider,
        String model
    ) {
        if (mail == null || isEmpty(sourceHash) || isEmpty(translation)) return;
        SQLiteDatabase db = getWritableDatabase();
        ContentValues values = new ContentValues();
        values.put("account_type", safe(mail.accountType));
        values.put("account_id", safe(mail.accountId));
        values.put("folder", safe(mail.folder));
        values.put("id", mailId(mail));
        values.put("source_hash", sourceHash);
        values.put("translation", safe(translation));
        values.put("format", safe(format));
        values.put("engine", safe(engine));
        values.put("provider", safe(provider));
        values.put("model", safe(model));
        values.put("updated_at", System.currentTimeMillis());
        db.insertWithOnConflict("translations", null, values, SQLiteDatabase.CONFLICT_REPLACE);
    }

    List<Models.Mail> readVirtualMails(
        List<Models.Account> accounts,
        boolean unreadOnly,
        String query,
        int limit,
        int offset
    ) {
        if (accounts == null || accounts.isEmpty()) return new ArrayList<>();
        List<String> args = new ArrayList<>();
        StringBuilder where = new StringBuilder("(");
        boolean added = false;
        for (Models.Account account : accounts) {
            if (isEmpty(account.type) || isEmpty(account.id)) continue;
            if (added) where.append(" OR ");
            where.append("(account_type=? AND account_id=?)");
            args.add(safe(account.type));
            args.add(safe(account.id));
            added = true;
        }
        where.append(")");
        if (!added) return new ArrayList<>();
        where.append(" AND folder NOT IN ('drafts','outbox')");
        appendFilters(where, args, query, unreadOnly);
        return queryMails(where.toString(), args, limit, offset);
    }

    private List<Models.Mail> queryMails(String where, List<String> args, int limit, int offset) {
        List<Models.Mail> items = new ArrayList<>();
        SQLiteDatabase db = getReadableDatabase();
        String limitClause = Math.max(1, limit) + " OFFSET " + Math.max(0, offset);
        try (Cursor cursor = db.query(
            "mails",
            MAIL_LIST_COLUMNS,
            where,
            args.toArray(new String[0]),
            null,
            null,
            "date_text DESC, updated_at DESC",
            limitClause
        )) {
            while (cursor.moveToNext()) {
                items.add(readMail(cursor));
            }
        }
        return items;
    }

    private static void appendFilters(StringBuilder where, List<String> args, String query, boolean unreadOnly) {
        if (unreadOnly) where.append(" AND seen=0");
        String q = query == null ? "" : query.trim();
        if (!q.isEmpty()) {
            where.append(" AND (sender LIKE ? OR subject LIKE ? OR preview LIKE ? OR text_body LIKE ? OR html_body LIKE ?)");
            String like = "%" + q + "%";
            args.add(like);
            args.add(like);
            args.add(like);
            args.add(like);
            args.add(like);
        }
    }

    private static Models.Folder readFolder(Cursor cursor) {
        Models.Folder folder = new Models.Folder();
        folder.accountType = get(cursor, "account_type");
        folder.accountId = get(cursor, "account_id");
        folder.path = get(cursor, "path");
        folder.name = get(cursor, "name");
        folder.count = cursor.getInt(cursor.getColumnIndexOrThrow("count"));
        return folder;
    }

    private static Models.Mail readMail(Cursor cursor) {
        Models.Mail mail = new Models.Mail();
        mail.accountType = get(cursor, "account_type");
        mail.accountId = get(cursor, "account_id");
        mail.folder = get(cursor, "folder");
        mail.id = get(cursor, "id");
        mail.sender = get(cursor, "sender");
        mail.subject = get(cursor, "subject");
        mail.preview = get(cursor, "preview");
        mail.date = get(cursor, "date_text");
        mail.kind = get(cursor, "kind");
        mail.to = get(cursor, "to_text");
        mail.text = get(cursor, "text_body");
        mail.html = get(cursor, "html_body");
        mail.error = get(cursor, "error");
        mail.seen = getInt(cursor, "seen", 1) != 0;
        mail.favorite = getInt(cursor, "favorite", 0) != 0;
        return mail;
    }

    private static Models.Mail readMailMergeFields(SQLiteDatabase db, String accountType, String accountId, String folder, String id) {
        try (Cursor cursor = db.query(
            "mails",
            MAIL_MERGE_COLUMNS,
            "account_type=? AND account_id=? AND folder=? AND id=?",
            new String[]{safe(accountType), safe(accountId), safe(folder), safe(id)},
            null,
            null,
            null,
            "1"
        )) {
            return cursor.moveToFirst() ? readMail(cursor) : null;
        }
    }

    private static String mailId(Models.Mail mail) {
        if (!isEmpty(mail.id)) return mail.id;
        String raw = safe(mail.accountType) + "|" + safe(mail.accountId) + "|" + safe(mail.folder)
            + "|" + safe(mail.sender) + "|" + safe(mail.subject) + "|" + safe(mail.date);
        return "local-" + Integer.toHexString(raw.hashCode());
    }

    private static String get(Cursor cursor, String column) {
        int index = cursor.getColumnIndex(column);
        if (index < 0) return "";
        return cursor.isNull(index) ? "" : cursor.getString(index);
    }

    private static int getInt(Cursor cursor, String column, int fallback) {
        int index = cursor.getColumnIndex(column);
        return index < 0 || cursor.isNull(index) ? fallback : cursor.getInt(index);
    }

    private static boolean isEmpty(String value) {
        return value == null || value.isEmpty();
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }

    private static String nonEmpty(String value, String fallback) {
        return value == null || value.isEmpty() ? fallback : value;
    }

    static final class TranslationCache {
        String translation;
        String format;
        String engine;
        String provider;
        String model;
    }
}
