package com.memail.mobile;

import org.json.JSONObject;

final class Models {
    private Models() {}

    static final class Account {
        String type;
        String id;
        String email;
        String name;
        String sendName;
        String group;
        int unread;

        String label() {
            return (name == null || name.isEmpty()) ? email : name;
        }
    }

    static final class Folder {
        String accountType;
        String accountId;
        String path;
        String name;
        int count;
    }

    static final class Mail {
        String accountType;
        String accountId;
        String folder;
        String id;
        String sender;
        String subject;
        String preview;
        String date;
        String kind;
        String to;
        String text;
        String html;
        String error;
        boolean seen;
        boolean favorite;

        static Mail fromLocal(JSONObject object, String accountId, String folder) {
            Mail mail = new Mail();
            mail.accountType = "local";
            mail.accountId = accountId;
            mail.folder = folder;
            mail.id = Json.anyStr(object, "id", "msgid", "uid");
            JSONObject from = object.optJSONObject("from");
            mail.sender = cleanPerson(from != null ? Json.anyStr(from, "name", "address") : Json.anyStr(object, "from", "from_address"));
            if (mail.sender.isEmpty() && object.has("to")) mail.sender = "发给 " + Json.str(object, "to");
            mail.subject = Json.str(object, "subject");
            mail.preview = Json.anyStr(object, "intro", "text");
            mail.date = Json.anyStr(object, "createdAt", "created_at", "date");
            mail.kind = folder;
            mail.to = Json.str(object, "to");
            mail.text = Json.anyStr(object, "text", "intro");
            mail.html = Json.str(object, "html");
            mail.error = Json.str(object, "error");
            mail.seen = object.optBoolean("seen", true);
            mail.favorite = object.optBoolean("flagged", false) || Json.obj(object, "meta").optBoolean("favorite", false);
            return mail;
        }

        static Mail fromExternal(JSONObject object, String accountId, String folder) {
            Mail mail = new Mail();
            mail.accountType = "external";
            mail.accountId = accountId;
            mail.folder = folder;
            mail.id = Json.anyStr(object, "uid", "id", "msgid");
            JSONObject from = object.optJSONObject("from");
            mail.sender = cleanPerson(from != null ? Json.anyStr(from, "name", "address") : Json.anyStr(object, "from", "sender"));
            mail.subject = Json.str(object, "subject");
            mail.preview = Json.anyStr(object, "intro", "text");
            mail.date = Json.anyStr(object, "date", "createdAt", "created_at");
            mail.kind = folder;
            mail.to = Json.str(object, "to");
            mail.text = Json.anyStr(object, "text", "intro");
            mail.html = Json.str(object, "html");
            mail.error = Json.str(object, "error");
            mail.seen = object.optBoolean("seen", true);
            mail.favorite = object.optBoolean("flagged", false) || Json.obj(object, "meta").optBoolean("favorite", false);
            return mail;
        }

        private static String cleanPerson(String value) {
            if (value == null) return "";
            String trimmed = value.trim();
            if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
                try {
                    JSONObject obj = new JSONObject(trimmed);
                    String name = Json.str(obj, "name");
                    String address = Json.str(obj, "address");
                    if (!name.isEmpty()) return name;
                    if (!address.isEmpty()) return address;
                } catch (Exception ignored) {
                    // Fall through to plain text cleanup.
                }
            }
            return trimmed
                .replace("\\\"", "\"")
                .replaceAll("^\"|\"$", "")
                .trim();
        }

        static Mail fromDraft(JSONObject object) {
            Mail mail = new Mail();
            mail.accountType = Json.anyStr(object, "account_type", "accountType");
            if (mail.accountType.isEmpty()) mail.accountType = "local";
            mail.accountId = Json.anyStr(object, "account_id", "accountId", "from_email");
            mail.folder = "drafts";
            mail.kind = "draft";
            mail.id = Json.str(object, "id");
            mail.sender = Json.anyStr(object, "to", "from_email");
            mail.to = Json.str(object, "to");
            mail.subject = Json.str(object, "subject");
            mail.text = Json.str(object, "text");
            mail.html = Json.str(object, "html");
            mail.preview = mail.text.isEmpty() ? mail.html : mail.text;
            mail.date = Json.anyStr(object, "updated_at", "created_at");
            mail.seen = true;
            mail.favorite = false;
            return mail;
        }

        static Mail fromOutbox(JSONObject object) {
            Mail mail = new Mail();
            mail.accountType = Json.anyStr(object, "account_type", "accountType");
            if (mail.accountType.isEmpty()) mail.accountType = "local";
            mail.accountId = Json.anyStr(object, "account_id", "accountId", "from_email");
            mail.folder = "outbox";
            mail.kind = "outbox";
            mail.id = Json.str(object, "id");
            mail.sender = Json.anyStr(object, "to", "from_email");
            mail.to = Json.str(object, "to");
            mail.subject = Json.str(object, "subject");
            mail.text = Json.str(object, "text");
            mail.html = Json.str(object, "html");
            mail.error = Json.str(object, "error");
            mail.preview = mail.error.isEmpty() ? mail.text : "失败原因：" + mail.error;
            mail.date = Json.anyStr(object, "updated_at", "last_attempt_at", "created_at");
            mail.seen = true;
            mail.favorite = false;
            return mail;
        }
    }
}
