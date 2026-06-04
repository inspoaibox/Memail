package com.memail.mobile;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.OpenableColumns;
import android.text.Html;
import android.text.InputType;
import android.util.Base64;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewParent;
import android.view.Window;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebViewClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.EditText;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TimeZone;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

public class MainActivity extends Activity {
    private static final int BG = Color.rgb(245, 248, 248);
    private static final int CARD = Color.WHITE;
    private static final int SURFACE = Color.rgb(248, 251, 251);
    private static final int TEXT = Color.rgb(18, 35, 42);
    private static final int MUTED = Color.rgb(94, 111, 119);
    private static final int PRIMARY = Color.rgb(3, 99, 104);
    private static final int PRIMARY_DARK = Color.rgb(5, 63, 70);
    private static final int PRIMARY_SOFT = Color.rgb(224, 241, 239);
    private static final int ACCENT = Color.rgb(178, 124, 65);
    private static final int LINE = Color.rgb(214, 226, 228);
    private static final int SOFT_LINE = Color.rgb(234, 240, 241);
    private static final int NAV_INACTIVE = Color.rgb(87, 107, 116);
    private static final String PREFS = "memail_mobile";
    private static final String CHANNEL_MAIL = "memail_mail";
    private static final long EVENT_RECONNECT_MS = 4_000L;
    private static final int EVENT_READ_TIMEOUT_MS = 65_000;
    private static final long SERVER_EVENT_REFRESH_DEBOUNCE_MS = 1_200L;
    private static final long FOREGROUND_REFRESH_MS = 120_000L;
    private static final long BOOTSTRAP_REFRESH_COOLDOWN_MS = 45_000L;
    private static final int REQ_PICK_ATTACHMENT = 4107;
    private static final int MAX_MOBILE_ATTACHMENT_BYTES = 8 * 1024 * 1024;
    private static final int KEYWORD_SCAN_LIMIT = 600;
    private static final int MOBILE_TRANSLATE_CHUNK_CHARS = 4600;
    private static final int DETAIL_FETCH_MAX_ATTEMPTS = 3;
    private static final long DETAIL_FETCH_RETRY_DELAY_MS = 900L;
    private static final String MOBILE_TRANSLATION_BATCH_SEPARATOR = "\n<<<MEMAIL_TRANSLATION_SPLIT_9EC1B7>>>\n";

    private final ExecutorService io = Executors.newFixedThreadPool(4);
    private final ApiClient api = new ApiClient();
    private final List<Models.Account> accounts = new ArrayList<>();
    private final List<Models.Folder> folders = new ArrayList<>();
    private final List<Models.Mail> mails = new ArrayList<>();
    private final List<Models.KeywordRule> keywordRules = new ArrayList<>();
    private final Object listLock = new Object();

    private SharedPreferences prefs;
    private LocalStore store;
    private Handler mainHandler;
    private Thread eventThread;
    private volatile boolean eventStreamRunning = false;
    private volatile HttpURLConnection eventConnection;
    private volatile boolean bootstrapRefreshRunning = false;
    private boolean foregroundRefreshLoopRunning = false;
    private final Runnable foregroundRefreshTask = new Runnable() {
        @Override
        public void run() {
            if (!foregroundRefreshLoopRunning || token.isEmpty() || mainHandler == null) return;
            refreshBootstrapSilentlyIfDue(false);
            mainHandler.postDelayed(this, FOREGROUND_REFRESH_MS);
        }
    };
    private final Runnable serverEventRefreshTask = new Runnable() {
        @Override
        public void run() {
            if (token.isEmpty()) return;
            fetchBootstrapThenAccounts(false, false);
            if ("list".equals(currentScreen) && (selectedAccount != null || !selectedVirtualMode.isEmpty())) {
                currentPage = 1;
                loadMails(true);
            }
        }
    };
    private LinearLayout root;
    private LinearLayout content;
    private LinearLayout navBar;
    private TextView title;
    private TextView subtitle;
    private ProgressBar progress;
    private Models.Account selectedAccount;
    private Models.Folder selectedFolder;
    private Models.KeywordRule selectedKeywordRule;
    private String selectedVirtualMode = "";
    private String selectedGroup = "";
    private String token = "";
    private String server = "";
    private String searchQuery = "";
    private boolean notifyEnabled = true;
    private int navIndex = 0;
    private int currentPage = 1;
    private int pageSize = 30;
    private boolean hasMore = false;
    private boolean mailPageLoading = false;
    private String activeMailLoadKey = "";
    private String activeDetailLoadKey = "";
    private String activeTranslationLoadKey = "";
    private String currentScreen = "boot";
    private Models.Mail currentDetailMail;
    private JSONObject currentDetailSource;
    private Models.Account composeSender;
    private final List<ComposeAttachment> composeAttachments = new ArrayList<>();
    private TextView composeFromAvatar;
    private TextView composeFromView;
    private LinearLayout composeAttachmentList;

    private static final class ComposeAttachment {
        String filename;
        String contentType;
        String content;
        int size;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        store = new LocalStore(this);
        mainHandler = new Handler(Looper.getMainLooper());
        if (Build.VERSION.SDK_INT >= 21) {
            Window window = getWindow();
            window.getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
            getWindow().setStatusBarColor(Color.WHITE);
            getWindow().setNavigationBarColor(Color.WHITE);
        }
        server = prefs.getString("server", "https://mail.aboen.co.uk");
        token = prefs.getString("token", "");
        notifyEnabled = prefs.getBoolean("notify", true);
        api.configure(server, token);
        createNotificationChannel();
        buildShell();
        if (token.isEmpty()) showLogin();
        else loadHome();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleLaunchIntent(intent);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_PICK_ATTACHMENT && resultCode == RESULT_OK && data != null) {
            Uri uri = data.getData();
            if (uri != null) addComposeAttachment(uri);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        startEventStream();
        startForegroundRefreshLoop();
        if (!token.isEmpty() && !accounts.isEmpty()) refreshBootstrapSilentlyIfDue(false);
    }

    @Override
    protected void onPause() {
        super.onPause();
        stopEventStream();
        stopForegroundRefreshLoop();
        if (mainHandler != null) mainHandler.removeCallbacks(serverEventRefreshTask);
    }

    @Override
    @SuppressLint("GestureBackNavigation")
    public void onBackPressed() {
        if ("detail".equals(currentScreen) || "translation".equals(currentScreen) || "compose".equals(currentScreen)) {
            navIndex = 1;
            if ("translation".equals(currentScreen) && currentDetailMail != null && currentDetailSource != null) {
                renderDetail(currentDetailMail, currentDetailSource);
            } else if (selectedAccount != null || !selectedVirtualMode.isEmpty()) {
                renderFoldersOrMails();
            } else {
                renderMailHub();
            }
            rebuildBottomNav();
            return;
        }
        if ("list".equals(currentScreen) && selectedAccount == null && !selectedVirtualMode.isEmpty()) {
            renderMailHub();
            return;
        }
        if ("list".equals(currentScreen) && selectedAccount != null) {
            selectedAccount = null;
            selectedFolder = null;
            replaceFolderList(new ArrayList<>());
            replaceMailList(new ArrayList<>(), true);
            navIndex = 0;
            renderAccounts();
            rebuildBottomNav();
            return;
        }
        if ("settings".equals(currentScreen)) {
            navIndex = 0;
            renderAccounts();
            rebuildBottomNav();
            return;
        }
        super.onBackPressed();
    }

