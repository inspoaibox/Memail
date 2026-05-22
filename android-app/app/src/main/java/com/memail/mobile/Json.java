package com.memail.mobile;

import org.json.JSONArray;
import org.json.JSONObject;

final class Json {
    private Json() {}

    static String str(JSONObject object, String key) {
        return object == null ? "" : object.optString(key, "");
    }

    static String anyStr(JSONObject object, String... keys) {
        if (object == null) return "";
        for (String key : keys) {
            String value = object.optString(key, "");
            if (value != null && !value.isEmpty()) return value;
        }
        return "";
    }

    static JSONArray array(JSONObject object, String key) {
        JSONArray value = object == null ? null : object.optJSONArray(key);
        return value == null ? new JSONArray() : value;
    }

    static JSONArray arrayAny(JSONObject object, String... keys) {
        if (object == null) return new JSONArray();
        for (String key : keys) {
            JSONArray value = object.optJSONArray(key);
            if (value != null) return value;
        }
        return new JSONArray();
    }

    static JSONObject obj(JSONObject object, String key) {
        JSONObject value = object == null ? null : object.optJSONObject(key);
        return value == null ? new JSONObject() : value;
    }
}
