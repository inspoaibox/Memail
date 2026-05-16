package com.memail.mobile;

import org.json.JSONObject;

final class Models {
    private Models() {}

    static final class Account {
        String type;
        String id;
        String email;
        String name;
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
        boolean seen;

        static Mail fromLocal(JSONObject object, String accountId, String folder) {
            Mail mail = new Mail();
            mail.accountType = "local";
            mail.accountId = accountId;
            mail.folder = folder;
            mail.id = Json.anyStr(object, "id", "msgid", "uid");
            JSONObject from = object.optJSONObject("from");
            mail.sender = from != null ? Json.anyStr(from, "name", "address") : Json.anyStr(object, "from", "from_address");
            mail.subject = Json.str(object, "subject");
            mail.preview = Json.anyStr(object, "intro", "text");
            mail.date = Json.anyStr(object, "createdAt", "created_at", "date");
            mail.seen = object.optBoolean("seen", true);
            return mail;
        }

        static Mail fromExternal(JSONObject object, String accountId, String folder) {
            Mail mail = new Mail();
            mail.accountType = "external";
            mail.accountId = accountId;
            mail.folder = folder;
            mail.id = Json.anyStr(object, "uid", "id", "msgid");
            mail.sender = Json.anyStr(object, "from", "sender");
            mail.subject = Json.str(object, "subject");
            mail.preview = Json.anyStr(object, "intro", "text");
            mail.date = Json.anyStr(object, "date", "createdAt", "created_at");
            mail.seen = object.optBoolean("seen", true);
            return mail;
        }
    }
}