    private void buildShell() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);
        setContentView(root);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(12), statusBarInset() + dp(6), dp(12), dp(6));
        header.setBackgroundColor(Color.WHITE);
        root.addView(header, new LinearLayout.LayoutParams(-1, -2));
        View headerLine = new View(this);
        headerLine.setBackgroundColor(Color.rgb(238, 241, 242));
        root.addView(headerLine, new LinearLayout.LayoutParams(-1, 1));

        TextView back = text("‹", 34, TEXT, false);
        back.setGravity(Gravity.CENTER);
        back.setOnClickListener(v -> onBackPressed());
        header.addView(back, new LinearLayout.LayoutParams(dp(42), dp(46)));

        LinearLayout titleBox = new LinearLayout(this);
        titleBox.setOrientation(LinearLayout.VERTICAL);
        titleBox.setPadding(0, 0, 0, 0);
        header.addView(titleBox, new LinearLayout.LayoutParams(0, -2, 1));

        title = text("Memail", 20, TEXT, true);
        title.setGravity(Gravity.CENTER);
        subtitle = text("", 1, Color.TRANSPARENT, false);
        subtitle.setGravity(Gravity.CENTER);
        titleBox.addView(title);
        titleBox.addView(subtitle);

        progress = new ProgressBar(this);
        progress.setVisibility(View.GONE);
        header.addView(progress, new LinearLayout.LayoutParams(dp(28), dp(28)));

        TextView composeTop = text("✎", 28, TEXT, false);
        composeTop.setGravity(Gravity.CENTER);
        composeTop.setOnClickListener(v -> renderCompose());
        header.addView(composeTop, new LinearLayout.LayoutParams(dp(42), dp(46)));

        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(0, 0, 0, 0);
        root.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));

        navBar = bottomNav();
        root.addView(navBar, new LinearLayout.LayoutParams(-1, dp(74)));
    }

    private LinearLayout bottomNav() {
        LinearLayout outer = new LinearLayout(this);
        outer.setOrientation(LinearLayout.VERTICAL);
        outer.setGravity(Gravity.CENTER);
        outer.setPadding(dp(12), dp(2), dp(12), dp(12));
        outer.setBackgroundColor(Color.WHITE);
        outer.setVisibility(token.isEmpty() ? View.GONE : View.VISIBLE);

        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER);
        bar.setPadding(dp(5), dp(4), dp(5), dp(4));
        bar.setBackground(bg(Color.WHITE, 26, SOFT_LINE, 1));
        String[] labels = {"账户", "邮件", "写信", "设置"};
        String[] icons = {"⌂", "✉", "✎", "⚙"};
        for (int i = 0; i < labels.length; i++) {
            final int idx = i;
            View item = navItem(icons[i], labels[i], idx == navIndex);
            item.setOnClickListener(v -> {
                navIndex = idx;
                if (idx == 0) renderAccounts();
                if (idx == 1) renderMailHub();
                if (idx == 2) renderCompose();
                if (idx == 3) renderSettings();
                rebuildBottomNav();
            });
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, -1, 1);
            lp.setMargins(dp(1), 0, dp(1), 0);
            bar.addView(item, lp);
        }
        outer.addView(bar, new LinearLayout.LayoutParams(-1, dp(54)));
        return outer;
    }

    private void rebuildBottomNav() {
        if (navBar == null) return;
        navBar.removeAllViews();
        LinearLayout fresh = bottomNav();
        for (int i = 0; i < fresh.getChildCount(); i++) {
            View child = fresh.getChildAt(i);
            fresh.removeViewAt(i);
            navBar.addView(child);
            i--;
        }
        navBar.setVisibility(token.isEmpty() ? View.GONE : View.VISIBLE);
    }

    private View navItem(String icon, String label, boolean active) {
        LinearLayout item = new LinearLayout(this);
        item.setOrientation(LinearLayout.VERTICAL);
        item.setGravity(Gravity.CENTER);
        item.setBaselineAligned(false);
        item.setPadding(dp(2), dp(1), dp(2), dp(4));
        item.setBackground(active
            ? bg(PRIMARY_SOFT, 21, Color.rgb(187, 219, 218), 1)
            : bg(Color.TRANSPARENT, 19, Color.TRANSPARENT, 0));

        TextView iconView = text(icon, 23, active ? PRIMARY : NAV_INACTIVE, true);
        iconView.setGravity(Gravity.CENTER);
        iconView.setIncludeFontPadding(false);
        iconView.setTranslationY(-dp(1));
        TextView labelView = text(label, 11, active ? PRIMARY_DARK : NAV_INACTIVE, true);
        labelView.setGravity(Gravity.CENTER);
        labelView.setIncludeFontPadding(false);
        labelView.setPadding(0, 0, 0, 0);
        labelView.setTranslationY(-dp(1));

        item.addView(iconView, new LinearLayout.LayoutParams(-1, dp(24)));
        item.addView(labelView, new LinearLayout.LayoutParams(-1, dp(15)));
        return item;
    }

    private void showLogin() {
        currentScreen = "login";
        navIndex = 0;
        setHeader("Memail", "");
        if (navBar != null) navBar.setVisibility(View.GONE);
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        LinearLayout box = column(dp(12));
        box.setPadding(dp(20), dp(10), dp(20), dp(22));
        scroll.addView(box);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));

        LinearLayout hero = new LinearLayout(this);
        hero.setOrientation(LinearLayout.VERTICAL);
        hero.setPadding(dp(4), dp(8), dp(4), dp(10));
        TextView heroTitle = text("连接邮件服务", 28, TEXT, true);
        TextView heroText = text("首次连接后会在手机本地保存账号和邮件缓存；以后打开先显示本地数据，服务端有更新再通知手机增量同步。", 14, MUTED, false);
        heroText.setPadding(0, dp(8), 0, 0);
        hero.addView(heroTitle);
        hero.addView(heroText);
        box.addView(hero);

        LinearLayout form = panel(dp(16), dp(14));
        form.setBackground(bg(Color.WHITE, 20, Color.rgb(222, 231, 232), 1));
        EditText serverInput = input("服务端地址，例如 https://mail.example.com", server, false);
        EditText userInput = input("管理员账号", prefs.getString("username", "admin"), false);
        EditText passInput = input("访问密码", "", true);
        EditText totpInput = input("2FA 验证码（启用时填写）", "", false);
        totpInput.setInputType(InputType.TYPE_CLASS_NUMBER);
        form.addView(serverInput);
        form.addView(userInput);
        form.addView(passInput);
        form.addView(totpInput);
        box.addView(form);

        TextView login = primaryButton("连接");
        LinearLayout.LayoutParams loginLp = new LinearLayout.LayoutParams(-1, dp(54));
        loginLp.setMargins(0, dp(8), 0, 0);
        login.setLayoutParams(loginLp);
        login.setOnClickListener(v -> runAsync("正在连接...", () -> {
            JSONObject resp = api.login(
                serverInput.getText().toString(),
                userInput.getText().toString(),
                passInput.getText().toString(),
                totpInput.getText().toString(),
                Build.MANUFACTURER + " " + Build.MODEL
            );
            token = Json.str(resp, "token");
            server = ApiClient.normalizeBaseUrl(serverInput.getText().toString());
            prefs.edit()
                .putString("server", server)
                .putString("username", userInput.getText().toString())
                .putString("token", token)
                .apply();
            api.configure(server, token);
            return "ok";
        }, result -> {
            if (navBar != null) navBar.setVisibility(View.VISIBLE);
            loadHome();
            rebuildBottomNav();
        }));
        box.addView(login);
    }

    private void loadHome() {
        navIndex = 0;
        requestNotificationPermission();
        BackgroundSyncService.schedule(this);
        RealtimeSyncService.start(this);
        startEventStream();
        startForegroundRefreshLoop();
        accounts.clear();
        accounts.addAll(store.readAccounts());
        loadCachedKeywordRules();
        if (accounts.isEmpty()) {
            fetchBootstrapThenAccounts(true, true);
        } else {
            renderAccounts();
            checkNotifications();
            refreshBootstrapSilentlyIfDue(true);
        }
        handleLaunchIntent(getIntent());
    }

    private void handleLaunchIntent(Intent intent) {
        if (intent == null || token.isEmpty()) return;
        if (!"mail".equals(intent.getStringExtra("open"))) return;
        mainHandler.postDelayed(() -> {
            navIndex = 1;
            renderMailHub();
            rebuildBottomNav();
        }, 180);
    }

    private void fetchBootstrapThenAccounts(boolean render) {
        fetchBootstrapThenAccounts(render, true);
    }

    private void fetchBootstrapThenAccounts(boolean render, boolean visible) {
        if (bootstrapRefreshRunning && !render) return;
        bootstrapRefreshRunning = true;
        runAsync(visible ? "同步账户..." : null, () -> {
            try {
                JSONObject bootstrap = api.get("/api/sync/bootstrap");
                JSONObject local = api.get("/api/mailboxes");
                JSONObject external = api.get("/imap/api/accounts");
                parseAccounts(local, external);
                replaceKeywordRules(parseKeywordRuleList(bootstrap));
                try {
                    List<Models.KeywordRule> rules = parseKeywordRuleList(api.get("/api/keyword-rules"));
                    if (!rules.isEmpty()) replaceKeywordRules(rules);
                } catch (Exception ignored) {
                    // Older servers can rely on /api/sync/bootstrap; the UI still shows cached rules.
                }
                saveKeywordRuleCache();
                store.replaceAccounts(accounts);
                prefs.edit().putLong("last_bootstrap_refresh_at", System.currentTimeMillis()).apply();
                return "ok";
            } finally {
                bootstrapRefreshRunning = false;
            }
        }, result -> {
            if (render) renderAccounts();
            else if ("mailHub".equals(currentScreen)) renderMailHub();
            else if ("accounts".equals(currentScreen)) renderAccounts();
            else if ("list".equals(currentScreen) && "keyword".equals(selectedVirtualMode)) loadMails(true);
            checkNotifications();
        });
    }

    private void refreshBootstrapSilentlyIfDue(boolean force) {
        if (token.isEmpty()) return;
        long last = prefs.getLong("last_bootstrap_refresh_at", 0L);
        long now = System.currentTimeMillis();
        if (!force && now - last < BOOTSTRAP_REFRESH_COOLDOWN_MS) return;
        fetchBootstrapThenAccounts(false, false);
    }

    private void startForegroundRefreshLoop() {
        if (mainHandler == null || token.isEmpty() || foregroundRefreshLoopRunning) return;
        foregroundRefreshLoopRunning = true;
        mainHandler.removeCallbacks(foregroundRefreshTask);
        mainHandler.postDelayed(foregroundRefreshTask, FOREGROUND_REFRESH_MS);
    }

    private void stopForegroundRefreshLoop() {
        foregroundRefreshLoopRunning = false;
        if (mainHandler != null) mainHandler.removeCallbacks(foregroundRefreshTask);
    }

    private void parseAccounts(JSONObject local, JSONObject external) {
        accounts.clear();
        JSONArray localBoxes = Json.array(local, "mailboxes");
        for (int i = 0; i < localBoxes.length(); i++) {
            JSONObject item = localBoxes.optJSONObject(i);
            Models.Account account = new Models.Account();
            account.type = "local";
            account.id = Json.str(item, "address");
            account.email = account.id;
            account.name = Json.anyStr(item, "display_name", "displayName");
            account.group = Json.str(item, "group");
            accounts.add(account);
        }
        JSONArray externalArray = external.optJSONArray("accounts");
        if (externalArray == null) externalArray = external.optJSONArray("items");
        if (externalArray == null) externalArray = external.optJSONArray("data");
        if (externalArray == null) externalArray = new JSONArray();
        for (int i = 0; i < externalArray.length(); i++) addExternalAccount(externalArray.optJSONObject(i));
    }

    private List<Models.KeywordRule> parseKeywordRuleList(JSONObject source) {
        List<Models.KeywordRule> parsed = new ArrayList<>();
        JSONArray arr = Json.array(source, "keywordRules");
        if (arr.length() == 0) arr = Json.array(source, "keyword_rules");
        if (arr.length() == 0) arr = Json.array(source, "rules");
        if (arr.length() == 0) arr = Json.array(source, "items");
        for (int i = 0; i < arr.length(); i++) {
            JSONObject item = arr.optJSONObject(i);
            if (item == null) continue;
            Models.KeywordRule rule = new Models.KeywordRule();
            rule.id = Json.str(item, "id");
            rule.name = Json.str(item, "name");
            rule.scopeType = Json.anyStr(item, "scope_type", "scopeType");
            rule.scopeGroup = Json.anyStr(item, "scope_group", "scopeGroup");
            rule.scopeAccounts = jsonStringArray(Json.arrayAny(item, "scope_accounts", "scopeAccounts"));
            rule.matchMode = Json.anyStr(item, "match_mode", "matchMode");
            rule.enabled = item.optBoolean("enabled", true);
            rule.keywords = jsonStringArray(Json.array(item, "keywords"));
            rule.fields = jsonStringArray(Json.array(item, "fields"));
            if (rule.enabled && !rule.id.isEmpty() && !rule.name.isEmpty()) parsed.add(rule);
        }
        return parsed;
    }

    private void replaceKeywordRules(List<Models.KeywordRule> rules) {
        keywordRules.clear();
        if (rules != null) keywordRules.addAll(rules);
    }

    private void loadCachedKeywordRules() {
        try {
            String raw = prefs.getString("keyword_rules_cache", "[]");
            replaceKeywordRules(parseKeywordRuleList(new JSONObject().put("rules", new JSONArray(raw))));
        } catch (Exception ignored) {
            keywordRules.clear();
        }
    }

    private void saveKeywordRuleCache() {
        try {
            JSONArray arr = new JSONArray();
            for (Models.KeywordRule rule : keywordRules) {
                JSONObject item = new JSONObject()
                    .put("id", rule.id)
                    .put("name", rule.name)
                    .put("scopeType", rule.scopeType)
                    .put("scopeGroup", rule.scopeGroup)
                    .put("matchMode", rule.matchMode)
                    .put("enabled", rule.enabled);
                JSONArray keywords = new JSONArray();
                if (rule.keywords != null) for (String keyword : rule.keywords) keywords.put(keyword);
                JSONArray fields = new JSONArray();
                if (rule.fields != null) for (String field : rule.fields) fields.put(field);
                JSONArray scopeAccounts = new JSONArray();
                if (rule.scopeAccounts != null) for (String account : rule.scopeAccounts) scopeAccounts.put(account);
                item.put("keywords", keywords).put("fields", fields).put("scopeAccounts", scopeAccounts);
                arr.put(item);
            }
            prefs.edit().putString("keyword_rules_cache", arr.toString()).apply();
        } catch (Exception ignored) {
            // Cache is only a startup hint; failing to save it should not block mail usage.
        }
    }

    private String[] jsonStringArray(JSONArray arr) {
        String[] values = new String[arr.length()];
        for (int i = 0; i < arr.length(); i++) values[i] = arr.optString(i, "");
        return values;
    }

    private void addExternalAccount(JSONObject item) {
        if (item == null) return;
        Models.Account account = new Models.Account();
        account.type = "external";
        account.id = String.valueOf(item.opt("id"));
        account.email = Json.str(item, "email");
        account.name = Json.anyStr(item, "displayName", "display_name", "name");
        account.sendName = Json.anyStr(item, "sendName", "send_name");
        account.group = Json.str(item, "group");
        JSONObject sync = Json.obj(item, "syncStatus");
        account.unread = sync.optInt("unseen", 0);
        accounts.add(account);
    }

    private void renderAccounts() {
        currentScreen = "accounts";
        currentDetailMail = null;
        selectedVirtualMode = "";
        selectedGroup = "";
        selectedKeywordRule = null;
        setHeader("账户与分组", accounts.size() + " 个账号");
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        LinearLayout list = column(dp(8));
        list.setPadding(dp(12), dp(10), dp(12), dp(18));
        scroll.addView(list);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));
        list.addView(dashboardCard("账户工作台", accounts.size() + " 个账号", totalUnread() + " 封未读 · " + allGroups().size() + " 个分组"));
        Map<String, List<Models.Account>> grouped = new LinkedHashMap<>();
        for (Models.Account account : accounts) {
            String group = account.group == null || account.group.isEmpty() ? "未分组" : account.group;
            grouped.computeIfAbsent(group, k -> new ArrayList<>()).add(account);
        }
        for (String group : grouped.keySet()) {
            list.addView(sectionHeader(group, grouped.get(group).size() + " 个账号 · 未读 " + groupUnread(group)));
            for (Models.Account account : grouped.get(group)) list.addView(accountRow(account));
        }
        if (accounts.isEmpty()) list.addView(empty("暂无账户，请先在服务端添加邮箱账号。"));
    }

    private View accountRow(Models.Account account) {
        LinearLayout row = panel(dp(12), dp(11));
        row.setBackground(bg(Color.WHITE, 22, SOFT_LINE, 1));
        row.setOnClickListener(v -> {
            selectedAccount = account;
            loadFolders(account);
        });
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        TextView icon = avatarView(account.label() + account.email);
        top.addView(icon, new LinearLayout.LayoutParams(dp(46), dp(46)));
        LinearLayout names = new LinearLayout(this);
        names.setOrientation(LinearLayout.VERTICAL);
        names.setPadding(dp(12), 0, 0, 0);
        TextView name = text(account.label(), 16, TEXT, true);
        name.setSingleLine(true);
        TextView meta = text(account.email, 12, MUTED, false);
        meta.setSingleLine(true);
        names.addView(name);
        names.addView(meta);
        top.addView(names, new LinearLayout.LayoutParams(0, -2, 1));
        if (account.unread > 0) {
            TextView badge = badge(String.valueOf(account.unread), ACCENT, Color.WHITE);
            top.addView(badge);
        }
        row.addView(top);
        LinearLayout chips = new LinearLayout(this);
        chips.setOrientation(LinearLayout.HORIZONTAL);
        chips.setPadding(dp(58), dp(8), 0, 0);
        chips.addView(tinyChip("local".equals(account.type) ? "本地邮箱" : "外部邮箱"));
        chips.addView(tinyChip(nonEmpty(account.group, "未分组")));
        row.addView(chips);
        return row;
    }

    private void renderMailHub() {
        currentScreen = "mailHub";
        selectedAccount = null;
        selectedFolder = null;
        selectedVirtualMode = "";
        selectedGroup = "";
        selectedKeywordRule = null;
        setHeader("邮件", "所有账号和分组");
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        LinearLayout list = column(dp(8));
        list.setPadding(dp(12), dp(10), dp(12), dp(18));
        scroll.addView(list);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));
        list.addView(dashboardCard("邮件中心", totalUnread() + " 封未读", accounts.size() + " 个账号 · " + allGroups().size() + " 个分组"));
        list.addView(sectionHeader("全局邮箱", "跨账号聚合查看"));
        list.addView(hubRow("全部账号", "所有邮件", totalUnread(), "▦", v -> openVirtualMailbox("global_all", "")));
        list.addView(hubRow("未读邮件", "所有账号的未读消息", totalUnread(), "●", v -> openVirtualMailbox("global_unread", "")));
        Map<String, Integer> keywordCounts = keywordRuleCounts();
        int keywordTotal = 0;
        for (Integer value : keywordCounts.values()) keywordTotal += value == null ? 0 : value;
        list.addView(sectionHeader(
            "关键词监控",
            keywordRules.isEmpty() ? "暂无规则" : keywordRules.size() + " 条规则 · " + keywordTotal + " 封命中"
        ));
        if (keywordRules.isEmpty()) {
            list.addView(hubRow("暂无关键词规则", "请在服务端设置中新增关键词监控规则", 0, "⌁", v -> toast("请先在服务端设置关键词规则")));
        } else {
            for (Models.KeywordRule rule : keywordRules) {
                int count = keywordCounts.containsKey(rule.id) ? keywordCounts.get(rule.id) : 0;
                list.addView(hubRow(rule.name, nonEmpty(rule.keywordLine(), "命中关键词邮件"), count, "⚑", v -> openKeywordMailbox(rule), true));
            }
        }
        list.addView(sectionHeader("分组邮箱", "按业务分组查看"));
        for (String group : allGroups()) {
            int unread = groupUnread(group);
            list.addView(hubRow(group, "分组所有邮件", unread, "▣", v -> openVirtualMailbox("group_all", group)));
            list.addView(hubRow(group + " · 未读", "分组未读邮件", unread, "●", v -> openVirtualMailbox("group_unread", group)));
        }
        if (accounts.isEmpty()) list.addView(empty("暂无账户，请先在服务端添加邮箱账号。"));
    }

    private View hubRow(String titleText, String subText, int unread, String iconText, View.OnClickListener listener) {
        return hubRow(titleText, subText, unread, iconText, listener, false);
    }

    private View hubRow(String titleText, String subText, int unread, String iconText, View.OnClickListener listener, boolean alwaysShowBadge) {
        LinearLayout row = panel(dp(12), dp(12));
        row.setBackground(bg(Color.WHITE, 22, SOFT_LINE, 1));
        row.setOnClickListener(listener);
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        TextView icon = text(iconText, 19, PRIMARY, true);
        icon.setGravity(Gravity.CENTER);
        icon.setBackground(bg(PRIMARY_SOFT, 16, Color.TRANSPARENT, 0));
        top.addView(icon, new LinearLayout.LayoutParams(dp(42), dp(42)));
        LinearLayout names = new LinearLayout(this);
        names.setOrientation(LinearLayout.VERTICAL);
        names.setPadding(dp(12), 0, 0, 0);
        TextView titleView = text(titleText, 16, TEXT, true);
        titleView.setSingleLine(true);
        TextView subView = text(subText, 13, MUTED, false);
        subView.setSingleLine(true);
        names.addView(titleView);
        names.addView(subView);
        TextView badgeView = (unread > 0 || alwaysShowBadge)
            ? badge(String.valueOf(Math.max(0, unread)), unread > 0 ? ACCENT : Color.rgb(231, 238, 239), unread > 0 ? Color.WHITE : MUTED)
            : null;
        top.addView(names, new LinearLayout.LayoutParams(0, -2, 1));
        if (badgeView != null) top.addView(badgeView);
        row.addView(top);
        return row;
    }

    private void openVirtualMailbox(String mode, String group) {
        selectedVirtualMode = mode;
        selectedGroup = group == null ? "" : group;
        selectedKeywordRule = null;
        selectedAccount = null;
        selectedFolder = folder("virtual", "", mode, virtualTitle(mode, selectedGroup), 0);
        currentPage = 1;
        hasMore = false;
        searchQuery = "";
        loadMails(false);
    }

    private void openKeywordMailbox(Models.KeywordRule rule) {
        selectedKeywordRule = rule;
        selectedVirtualMode = "keyword";
        selectedGroup = "";
        selectedAccount = null;
        selectedFolder = folder("keyword", "", rule.id, rule.name, 0);
        currentPage = 1;
        hasMore = false;
        searchQuery = "";
        loadMails(false);
    }

    private void loadFolders(Models.Account account) {
        selectedAccount = account;
        selectedKeywordRule = null;
        selectedVirtualMode = "";
        selectedFolder = null;
        replaceFolderList(new ArrayList<>());
        searchQuery = "";
        currentPage = 1;
        hasMore = false;
        List<Models.Folder> cachedFolders = store.readFolders(account.type, account.id);
        if (!cachedFolders.isEmpty()) {
            replaceFolderList(cachedFolders);
            selectedFolder = pickDefaultFolder(account.id);
            renderFoldersOrMails();
            showCachedMailsIfAny();
        } else {
            replaceMailList(new ArrayList<>(), true);
        }
        if ("local".equals(account.type)) {
            List<Models.Folder> fixed = localFolders(account);
            replaceFolderList(fixed);
            store.replaceFolders(account, fixed);
            if (selectedFolder == null) selectedFolder = pickDefaultFolder(account.id);
            renderFoldersOrMails();
            loadMails(false);
            return;
        }
        runAsync("加载文件夹...", () -> {
            JSONArray arr = asArray(api.get("/imap/api/accounts/" + encode(account.id) + "/folders"));
            List<Models.Folder> loadedFolders = new ArrayList<>();
            loadedFolders.add(folder("external", account.id, "drafts", "草稿箱", 0));
            loadedFolders.add(folder("external", account.id, "outbox", "发送失败", 0));
            for (int i = 0; i < arr.length(); i++) {
                JSONObject item = arr.optJSONObject(i);
                String path = Json.anyStr(item, "path", "name");
                if (path.isEmpty()) continue;
                loadedFolders.add(folder("external", account.id, path, Json.anyStr(item, "name", "path"), 0));
            }
            store.replaceFolders(account, loadedFolders);
            return new JSONObject().put("folders", folderListToJson(loadedFolders));
        }, result -> {
            replaceFolderList(foldersFromJson(Json.array(result, "folders")));
            selectedFolder = pickDefaultFolder(account.id);
            renderFoldersOrMails();
            loadMails(false);
        });
    }

    private List<Models.Folder> localFolders(Models.Account account) {
        List<Models.Folder> fixed = new ArrayList<>();
        fixed.add(folder("local", account.id, "unread", "未读邮件", 0));
        fixed.add(folder("local", account.id, "inbox", "收件箱", 0));
        fixed.add(folder("local", account.id, "sent", "已发送", 0));
        fixed.add(folder("local", account.id, "drafts", "草稿箱", 0));
        fixed.add(folder("local", account.id, "outbox", "发送失败", 0));
        return fixed;
    }

    private Models.Folder pickDefaultFolder(String accountId) {
        List<Models.Folder> snapshot = folderSnapshot();
        for (Models.Folder folder : snapshot) {
            if ("INBOX".equalsIgnoreCase(folder.path)) return folder;
        }
        for (Models.Folder folder : snapshot) {
            if (!"drafts".equals(folder.path) && !"outbox".equals(folder.path)) return folder;
        }
        return snapshot.isEmpty() ? folder("external", accountId, "INBOX", "INBOX", 0) : snapshot.get(0);
    }

    private void renderFoldersOrMails() {
        currentScreen = "list";
        if (selectedAccount == null && selectedVirtualMode.isEmpty()) {
            renderMailHub();
            return;
        }
        setHeader(selectedAccount == null ? virtualTitle(selectedVirtualMode, selectedGroup) : selectedAccount.label(), selectedFolder == null ? "选择文件夹" : selectedFolder.name);
        content.removeAllViews();
        LinearLayout page = column(0);
        content.addView(page, new LinearLayout.LayoutParams(-1, -1));
        if (selectedAccount != null) page.addView(folderChips(), new LinearLayout.LayoutParams(-1, dp(48)));
        page.addView(searchBar(), new LinearLayout.LayoutParams(-1, dp(46)));
        LinearLayout list = column(0);
        list.setPadding(dp(8), 0, dp(8), dp(12));
        ScrollView scroll = new ScrollView(this);
        attachAutoLoadMore(scroll);
        scroll.addView(list);
        page.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        List<Models.Mail> mailRows = mailSnapshot();
        boolean showMore = hasMore;
        boolean loadingMore = mailPageLoading;
        if (mailRows.isEmpty()) {
            list.addView(empty("暂无邮件"));
        } else {
            for (Models.Mail mail : mailRows) list.addView(mailRow(mail));
            if (showMore) {
                TextView more = outlineButton(loadingMore ? "加载中..." : "继续加载", v -> loadNextMailPage());
                list.addView(more);
            }
        }
    }

    private void attachAutoLoadMore(ScrollView scroll) {
        if (scroll == null) return;
        scroll.getViewTreeObserver().addOnScrollChangedListener(() -> {
            if (!hasMore || mailPageLoading || !"list".equals(currentScreen)) return;
            View child = scroll.getChildAt(0);
            if (child == null) return;
            int remaining = child.getBottom() - (scroll.getHeight() + scroll.getScrollY());
            if (remaining <= dp(220)) loadNextMailPage();
        });
    }

    private void loadNextMailPage() {
        if (!hasMore || mailPageLoading) return;
        currentPage += 1;
        loadMails(true);
    }

    private LinearLayout searchBar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setPadding(dp(10), dp(4), dp(10), dp(4));
        bar.setBackgroundColor(Color.WHITE);
        EditText input = input("搜索发件人 / 主题 / 内容", searchQuery, false);
        input.setSingleLine(true);
        bar.addView(input, new LinearLayout.LayoutParams(0, -1, 1));
        TextView search = outlineButton("搜索", v -> {
            searchQuery = input.getText().toString().trim();
            currentPage = 1;
            loadMails(false);
        });
        bar.addView(search, new LinearLayout.LayoutParams(dp(60), -1));
        if (!searchQuery.isEmpty()) {
            TextView clear = outlineButton("清空", v -> {
            searchQuery = "";
            currentPage = 1;
            loadMails(false);
        });
            bar.addView(clear, new LinearLayout.LayoutParams(dp(54), -1));
        }
        return bar;
    }

    private LinearLayout listActions() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(0, 0, 0, dp(6));
        TextView refresh = outlineButton("刷新", v -> {
            currentPage = 1;
            loadMails(false);
        });
        bar.addView(refresh, new LinearLayout.LayoutParams(0, dp(38), 1));
        TextView sync = outlineButton(selectedAccount == null ? "刷新聚合" : "同步账号", v -> syncSelectedAccount());
        bar.addView(sync, new LinearLayout.LayoutParams(0, dp(38), 1));
        return bar;
    }

    private HorizontalScrollView folderChips() {
        HorizontalScrollView scroll = new HorizontalScrollView(this);
        scroll.setHorizontalScrollBarEnabled(false);
        LinearLayout chips = new LinearLayout(this);
        chips.setOrientation(LinearLayout.HORIZONTAL);
        chips.setPadding(dp(10), dp(7), dp(10), dp(7));
        chips.setBackgroundColor(Color.WHITE);
        scroll.addView(chips);
        List<Models.Folder> folderRows = folderSnapshot();
        for (Models.Folder folder : folderRows) {
            TextView chip = actionText(folder.name, folder == selectedFolder);
            chip.setOnClickListener(v -> {
                selectedFolder = folder;
                searchQuery = "";
                currentPage = 1;
                hasMore = false;
                loadMails(false);
            });
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-2, -1);
            lp.setMargins(dp(2), 0, dp(8), 0);
            chips.addView(chip, lp);
        }
        return scroll;
    }

    private void loadMails(boolean silent) {
        if ((selectedAccount == null && selectedVirtualMode.isEmpty()) || selectedFolder == null) return;
        final String requestKey = mailLoadKey();
        final Models.Account requestAccount = selectedAccount;
        final Models.Folder requestFolder = selectedFolder;
        final String requestSearchQuery = searchQuery;
        final String requestVirtualMode = selectedVirtualMode;
        final int requestPage = currentPage;
        final int requestPageSize = pageSize;
        final boolean requestIsVirtual = requestAccount == null && !requestVirtualMode.isEmpty();
        if (mailPageLoading && requestKey.equals(activeMailLoadKey)) return;
        mailPageLoading = true;
        activeMailLoadKey = requestKey;
        if (currentPage <= 1) {
            if (showCachedMailsIfAny()) {
                silent = true;
            } else {
                replaceMailList(new ArrayList<>(), true);
                hasMore = false;
                renderFoldersOrMails();
            }
        }
        boolean keepExistingKeyword = "keyword".equals(requestVirtualMode) && requestPage <= 1 && !mailSnapshot().isEmpty();
        runAsync(silent ? null : "加载邮件...", () -> {
            List<Models.Mail> next = new ArrayList<>();
            hasMore = false;
            if (requestIsVirtual) {
                next.addAll(loadVirtualMails());
            } else if ("drafts".equals(requestFolder.path)) {
                JSONObject data = api.get("/api/drafts?account_type=" + encode(requestAccount.type) + "&account_id=" + encode(requestAccount.id));
                JSONArray arr = Json.array(data, "drafts");
                for (int i = 0; i < arr.length(); i++) {
                    Models.Mail mail = Models.Mail.fromDraft(arr.optJSONObject(i));
                    if (matchesSearch(mail, requestSearchQuery)) next.add(mail);
                }
                hasMore = false;
            } else if ("outbox".equals(requestFolder.path)) {
                JSONObject data = api.get("/api/outbox?account_type=" + encode(requestAccount.type) + "&account_id=" + encode(requestAccount.id));
                JSONArray arr = Json.array(data, "messages");
                for (int i = 0; i < arr.length(); i++) {
                    Models.Mail mail = Models.Mail.fromOutbox(arr.optJSONObject(i));
                    if (matchesSearch(mail, requestSearchQuery)) next.add(mail);
                }
                hasMore = false;
            } else if ("local".equals(requestAccount.type)) {
                JSONObject data;
                if (!requestSearchQuery.isEmpty()) {
                    data = api.post("/api/inbox/search", new JSONObject()
                        .put("email", requestAccount.email)
                        .put("query", requestSearchQuery));
                    hasMore = false;
                } else {
                    int offset = Math.max(0, (requestPage - 1) * requestPageSize);
                    JSONObject body = new JSONObject()
                        .put("email", requestAccount.email)
                        .put("offset", offset)
                        .put("limit", requestPageSize)
                        .put("unread_only", "unread".equals(requestFolder.path));
                    data = "sent".equals(requestFolder.path)
                        ? api.post("/api/sent/query", body)
                        : api.post("/api/inbox/query", body);
                    int total = data.optInt("total", 0);
                    hasMore = offset + requestPageSize < total;
                }
                JSONArray arr = Json.array(data, "messages");
                for (int i = 0; i < arr.length(); i++) {
                    String cacheFolder = "unread".equals(requestFolder.path) ? "inbox" : requestFolder.path;
                    next.add(Models.Mail.fromLocal(arr.optJSONObject(i), requestAccount.id, cacheFolder));
                }
            } else {
                JSONObject data;
                if (!requestSearchQuery.isEmpty()) {
                    data = api.get("/imap/api/accounts/" + encode(requestAccount.id) + "/search?folder=" + encode(requestFolder.path) + "&q=" + encode(requestSearchQuery) + "&count=" + requestPageSize + "&offset=" + ((requestPage - 1) * requestPageSize));
                } else {
                    data = api.get("/imap/api/accounts/" + encode(requestAccount.id) + "/mails?folder=" + encode(requestFolder.path) + "&count=" + requestPageSize + "&page=" + requestPage + "&cacheOnly=1");
                }
                hasMore = data.optBoolean("hasMore", false) || requestPage * requestPageSize < data.optInt("total", 0);
                JSONArray arr = Json.array(data, "mails");
                for (int i = 0; i < arr.length(); i++) {
                    next.add(Models.Mail.fromExternal(arr.optJSONObject(i), requestAccount.id, requestFolder.path));
                }
            }
            store.upsertMails(next);
            return new JSONObject()
                .put("mails", mailListToJson(next))
                .put("keepCachedKeyword", keepExistingKeyword && next.isEmpty());
        }, result -> {
            boolean isLatestRequest = requestKey.equals(activeMailLoadKey);
            if (isLatestRequest) mailPageLoading = false;
            if (!isLatestRequest || !requestKey.equals(mailLoadKey())) return;
            if (!result.optBoolean("keepCachedKeyword", false)) {
                List<Models.Mail> renderedMails = mailsFromJson(Json.array(result, "mails"));
                replaceMailList(renderedMails, currentPage <= 1);
            } else {
                hasMore = false;
            }
            if (!"list".equals(currentScreen) || !requestKey.equals(mailLoadKey())) return;
            renderFoldersOrMails();
        });
    }

    private String mailLoadKey() {
        String accountKey = selectedAccount == null
            ? "virtual"
            : nonEmpty(selectedAccount.type, "") + ":" + nonEmpty(selectedAccount.id, "");
        String folderKey = selectedFolder == null
            ? ""
            : nonEmpty(selectedFolder.accountType, "") + ":" + nonEmpty(selectedFolder.path, "");
        String keywordKey = selectedKeywordRule == null ? "" : nonEmpty(selectedKeywordRule.id, "");
        return accountKey
            + "|" + folderKey
            + "|" + nonEmpty(selectedVirtualMode, "")
            + "|" + nonEmpty(selectedGroup, "")
            + "|" + keywordKey
            + "|" + nonEmpty(searchQuery, "")
            + "|" + currentPage
            + "|" + pageSize;
    }

    private boolean showCachedMailsIfAny() {
        List<Models.Mail> cached = cachedMails();
        if (cached.isEmpty()) return false;
        replaceMailList(cached, true);
        hasMore = cached.size() >= pageSize;
        renderFoldersOrMails();
        return true;
    }

    private List<Models.Mail> cachedMails() {
        int offset = Math.max(0, (currentPage - 1) * pageSize);
        if (selectedAccount == null && !selectedVirtualMode.isEmpty()) {
            if ("keyword".equals(selectedVirtualMode) && selectedKeywordRule != null) {
                List<Models.Mail> cached = store.readVirtualMails(scopedAccounts(), false, "", 500, 0);
                List<Models.Mail> matched = filterKeywordMails(cached, selectedKeywordRule, searchQuery);
                hasMore = offset + pageSize < matched.size();
                return slice(matched, offset, pageSize);
            }
            return store.readVirtualMails(scopedAccounts(), selectedVirtualMode.endsWith("_unread"), searchQuery, pageSize, offset);
        }
        if (selectedAccount == null || selectedFolder == null) return new ArrayList<>();
        boolean unreadOnly = "unread".equals(selectedFolder.path);
        return store.readMails(selectedAccount.type, selectedAccount.id, selectedFolder.path, searchQuery, pageSize, offset, unreadOnly);
    }

    private List<Models.Mail> loadVirtualMails() throws Exception {
        List<Models.Mail> merged = new ArrayList<>();
        if ("keyword".equals(selectedVirtualMode) && selectedKeywordRule != null) {
            List<Models.Mail> source = loadKeywordSourceMails();
            List<Models.Mail> matched = filterKeywordMails(source, selectedKeywordRule, searchQuery);
            int offset = Math.max(0, (currentPage - 1) * pageSize);
            hasMore = offset + pageSize < matched.size();
            return slice(matched, offset, pageSize);
        }
        boolean unreadOnly = selectedVirtualMode.endsWith("_unread");
        List<Models.Account> scoped = scopedAccounts();
        int offset = Math.max(0, (currentPage - 1) * pageSize);
        List<String> externalIds = new ArrayList<>();
        for (Models.Account account : scoped) {
            if ("external".equals(account.type)) externalIds.add(account.id);
        }
        if (!externalIds.isEmpty()) {
            String joined = join(externalIds, ",");
            JSONObject data = !searchQuery.isEmpty()
                ? api.get("/imap/api/search?accountIds=" + encode(joined) + "&q=" + encode(searchQuery) + "&count=" + pageSize + "&unread=" + unreadOnly)
                : api.get("/imap/api/mails?accountIds=" + encode(joined) + "&count=" + pageSize + "&offset=" + offset + "&unread=" + unreadOnly);
            hasMore = data.optBoolean("hasMore", false) || hasMore;
            JSONArray arr = Json.array(data, "mails");
            for (int i = 0; i < arr.length(); i++) {
                JSONObject item = arr.optJSONObject(i);
                Models.Mail mail = Models.Mail.fromExternal(item, Json.anyStr(item, "accountId", "account_id"), Json.anyStr(item, "folder", "folderName"));
                mail.accountType = "external";
                if (matchesSearch(mail)) merged.add(mail);
            }
        }
        for (Models.Account account : scoped) {
            if (!"local".equals(account.type)) continue;
            JSONObject body = new JSONObject()
                .put("email", account.email)
                .put("offset", offset)
                .put("limit", pageSize)
                .put("unread_only", unreadOnly);
            JSONObject data = !searchQuery.isEmpty()
                ? api.post("/api/inbox/search", new JSONObject().put("email", account.email).put("query", searchQuery))
                : api.post("/api/inbox/query", body);
            hasMore = hasMore || offset + pageSize < data.optInt("total", 0);
            JSONArray arr = Json.array(data, "messages");
            for (int i = 0; i < arr.length(); i++) {
                Models.Mail mail = Models.Mail.fromLocal(arr.optJSONObject(i), account.id, "inbox");
                if (!unreadOnly || !mail.seen) merged.add(mail);
            }
        }
        merged.sort((a, b) -> nonEmpty(b.date, "").compareTo(nonEmpty(a.date, "")));
        return merged.size() > pageSize ? new ArrayList<>(merged.subList(0, pageSize)) : merged;
    }

    private List<Models.Mail> loadKeywordSourceMails() throws Exception {
        Map<String, Models.Mail> sourceMap = new LinkedHashMap<>();
        List<Models.Account> scoped = scopedAccounts();
        for (Models.Mail mail : store.readVirtualMails(scoped, false, "", KEYWORD_SCAN_LIMIT * 2, 0)) {
            sourceMap.put(mailStableKey(mail), mail);
        }
        List<String> externalIds = new ArrayList<>();
        for (Models.Account account : scoped) {
            if ("external".equals(account.type)) externalIds.add(account.id);
        }
        if (!externalIds.isEmpty()) {
            JSONObject data = api.get("/imap/api/mails?accountIds=" + encode(join(externalIds, ",")) + "&count=" + KEYWORD_SCAN_LIMIT + "&offset=0&cacheOnly=1");
            JSONArray arr = Json.array(data, "mails");
            for (int i = 0; i < arr.length(); i++) {
                JSONObject item = arr.optJSONObject(i);
                Models.Mail mail = Models.Mail.fromExternal(item, Json.anyStr(item, "accountId", "account_id"), Json.anyStr(item, "folder", "folderName"));
                mail.accountType = "external";
                sourceMap.put(mailStableKey(mail), mail);
            }
        }
        for (Models.Account account : scoped) {
            if (!"local".equals(account.type)) continue;
            JSONObject data = api.post("/api/inbox/query", new JSONObject()
                .put("email", account.email)
                .put("offset", 0)
                .put("limit", KEYWORD_SCAN_LIMIT)
                .put("unread_only", false));
            JSONArray arr = Json.array(data, "messages");
            for (int i = 0; i < arr.length(); i++) {
                Models.Mail mail = Models.Mail.fromLocal(arr.optJSONObject(i), account.id, "inbox");
                sourceMap.put(mailStableKey(mail), mail);
            }
        }
        List<Models.Mail> source = new ArrayList<>(sourceMap.values());
        store.upsertMails(source);
        return source;
    }

    private List<Models.Mail> filterKeywordMails(List<Models.Mail> source, Models.KeywordRule rule, String extraQuery) {
        List<Models.Mail> matched = new ArrayList<>();
        for (Models.Mail mail : source) if (rule.matches(mail, extraQuery)) matched.add(mail);
        matched.sort((a, b) -> nonEmpty(b.date, "").compareTo(nonEmpty(a.date, "")));
        return matched;
    }

    private Map<String, Integer> keywordRuleCounts() {
        Map<String, Integer> counts = new LinkedHashMap<>();
        if (keywordRules.isEmpty() || accounts.isEmpty()) return counts;
        List<Models.Mail> source = store.readVirtualMails(accounts, false, "", KEYWORD_SCAN_LIMIT * 3, 0);
        for (Models.KeywordRule rule : keywordRules) {
            int count = 0;
            for (Models.Mail mail : source) {
                if (keywordRuleIncludesMail(rule, mail) && rule.matches(mail, "")) count++;
            }
            counts.put(rule.id, count);
        }
        return counts;
    }

    private boolean keywordRuleIncludesMail(Models.KeywordRule rule, Models.Mail mail) {
        if (rule == null || mail == null) return false;
        Models.Account account = findMailAccountStrict(mail);
        if (account != null) return keywordRuleIncludesAccount(rule, account);
        if ("group".equals(rule.scopeType)) return false;
        if ("accounts".equals(rule.scopeType)) {
            if (rule.scopeAccounts == null || rule.scopeAccounts.length == 0) return true;
            for (String scopeAccount : rule.scopeAccounts) {
                if (sameId(scopeAccount, mail.accountId)) return true;
            }
            return false;
        }
        return true;
    }

    private List<Models.Mail> slice(List<Models.Mail> source, int offset, int limit) {
        if (source == null || source.isEmpty() || offset >= source.size()) return new ArrayList<>();
        return new ArrayList<>(source.subList(Math.max(0, offset), Math.min(source.size(), Math.max(0, offset) + Math.max(1, limit))));
    }

    private List<Models.Account> scopedAccounts() {
        List<Models.Account> scoped = new ArrayList<>();
        for (Models.Account account : accounts) {
            if (selectedVirtualMode.startsWith("group_")) {
                String group = account.group == null || account.group.isEmpty() ? "未分组" : account.group;
                if (!group.equals(selectedGroup)) continue;
            }
            if ("keyword".equals(selectedVirtualMode) && selectedKeywordRule != null && !keywordRuleIncludesAccount(selectedKeywordRule, account)) continue;
            scoped.add(account);
        }
        return scoped;
    }

    private boolean keywordRuleIncludesAccount(Models.KeywordRule rule, Models.Account account) {
        if (rule == null || account == null) return false;
        if ("group".equals(rule.scopeType)) {
            String group = account.group == null || account.group.isEmpty() ? "未分组" : account.group;
            return group.equals(nonEmpty(rule.scopeGroup, "未分组"));
        }
        if ("accounts".equals(rule.scopeType)) {
            if (rule.scopeAccounts == null || rule.scopeAccounts.length == 0) return true;
            for (String scopeAccount : rule.scopeAccounts) {
                if (sameId(scopeAccount, account.id) || sameId(scopeAccount, account.email)) return true;
            }
            return false;
        }
        return true;
    }

    private String mailStableKey(Models.Mail mail) {
        if (mail == null) return "";
        return nonEmpty(mail.accountType, "")
            + "|" + nonEmpty(mail.accountId, "")
            + "|" + nonEmpty(mail.folder, "")
            + "|" + nonEmpty(mail.id, "")
            + "|" + nonEmpty(mail.subject, "")
            + "|" + nonEmpty(mail.date, "");
    }

    private String mailActionKey(Models.Mail mail) {
        if (mail == null) return "";
        return nonEmpty(mail.accountType, "")
            + "|" + nonEmpty(mail.accountId, "")
            + "|" + nonEmpty(mail.folder, "")
            + "|" + nonEmpty(mail.id, "");
    }

    private boolean matchesSearch(Models.Mail mail) {
        return matchesSearch(mail, searchQuery);
    }

    private boolean matchesSearch(Models.Mail mail, String query) {
        if (query == null || query.isEmpty()) return true;
        String q = query.toLowerCase();
        String blob = (nonEmpty(mail.sender, "") + " " + nonEmpty(mail.to, "") + " " + nonEmpty(mail.subject, "") + " " + nonEmpty(mail.preview, "")).toLowerCase();
        return blob.contains(q);
    }

    private String mailAccountLine(Models.Mail mail) {
        Models.Account account = findMailAccount(mail);
        String accountName = account == null ? nonEmpty(mail.accountId, "未知邮箱") : account.label();
        String folderName = mobileFolderName(mail.folder);
        if (folderName.isEmpty()) return accountName;
        return accountName + "  ·  " + folderName;
    }

    private Models.Account findMailAccount(Models.Mail mail) {
        Models.Account account = findMailAccountStrict(mail);
        return account == null ? selectedAccount : account;
    }

    private Models.Account findMailAccountStrict(Models.Mail mail) {
        if (mail == null) return null;
        String type = nonEmpty(mail.accountType, "");
        String id = nonEmpty(mail.accountId, "");
        for (Models.Account account : accounts) {
            if (!type.isEmpty() && !type.equals(account.type)) continue;
            if (sameId(id, account.id) || sameId(id, account.email)) return account;
        }
        for (Models.Account account : accounts) {
            if (sameId(id, account.id) || sameId(id, account.email)) return account;
        }
        return null;
    }

    private boolean sameId(String left, String right) {
        if (left == null || right == null) return false;
        String a = left.trim();
        String b = right.trim();
        return !a.isEmpty() && a.equalsIgnoreCase(b);
    }

    private String mobileFolderName(String folder) {
        String value = nonEmpty(folder, "").trim();
        if (value.isEmpty()) return "";
        if ("inbox".equalsIgnoreCase(value)) return "收件箱";
        if ("sent".equalsIgnoreCase(value) || "sent messages".equalsIgnoreCase(value)) return "已发送";
        if ("drafts".equalsIgnoreCase(value)) return "草稿箱";
        if ("junk".equalsIgnoreCase(value) || "spam".equalsIgnoreCase(value)) return "垃圾邮件";
        if ("trash".equalsIgnoreCase(value) || "deleted messages".equalsIgnoreCase(value)) return "已删除";
        if ("archive".equalsIgnoreCase(value)) return "归档";
        if ("unread".equalsIgnoreCase(value)) return "未读";
        if ("outbox".equalsIgnoreCase(value)) return "发送失败";
        return value;
    }

    private void syncSelectedAccount() {
        if (selectedAccount == null) {
            currentPage = 1;
            hasMore = false;
            loadMails(false);
            return;
        }
        if ("external".equals(selectedAccount.type)) {
        runAsync("同步账号...", () -> api.post("/imap/api/accounts/" + encode(selectedAccount.id) + "/sync",
            new JSONObject().put("force", true).put("folder", selectedFolder == null ? "INBOX" : selectedFolder.path)), result -> {
                currentPage = 1;
                loadFolders(selectedAccount);
                toast("同步已触发");
            });
            return;
        }
        fetchBootstrapThenAccounts(false, false);
        currentPage = 1;
        loadMails(false);
    }

    private void handleServerEvent(JSONObject event) {
        int seq = event.optInt("seq", 0);
        if (seq > 0) prefs.edit().putInt("sync_seq", seq).apply();
        String type = Json.str(event, "type");
        if (type.startsWith("mail.") || type.startsWith("draft.") || type.startsWith("outbox.") || type.startsWith("message.") || type.startsWith("keyword_rule.")) {
            scheduleServerEventRefresh();
        }
    }

    private void scheduleServerEventRefresh() {
        if (mainHandler == null) return;
        mainHandler.removeCallbacks(serverEventRefreshTask);
        mainHandler.postDelayed(serverEventRefreshTask, SERVER_EVENT_REFRESH_DEBOUNCE_MS);
    }

    private void startEventStream() {
        if (token.isEmpty() || eventStreamRunning) return;
        eventStreamRunning = true;
        eventThread = new Thread(this::eventStreamLoop, "memail-mobile-events");
        eventThread.start();
    }

    private void stopEventStream() {
        eventStreamRunning = false;
        HttpURLConnection conn = eventConnection;
        if (conn != null) conn.disconnect();
        if (eventThread != null) eventThread.interrupt();
        eventThread = null;
    }

    private void eventStreamLoop() {
        while (eventStreamRunning && !token.isEmpty()) {
            HttpURLConnection conn = null;
            try {
                int since = prefs.getInt("sync_seq", 0);
                URL url = new URL(api.baseUrl() + "/api/mobile/events?since=" + since);
                conn = (HttpURLConnection) url.openConnection();
                eventConnection = conn;
                conn.setConnectTimeout(12000);
                conn.setReadTimeout(EVENT_READ_TIMEOUT_MS);
                conn.setRequestProperty("Accept", "text/event-stream");
                conn.setRequestProperty("Authorization", "Bearer " + api.token());
                int code = conn.getResponseCode();
                if (code >= 400) throw new Exception("events HTTP " + code);
                readEventStream(conn.getInputStream());
            } catch (Exception ignored) {
                sleepQuietly(EVENT_RECONNECT_MS);
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
            while (eventStreamRunning && (line = reader.readLine()) != null) {
                if (line.isEmpty()) {
                    if (data.length() > 0) {
                        String raw = data.toString();
                        data.setLength(0);
                        JSONObject event = new JSONObject(raw);
                        runOnUiThread(() -> handleServerEvent(event));
                    }
                    continue;
                }
                if (line.startsWith("data:")) data.append(line.substring(5).trim());
            }
        }
    }

    private View mailRow(Models.Mail mail) {
        LinearLayout shell = new LinearLayout(this);
        shell.setOrientation(LinearLayout.HORIZONTAL);
        shell.setGravity(Gravity.TOP);
        shell.setPadding(dp(8), dp(9), dp(8), 0);
        shell.setBackgroundColor(Color.WHITE);
        LinearLayout.LayoutParams shellLp = new LinearLayout.LayoutParams(-1, -2);
        shellLp.setMargins(0, 0, 0, 0);
        shell.setLayoutParams(shellLp);

        TextView avatar = avatarView(mail.sender);
        shell.addView(avatar, new LinearLayout.LayoutParams(dp(44), dp(44)));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.VERTICAL);
        row.setPadding(dp(9), 0, 0, 0);
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout senderWrap = new LinearLayout(this);
        senderWrap.setOrientation(LinearLayout.HORIZONTAL);
        senderWrap.setGravity(Gravity.CENTER_VERTICAL);
        if (!mail.seen) {
            TextView dot = text("●", 12, Color.rgb(59, 159, 225), true);
            senderWrap.addView(dot, new LinearLayout.LayoutParams(dp(16), -2));
        }
        TextView sender = text((mail.favorite ? "★ " : "") + nonEmpty(mail.sender, "未知发件人"), 17, TEXT, !mail.seen);
        sender.setSingleLine(true);
        senderWrap.addView(sender, new LinearLayout.LayoutParams(0, -2, 1));
        TextView date = text(shortMailDate(mail.date), 12, MUTED, false);
        date.setGravity(Gravity.RIGHT);
        top.addView(senderWrap, new LinearLayout.LayoutParams(0, -2, 1));
        top.addView(date, new LinearLayout.LayoutParams(dp(78), -2));
        TextView subject = text(nonEmpty(mail.subject, "无主题"), 16, TEXT, !mail.seen);
        subject.setPadding(0, dp(3), 0, dp(2));
        subject.setMaxLines(1);
        TextView account = text(mailAccountLine(mail), 12, PRIMARY_DARK, false);
        account.setSingleLine(true);
        account.setPadding(0, 0, 0, dp(3));
        TextView preview = text(cleanPreview(mail.preview), 14, Color.rgb(135, 143, 148), false);
        preview.setPadding(0, 0, 0, dp(8));
        preview.setMaxLines(2);
        row.addView(top);
        row.addView(subject);
        row.addView(account);
        if (!mail.preview.isEmpty()) row.addView(preview);
        View divider = new View(this);
        divider.setBackgroundColor(Color.rgb(238, 241, 242));
        row.addView(divider, new LinearLayout.LayoutParams(-1, 1));
        shell.addView(row, new LinearLayout.LayoutParams(0, -2, 1));
        shell.setOnClickListener(v -> loadDetail(mail));
        return shell;
    }

    private void loadDetail(Models.Mail mail) {
        activeTranslationLoadKey = "";
        activeDetailLoadKey = mailActionKey(mail);
        if ("draft".equals(mail.kind) || "outbox".equals(mail.kind)) {
            renderLocalMessageDetail(mail);
            return;
        }
        markReadOnOpen(mail);
        Models.Mail cached = store.readMailDetail(mail);
        Models.Mail display = cached == null ? mail : cached;
        display.seen = true;
        renderLocalMessageDetail(display);
        if (!hasFullBody(display)) {
            fetchAndCacheDetail(mail, true, activeDetailLoadKey);
            return;
        }
        refreshDetailIfStale(mail, activeDetailLoadKey);
    }

    private void fetchAndCacheDetail(Models.Mail mail, boolean silent, String requestKey) {
        runAsync(silent ? null : "加载正文...", () -> fetchDetailDataWithRetry(mail), data -> {
            Models.Mail merged = mergeDetailIntoMail(mail, data);
            store.upsertMailDetail(merged);
            if (!requestKey.equals(activeDetailLoadKey) || !sameMail(currentDetailMail, mail)) return;
            if (!"detail".equals(currentScreen)) return;
            renderDetail(merged, data);
        });
    }

    private void fetchAndCacheDetail(Models.Mail mail, boolean silent) {
        fetchAndCacheDetail(mail, silent, mailActionKey(mail));
    }

    private void refreshDetailIfStale(Models.Mail mail, String requestKey) {
        io.submit(() -> {
            try {
                JSONObject data = fetchDetailData(mail);
                Models.Mail merged = mergeDetailIntoMail(mail, data);
                store.upsertMailDetail(merged);
                runOnUiThread(() -> {
                    if (!requestKey.equals(activeDetailLoadKey) || !sameMail(currentDetailMail, mail)) return;
                    if (!"detail".equals(currentScreen)) return;
                    renderDetail(merged, data);
                });
            } catch (Exception ignored) {
                // The cached local body is already rendered; refresh failures must not block reading.
            }
        });
    }

    private JSONObject fetchDetailData(Models.Mail mail) throws Exception {
        if ("external".equals(mail.accountType)) {
            return api.get("/imap/api/accounts/" + encode(mail.accountId) + "/mails/" + encode(mail.id) + "?folder=" + encode(mail.folder) + "&markSeen=1");
        }
        if ("sent".equals(mail.folder)) {
            return api.post("/api/sent/detail", new JSONObject().put("email", mail.accountId).put("message_id", mail.id));
        }
        return api.post("/api/inbox/detail", new JSONObject().put("email", mail.accountId).put("message_id", mail.id));
    }

    private JSONObject fetchDetailDataWithRetry(Models.Mail mail) throws Exception {
        Exception last = null;
        for (int attempt = 1; attempt <= DETAIL_FETCH_MAX_ATTEMPTS; attempt++) {
            try {
                return fetchDetailData(mail);
            } catch (Exception e) {
                last = e;
                if (attempt >= DETAIL_FETCH_MAX_ATTEMPTS) break;
                try {
                    Thread.sleep(DETAIL_FETCH_RETRY_DELAY_MS * attempt);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    throw interrupted;
                }
            }
        }
        throw last == null ? new Exception("邮件正文读取失败") : last;
    }

    private void renderLocalMessageDetail(Models.Mail mail) {
        JSONObject data = new JSONObject();
        try {
            data.put("html", mail.html);
            data.put("text", mail.text);
            data.put("to", mail.to);
            data.put("error", mail.error);
        } catch (Exception ignored) {
            // JSONObject only fails for invalid numeric values; string mail fields are safe here.
        }
        renderDetail(mail, data);
    }

    private Models.Mail mergeDetailIntoMail(Models.Mail base, JSONObject data) {
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
        mail.seen = true;
        mail.favorite = base.favorite || source.optBoolean("flagged", false) || Json.obj(source, "meta").optBoolean("favorite", false);
        return mail;
    }

    private boolean sameMail(Models.Mail a, Models.Mail b) {
        return a != null && b != null
            && nonEmpty(a.accountType, "").equals(nonEmpty(b.accountType, ""))
            && nonEmpty(a.accountId, "").equals(nonEmpty(b.accountId, ""))
            && nonEmpty(a.folder, "").equals(nonEmpty(b.folder, ""))
            && nonEmpty(a.id, "").equals(nonEmpty(b.id, ""));
    }

    private void markReadOnOpen(Models.Mail mail) {
        if (mail == null || mail.seen || "sent".equals(mail.folder)) return;
        mail.seen = true;
        for (Models.Mail item : mails) {
            if (sameMail(item, mail)) item.seen = true;
        }
        decrementUnreadFor(mail);
        List<Models.Mail> changed = new ArrayList<>();
        changed.add(mail);
        store.upsertMails(changed);
        io.submit(() -> {
            for (int attempt = 1; attempt <= 3; attempt++) {
                try {
                    if ("external".equals(mail.accountType)) {
                        api.put("/imap/api/accounts/" + encode(mail.accountId) + "/mails/" + encode(mail.id) + "/flags?folder=" + encode(mail.folder),
                            new JSONObject().put("action", "add").put("flags", new JSONArray().put("\\Seen")));
                    } else {
                        api.post("/api/inbox/mark", new JSONObject()
                            .put("email", mail.accountId)
                            .put("message_id", mail.id)
                            .put("seen", true));
                    }
                    return;
                } catch (Exception ignored) {
                    try {
                        Thread.sleep(700L * attempt);
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                }
            }
        });
    }

    private void decrementUnreadFor(Models.Mail mail) {
        for (Models.Account account : accounts) {
            if (nonEmpty(account.type, "").equals(nonEmpty(mail.accountType, ""))
                && nonEmpty(account.id, "").equals(nonEmpty(mail.accountId, ""))
                && account.unread > 0) {
                account.unread--;
                break;
            }
        }
    }

    private void renderDetail(Models.Mail mail, JSONObject data) {
        currentScreen = "detail";
        currentDetailMail = mail;
        activeDetailLoadKey = mailActionKey(mail);
        setHeader("邮件详情", mail.sender);
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(BG);
        LinearLayout box = column(dp(10));
        box.setPadding(dp(14), dp(10), dp(14), dp(18));
        scroll.addView(box);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));

        JSONObject detail = data.optJSONObject("detail");
        JSONObject source = detail == null ? data : detail;
        currentDetailSource = source;
        String html = Json.str(source, "html");
        String text = nonEmpty(Json.str(source, "text"), nonEmpty(mail.text, mail.preview));
        String senderText = nonEmpty(mail.sender, Json.anyStr(source, "from", "from_address"));
        String dateText = nonEmpty(mail.date, Json.anyStr(source, "date", "createdAt", "created_at"));
        String toText = Json.anyStr(source, "to", "to_address");

        box.addView(detailHeaderCard(mail, senderText, toText, dateText));
        box.addView(detailActionStrip(mail, source));

        LinearLayout bodyCard = panel(dp(0), dp(0));
        bodyCard.setBackground(bg(Color.WHITE, 22, SOFT_LINE, 1));
        TextView bodyLabel = sectionLabel("正文");
        bodyLabel.setPadding(dp(18), dp(16), dp(18), dp(8));
        bodyCard.addView(bodyLabel);
        WebView web = mailWebView();
        String body = wrapMailHtml(html.isEmpty() ? plainTextHtml(text) : html);
        web.loadDataWithBaseURL(api.baseUrl(), body, "text/html", "UTF-8", null);
        bodyCard.addView(web, new LinearLayout.LayoutParams(-1, dp(760)));
        box.addView(bodyCard);
    }

    private View detailHeaderCard(Models.Mail mail, String senderText, String toText, String dateText) {
        LinearLayout card = panel(dp(16), dp(16));
        card.setBackground(bg(Color.WHITE, 24, SOFT_LINE, 1));
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        TextView avatar = avatarView(senderText);
        top.addView(avatar, new LinearLayout.LayoutParams(dp(48), dp(48)));

        LinearLayout names = new LinearLayout(this);
        names.setOrientation(LinearLayout.VERTICAL);
        names.setPadding(dp(12), 0, 0, 0);
        TextView sender = text(nonEmpty(senderText, "未知发件人"), 16, TEXT, true);
        sender.setMaxLines(2);
        TextView date = text(shortDate(dateText), 12, MUTED, false);
        date.setPadding(0, dp(3), 0, 0);
        names.addView(sender);
        names.addView(date);
        top.addView(names, new LinearLayout.LayoutParams(0, -2, 1));
        if (!mail.seen) top.addView(badge("未读", Color.rgb(59, 159, 225), Color.WHITE));
        card.addView(top);

        TextView subject = text(nonEmpty(mail.subject, "无主题"), 22, TEXT, true);
        subject.setLineSpacing(dp(4), 1.0f);
        subject.setPadding(0, dp(18), 0, dp(10));
        card.addView(subject);
        if (!toText.isEmpty()) {
            TextView to = text("收件人：" + toText, 13, MUTED, false);
            to.setMaxLines(2);
            card.addView(to);
        }
        return card;
    }

    private View detailActionStrip(Models.Mail mail, JSONObject source) {
        return detailActionStrip(mail, source, false);
    }

    private View detailActionStrip(Models.Mail mail, JSONObject source, boolean translationMode) {
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.VERTICAL);
        actions.setPadding(dp(2), dp(2), dp(2), dp(2));
        List<TextView> buttons = new ArrayList<>();
        if (translationMode) {
            buttons.add(detailAction("原", "查看原文", v -> showOriginalMail(mail, source)));
        }
        if ("draft".equals(mail.kind)) {
            buttons.add(detailAction("✎", "继续编辑", v -> {
                composeSender = findMailAccount(mail);
                renderComposeEditor(mail.to, mail.subject, mail.text, mail.id);
            }));
            buttons.add(detailAction("×", "删除草稿", v -> deleteDraft(mail)));
        } else if ("outbox".equals(mail.kind)) {
            buttons.add(detailAction("↻", "重试", v -> retryOutbox(mail)));
            buttons.add(detailAction("✎", "编辑再发", v -> {
                composeSender = findMailAccount(mail);
                renderComposeEditor(mail.to, mail.subject, mail.text, "");
            }));
            buttons.add(detailAction("×", "删除记录", v -> deleteOutbox(mail)));
        } else if (!"sent".equals(mail.folder)) {
            buttons.add(detailAction(mail.seen ? "○" : "✓", mail.seen ? "标未读" : "标已读", v -> toggleSeen(mail)));
            buttons.add(detailAction(mail.favorite ? "★" : "☆", mail.favorite ? "取消星标" : "星标", v -> toggleFavorite(mail)));
        }
        if (!"draft".equals(mail.kind) && !"outbox".equals(mail.kind)) {
            if (!translationMode) buttons.add(detailAction("译", "翻译", v -> translateMail(mail)));
            buttons.add(detailAction("⧉", "复制", v -> copyMailContent(mail)));
            buttons.add(detailAction("↩", "回复", v -> renderComposeFor(mail, "回复：" + mail.subject)));
            buttons.add(detailAction("↪", "转发", v -> renderComposeFor(mail, "转发：" + mail.subject)));
            if (!"sent".equals(mail.folder)) buttons.add(detailAction("×", "删除", v -> confirmDelete(mail)));
        }
        LinearLayout row = null;
        for (int i = 0; i < buttons.size(); i++) {
            if (i % 3 == 0) {
                row = new LinearLayout(this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                row.setGravity(Gravity.START);
                actions.addView(row, new LinearLayout.LayoutParams(-1, -2));
            }
            row.addView(buttons.get(i), new LinearLayout.LayoutParams(0, dp(42), 1));
        }
        return actions;
    }

    private void showOriginalMail(Models.Mail mail, JSONObject source) {
        Models.Mail cachedMail = store.readMailDetail(mail);
        Models.Mail sourceMail = cachedMail == null ? mail : cachedMail;
        JSONObject data = mailToSourceJson(sourceMail);
        try {
            String sourceHtml = Json.str(source, "html");
            String sourceText = Json.str(source, "text");
            if (!sourceHtml.isEmpty()) data.put("html", sourceHtml);
            if (!sourceText.isEmpty()) data.put("text", sourceText);
        } catch (Exception ignored) {
        }
        renderDetail(sourceMail, data);
    }

    private void toggleSeen(Models.Mail mail) {
        boolean target = !mail.seen;
        runAsync("处理中...", () -> {
            if ("external".equals(mail.accountType)) {
                return api.put("/imap/api/accounts/" + encode(mail.accountId) + "/mails/" + encode(mail.id) + "/flags?folder=" + encode(mail.folder),
                    new JSONObject().put("action", target ? "add" : "remove").put("flags", new JSONArray().put("\\Seen")));
            }
            return api.post("/api/inbox/mark", new JSONObject()
                .put("email", mail.accountId)
                .put("message_id", mail.id)
                .put("seen", target));
        }, result -> {
            mail.seen = target;
            List<Models.Mail> changed = new ArrayList<>();
            changed.add(mail);
            store.upsertMails(changed);
            toast(target ? "已标记为已读" : "已标记为未读");
            renderFoldersOrMails();
        });
    }

    private void toggleFavorite(Models.Mail mail) {
        boolean target = !mail.favorite;
        runAsync("处理中...", () -> {
            if ("external".equals(mail.accountType)) {
                return api.put("/imap/api/accounts/" + encode(mail.accountId) + "/mails/" + encode(mail.id) + "/flags?folder=" + encode(mail.folder),
                    new JSONObject().put("action", target ? "add" : "remove").put("flags", new JSONArray().put("\\Flagged")));
            }
            return api.post("/api/message-meta", new JSONObject()
                .put("account_type", mail.accountType)
                .put("account_id", mail.accountId)
                .put("folder", mail.folder)
                .put("message_id", mail.id)
                .put("meta", new JSONObject().put("favorite", target)));
        }, result -> {
            mail.favorite = target;
            List<Models.Mail> changed = new ArrayList<>();
            changed.add(mail);
            store.upsertMails(changed);
            toast(target ? "已收藏" : "已取消收藏");
            renderFoldersOrMails();
        });
    }

    private void translateMail(Models.Mail mail) {
        String requestKey = mailActionKey(mail) + "|translate|" + System.nanoTime();
        activeTranslationLoadKey = requestKey;
        Models.Mail cachedMail = store.readMailDetail(mail);
        Models.Mail sourceMail = cachedMail == null ? mail : cachedMail;
        if (!hasFullBody(sourceMail)) {
            runAsync("加载正文后翻译...", () -> {
                JSONObject detail = fetchDetailDataWithRetry(mail);
                Models.Mail merged = mergeDetailIntoMail(mail, detail);
                store.upsertMailDetail(merged);
                JSONObject source = detailSource(detail);
                return new JSONObject()
                    .put("source", source)
                    .put("mail", mailToJson(merged));
            }, result -> {
                if (!requestKey.equals(activeTranslationLoadKey) || !sameMail(currentDetailMail, mail)) return;
                Models.Mail merged = mailFromJson(Json.obj(result, "mail"));
                translateMailWithSource(merged, Json.obj(result, "source"), requestKey);
            });
            return;
        }
        translateMailWithSource(sourceMail, mailToSourceJson(sourceMail), requestKey);
    }

    private void translateMailWithSource(Models.Mail mail, JSONObject source) {
        String requestKey = activeTranslationLoadKey.isEmpty()
            ? mailActionKey(mail) + "|translate|" + System.nanoTime()
            : activeTranslationLoadKey;
        activeTranslationLoadKey = requestKey;
        translateMailWithSource(mail, source, requestKey);
    }

    private void translateMailWithSource(Models.Mail mail, JSONObject source, String requestKey) {
        String sourceHash = translationSourceHash(mail, source, mobileTranslatorIdentity());
        LocalStore.TranslationCache cached = store.readTranslation(mail, sourceHash);
        if (cached != null) {
            if (!requestKey.equals(activeTranslationLoadKey) || !sameMail(currentDetailMail, mail)) return;
            showTranslation(mail, source, cached.translation, cached.format, true);
            return;
        }
        runAsync("手机端翻译中...", () -> callMobileTranslator(mail, source), result -> {
            if (!requestKey.equals(activeTranslationLoadKey) || !sameMail(currentDetailMail, mail)) return;
            String translated = Json.str(result, "translation");
            String format = Json.str(result, "format");
            store.saveTranslation(
                mail,
                sourceHash,
                translated,
                format,
                Json.str(result, "engine"),
                Json.str(result, "provider"),
                Json.str(result, "model")
            );
            showTranslation(mail, source, translated, format, result.optBoolean("cached"));
        });
    }

    private JSONObject callMobileTranslator(Models.Mail mail, JSONObject source) throws Exception {
        String provider = prefs.getString("mobile_translate_provider", "baidu");
        boolean enabled = prefs.getBoolean("mobile_translate_enabled", false);
        if (!enabled) throw new Exception("请先在手机端设置里开启并配置翻译");
        String html = Json.str(source, "html");
        if (html.isEmpty() && mail != null) html = nonEmpty(mail.html, "");
        if (!html.trim().isEmpty()) {
            return mobileTranslatorResult(provider, translateHtmlPreservingLayout(html, provider), "html");
        }
        String text = mobileTranslationSourceText(mail, source);
        if (text.isEmpty()) throw new Exception("没有可翻译的正文内容");
        return mobileTranslatorResult(provider, translatePlainTextWithMobileProvider(provider, text), "text");
    }

    private JSONObject mobileTranslatorResult(String provider, String translated, String format) throws Exception {
        return new JSONObject()
            .put("translation", translated)
            .put("format", format)
            .put("engine", "mobile")
            .put("provider", provider)
            .put("model", mobileTranslatorModel(provider));
    }

    private String translatePlainTextWithMobileProvider(String provider, String text) throws Exception {
        if ("tencent".equals(provider)) return Json.str(callTencentTranslate(text), "translation");
        if ("google_cloud".equals(provider)) return Json.str(callGoogleCloudTranslate(text), "translation");
        return Json.str(callBaiduTranslate(text), "translation");
    }

    private Map<String, String> translatePlainTextsWithMobileProvider(String provider, List<String> texts) throws Exception {
        Map<String, String> result = new LinkedHashMap<>();
        List<String> unique = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        for (String text : texts) {
            String source = text == null ? "" : text;
            if (source.trim().isEmpty() || seen.contains(source)) continue;
            seen.add(source);
            unique.add(source);
        }
        if ("google_cloud".equals(provider)) {
            return callGoogleCloudTranslateBatch(unique);
        }
        int batchSize = "tencent".equals(provider) ? 12 : 18;
        int maxBatchTextChars = "tencent".equals(provider) ? 2000 : 1500;
        int maxCombinedChars = Math.max(1200, MOBILE_TRANSLATE_CHUNK_CHARS - 600);
        List<String> pending = new ArrayList<>();
        int pendingChars = 0;
        for (String source : unique) {
            if (source.length() > maxBatchTextChars) {
                flushMobileTranslationBatch(provider, pending, result);
                pendingChars = 0;
                result.put(source, translatePlainTextWithMobileProvider(provider, source));
                continue;
            }
            int nextChars = pendingChars + source.length() + (pending.isEmpty() ? 0 : MOBILE_TRANSLATION_BATCH_SEPARATOR.length());
            if (!pending.isEmpty() && nextChars > maxCombinedChars) {
                flushMobileTranslationBatch(provider, pending, result);
                pendingChars = 0;
            }
            pending.add(source);
            pendingChars += source.length() + (pending.size() == 1 ? 0 : MOBILE_TRANSLATION_BATCH_SEPARATOR.length());
            if (pending.size() >= batchSize) flushMobileTranslationBatch(provider, pending, result);
            if (pending.isEmpty()) pendingChars = 0;
        }
        flushMobileTranslationBatch(provider, pending, result);
        return result;
    }

    private void flushMobileTranslationBatch(String provider, List<String> pending, Map<String, String> result) throws Exception {
        if (pending.isEmpty()) return;
        List<String> group = new ArrayList<>(pending);
        pending.clear();
        try {
            String joined = join(group, MOBILE_TRANSLATION_BATCH_SEPARATOR);
            String translated = translatePlainTextWithMobileProvider(provider, joined);
            String[] parts = translated.split(Pattern.quote(MOBILE_TRANSLATION_BATCH_SEPARATOR), -1);
            if (parts.length != group.size()) {
                throw new Exception("批量翻译分隔符未保留");
            }
            for (int i = 0; i < group.size(); i++) {
                result.put(group.get(i), parts[i].trim());
            }
            return;
        } catch (Exception ignored) {
        }
        for (String source : group) {
            result.put(source, translatePlainTextWithMobileProvider(provider, source));
        }
    }

    private String mobileTranslationSourceText(Models.Mail mail, JSONObject source) {
        return mailBodyTextForCopy(mail, source);
    }

    private JSONObject callBaiduTranslate(String text) throws Exception {
        String appId = prefs.getString("mobile_baidu_app_id", "").trim();
        String key = prefs.getString("mobile_baidu_key", "").trim();
        if (appId.isEmpty() || key.isEmpty()) throw new Exception("请先配置百度翻译 AppID 和密钥");
        StringBuilder translated = new StringBuilder();
        for (String chunk : splitTranslationChunks(text, MOBILE_TRANSLATE_CHUNK_CHARS)) {
            String part = callBaiduTranslateChunk(appId, key, chunk);
            if (translated.length() > 0) translated.append('\n');
            translated.append(part);
        }
        return new JSONObject()
            .put("translation", translated.toString())
            .put("format", "text")
            .put("engine", "mobile")
            .put("provider", "baidu")
            .put("model", "baidu-general");
    }

    private String callBaiduTranslateChunk(String appId, String key, String text) throws Exception {
        String salt = String.valueOf(System.currentTimeMillis());
        String sign = md5(appId + text + salt + key);
        String body = "q=" + formEncode(text)
            + "&from=auto&to=zh"
            + "&appid=" + formEncode(appId)
            + "&salt=" + formEncode(salt)
            + "&sign=" + formEncode(sign);
        JSONObject data = httpJson("POST", "https://fanyi-api.baidu.com/api/trans/vip/translate", body, "application/x-www-form-urlencoded", null);
        JSONArray arr = Json.array(data, "trans_result");
        if (arr.length() == 0) throw new Exception(Json.anyStr(data, "error_msg", "message", "百度翻译失败"));
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject item = arr.optJSONObject(i);
            if (item == null) continue;
            if (sb.length() > 0) sb.append('\n');
            sb.append(Json.str(item, "dst"));
        }
        return sb.toString();
    }

    private JSONObject callTencentTranslate(String text) throws Exception {
        String secretId = prefs.getString("mobile_tencent_secret_id", "").trim();
        String secretKey = prefs.getString("mobile_tencent_secret_key", "").trim();
        String region = prefs.getString("mobile_tencent_region", "ap-guangzhou").trim();
        if (secretId.isEmpty() || secretKey.isEmpty()) throw new Exception("请先配置腾讯翻译 SecretId 和 SecretKey");
        StringBuilder translated = new StringBuilder();
        for (String chunk : splitTranslationChunks(text, MOBILE_TRANSLATE_CHUNK_CHARS)) {
            String part = callTencentTranslateChunk(secretId, secretKey, region, chunk);
            if (translated.length() > 0) translated.append('\n');
            translated.append(part);
        }
        return new JSONObject()
            .put("translation", translated.toString())
            .put("format", "text")
            .put("engine", "mobile")
            .put("provider", "tencent")
            .put("model", "tencent-tmt");
    }

    private String callTencentTranslateChunk(String secretId, String secretKey, String region, String text) throws Exception {
        long timestamp = System.currentTimeMillis() / 1000L;
        JSONObject payload = new JSONObject()
            .put("SourceText", text)
            .put("Source", "auto")
            .put("Target", "zh")
            .put("ProjectId", 0);
        String body = payload.toString();
        String authorization = tencentAuthorization(secretId, secretKey, timestamp, body);
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("Authorization", authorization);
        headers.put("Host", "tmt.tencentcloudapi.com");
        headers.put("X-TC-Action", "TextTranslate");
        headers.put("X-TC-Version", "2018-03-21");
        headers.put("X-TC-Timestamp", String.valueOf(timestamp));
        headers.put("X-TC-Region", region.isEmpty() ? "ap-guangzhou" : region);
        JSONObject data = httpJson("POST", "https://tmt.tencentcloudapi.com", body, "application/json; charset=utf-8", headers);
        JSONObject response = Json.obj(data, "Response");
        String translated = Json.str(response, "TargetText");
        if (translated.isEmpty()) {
            JSONObject error = Json.obj(response, "Error");
            throw new Exception(nonEmpty(Json.str(error, "Message"), "腾讯翻译失败"));
        }
        return translated;
    }

    private JSONObject callGoogleCloudTranslate(String text) throws Exception {
        List<String> source = new ArrayList<>();
        source.add(text);
        Map<String, String> translated = callGoogleCloudTranslateBatch(source);
        String value = translated.get(text);
        if (value == null || value.trim().isEmpty()) throw new Exception("Google Cloud Translation 返回空内容");
        return new JSONObject()
            .put("translation", value)
            .put("format", "text")
            .put("engine", "mobile")
            .put("provider", "google_cloud")
            .put("model", "cloud-translation-basic-v2");
    }

    private Map<String, String> callGoogleCloudTranslateBatch(List<String> texts) throws Exception {
        String apiKey = prefs.getString("mobile_google_cloud_api_key", "").trim();
        if (apiKey.isEmpty()) throw new Exception("请先配置 Google Cloud Translation API Key");
        List<String> unique = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        for (String text : texts) {
            String source = text == null ? "" : text;
            if (source.trim().isEmpty() || seen.contains(source)) continue;
            seen.add(source);
            unique.add(source);
        }
        Map<String, String> result = new LinkedHashMap<>();
        if (unique.isEmpty()) return result;
        for (int start = 0; start < unique.size(); start += 128) {
            int end = Math.min(unique.size(), start + 128);
            List<String> batch = unique.subList(start, end);
            JSONArray q = new JSONArray();
            for (String item : batch) q.put(item);
            JSONObject body = new JSONObject()
                .put("q", q)
                .put("target", "zh-CN")
                .put("format", "text");
            JSONObject data = httpJson(
                "POST",
                "https://translation.googleapis.com/language/translate/v2?key=" + encode(apiKey),
                body.toString(),
                "application/json; charset=utf-8",
                null
            );
            JSONArray arr = Json.array(Json.obj(data, "data"), "translations");
            if (arr.length() != batch.size()) throw new Exception("Google Cloud Translation 返回数量不匹配");
            for (int i = 0; i < batch.size(); i++) {
                JSONObject item = arr.optJSONObject(i);
                String translated = item == null ? "" : decodeHtmlEntities(Json.str(item, "translatedText")).trim();
                if (!translated.isEmpty()) result.put(batch.get(i), translated);
            }
        }
        return result;
    }

    private List<String> splitTranslationChunks(String text, int chunkChars) {
        List<String> chunks = new ArrayList<>();
        String value = text == null ? "" : text;
        int safeSize = Math.max(1000, chunkChars);
        int start = 0;
        while (start < value.length()) {
            int end = Math.min(value.length(), start + safeSize);
            if (end < value.length()) {
                int cut = value.lastIndexOf("\n\n", end);
                if (cut <= start + safeSize / 3) cut = value.lastIndexOf('\n', end);
                if (cut <= start + safeSize / 3) cut = value.lastIndexOf('。', end);
                if (cut <= start + safeSize / 3) cut = value.lastIndexOf('.', end);
                if (cut > start + safeSize / 3) end = cut + 1;
            }
            String chunk = value.substring(start, end).trim();
            if (!chunk.isEmpty()) chunks.add(chunk);
            start = end;
        }
        return chunks;
    }

    private String translateHtmlPreservingLayout(String html, String provider) throws Exception {
        if (html == null || html.trim().isEmpty()) return "";
        List<HtmlTextFragment> fragments = new ArrayList<>();
        StringBuilder out = new StringBuilder(html.length() + 1024);
        String rawTag = "";
        int index = 0;
        while (index < html.length()) {
            int tagStart = html.indexOf('<', index);
            if (tagStart < 0) {
                appendHtmlTextToken(out, fragments, html.substring(index), rawTag.isEmpty());
                break;
            }
            if (tagStart > index) {
                appendHtmlTextToken(out, fragments, html.substring(index, tagStart), rawTag.isEmpty());
            }
            int tagEnd = findHtmlTagEnd(html, tagStart);
            if (tagEnd < 0) {
                out.append(html.substring(tagStart));
                break;
            }
            String tag = html.substring(tagStart, tagEnd + 1);
            String tagName = htmlTagName(tag);
            boolean closing = tag.startsWith("</");
            out.append(tag);
            if (!tagName.isEmpty()) {
                if (!rawTag.isEmpty()) {
                    if (closing && tagName.equals(rawTag)) rawTag = "";
                } else if (!closing && isRawHtmlTag(tagName)) {
                    rawTag = tagName;
                }
            }
            index = tagEnd + 1;
        }
        if (fragments.isEmpty()) return out.toString();
        List<String> sources = new ArrayList<>();
        for (HtmlTextFragment fragment : fragments) {
            sources.add(fragment.source);
        }
        Map<String, String> translated = translatePlainTextsWithMobileProvider(provider, sources);
        String translatedHtml = out.toString();
        for (HtmlTextFragment fragment : fragments) {
            String value = translated.get(fragment.source);
            if (value == null || value.trim().isEmpty()) value = fragment.source;
            String replacement = fragment.prefix + escape(value).replace("\n", "<br>") + fragment.suffix;
            translatedHtml = translatedHtml.replace(fragment.token, replacement);
        }
        return translatedHtml;
    }

    private void appendHtmlTextToken(StringBuilder out, List<HtmlTextFragment> fragments, String fragment, boolean translatable) {
        if (fragment == null || fragment.isEmpty()) return;
        if (!translatable) {
            out.append(fragment);
            return;
        }
        String sourceText = normalizeHtmlTextNode(htmlToPlainText(fragment));
        if (!htmlTextNeedsTranslation(sourceText)) {
            out.append(fragment);
            return;
        }
        int leading = leadingWhitespace(fragment);
        int trailing = trailingWhitespace(fragment, leading);
        String prefix = fragment.substring(0, leading);
        String suffix = fragment.substring(fragment.length() - trailing);
        String core = fragment.substring(leading, fragment.length() - trailing);
        String normalizedCore = normalizeHtmlTextNode(htmlToPlainText(core));
        if (!htmlTextNeedsTranslation(normalizedCore)) {
            out.append(fragment);
            return;
        }
        String token = "\uE000" + fragments.size() + "\uE001";
        fragments.add(new HtmlTextFragment(token, prefix, suffix, normalizedCore));
        out.append(token);
    }

    private static int findHtmlTagEnd(String html, int start) {
        char quote = 0;
        for (int i = start + 1; i < html.length(); i++) {
            char ch = html.charAt(i);
            if (quote != 0) {
                if (ch == quote) quote = 0;
            } else if (ch == '"' || ch == '\'') {
                quote = ch;
            } else if (ch == '>') {
                return i;
            }
        }
        return -1;
    }

    private static String htmlTagName(String tag) {
        if (tag == null || tag.length() < 3 || tag.charAt(0) != '<') return "";
        int i = 1;
        if (i < tag.length() && tag.charAt(i) == '/') i++;
        while (i < tag.length() && Character.isWhitespace(tag.charAt(i))) i++;
        if (i >= tag.length() || tag.charAt(i) == '!' || tag.charAt(i) == '?') return "";
        int start = i;
        while (i < tag.length()) {
            char ch = tag.charAt(i);
            if (!Character.isLetterOrDigit(ch) && ch != '-' && ch != ':') break;
            i++;
        }
        return tag.substring(start, i).toLowerCase(Locale.ROOT);
    }

    private static boolean isRawHtmlTag(String tagName) {
        return "script".equals(tagName)
            || "style".equals(tagName)
            || "template".equals(tagName)
            || "svg".equals(tagName)
            || "head".equals(tagName);
    }

    private static class HtmlTextFragment {
        final String token;
        final String prefix;
        final String suffix;
        final String source;

        HtmlTextFragment(String token, String prefix, String suffix, String source) {
            this.token = token;
            this.prefix = prefix;
            this.suffix = suffix;
            this.source = source;
        }
    }

    private static String normalizeHtmlTextNode(String value) {
        if (value == null) return "";
        return value
            .replace('\u00a0', ' ')
            .replaceAll("[ \\t\\x0B\\f\\r]+", " ")
            .replaceAll("\\n{3,}", "\n\n")
            .trim();
    }

    private static boolean htmlTextNeedsTranslation(String value) {
        if (value == null) return false;
        String text = value.trim();
        if (text.length() < 2) return false;
        boolean hasLetter = false;
        boolean hasLowercase = false;
        boolean hasDigit = false;
        boolean hasWhitespace = false;
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (Character.isLetter(ch)) hasLetter = true;
            if (Character.isLowerCase(ch)) hasLowercase = true;
            if (Character.isDigit(ch)) hasDigit = true;
            if (Character.isWhitespace(ch)) hasWhitespace = true;
        }
        if (!hasLetter) return false;
        if (!hasWhitespace && hasDigit && text.length() <= 80) return false;
        if (!hasWhitespace && !hasLowercase && text.length() <= 12) return false;
        return true;
    }

    private static int leadingWhitespace(String value) {
        int count = 0;
        while (count < value.length() && Character.isWhitespace(value.charAt(count))) count++;
        return count;
    }

    private static int trailingWhitespace(String value, int leading) {
        int count = 0;
        int index = value.length() - 1;
        while (index >= leading && Character.isWhitespace(value.charAt(index))) {
            count++;
            index--;
        }
        return count;
    }

    private void copyMailContent(Models.Mail mail) {
        Models.Mail cachedMail = store.readMailDetail(mail);
        Models.Mail sourceMail = cachedMail == null ? mail : cachedMail;
        if (!hasFullBody(sourceMail)) {
            runAsync("加载正文后复制...", () -> {
                JSONObject detail = fetchDetailDataWithRetry(mail);
                Models.Mail merged = mergeDetailIntoMail(mail, detail);
                store.upsertMailDetail(merged);
                return new JSONObject()
                    .put("source", detailSource(detail))
                    .put("mail", mailToJson(merged));
            }, result -> {
                Models.Mail merged = mailFromJson(Json.obj(result, "mail"));
                copyMailContentToClipboard(merged, Json.obj(result, "source"));
            });
            return;
        }
        copyMailContentToClipboard(sourceMail, mailToSourceJson(sourceMail));
    }

    private void copyMailContentToClipboard(Models.Mail mail, JSONObject source) {
        String content = mailBodyTextForCopy(mail, source);
        if (content.trim().isEmpty()) {
            toast("没有可复制的正文内容");
            return;
        }
        ClipboardManager clipboard = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (clipboard == null) {
            toast("系统剪贴板不可用");
            return;
        }
        clipboard.setPrimaryClip(ClipData.newPlainText("Memail 邮件正文", content));
        toast("邮件内容已复制");
    }

    private void showTranslation(Models.Mail mail, JSONObject source, String translated, String format, boolean cached) {
        currentScreen = "translation";
        currentDetailMail = mail;
        currentDetailSource = source;
        setHeader("中文翻译", "");
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(BG);
        LinearLayout box = column(dp(10));
        box.setPadding(dp(14), dp(10), dp(14), dp(18));
        scroll.addView(box);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));

        box.addView(detailHeaderCard(mail, nonEmpty(mail.sender, Json.anyStr(source, "from", "from_address")), Json.anyStr(source, "to", "to_address"), nonEmpty(mail.date, Json.anyStr(source, "date", "createdAt", "created_at"))));
        box.addView(detailActionStrip(mail, source, true));
        box.addView(readingSection(cached ? "中文翻译 · 已缓存" : "中文翻译", "html".equals(format) ? translated : plainTextHtml(translated), dp(520)));

        Models.Mail cachedMail = store.readMailDetail(mail);
        String originalHtml = nonEmpty(Json.str(source, "html"), cachedMail == null ? "" : cachedMail.html);
        if (originalHtml.isEmpty()) originalHtml = nonEmpty(mail.html, "");
        String originalText = nonEmpty(Json.str(source, "text"), cachedMail == null ? "" : cachedMail.text);
        if (originalText.isEmpty()) originalText = nonEmpty(mail.text, mail.preview);
        box.addView(readingSection("原文", originalHtml.isEmpty() ? plainTextHtml(originalText) : originalHtml, dp(620)));
    }

    private void confirmDelete(Models.Mail mail) {
        new android.app.AlertDialog.Builder(this)
            .setTitle("删除邮件")
            .setMessage("确定删除这封邮件？")
            .setNegativeButton("取消", null)
            .setPositiveButton("删除", (dialog, which) -> deleteMail(mail))
            .show();
    }

    private void deleteMail(Models.Mail mail) {
        runAsync("删除中...", () -> {
            if ("external".equals(mail.accountType)) {
                return api.delete("/imap/api/accounts/" + encode(mail.accountId) + "/mails/" + encode(mail.id) + "?folder=" + encode(mail.folder));
            }
            return api.post("/api/inbox/delete", new JSONObject().put("email", mail.accountId).put("message_id", mail.id));
        }, result -> {
            removeMailFromList(mail);
            store.deleteMail(mail);
            toast("已删除");
            renderFoldersOrMails();
        });
    }

    private void deleteDraft(Models.Mail mail) {
        runAsync("删除草稿...", () -> api.delete("/api/drafts/" + mail.id), result -> {
            removeMailFromList(mail);
            store.deleteMail(mail);
            toast("草稿已删除");
            renderFoldersOrMails();
        });
    }

    private void retryOutbox(Models.Mail mail) {
        runAsync("重试发送...", () -> api.post("/api/outbox/" + mail.id + "/retry", new JSONObject()), result -> {
            removeMailFromList(mail);
            toast("重试发送成功");
            renderFoldersOrMails();
        });
    }

    private void deleteOutbox(Models.Mail mail) {
        runAsync("删除记录...", () -> api.delete("/api/outbox/" + mail.id), result -> {
            removeMailFromList(mail);
            store.deleteMail(mail);
            toast("发送失败记录已删除");
            renderFoldersOrMails();
        });
    }

    private void renderCompose() {
        selectedVirtualMode = "";
        selectedGroup = "";
        composeSender = selectedAccount;
        renderComposeFor(null, "");
    }

    private void renderComposeFor(Models.Mail source, String subject) {
        if (source != null) composeSender = findMailAccount(source);
        String toValue = source == null ? "" : cleanAddress(source.sender);
        String bodyValue = source == null ? "" : "\n\n---- 原邮件 ----\n" + nonEmpty(source.preview, "");
        renderComposeEditor(toValue, subject, bodyValue, "");
    }

    private JSONObject detailSource(JSONObject data) {
        JSONObject detail = data == null ? null : data.optJSONObject("detail");
        return detail == null ? (data == null ? new JSONObject() : data) : detail;
    }

    private boolean hasFullBody(Models.Mail mail) {
        return mail != null
            && ((mail.html != null && !mail.html.isEmpty()) || (mail.text != null && !mail.text.isEmpty()));
    }

    private JSONObject mailToSourceJson(Models.Mail mail) {
        JSONObject data = new JSONObject();
        try {
            data.put("html", mail == null ? "" : mail.html);
            data.put("text", mail == null ? "" : mail.text);
            data.put("to", mail == null ? "" : mail.to);
            data.put("error", mail == null ? "" : mail.error);
            data.put("from", mail == null ? "" : mail.sender);
            data.put("subject", mail == null ? "" : mail.subject);
            data.put("date", mail == null ? "" : mail.date);
        } catch (Exception ignored) {
        }
        return data;
    }

    private String mailBodyTextForCopy(Models.Mail mail, JSONObject source) {
        String text = Json.str(source, "text");
        if (text.isEmpty() && mail != null) text = nonEmpty(mail.text, "");
        if (!text.trim().isEmpty()) return normalizeCopiedText(text);
        String html = Json.str(source, "html");
        if (html.isEmpty() && mail != null) html = nonEmpty(mail.html, "");
        if (!html.trim().isEmpty()) return normalizeCopiedText(htmlToPlainText(html));
        return normalizeCopiedText(mail == null ? "" : nonEmpty(mail.preview, ""));
    }

    private String htmlToPlainText(String html) {
        if (html == null || html.isEmpty()) return "";
        if (Build.VERSION.SDK_INT >= 24) {
            return Html.fromHtml(html, Html.FROM_HTML_MODE_LEGACY).toString();
        }
        return Html.fromHtml(html).toString();
    }

    private String decodeHtmlEntities(String value) {
        if (value == null || value.isEmpty()) return "";
        if (Build.VERSION.SDK_INT >= 24) {
            return Html.fromHtml(value, Html.FROM_HTML_MODE_LEGACY).toString();
        }
        return Html.fromHtml(value).toString();
    }

    private String normalizeCopiedText(String value) {
        if (value == null) return "";
        return value
            .replace('\u00a0', ' ')
            .replaceAll("[ \\t\\x0B\\f\\r]+\\n", "\n")
            .replaceAll("\\n{3,}", "\n\n")
            .trim();
    }

    private JSONObject httpJson(String method, String urlText, String body, String contentType, Map<String, String> headers) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(urlText).openConnection();
        conn.setConnectTimeout(12000);
        conn.setReadTimeout(45000);
        conn.setRequestMethod(method);
        conn.setRequestProperty("Accept", "application/json");
        if (contentType != null && !contentType.isEmpty()) conn.setRequestProperty("Content-Type", contentType);
        if (headers != null) {
            for (Map.Entry<String, String> entry : headers.entrySet()) {
                conn.setRequestProperty(entry.getKey(), entry.getValue());
            }
        }
        if (body != null) {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            conn.setDoOutput(true);
            conn.setFixedLengthStreamingMode(bytes.length);
            try (java.io.OutputStream output = conn.getOutputStream()) {
                output.write(bytes);
            }
        }
        int code = conn.getResponseCode();
        String text = readHttpText(code >= 400 ? conn.getErrorStream() : conn.getInputStream());
        conn.disconnect();
        if (code >= 400) throw new Exception("翻译接口 HTTP " + code + ": " + text);
        return new JSONObject(text == null || text.trim().isEmpty() ? "{}" : text);
    }

    private static String readHttpText(InputStream input) throws Exception {
        if (input == null) return "";
        StringBuilder sb = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) sb.append(line).append('\n');
        }
        return sb.toString();
    }

    private static String formEncode(String value) {
        return Uri.encode(value == null ? "" : value);
    }

    private static String md5(String value) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("MD5");
        return hex(digest.digest((value == null ? "" : value).getBytes(StandardCharsets.UTF_8)));
    }

    private static String tencentAuthorization(String secretId, String secretKey, long timestamp, String payload) throws Exception {
        String service = "tmt";
        String host = "tmt.tencentcloudapi.com";
        String date = utcDate(timestamp);
        String canonicalHeaders = "content-type:application/json; charset=utf-8\nhost:" + host + "\n";
        String signedHeaders = "content-type;host";
        String hashedPayload = sha256(payload);
        String canonicalRequest = "POST\n/\n\n" + canonicalHeaders + "\n" + signedHeaders + "\n" + hashedPayload;
        String credentialScope = date + "/" + service + "/tc3_request";
        String stringToSign = "TC3-HMAC-SHA256\n" + timestamp + "\n" + credentialScope + "\n" + sha256(canonicalRequest);
        byte[] secretDate = hmacSha256(("TC3" + secretKey).getBytes(StandardCharsets.UTF_8), date);
        byte[] secretService = hmacSha256(secretDate, service);
        byte[] secretSigning = hmacSha256(secretService, "tc3_request");
        String signature = hex(hmacSha256(secretSigning, stringToSign));
        return "TC3-HMAC-SHA256 Credential=" + secretId + "/" + credentialScope
            + ", SignedHeaders=" + signedHeaders
            + ", Signature=" + signature;
    }

    private static byte[] hmacSha256(byte[] key, String value) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(key, "HmacSHA256"));
        return mac.doFinal(value.getBytes(StandardCharsets.UTF_8));
    }

    private static String utcDate(long timestamp) {
        SimpleDateFormat format = new SimpleDateFormat("yyyy-MM-dd", Locale.US);
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        return format.format(new Date(timestamp * 1000L));
    }

    private static String hex(byte[] bytes) {
        StringBuilder sb = new StringBuilder();
        for (byte b : bytes) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    private void renderComposeEditor(String toValue, String subjectValue, String bodyValue, String draftId) {
        currentScreen = "compose";
        navIndex = 2;
        rebuildBottomNav();
        composeSender = composeSender == null ? defaultComposeSender() : composeSender;
        composeAttachments.clear();
        setHeader("写邮件", composeSender == null ? "选择发件账号" : composeSender.label());
        content.removeAllViews();
        LinearLayout page = column(0);
        page.setBackgroundColor(BG);
        content.addView(page, new LinearLayout.LayoutParams(-1, -1));
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(false);
        scroll.setBackgroundColor(BG);
        LinearLayout box = column(dp(10));
        box.setPadding(dp(12), dp(10), dp(12), dp(22));
        scroll.addView(box);
        page.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        EditText to = input("收件人，多个用逗号分隔", toValue, false);
        EditText cc = input("抄送 CC，可选", "", false);
        EditText bcc = input("密送 BCC，可选", "", false);
        EditText sub = input("主题", subjectValue, false);
        EditText body = input("正文", bodyValue, false);
        body.setMinLines(10);
        body.setGravity(Gravity.TOP);
        box.addView(composeSenderCard());
        box.addView(composeEnvelopeCard(to, cc, bcc, sub));
        box.addView(composeEditorCard(body));
        TextView attach = outlineButton("＋ 添加附件", v -> pickComposeAttachment());
        box.addView(composeAttachmentCard(attach));
        composeAttachmentList = column(dp(2));
        box.addView(composeAttachmentList);
        updateComposeAttachmentList();
        TextView save = outlineButton("保存草稿", v -> saveDraft(to.getText().toString(), cc.getText().toString(), bcc.getText().toString(), sub.getText().toString(), body.getText().toString(), draftId));
        TextView send = primaryButton("发送");
        send.setOnClickListener(v -> sendMail(to.getText().toString(), cc.getText().toString(), bcc.getText().toString(), sub.getText().toString(), body.getText().toString(), draftId));
        page.addView(composeActionBar(save, send), new LinearLayout.LayoutParams(-1, dp(72)));
    }

    private Models.Account defaultComposeSender() {
        if (selectedAccount != null) return selectedAccount;
        if (composeSender != null) return composeSender;
        return accounts.isEmpty() ? null : accounts.get(0);
    }

    private String composeSenderLabel() {
        return composeSender == null
            ? "发件账号：请选择"
            : "发件账号：" + composeSender.label() + " <" + composeSender.email + ">";
    }

    private String composeSenderDisplay() {
        return composeSender == null
            ? "请选择发件邮箱"
            : composeSender.label() + " <" + composeSender.email + ">";
    }

    private View composeSenderCard() {
        LinearLayout card = panel(dp(14), dp(12));
        card.setBackground(bg(Color.WHITE, 24, SOFT_LINE, 1));
        card.setOnClickListener(v -> chooseComposeSender());
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);

        composeFromAvatar = avatarView(composeSender == null ? "Memail" : composeSender.label() + composeSender.email);
        row.addView(composeFromAvatar, new LinearLayout.LayoutParams(dp(46), dp(46)));

        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        copy.setPadding(dp(12), 0, dp(8), 0);
        TextView label = text("发件账号", 12, MUTED, true);
        composeFromView = text(composeSenderDisplay(), 15, TEXT, true);
        composeFromView.setSingleLine(true);
        copy.addView(label);
        copy.addView(composeFromView);
        row.addView(copy, new LinearLayout.LayoutParams(0, -2, 1));

        TextView change = tinyChip("切换");
        row.addView(change);
        card.addView(row);
        return card;
    }

    private View composeEnvelopeCard(EditText to, EditText cc, EditText bcc, EditText subject) {
        LinearLayout card = panel(dp(14), dp(12));
        card.setBackground(bg(Color.WHITE, 24, SOFT_LINE, 1));
        card.addView(sectionLabel("收件信息"));
        card.addView(composeField("收件人", to));
        card.addView(composeField("抄送", cc));
        card.addView(composeField("密送", bcc));
        card.addView(composeField("主题", subject));
        return card;
    }

    private View composeEditorCard(EditText body) {
        LinearLayout card = panel(dp(14), dp(12));
        card.setBackground(bg(Color.WHITE, 24, SOFT_LINE, 1));
        card.addView(sectionLabel("正文编辑"));
        card.addView(formatToolbar(body));
        card.addView(body, new LinearLayout.LayoutParams(-1, -2));
        return card;
    }

    private View composeAttachmentCard(TextView attach) {
        LinearLayout card = panel(dp(14), dp(12));
        card.setBackground(bg(Color.WHITE, 24, SOFT_LINE, 1));
        card.addView(sectionLabel("附件"));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, dp(44));
        lp.setMargins(0, dp(8), 0, 0);
        card.addView(attach, lp);
        return card;
    }

    private View composeField(String labelText, EditText edit) {
        LinearLayout field = new LinearLayout(this);
        field.setOrientation(LinearLayout.VERTICAL);
        field.setPadding(0, dp(6), 0, 0);
        TextView label = text(labelText, 12, MUTED, true);
        label.setPadding(dp(2), 0, dp(2), dp(2));
        field.addView(label);
        field.addView(edit, new LinearLayout.LayoutParams(-1, -2));
        return field;
    }

    private void chooseComposeSender() {
        if (accounts.isEmpty()) {
            toast("暂无可用邮箱账号");
            return;
        }
        android.app.Dialog dialog = new android.app.Dialog(this);
        LinearLayout sheet = column(dp(8));
        sheet.setPadding(dp(16), dp(14), dp(16), dp(16));
        sheet.setBackground(bg(Color.WHITE, 28, SOFT_LINE, 1));

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        TextView titleView = text("选择发件邮箱", 20, TEXT, true);
        TextView close = text("×", 28, MUTED, false);
        close.setGravity(Gravity.CENTER);
        close.setOnClickListener(v -> dialog.dismiss());
        header.addView(titleView, new LinearLayout.LayoutParams(0, dp(42), 1));
        header.addView(close, new LinearLayout.LayoutParams(dp(42), dp(42)));
        sheet.addView(header);

        TextView hint = text("选择后将作为本次写信的发件账号，不会清空已填写内容。", 13, MUTED, false);
        hint.setPadding(dp(2), 0, dp(2), dp(6));
        sheet.addView(hint);

        ScrollView scroll = new ScrollView(this);
        LinearLayout list = column(dp(4));
        scroll.addView(list);
        Map<String, List<Models.Account>> grouped = new LinkedHashMap<>();
        for (Models.Account account : accounts) {
            String group = nonEmpty(account.group, "未分组");
            if (!grouped.containsKey(group)) grouped.put(group, new ArrayList<>());
            grouped.get(group).add(account);
        }
        for (Map.Entry<String, List<Models.Account>> entry : grouped.entrySet()) {
            list.addView(composeSenderGroupHeader(entry.getKey(), entry.getValue().size()));
            for (Models.Account account : entry.getValue()) {
                list.addView(composeSenderOption(account, dialog));
            }
        }

        sheet.addView(scroll, new LinearLayout.LayoutParams(-1, Math.min(dp(480), getResources().getDisplayMetrics().heightPixels - dp(190))));
        TextView cancel = outlineButton("取消", v -> dialog.dismiss());
        LinearLayout.LayoutParams cancelLp = new LinearLayout.LayoutParams(-1, dp(46));
        cancelLp.setMargins(0, dp(8), 0, 0);
        sheet.addView(cancel, cancelLp);

        dialog.setContentView(sheet);
        dialog.show();
        Window window = dialog.getWindow();
        if (window != null) {
            window.setGravity(Gravity.BOTTOM);
            window.setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(Color.TRANSPARENT));
            window.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            window.addFlags(android.view.WindowManager.LayoutParams.FLAG_DIM_BEHIND);
            android.view.WindowManager.LayoutParams params = window.getAttributes();
            params.dimAmount = 0.32f;
            window.setAttributes(params);
        }
    }

    private View composeSenderGroupHeader(String group, int count) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(2), dp(10), dp(2), dp(4));
        TextView name = text(group, 13, MUTED, true);
        TextView meta = text(count + " 个账号", 12, MUTED, false);
        meta.setGravity(Gravity.RIGHT);
        row.addView(name, new LinearLayout.LayoutParams(0, -2, 1));
        row.addView(meta, new LinearLayout.LayoutParams(0, -2, 1));
        return row;
    }

    private View composeSenderOption(Models.Account account, android.app.Dialog dialog) {
        boolean active = composeSender != null && sameId(composeSender.type, account.type) && sameId(composeSender.id, account.id);
        LinearLayout card = panel(dp(12), dp(10));
        card.setBackground(bg(active ? PRIMARY_SOFT : Color.WHITE, 22, active ? Color.rgb(149, 205, 202) : SOFT_LINE, 1));
        card.setOnClickListener(v -> {
            composeSender = account;
            updateComposeSenderViews();
            dialog.dismiss();
        });

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        TextView avatar = avatarView(account.label() + account.email);
        row.addView(avatar, new LinearLayout.LayoutParams(dp(46), dp(46)));

        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        copy.setPadding(dp(12), 0, dp(8), 0);
        TextView name = text(account.label(), 16, TEXT, true);
        name.setSingleLine(true);
        TextView email = text(account.email, 12, MUTED, false);
        email.setSingleLine(true);
        copy.addView(name);
        copy.addView(email);
        row.addView(copy, new LinearLayout.LayoutParams(0, -2, 1));

        if (account.unread > 0) row.addView(badge(String.valueOf(account.unread), ACCENT, Color.WHITE));
        TextView check = text(active ? "✓" : "›", 22, active ? PRIMARY : MUTED, true);
        check.setGravity(Gravity.CENTER);
        row.addView(check, new LinearLayout.LayoutParams(dp(34), dp(42)));
        card.addView(row);

        LinearLayout chips = new LinearLayout(this);
        chips.setOrientation(LinearLayout.HORIZONTAL);
        chips.setPadding(dp(58), dp(8), 0, 0);
        chips.addView(tinyChip("local".equals(account.type) ? "本地" : "外部"));
        chips.addView(tinyChip(nonEmpty(account.group, "未分组")));
        if (!nonEmpty(account.sendName, "").isEmpty()) chips.addView(tinyChip("发件名 " + account.sendName));
        card.addView(chips);
        return card;
    }

    private void updateComposeSenderViews() {
        if (composeSender == null) return;
        if (composeFromView != null) composeFromView.setText(composeSenderDisplay());
        if (composeFromAvatar != null) {
            String label = composeSender.label() + composeSender.email;
            int color = avatarColor(label);
            composeFromAvatar.setText(avatarLabel(label));
            composeFromAvatar.setBackground(bg(color, 23, color, 0));
        }
        setHeader("写邮件", composeSender.label());
    }

    private LinearLayout formatToolbar(EditText body) {
        LinearLayout tools = new LinearLayout(this);
        tools.setOrientation(LinearLayout.VERTICAL);
        tools.setPadding(0, dp(8), 0, dp(8));
        tools.setBackground(bg(SURFACE, 18, SOFT_LINE, 1));

        LinearLayout row1 = toolbarRow();
        addToolbarButton(row1, toolButton("加粗", v -> wrapSelection(body, "<b>", "</b>")));
        addToolbarButton(row1, toolButton("斜体", v -> wrapSelection(body, "<i>", "</i>")));
        addToolbarButton(row1, toolButton("下划线", v -> wrapSelection(body, "<u>", "</u>")));
        addToolbarButton(row1, toolButton("链接", v -> insertLink(body)));
        tools.addView(row1);

        LinearLayout row2 = toolbarRow();
        addToolbarButton(row2, toolButton("列表", v -> wrapSelection(body, "<ul><li>", "</li></ul>")));
        addToolbarButton(row2, toolButton("编号", v -> wrapSelection(body, "<ol><li>", "</li></ol>")));
        addToolbarButton(row2, toolButton("引用", v -> wrapSelection(body, "<blockquote>", "</blockquote>")));
        addToolbarButton(row2, toolButton("分隔", v -> insertText(body, "\n<hr>\n")));
        addToolbarButton(row2, toolButton("清除", v -> clearSelectionFormat(body)));
        tools.addView(row2);
        return tools;
    }

    private LinearLayout toolbarRow() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(8), dp(3), dp(8), dp(3));
        return row;
    }

    private TextView toolButton(String label, View.OnClickListener listener) {
        TextView button = text(label, 12, PRIMARY_DARK, true);
        button.setGravity(Gravity.CENTER);
        button.setSingleLine(true);
        button.setPadding(dp(7), dp(8), dp(7), dp(8));
        button.setBackground(bg(Color.WHITE, 14, LINE, 1));
        button.setOnClickListener(listener);
        return button;
    }

    private void addToolbarButton(LinearLayout tools, TextView button) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(38), 1);
        lp.setMargins(dp(3), 0, dp(3), 0);
        tools.addView(button, lp);
    }

    private View composeActionBar(TextView save, TextView send) {
        LinearLayout wrap = new LinearLayout(this);
        wrap.setOrientation(LinearLayout.VERTICAL);
        wrap.setBackgroundColor(Color.WHITE);
        View line = new View(this);
        line.setBackgroundColor(SOFT_LINE);
        wrap.addView(line, new LinearLayout.LayoutParams(-1, 1));

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.setGravity(Gravity.CENTER_VERTICAL);
        actions.setPadding(dp(12), dp(8), dp(12), dp(10));
        LinearLayout.LayoutParams saveLp = new LinearLayout.LayoutParams(0, dp(48), 1);
        saveLp.setMargins(0, 0, dp(10), 0);
        actions.addView(save, saveLp);
        actions.addView(send, new LinearLayout.LayoutParams(0, dp(48), 1));
        wrap.addView(actions, new LinearLayout.LayoutParams(-1, 0, 1));
        return wrap;
    }

    private void wrapSelection(EditText edit, String open, String close) {
        int start = Math.max(0, edit.getSelectionStart());
        int end = Math.max(start, edit.getSelectionEnd());
        String selected = edit.getText().subSequence(start, end).toString();
        edit.getText().replace(start, end, open + selected + close);
        edit.setSelection(start + open.length(), start + open.length() + selected.length());
    }

    private void insertText(EditText edit, String value) {
        int start = Math.max(0, edit.getSelectionStart());
        int end = Math.max(start, edit.getSelectionEnd());
        edit.getText().replace(start, end, value);
        edit.setSelection(start + value.length());
    }

    private void clearSelectionFormat(EditText edit) {
        int start = Math.max(0, edit.getSelectionStart());
        int end = Math.max(start, edit.getSelectionEnd());
        if (end <= start) {
            toast("请先选中要清除格式的文字");
            return;
        }
        String selected = edit.getText().subSequence(start, end).toString()
            .replaceAll("</?(b|i|u|blockquote|ul|ol|li)>", "")
            .replaceAll("<a\\s+href=\"[^\"]*\">", "")
            .replace("</a>", "");
        edit.getText().replace(start, end, selected);
        edit.setSelection(start, start + selected.length());
    }

    private void insertLink(EditText edit) {
        EditText url = input("https://example.com", "", false);
        new android.app.AlertDialog.Builder(this)
            .setTitle("插入链接")
            .setView(url)
            .setNegativeButton("取消", null)
            .setPositiveButton("插入", (dialog, which) -> {
                String href = url.getText().toString().trim();
                if (href.isEmpty()) return;
                int start = Math.max(0, edit.getSelectionStart());
                int end = Math.max(start, edit.getSelectionEnd());
                String selected = edit.getText().subSequence(start, end).toString();
                String label = selected.isEmpty() ? href : selected;
                edit.getText().replace(start, end, "<a href=\"" + href + "\">" + label + "</a>");
            })
            .show();
    }

    private void pickComposeAttachment() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        startActivityForResult(intent, REQ_PICK_ATTACHMENT);
    }

    private void addComposeAttachment(Uri uri) {
        setLoading(true, "读取附件...");
        io.submit(() -> {
            try {
                ComposeAttachment item = readAttachment(uri);
                runOnUiThread(() -> {
                    setLoading(false, "");
                    composeAttachments.add(item);
                    updateComposeAttachmentList();
                    toast("已添加附件：" + item.filename);
                });
            } catch (Exception e) {
                runOnUiThread(() -> {
                    setLoading(false, "");
                    toast("附件添加失败：" + e.getMessage());
                });
            }
        });
    }

    private ComposeAttachment readAttachment(Uri uri) throws Exception {
        ComposeAttachment item = new ComposeAttachment();
        item.filename = attachmentName(uri);
        item.contentType = nonEmpty(getContentResolver().getType(uri), "application/octet-stream");
        try (InputStream input = getContentResolver().openInputStream(uri);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            if (input == null) throw new Exception("无法读取文件");
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                output.write(buffer, 0, read);
                if (output.size() > MAX_MOBILE_ATTACHMENT_BYTES) throw new Exception("单个附件不能超过 8MB");
            }
            byte[] bytes = output.toByteArray();
            item.size = bytes.length;
            item.content = Base64.encodeToString(bytes, Base64.NO_WRAP);
        }
        return item;
    }

    private String attachmentName(Uri uri) {
        String name = "";
        try (android.database.Cursor cursor = getContentResolver().query(uri, null, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (index >= 0) name = cursor.getString(index);
            }
        } catch (Exception ignored) {
            // Fall through to URI based name.
        }
        if (!name.isEmpty()) return name;
        String path = uri.getLastPathSegment();
        return path == null || path.isEmpty() ? "attachment" : path;
    }

    private void updateComposeAttachmentList() {
        if (composeAttachmentList == null) return;
        composeAttachmentList.removeAllViews();
        if (composeAttachments.isEmpty()) {
            composeAttachmentList.addView(text("暂无附件", 12, MUTED, false));
            return;
        }
        for (ComposeAttachment item : new ArrayList<>(composeAttachments)) {
            TextView row = outlineButton("附件：" + item.filename + "  " + formatBytes(item.size) + "   ×", v -> {
                composeAttachments.remove(item);
                updateComposeAttachmentList();
            });
            composeAttachmentList.addView(row, new LinearLayout.LayoutParams(-1, dp(42)));
        }
    }

    private String composeBodyHtml(String body) {
        String html = escape(body).replace("\n", "<br>");
        html = html
            .replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
            .replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>")
            .replace("&lt;u&gt;", "<u>").replace("&lt;/u&gt;", "</u>");
        html = html.replaceAll("&lt;a href=&quot;([^&]+)&quot;&gt;", "<a href=\"$1\">")
            .replace("&lt;/a&gt;", "</a>");
        return "<div>" + html + "</div>";
    }

    private String formatBytes(int size) {
        if (size >= 1024 * 1024) return String.format("%.1fMB", size / 1024f / 1024f);
        if (size >= 1024) return String.format("%.1fKB", size / 1024f);
        return size + "B";
    }

    private JSONObject composePayload(Models.Account sender, String to, String cc, String bcc, String subject, String body) throws Exception {
        JSONObject payload = new JSONObject()
            .put("account_type", sender.type)
            .put("account_id", sender.id)
            .put("from_email", sender.email)
            .put("from_name", nonEmpty(sender.sendName, sender.name))
            .put("to", to)
            .put("cc", cc)
            .put("bcc", bcc)
            .put("subject", subject)
            .put("text", body)
            .put("html", composeBodyHtml(body));
        if (!composeAttachments.isEmpty()) {
            JSONArray attachments = new JSONArray();
            for (ComposeAttachment item : composeAttachments) {
                attachments.put(new JSONObject()
                    .put("filename", item.filename)
                    .put("contentType", item.contentType)
                    .put("content", item.content)
                    .put("size", item.size));
            }
            payload.put("attachments", attachments);
        }
        if ("external".equals(sender.type)) payload.put("fromName", nonEmpty(sender.sendName, sender.name));
        return payload;
    }

    private void saveDraft(String to, String cc, String bcc, String subject, String body, String draftId) {
        Models.Account account = composeSender == null ? defaultComposeSender() : composeSender;
        if (account == null) {
            toast("请先选择发件账号");
            return;
        }
        Models.Account sender = account;
        runAsync("保存草稿...", () -> {
            JSONObject payload = composePayload(sender, to, cc, bcc, subject, body);
            if (draftId != null && !draftId.isEmpty()) payload.put("id", draftId);
            return api.post("/api/drafts", payload);
        }, result -> {
            toast("草稿已保存");
            if (selectedFolder != null && "drafts".equals(selectedFolder.path)) loadMails(false);
        });
    }

    private void sendMail(String to, String cc, String bcc, String subject, String body, String draftId) {
        Models.Account account = composeSender == null ? defaultComposeSender() : composeSender;
        if (account == null) {
            toast("请先选择发件账号");
            return;
        }
        Models.Account sender = account;
        runAsync("发送中...", () -> {
            JSONObject payload = composePayload(sender, to, cc, bcc, subject, body);
            JSONObject result;
            try {
                if ("external".equals(sender.type)) {
                    result = api.post("/imap/api/accounts/" + encode(sender.id) + "/send", payload);
                } else {
                    result = api.post("/api/send", payload);
                }
            } catch (Exception e) {
                payload.put("error", e.getMessage());
                try {
                    api.post("/api/outbox", payload);
                } catch (Exception ignored) {
                    // Preserve the original sending error for the user.
                }
                throw e;
            }
            if (draftId != null && !draftId.isEmpty()) api.delete("/api/drafts/" + draftId);
            return result;
        }, result -> {
            toast("已发送");
            navIndex = 1;
            loadMails(false);
        });
    }

    private void renderSettings() {
        currentScreen = "settings";
        setHeader("设置", "偏好与设备");
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(BG);
        LinearLayout box = column(dp(8));
        box.setPadding(dp(12), dp(10), dp(12), dp(24));
        scroll.addView(box);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));

        box.addView(settingsStatusCard());
        box.addView(sectionHeader("同步与缓存", "本地缓存优先，后台静默更新"));
        box.addView(settingsSyncCard());

        Set<String> enabledGroups = notificationGroups();
        box.addView(sectionHeader("新邮件通知", notifyEnabled ? "已开启" : "已关闭"));
        box.addView(settingsNotificationCard(enabledGroups));

        box.addView(sectionHeader("手机端翻译", mobileTranslateSummary()));
        box.addView(settingsMobileTranslationCard());

        box.addView(sectionHeader("安全", "仅退出当前手机"));
        box.addView(settingsSecurityCard());
    }

    private View settingsStatusCard() {
        LinearLayout card = panel(dp(18), dp(16));
        card.setBackground(bg(PRIMARY_DARK, 28, PRIMARY_DARK, 0));

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        TextView label = text("Memail Mobile", 13, Color.rgb(198, 226, 224), true);
        TextView value = text(accounts.size() + " 个账号", 27, Color.WHITE, true);
        value.setPadding(0, dp(6), 0, dp(3));
        TextView meta = text(totalUnread() + " 封未读 · " + allGroups().size() + " 个分组", 13, Color.rgb(220, 235, 233), false);
        copy.addView(label);
        copy.addView(value);
        copy.addView(meta);
        top.addView(copy, new LinearLayout.LayoutParams(0, -2, 1));

        TextView mark = text("⚙", 24, Color.WHITE, true);
        mark.setGravity(Gravity.CENTER);
        mark.setBackground(bg(Color.rgb(21, 128, 132), 24, Color.rgb(21, 128, 132), 0));
        top.addView(mark, new LinearLayout.LayoutParams(dp(48), dp(48)));
        card.addView(top);

        LinearLayout chips = new LinearLayout(this);
        chips.setOrientation(LinearLayout.HORIZONTAL);
        chips.setPadding(0, dp(12), 0, 0);
        chips.addView(settingsMiniChip(notifyEnabled ? "通知开启" : "通知关闭"));
        chips.addView(settingsMiniChip("本地缓存"));
        chips.addView(settingsMiniChip("Token 设备"));
        card.addView(chips);
        return card;
    }

    private View settingsSyncCard() {
        LinearLayout card = panel(dp(14), dp(8));
        card.setBackground(bg(Color.WHITE, 24, SOFT_LINE, 1));
        card.addView(settingsRow("◎", "服务端", server, "当前手机通过设备 Token 访问服务端 API", null, "", false));
        card.addView(settingsDivider());
        card.addView(settingsRow("⇄", "最近缓存", lastSyncText(), "打开时优先读取本地缓存，后台静默补齐", null, "", false));
        card.addView(settingsDivider());
        card.addView(settingsRow("⟳", "刷新账户和邮件", "立即同步", "手动触发一次账户、规则和邮件缓存更新", v -> fetchBootstrapThenAccounts(false, true), "刷新", true));
        return card;
    }

    private View settingsNotificationCard(Set<String> enabledGroups) {
        LinearLayout card = panel(dp(14), dp(8));
        card.setBackground(bg(Color.WHITE, 24, SOFT_LINE, 1));
        card.addView(settingsRow(
            "●",
            "新邮件通知",
            notifyEnabled ? "已开启" : "已关闭",
            notifyEnabled ? "收到新邮件时会显示系统通知" : "当前不会弹出新邮件通知",
            v -> {
                notifyEnabled = !notifyEnabled;
                prefs.edit().putBoolean("notify", notifyEnabled).apply();
                renderSettings();
            },
            notifyEnabled ? "关闭" : "开启",
            true
        ));

        card.addView(settingsDivider());
        TextView scope = text(enabledGroups.isEmpty() ? "通知范围：所有分组" : "通知范围：" + enabledGroups.size() + " 个分组", 13, MUTED, false);
        scope.setPadding(dp(4), dp(10), dp(4), dp(6));
        card.addView(scope);

        Set<String> groups = allGroups();
        if (groups.isEmpty()) {
            TextView emptyGroups = text("暂无分组。添加邮箱后可在这里选择哪些分组允许通知。", 13, MUTED, false);
            emptyGroups.setPadding(dp(4), dp(8), dp(4), dp(10));
            card.addView(emptyGroups);
            return card;
        }
        for (String group : groups) {
            boolean enabled = enabledGroups.isEmpty() || enabledGroups.contains(group);
            card.addView(settingsGroupRow(group, enabled, groupUnread(group)));
        }
        return card;
    }

    private View settingsMobileTranslationCard() {
        LinearLayout card = panel(dp(14), dp(10));
        card.setBackground(bg(Color.WHITE, 24, SOFT_LINE, 1));

        boolean enabled = prefs.getBoolean("mobile_translate_enabled", false);
        String provider = prefs.getString("mobile_translate_provider", "baidu");
        card.addView(settingsRow(
            "译",
            "手机端翻译",
            enabled ? "已开启" : "未开启",
            "开启后手机直接调用所选翻译渠道，不再使用服务端 AI 默认模型",
            v -> {
                prefs.edit().putBoolean("mobile_translate_enabled", !enabled).apply();
                renderSettings();
            },
            enabled ? "关闭" : "开启",
            true
        ));
        card.addView(settingsDivider());

        LinearLayout providers = new LinearLayout(this);
        providers.setOrientation(LinearLayout.HORIZONTAL);
        providers.setPadding(dp(4), dp(10), dp(4), dp(8));
        TextView baidu = settingsProviderButton("百度翻译", "baidu".equals(provider), v -> {
            prefs.edit().putString("mobile_translate_provider", "baidu").apply();
            renderSettings();
        });
        TextView tencent = settingsProviderButton("腾讯翻译", "tencent".equals(provider), v -> {
            prefs.edit().putString("mobile_translate_provider", "tencent").apply();
            renderSettings();
        });
        TextView googleCloud = settingsProviderButton("Google", "google_cloud".equals(provider), v -> {
            prefs.edit().putString("mobile_translate_provider", "google_cloud").apply();
            renderSettings();
        });
        providers.addView(baidu, new LinearLayout.LayoutParams(0, dp(42), 1));
        LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(0, dp(42), 1);
        tp.setMargins(dp(8), 0, 0, 0);
        providers.addView(tencent, tp);
        LinearLayout.LayoutParams gp = new LinearLayout.LayoutParams(0, dp(42), 1);
        gp.setMargins(dp(8), 0, 0, 0);
        providers.addView(googleCloud, gp);
        card.addView(providers);

        if ("tencent".equals(provider)) {
            EditText secretId = input("腾讯 SecretId", prefs.getString("mobile_tencent_secret_id", ""), false);
            EditText secretKey = input("腾讯 SecretKey", prefs.getString("mobile_tencent_secret_key", ""), true);
            EditText region = input("地域，例如 ap-guangzhou", prefs.getString("mobile_tencent_region", "ap-guangzhou"), false);
            card.addView(secretId);
            card.addView(secretKey);
            card.addView(region);
            card.addView(settingsSaveButton("保存腾讯翻译配置", v -> {
                prefs.edit()
                    .putString("mobile_tencent_secret_id", secretId.getText().toString().trim())
                    .putString("mobile_tencent_secret_key", secretKey.getText().toString().trim())
                    .putString("mobile_tencent_region", nonEmpty(region.getText().toString().trim(), "ap-guangzhou"))
                    .putString("mobile_translate_provider", "tencent")
                    .putBoolean("mobile_translate_enabled", true)
                    .apply();
                toast("手机端腾讯翻译配置已保存");
                renderSettings();
            }));
        } else if ("google_cloud".equals(provider)) {
            EditText apiKey = input("Google Cloud Translation API Key", prefs.getString("mobile_google_cloud_api_key", ""), true);
            card.addView(apiKey);
            card.addView(settingsSaveButton("保存 Google Cloud 翻译配置", v -> {
                prefs.edit()
                    .putString("mobile_google_cloud_api_key", apiKey.getText().toString().trim())
                    .putString("mobile_translate_provider", "google_cloud")
                    .putBoolean("mobile_translate_enabled", true)
                    .apply();
                toast("手机端 Google Cloud 翻译配置已保存");
                renderSettings();
            }));
        } else {
            EditText appId = input("百度翻译 AppID", prefs.getString("mobile_baidu_app_id", ""), false);
            EditText key = input("百度翻译密钥", prefs.getString("mobile_baidu_key", ""), true);
            card.addView(appId);
            card.addView(key);
            card.addView(settingsSaveButton("保存百度翻译配置", v -> {
                prefs.edit()
                    .putString("mobile_baidu_app_id", appId.getText().toString().trim())
                    .putString("mobile_baidu_key", key.getText().toString().trim())
                    .putString("mobile_translate_provider", "baidu")
                    .putBoolean("mobile_translate_enabled", true)
                    .apply();
                toast("手机端百度翻译配置已保存");
                renderSettings();
            }));
        }
        return card;
    }

    private TextView settingsProviderButton(String label, boolean active, View.OnClickListener listener) {
        TextView btn = text(label, 14, active ? Color.WHITE : PRIMARY_DARK, true);
        btn.setGravity(Gravity.CENTER);
        btn.setBackground(bg(active ? PRIMARY : Color.rgb(242, 247, 247), 18, active ? PRIMARY : LINE, 1));
        btn.setOnClickListener(listener);
        return btn;
    }

    private TextView settingsSaveButton(String label, View.OnClickListener listener) {
        TextView btn = primaryButton(label);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, dp(48));
        lp.setMargins(0, dp(8), 0, dp(2));
        btn.setLayoutParams(lp);
        btn.setOnClickListener(listener);
        return btn;
    }

    private String mobileTranslateSummary() {
        if (!prefs.getBoolean("mobile_translate_enabled", false)) return "未开启";
        String provider = prefs.getString("mobile_translate_provider", "baidu");
        if ("tencent".equals(provider)) return "腾讯翻译";
        if ("google_cloud".equals(provider)) return "Google Cloud";
        return "百度翻译";
    }

    private View settingsSecurityCard() {
        LinearLayout card = panel(dp(14), dp(8));
        card.setBackground(bg(Color.WHITE, 24, SOFT_LINE, 1));
        card.addView(settingsRow("⌁", "设备 Token", "已连接", "退出只删除当前手机保存的 Token，不影响服务端和其它设备", null, "", false));
        card.addView(settingsDivider());
        card.addView(settingsRow("↯", "退出当前手机", "清除本机访问凭据", "下次打开需要重新连接服务端", v -> {
            prefs.edit().remove("token").apply();
            token = "";
            api.configure(server, "");
            BackgroundSyncService.cancel(this);
            RealtimeSyncService.stop(this);
            stopEventStream();
            stopForegroundRefreshLoop();
            if (navBar != null) navBar.setVisibility(View.GONE);
            showLogin();
        }, "退出", true));
        return card;
    }

    private View settingsRow(String iconText, String titleText, String valueText, String noteText, View.OnClickListener listener, String actionText, boolean clickable) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(4), dp(10), dp(4), dp(10));
        if (clickable) row.setOnClickListener(listener);

        TextView icon = text(iconText, 18, PRIMARY, true);
        icon.setGravity(Gravity.CENTER);
        icon.setBackground(bg(PRIMARY_SOFT, 16, PRIMARY_SOFT, 0));
        row.addView(icon, new LinearLayout.LayoutParams(dp(42), dp(42)));

        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        copy.setPadding(dp(12), 0, dp(10), 0);
        TextView titleView = text(titleText, 15, TEXT, true);
        TextView valueView = text(valueText, 13, PRIMARY_DARK, true);
        TextView noteView = text(noteText, 12, MUTED, false);
        noteView.setLineSpacing(dp(1), 1.0f);
        copy.addView(titleView);
        copy.addView(valueView);
        copy.addView(noteView);
        row.addView(copy, new LinearLayout.LayoutParams(0, -2, 1));

        if (!actionText.isEmpty()) {
            TextView action = settingsPill(actionText, clickable);
            row.addView(action);
        }
        return row;
    }

    private View settingsGroupRow(String group, boolean enabled, int unread) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(4), dp(7), dp(4), dp(7));
        row.setOnClickListener(v -> {
            Set<String> current = notificationGroups();
            if (current.isEmpty()) current.addAll(allGroups());
            if (current.contains(group)) current.remove(group);
            else current.add(group);
            if (current.size() == allGroups().size()) current.clear();
            saveNotificationGroups(current);
            renderSettings();
        });

        TextView state = text(enabled ? "✓" : "○", 17, enabled ? Color.WHITE : MUTED, true);
        state.setGravity(Gravity.CENTER);
        state.setBackground(bg(enabled ? PRIMARY : Color.rgb(238, 243, 244), 15, enabled ? PRIMARY : Color.rgb(238, 243, 244), 0));
        row.addView(state, new LinearLayout.LayoutParams(dp(30), dp(30)));

        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        copy.setPadding(dp(12), 0, dp(8), 0);
        TextView name = text(group, 15, TEXT, true);
        TextView meta = text(unread > 0 ? unread + " 封未读" : "暂无未读", 12, MUTED, false);
        copy.addView(name);
        copy.addView(meta);
        row.addView(copy, new LinearLayout.LayoutParams(0, -2, 1));

        row.addView(settingsPill(enabled ? "通知" : "静默", enabled));
        return row;
    }

    private TextView settingsMiniChip(String value) {
        TextView chip = text(value, 12, Color.WHITE, true);
        chip.setGravity(Gravity.CENTER);
        chip.setPadding(dp(9), dp(5), dp(9), dp(5));
        chip.setBackground(bg(Color.rgb(33, 118, 123), 13, Color.rgb(33, 118, 123), 0));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-2, -2);
        lp.setMargins(0, 0, dp(8), 0);
        chip.setLayoutParams(lp);
        return chip;
    }

    private TextView settingsPill(String value, boolean active) {
        TextView pill = text(value, 12, active ? PRIMARY_DARK : MUTED, true);
        pill.setGravity(Gravity.CENTER);
        pill.setSingleLine(true);
        pill.setPadding(dp(10), dp(6), dp(10), dp(6));
        pill.setBackground(bg(active ? PRIMARY_SOFT : Color.rgb(239, 244, 245), 14, active ? Color.rgb(186, 218, 216) : Color.rgb(226, 234, 235), 1));
        return pill;
    }

    private View settingsDivider() {
        View line = new View(this);
        line.setBackgroundColor(SOFT_LINE);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, 1);
        lp.setMargins(dp(58), 0, dp(4), 0);
        line.setLayoutParams(lp);
        return line;
    }

    private String lastSyncText() {
        long value = prefs.getLong("last_bootstrap_refresh_at", 0L);
        if (value <= 0) return "尚未缓存";
        return android.text.format.DateFormat.format("MM-dd HH:mm", value).toString();
    }

    private void checkNotifications() {
        if (!notifyEnabled) return;
        int total = 0;
        Set<String> enabledGroups = notificationGroups();
        for (Models.Account account : accounts) {
            String group = account.group == null || account.group.isEmpty() ? "未分组" : account.group;
            if (enabledGroups.isEmpty() || enabledGroups.contains(group)) total += Math.max(0, account.unread);
        }
        int old = prefs.getInt("last_unread_total", 0);
        prefs.edit().putInt("last_unread_total", total).apply();
        if (total > old && old > 0) notifyMail("Memail 新邮件", "未读邮件增加到 " + total + " 封");
    }

    private int totalUnread() {
        int total = 0;
        for (Models.Account account : accounts) total += Math.max(0, account.unread);
        return total;
    }

    private int groupUnread(String groupName) {
        int total = 0;
        for (Models.Account account : accounts) {
            String group = account.group == null || account.group.isEmpty() ? "未分组" : account.group;
            if (group.equals(groupName)) total += Math.max(0, account.unread);
        }
        return total;
    }

    private Set<String> allGroups() {
        Set<String> groups = new LinkedHashSet<>();
        for (Models.Account account : accounts) {
            groups.add(account.group == null || account.group.isEmpty() ? "未分组" : account.group);
        }
        return groups;
    }

    private String virtualTitle(String mode, String group) {
        if ("keyword".equals(mode)) return selectedKeywordRule == null ? "关键词监控" : selectedKeywordRule.name;
        if ("global_unread".equals(mode)) return "全部未读邮件";
        if ("global_all".equals(mode)) return "全部邮件";
        if ("group_unread".equals(mode)) return nonEmpty(group, "未分组") + " · 未读邮件";
        if ("group_all".equals(mode)) return nonEmpty(group, "未分组") + " · 所有邮件";
        return "邮件";
    }

    private Set<String> notificationGroups() {
        String saved = prefs.getString("notify_groups", "");
        Set<String> groups = new HashSet<>();
        if (saved == null || saved.isEmpty()) return groups;
        String[] parts = saved.split("\\|", -1);
        for (String part : parts) if (!part.isEmpty()) groups.add(part);
        return groups;
    }

    private void saveNotificationGroups(Set<String> groups) {
        StringBuilder sb = new StringBuilder();
        for (String group : groups) {
            if (sb.length() > 0) sb.append('|');
            sb.append(group);
        }
        prefs.edit().putString("notify_groups", sb.toString()).apply();
    }

    private void notifyMail(String title, String text) {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        Intent intent = new Intent(this, MainActivity.class);
        intent.setAction("com.memail.mobile.OPEN_MAIL");
        intent.putExtra("open", "mail");
        intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pendingIntent = PendingIntent.getActivity(this, 1001, intent, flags);
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
        ((NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE)).notify(1001, notification);
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 10);
        }
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationChannel channel = new NotificationChannel(CHANNEL_MAIL, "Memail 新邮件", NotificationManager.IMPORTANCE_DEFAULT);
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }

    private interface Task {
        Object run() throws Exception;
    }

    private void runAsync(String loading, Task task, Consumer<JSONObject> success) {
        boolean visible = loading != null;
        if (visible) setLoading(true, loading);
        io.submit(() -> {
            try {
                Object result = task.run();
                JSONObject object = result instanceof JSONObject ? (JSONObject) result : new JSONObject().put("value", String.valueOf(result));
                runOnUiThread(() -> {
                    if (visible) setLoading(false, "");
                    try {
                        success.accept(object);
                    } catch (Exception uiError) {
                        toast("界面渲染失败: " + nonEmpty(uiError.getMessage(), uiError.getClass().getSimpleName()));
                    }
                });
            } catch (Exception e) {
                runOnUiThread(() -> {
                    mailPageLoading = false;
                    if (visible) {
                        setLoading(false, "");
                        toast(e.getMessage());
                    }
                });
            }
        });
    }

    private void setLoading(boolean loading, String message) {
        progress.setVisibility(loading ? View.VISIBLE : View.GONE);
        if (loading && message != null) subtitle.setText(message);
        if (!loading) subtitle.setText("");
    }

    private void setHeader(String main, String sub) {
        title.setText(main);
        subtitle.setText("");
    }

    private EditText input(String hint, String value, boolean password) {
        EditText edit = new EditText(this);
        edit.setHint(hint);
        edit.setText(value == null ? "" : value);
        edit.setTextSize(14);
        edit.setSingleLine(!hint.equals("正文"));
        edit.setPadding(dp(14), dp(11), dp(14), dp(11));
        edit.setBackground(bg(Color.WHITE, 16, LINE, 1));
        if (password) edit.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.setMargins(0, dp(6), 0, dp(6));
        edit.setLayoutParams(lp);
        return edit;
    }

    private TextView text(String value, int sp, int color, boolean bold) {
        TextView tv = new TextView(this);
        tv.setText(value == null ? "" : value);
        tv.setTextSize(sp);
        tv.setTextColor(color);
        tv.setLineSpacing(dp(2), 1.0f);
        if (bold) tv.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        return tv;
    }

    private LinearLayout column(int gapPadding) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(0, gapPadding, 0, gapPadding);
        return box;
    }

    private View card(View child) {
        LinearLayout card = cardContainer();
        card.addView(child);
        return card;
    }

    private LinearLayout panel(int horizontalPadding, int verticalPadding) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(horizontalPadding, verticalPadding, horizontalPadding, verticalPadding);
        card.setBackground(bg(CARD, 18, Color.rgb(225, 233, 235), 1));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.setMargins(0, dp(6), 0, dp(6));
        card.setLayoutParams(lp);
        return card;
    }

    private LinearLayout cardContainer() {
        return panel(dp(14), dp(12));
    }

    private View dashboardCard(String titleText, String valueText, String metaText) {
        LinearLayout card = panel(dp(18), dp(16));
        card.setBackground(bg(PRIMARY_DARK, 28, PRIMARY_DARK, 0));
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);

        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        TextView titleView = text(titleText, 13, Color.rgb(198, 226, 224), true);
        TextView valueView = text(valueText, 26, Color.WHITE, true);
        valueView.setPadding(0, dp(6), 0, dp(3));
        TextView metaView = text(metaText, 13, Color.rgb(220, 235, 233), false);
        copy.addView(titleView);
        copy.addView(valueView);
        copy.addView(metaView);
        top.addView(copy, new LinearLayout.LayoutParams(0, -2, 1));

        TextView mark = text("✉", 25, Color.WHITE, true);
        mark.setGravity(Gravity.CENTER);
        mark.setBackground(bg(Color.rgb(21, 128, 132), 24, Color.rgb(21, 128, 132), 0));
        top.addView(mark, new LinearLayout.LayoutParams(dp(48), dp(48)));
        card.addView(top);
        return card;
    }

    private View sectionHeader(String titleText, String metaText) {
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(2), dp(12), dp(2), dp(4));
        TextView titleView = text(titleText, 14, TEXT, true);
        TextView metaView = text(metaText, 12, MUTED, false);
        metaView.setGravity(Gravity.RIGHT);
        header.addView(titleView, new LinearLayout.LayoutParams(0, -2, 1));
        header.addView(metaView, new LinearLayout.LayoutParams(0, -2, 1));
        return header;
    }

    private TextView tinyChip(String value) {
        TextView chip = text(value, 11, PRIMARY_DARK, true);
        chip.setGravity(Gravity.CENTER);
        chip.setSingleLine(true);
        chip.setPadding(dp(8), dp(4), dp(8), dp(4));
        chip.setBackground(bg(PRIMARY_SOFT, 12, PRIMARY_SOFT, 0));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-2, -2);
        lp.setMargins(0, 0, dp(6), 0);
        chip.setLayoutParams(lp);
        return chip;
    }

    private TextView primaryButton(String text) {
        TextView btn = text(text, 15, Color.WHITE, true);
        btn.setGravity(Gravity.CENTER);
        btn.setPadding(dp(14), dp(12), dp(14), dp(12));
        btn.setBackground(bg(PRIMARY, 18, PRIMARY, 0));
        return btn;
    }

    private TextView outlineButton(String text, View.OnClickListener listener) {
        TextView btn = text(text, 12, PRIMARY_DARK, true);
        btn.setGravity(Gravity.CENTER);
        btn.setPadding(dp(12), dp(9), dp(12), dp(9));
        btn.setBackground(bg(SURFACE, 16, LINE, 1));
        btn.setOnClickListener(listener);
        return btn;
    }

    private TextView detailAction(String icon, String label, View.OnClickListener listener) {
        TextView btn = text(icon + "  " + label, 13, PRIMARY_DARK, true);
        btn.setGravity(Gravity.CENTER);
        btn.setSingleLine(true);
        btn.setPadding(dp(13), dp(10), dp(13), dp(10));
        btn.setBackground(bg(Color.WHITE, 18, Color.rgb(204, 222, 224), 1));
        btn.setOnClickListener(listener);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-2, dp(42));
        lp.setMargins(0, dp(4), dp(8), dp(4));
        btn.setLayoutParams(lp);
        return btn;
    }

    private TextView sectionLabel(String value) {
        TextView label = text(value, 15, TEXT, true);
        label.setGravity(Gravity.CENTER_VERTICAL);
        return label;
    }

    private View readingSection(String titleText, String html, int minHeight) {
        LinearLayout card = panel(dp(0), dp(0));
        card.setBackground(bg(Color.WHITE, 22, SOFT_LINE, 1));
        TextView label = sectionLabel(titleText);
        label.setPadding(dp(18), dp(16), dp(18), dp(8));
        card.addView(label);
        WebView web = mailWebView();
        web.loadDataWithBaseURL(api.baseUrl(), wrapMailHtml(html), "text/html", "UTF-8", null);
        card.addView(web, new LinearLayout.LayoutParams(-1, minHeight));
        return card;
    }

    private TextView actionText(String text, boolean active) {
        TextView chip = text(text, 13, active ? Color.WHITE : PRIMARY_DARK, true);
        chip.setGravity(Gravity.CENTER);
        chip.setPadding(dp(16), dp(8), dp(16), dp(8));
        chip.setBackground(bg(active ? PRIMARY : Color.WHITE, 18, active ? PRIMARY : LINE, 1));
        return chip;
    }

    private TextView badge(String value, int bgColor, int fgColor) {
        TextView tv = text(value, 11, fgColor, true);
        tv.setGravity(Gravity.CENTER);
        tv.setPadding(dp(8), dp(3), dp(8), dp(3));
        tv.setBackground(bg(bgColor, 12, bgColor, 0));
        return tv;
    }

    private TextView avatarView(String sender) {
        String label = avatarLabel(sender);
        int color = avatarColor(sender);
        TextView tv = text(label, 18, Color.WHITE, true);
        tv.setGravity(Gravity.CENTER);
        tv.setBackground(bg(color, 23, color, 0));
        return tv;
    }

    private View empty(String message) {
        TextView tv = text(message, 15, MUTED, false);
        tv.setGravity(Gravity.CENTER);
        tv.setPadding(dp(16), dp(60), dp(16), dp(60));
        return tv;
    }

    private Models.Folder folder(String type, String accountId, String path, String name, int count) {
        Models.Folder folder = new Models.Folder();
        folder.accountType = type;
        folder.accountId = accountId;
        folder.path = path;
        folder.name = name == null || name.isEmpty() ? path : name;
        folder.count = count;
        return folder;
    }

    private List<Models.Folder> folderSnapshot() {
        synchronized (listLock) {
            return new ArrayList<>(folders);
        }
    }

    private List<Models.Mail> mailSnapshot() {
        synchronized (listLock) {
            return new ArrayList<>(mails);
        }
    }

    private void replaceFolderList(List<Models.Folder> next) {
        synchronized (listLock) {
            folders.clear();
            if (next != null) folders.addAll(next);
        }
    }

    private void replaceMailList(List<Models.Mail> next, boolean clearFirst) {
        synchronized (listLock) {
            if (clearFirst) mails.clear();
            if (next != null) mails.addAll(next);
        }
    }

    private void removeMailFromList(Models.Mail target) {
        synchronized (listLock) {
            for (int i = mails.size() - 1; i >= 0; i--) {
                if (sameMail(mails.get(i), target)) mails.remove(i);
            }
        }
    }

    private JSONArray folderListToJson(List<Models.Folder> items) throws Exception {
        JSONArray arr = new JSONArray();
        if (items == null) return arr;
        for (Models.Folder item : items) {
            arr.put(new JSONObject()
                .put("accountType", item.accountType)
                .put("accountId", item.accountId)
                .put("path", item.path)
                .put("name", item.name)
                .put("count", item.count));
        }
        return arr;
    }

    private List<Models.Folder> foldersFromJson(JSONArray arr) {
        List<Models.Folder> list = new ArrayList<>();
        if (arr == null) return list;
        for (int i = 0; i < arr.length(); i++) {
            JSONObject item = arr.optJSONObject(i);
            if (item == null) continue;
            list.add(folder(
                Json.anyStr(item, "accountType", "account_type"),
                Json.anyStr(item, "accountId", "account_id"),
                Json.str(item, "path"),
                Json.str(item, "name"),
                item.optInt("count", 0)
            ));
        }
        return list;
    }

    private JSONArray mailListToJson(List<Models.Mail> items) throws Exception {
        JSONArray arr = new JSONArray();
        if (items == null) return arr;
        for (Models.Mail item : items) {
            arr.put(mailToJson(item));
        }
        return arr;
    }

    private JSONObject mailToJson(Models.Mail item) throws Exception {
        if (item == null) return new JSONObject();
        return new JSONObject()
            .put("accountType", item.accountType)
            .put("accountId", item.accountId)
            .put("folder", item.folder)
            .put("id", item.id)
            .put("sender", item.sender)
            .put("subject", item.subject)
            .put("preview", item.preview)
            .put("date", item.date)
            .put("kind", item.kind)
            .put("to", item.to)
            .put("text", item.text)
            .put("html", item.html)
            .put("error", item.error)
            .put("seen", item.seen)
            .put("favorite", item.favorite);
    }

    private List<Models.Mail> mailsFromJson(JSONArray arr) {
        List<Models.Mail> list = new ArrayList<>();
        if (arr == null) return list;
        for (int i = 0; i < arr.length(); i++) {
            JSONObject item = arr.optJSONObject(i);
            if (item == null) continue;
            list.add(mailFromJson(item));
        }
        return list;
    }

    private Models.Mail mailFromJson(JSONObject item) {
        Models.Mail mail = new Models.Mail();
        if (item == null) return mail;
        mail.accountType = Json.anyStr(item, "accountType", "account_type");
        mail.accountId = Json.anyStr(item, "accountId", "account_id");
        mail.folder = Json.str(item, "folder");
        mail.id = Json.str(item, "id");
        mail.sender = Json.str(item, "sender");
        mail.subject = Json.str(item, "subject");
        mail.preview = Json.str(item, "preview");
        mail.date = Json.str(item, "date");
        mail.kind = Json.str(item, "kind");
        mail.to = Json.str(item, "to");
        mail.text = Json.str(item, "text");
        mail.html = Json.str(item, "html");
        mail.error = Json.str(item, "error");
        mail.seen = item.optBoolean("seen", true);
        mail.favorite = item.optBoolean("favorite", false);
        return mail;
    }

    private JSONArray asArray(JSONObject object) {
        JSONArray arr = object.optJSONArray("items");
        if (arr == null) arr = object.optJSONArray("folders");
        if (arr == null) arr = object.optJSONArray("data");
        return arr == null ? new JSONArray() : arr;
    }

    private static String encode(String value) {
        return Uri.encode(value == null ? "" : value);
    }

    private static String join(List<String> values, String separator) {
        StringBuilder sb = new StringBuilder();
        for (String value : values) {
            if (sb.length() > 0) sb.append(separator);
            sb.append(value);
        }
        return sb.toString();
    }

    private static String escape(String value) {
        return value == null ? "" : Html.escapeHtml(value);
    }

    private static String plainTextHtml(String value) {
        return "<pre class=\"memail-plain\">" + escape(value) + "</pre>";
    }

    private WebView mailWebView() {
        WebView web = new WebView(this);
        WebSettings settings = web.getSettings();
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setSupportZoom(true);
        settings.setTextZoom(106);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        settings.setDomStorageEnabled(true);
        web.setInitialScale(100);
        web.setFocusable(true);
        web.setFocusableInTouchMode(true);
        web.setNestedScrollingEnabled(true);
        web.setBackgroundColor(Color.TRANSPARENT);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.setVerticalScrollBarEnabled(true);
        web.setHorizontalScrollBarEnabled(true);
        ScaleGestureDetector scaleDetector = new ScaleGestureDetector(this, new ScaleGestureDetector.SimpleOnScaleGestureListener() {
            @Override
            public boolean onScaleBegin(ScaleGestureDetector detector) {
                requestAncestorTouchInterception(web, true);
                web.requestFocusFromTouch();
                return true;
            }

            @Override
            public boolean onScale(ScaleGestureDetector detector) {
                float factor = detector.getScaleFactor();
                if (Float.isNaN(factor) || Float.isInfinite(factor)) return false;
                factor = Math.max(0.75f, Math.min(1.35f, factor));
                web.zoomBy(factor);
                return true;
            }

            @Override
            public void onScaleEnd(ScaleGestureDetector detector) {
                web.postDelayed(() -> requestAncestorTouchInterception(web, false), 160);
            }
        });
        web.setOnTouchListener((view, event) -> {
            boolean scaling = event.getPointerCount() > 1 || scaleDetector.isInProgress();
            if (event.getActionMasked() == MotionEvent.ACTION_DOWN) {
                requestAncestorTouchInterception(view, true);
            }
            if (scaling) {
                requestAncestorTouchInterception(view, true);
                scaleDetector.onTouchEvent(event);
                int action = event.getActionMasked();
                if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) {
                    view.postDelayed(() -> requestAncestorTouchInterception(view, false), 160);
                }
                return true;
            }
            if (event.getActionMasked() == MotionEvent.ACTION_UP || event.getActionMasked() == MotionEvent.ACTION_CANCEL) {
                requestAncestorTouchInterception(view, false);
            }
            return false;
        });
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                fitMailContentToViewport(view);
            }

            @Override
            public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                ViewParent parent = view == null ? null : view.getParent();
                if (parent instanceof ViewGroup) {
                    ViewGroup group = (ViewGroup) parent;
                    int index = group.indexOfChild(view);
                    group.removeView(view);
                    TextView fallback = text("邮件内容渲染进程已恢复，请重新打开这封邮件。", 15, MUTED, false);
                    fallback.setGravity(Gravity.CENTER);
                    fallback.setPadding(dp(18), dp(40), dp(18), dp(40));
                    group.addView(fallback, Math.max(0, index), new LinearLayout.LayoutParams(-1, dp(260)));
                }
                toast("邮件内容过大，已保护应用不闪退");
                return true;
            }
        });
        return web;
    }

    private void fitMailContentToViewport(WebView view) {
        if (view == null) return;
        view.postDelayed(() -> view.evaluateJavascript(
            "(function(){"
                + "document.querySelectorAll('table').forEach(function(t){"
                + "if(t.closest('.memail-table-wrap'))return;"
                + "var w=document.createElement('div');"
                + "w.className='memail-table-wrap';"
                + "t.parentNode.insertBefore(w,t);"
                + "w.appendChild(t);"
                + "});"
                + "document.querySelectorAll('[width]').forEach(function(el){"
                + "var n=(el.getAttribute('width')||'').replace(/[^0-9.]/g,'');"
                + "if(n&&parseFloat(n)>document.documentElement.clientWidth&&!/^(TABLE|TBODY|THEAD|TFOOT|TR|TD|TH|COL|COLGROUP)$/.test(el.tagName)){el.style.maxWidth='100%';el.style.width='auto';}"
                + "});"
                + "document.querySelectorAll('[style]').forEach(function(el){"
                + "if(/^(TABLE|TBODY|THEAD|TFOOT|TR|TD|TH|COL|COLGROUP)$/.test(el.tagName))return;"
                + "var s=el.getAttribute('style')||'';"
                + "if(/(?:width|min-width|max-width)\\s*:\\s*\\d{3,}/i.test(s)){el.style.maxWidth='100%';el.style.width='auto';el.style.minWidth='0';}"
                + "});"
                + "return true;"
                + "})()",
            null
        ), 60);
    }

    private static void requestAncestorTouchInterception(View view, boolean disallow) {
        if (view == null) return;
        ViewParent parent = view.getParent();
        while (parent != null) {
            parent.requestDisallowInterceptTouchEvent(disallow);
            parent = parent.getParent();
        }
    }

    private void resizeMailWebView(WebView view, long delayMs) {
        if (view == null) return;
        view.postDelayed(() -> view.evaluateJavascript(
                    "(function(){var b=document.body||{},e=document.documentElement||{},s=document.scrollingElement||{};"
                        + "return Math.max(b.scrollHeight||0,b.offsetHeight||0,e.clientHeight||0,e.scrollHeight||0,e.offsetHeight||0,s.scrollHeight||0);})()",
                    value -> {
                        try {
                            String clean = value == null ? "" : value.replace("\"", "").trim();
                            if (clean.isEmpty() || "null".equals(clean)) return;
                            int cssHeight = (int) Math.ceil(Double.parseDouble(clean));
                            int px = Math.max(dp(320), (int) (cssHeight * getResources().getDisplayMetrics().density) + dp(56));
                            ViewGroup.LayoutParams lp = view.getLayoutParams();
                            if (lp != null && Math.abs(lp.height - px) > dp(16)) {
                                lp.height = px;
                                view.setLayoutParams(lp);
                            }
                        } catch (Exception ignored) {
                        }
                    }), delayMs);
    }

    private static String wrapMailHtml(String body) {
        String safeBody = normalizeMailBodyHtml(body == null ? "" : body);
        return "<!doctype html><html><head>"
            + "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, minimum-scale=0.25, maximum-scale=5, user-scalable=yes\">"
            + "<style>"
            + "html{margin:0;padding:0;background:transparent;width:100%;overflow-x:hidden;}"
            + "body{margin:0;padding:12px 12px 24px 12px;background:transparent;color:#14252c;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:16px;line-height:1.68;width:100%;max-width:100vw;overflow-x:hidden;box-sizing:border-box;word-break:normal;overflow-wrap:anywhere;touch-action:pan-x pan-y pinch-zoom;}"
            + "p{margin:0 0 14px 0;}div,section,article,header,footer,main,aside,span{max-width:100%!important;box-sizing:border-box;}"
            + "img,video{max-width:100%!important;height:auto!important;}"
            + "body>:not(.memail-table-wrap),body div:not(.memail-table-wrap),body section,body article{min-width:0!important;width:auto;}"
            + ".memail-table-wrap,table{display:block!important;width:100%!important;max-width:100%!important;overflow-x:auto!important;-webkit-overflow-scrolling:touch;box-sizing:border-box;}"
            + ".memail-table-wrap{margin:12px 0;border:1px solid #d8e5e7;border-radius:12px;background:#fff;}"
            + "table{margin:0!important;border-collapse:separate!important;border-spacing:0!important;border:0!important;background:#fff;}"
            + "tbody,thead,tfoot{display:table!important;min-width:100%;}"
            + "tr{display:table-row!important;}"
            + "td,th{display:table-cell!important;word-break:normal!important;overflow-wrap:normal!important;white-space:normal!important;min-width:76px;padding:9px 8px!important;border-right:1px solid #d8e5e7!important;border-bottom:1px solid #d8e5e7!important;vertical-align:top!important;box-sizing:border-box!important;}"
            + "th{background:#eef7f6!important;color:#14343b!important;font-weight:700!important;}"
            + "tr:nth-child(even) td{background:#fbfdfd;}"
            + "a{color:#0b7285;word-break:break-word;overflow-wrap:break-word;}"
            + "pre,.memail-plain{white-space:pre-wrap;font:16px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.7;margin:0;}"
            + "body>*{max-width:100%!important;}"
            + "*,*:before,*:after{box-sizing:border-box;}"
            + "</style></head><body>" + safeBody + "</body></html>";
    }

    private static String normalizeMailBodyHtml(String value) {
        if (value == null || value.isEmpty()) return "";
        String normalized = value.replaceAll("(?is)<meta\\b[^>]*name\\s*=\\s*['\"]?viewport['\"]?[^>]*>", "");
        normalized = normalized.replaceAll("(?is)<head\\b[^>]*>.*?</head>", "");
        Matcher bodyMatcher = Pattern.compile("(?is)<body\\b[^>]*>(.*?)</body>").matcher(normalized);
        if (bodyMatcher.find()) normalized = bodyMatcher.group(1);
        normalized = normalized.replaceAll("(?is)</?(?:html|body)\\b[^>]*>", "");
        return normalized;
    }

    private static String nonEmpty(String value, String fallback) {
        return value == null || value.isEmpty() ? fallback : value;
    }

    private static String cleanAddress(String value) {
        if (value == null) return "";
        String trimmed = value.trim();
        int lt = trimmed.indexOf('<');
        int gt = trimmed.indexOf('>');
        if (lt >= 0 && gt > lt) return trimmed.substring(lt + 1, gt).trim();
        return trimmed.replace("\"", "").trim();
    }

    private static String cleanPreview(String value) {
        if (value == null) return "";
        return value.replaceAll("\\s+", " ").trim();
    }

    private String mobileTranslatorIdentity() {
        if (!prefs.getBoolean("mobile_translate_enabled", false)) return "mobile-disabled";
        return prefs.getString("mobile_translate_provider", "baidu");
    }

    private static String mobileTranslatorModel(String provider) {
        if ("tencent".equals(provider)) return "tencent-tmt";
        if ("google_cloud".equals(provider)) return "cloud-translation-basic-v2";
        return "baidu-general";
    }

    private static String translationSourceHash(Models.Mail mail, JSONObject source, String translatorIdentity) {
        String raw = "mobile:" + nonEmpty(translatorIdentity, "mobile")
            + "\n" + nonEmpty(mail.subject, "")
            + "\n" + Json.str(source, "html")
            + "\n" + Json.str(source, "text");
        return sha256(raw);
    }

    private static String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest((value == null ? "" : value).getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : bytes) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception ignored) {
            return Integer.toHexString((value == null ? "" : value).hashCode());
        }
    }

    private static String shortDate(String value) {
        if (value == null) return "";
        String text = value.replace("T", " ");
        if (text.length() >= 16) return text.substring(0, 16);
        return text;
    }

    private static String shortMailDate(String value) {
        String text = shortDate(value);
        if (text.length() >= 16) return text.substring(5, 16);
        return text;
    }

    private static String avatarLabel(String sender) {
        String clean = nonEmpty(sender, "?").trim();
        if (clean.startsWith("\"") && clean.length() > 1) clean = clean.substring(1);
        if (clean.isEmpty()) return "?";
        int cp = clean.codePointAt(0);
        return new String(Character.toChars(cp)).toUpperCase();
    }

    private static int avatarColor(String sender) {
        int hash = Math.abs(nonEmpty(sender, "memail").hashCode());
        int[] colors = {
            Color.rgb(39, 110, 241),
            Color.rgb(3, 99, 104),
            Color.rgb(221, 134, 31),
            Color.rgb(126, 87, 194),
            Color.rgb(67, 160, 71),
            Color.rgb(84, 110, 122),
        };
        return colors[hash % colors.length];
    }

    private static String unreadSuffix(int unread) {
        return unread > 0 ? " · 未读 " + unread : "";
    }

    private void toast(String message) {
        Toast.makeText(this, message == null || message.isEmpty() ? "操作失败" : message, Toast.LENGTH_LONG).show();
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private int statusBarInset() {
        int id = getResources().getIdentifier("status_bar_height", "dimen", "android");
        return id > 0 ? getResources().getDimensionPixelSize(id) : 0;
    }

    private static void sleepQuietly(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }

    private GradientDrawable bg(int color, int radiusDp, int strokeColor, int strokeWidthDp) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(radiusDp));
        if (strokeWidthDp > 0) drawable.setStroke(dp(strokeWidthDp), strokeColor);
        return drawable;
    }
}
