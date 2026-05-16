package com.memail.mobile;

import org.json.JSONObject;
import org.json.JSONArray;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class ApiClient {
    private String baseUrl = "";
    private String token = "";

    void configure(String baseUrl, String token) {
        this.baseUrl = normalizeBaseUrl(baseUrl);
        this.token = token == null ? "" : token.trim();
    }

    String baseUrl() {
        return baseUrl;
    }

    JSONObject get(String path) throws Exception {
        return request("GET", path, null);
    }

    JSONObject post(String path, JSONObject body) throws Exception {
        return request("POST", path, body == null ? new JSONObject() : body);
    }

    JSONObject put(String path, JSONObject body) throws Exception {
        return request("PUT", path, body == null ? new JSONObject() : body);
    }

    JSONObject delete(String path) throws Exception {
        return request("DELETE", path, null);
    }

    JSONObject login(String server, String username, String password, String totp, String deviceName) throws Exception {
        this.baseUrl = normalizeBaseUrl(server);
        this.token = "";
        JSONObject body = new JSONObject()
            .put("username", username)
            .put("password", password)
            .put("totp_code", totp)
            .put("device_name", deviceName);
        return request("POST", "/api/mobile/login", body);
    }

    private JSONObject request(String method, String path, JSONObject body) throws Exception {
        if (baseUrl.isEmpty()) throw new IOException("服务端地址不能为空");
        URL url = new URL(baseUrl + normalizePath(path));
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(75000);
        conn.setRequestMethod(method);
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        if (!token.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + token);
        if (body != null) {
            conn.setDoOutput(true);
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
            conn.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream os = conn.getOutputStream()) {
                os.write(bytes);
            }
        }
        int code = conn.getResponseCode();
        String text = readAll(code >= 400 ? conn.getErrorStream() : conn.getInputStream());
        JSONObject json;
        try {
            String trimmed = text == null ? "" : text.trim();
            if (trimmed.isEmpty()) {
                json = new JSONObject();
            } else if (trimmed.startsWith("[")) {
                json = new JSONObject().put("items", new JSONArray(trimmed));
            } else {
                json = new JSONObject(trimmed);
            }
        } catch (Exception e) {
            throw new IOException("服务端返回非 JSON 内容: " + compact(text));
        }
        if (code >= 400 || !json.optBoolean("success", true)) {
            throw new IOException(json.optString("message", "请求失败: HTTP " + code));
        }
        return json;
    }

    private static String readAll(InputStream input) throws IOException {
        if (input == null) return "";
        StringBuilder sb = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) sb.append(line).append('\n');
        }
        return sb.toString();
    }

    static String normalizeBaseUrl(String value) {
        String url = value == null ? "" : value.trim();
        if (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        if (!url.isEmpty() && !url.startsWith("http://") && !url.startsWith("https://")) {
            url = "https://" + url;
        }
        return url;
    }

    static String normalizePath(String path) {
        return path.startsWith("/") ? path : "/" + path;
    }

    private static String compact(String value) {
        if (value == null) return "";
        return value.replaceAll("\\s+", " ").trim();
    }
}
