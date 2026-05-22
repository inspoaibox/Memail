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

    static final class KeywordRule {
        String id;
        String name;
        String scopeType;
        String scopeGroup;
        String[] scopeAccounts = new String[0];
        String matchMode;
        String[] keywords = new String[0];
        String[] fields = new String[0];
        boolean enabled = true;

        String keywordLine() {
            if (keywords == null || keywords.length == 0) return "";
            StringBuilder sb = new StringBuilder();
            for (String keyword : keywords) {
                if (keyword == null || keyword.isEmpty()) continue;
                if (sb.length() > 0) sb.append("、");
                sb.append(keyword);
            }
            return sb.toString();
        }

        boolean matches(Mail mail, String extraQuery) {
            if (!enabled || keywords == null || keywords.length == 0 || mail == null) return false;
            String blob = searchableText(mail).toLowerCase();
            boolean all = "all".equalsIgnoreCase(matchMode);
            boolean matched = all;
            for (String keyword : keywords) {
                String key = keyword == null ? "" : keyword.trim().toLowerCase();
                if (key.isEmpty()) continue;
                boolean hit = blob.contains(key);
                if (all && !hit) {
                    matched = false;
                    break;
                }
                if (!all && hit) {
                    matched = true;
                    break;
                }
            }
            if (!matched) return false;
            String q = extraQuery == null ? "" : extraQuery.trim().toLowerCase();
            return q.isEmpty() || blob.contains(q);
        }

        private String searchableText(Mail mail) {
            StringBuilder sb = new StringBuilder();
            if (usesField("from")) sb.append(mail.sender).append(' ');
            if (usesField("to")) sb.append(mail.to).append(' ');
            if (usesField("subject")) sb.append(mail.subject).append(' ');
            if (usesField("intro")) sb.append(mail.preview).append(' ');
            if (usesField("body")) sb.append(mail.text).append(' ').append(mail.html);
            return sb.toString();
        }

        private boolean usesField(String field) {
            if (fields == null || fields.length == 0) return "subject".equals(field) || "from".equals(field) || "intro".equals(field);
            for (String item : fields) if (field.equalsIgnoreCase(item)) return true;
            return false;
        }
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
