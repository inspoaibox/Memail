package com.memail.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.Html;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
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

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URLEncoder;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

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

    private final ExecutorService io = Executors.newFixedThreadPool(4);
    private final ApiClient api = new ApiClient();
    private final List<Models.Account> accounts = new ArrayList<>();
    private final List<Models.Folder> folders = new ArrayList<>();
    private final List<Models.Mail> mails = new ArrayList<>();

    private SharedPreferences prefs;
    private LocalStore store;
    private Handler mainHandler;
    private Thread eventThread;
    private volatile boolean eventStreamRunning = false;
    private LinearLayout root;
    private LinearLayout content;
    private LinearLayout navBar;
    private TextView title;
    private TextView subtitle;
    private ProgressBar progress;
    private Models.Account selectedAccount;
    private Models.Folder selectedFolder;
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
    private String currentScreen = "boot";
    private Models.Mail currentDetailMail;
    private JSONObject currentDetailSource;

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
    protected void onResume() {
        super.onResume();
        startEventStream();
    }

    @Override
    protected void onPause() {
        super.onPause();
        stopEventStream();
    }

    @Override
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
        header.setPadding(dp(16), statusBarInset() + dp(8), dp(16), dp(8));
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

        title = text("Memail", 18, TEXT, true);
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
        root.addView(navBar, new LinearLayout.LayoutParams(-1, dp(88)));
    }

    private LinearLayout bottomNav() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER);
        bar.setPadding(dp(14), dp(9), dp(14), dp(14));
        bar.setBackgroundColor(BG);
        bar.setVisibility(token.isEmpty() ? View.GONE : View.VISIBLE);
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
            lp.setMargins(dp(3), 0, dp(3), 0);
            bar.addView(item, lp);
        }
        return bar;
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
        item.setOrientation(active ? LinearLayout.HORIZONTAL : LinearLayout.VERTICAL);
        item.setGravity(Gravity.CENTER);
        item.setPadding(active ? dp(12) : dp(4), dp(6), active ? dp(14) : dp(4), dp(6));
        item.setBackground(active
            ? bg(PRIMARY, 24, PRIMARY, 0)
            : bg(Color.WHITE, 22, Color.rgb(232, 239, 240), 1));

        TextView iconView = text(icon, active ? 20 : 22, active ? Color.WHITE : NAV_INACTIVE, true);
        iconView.setGravity(Gravity.CENTER);
        TextView labelView = text(label, active ? 13 : 11, active ? Color.WHITE : NAV_INACTIVE, true);
        labelView.setGravity(Gravity.CENTER);
        labelView.setPadding(active ? dp(7) : 0, active ? 0 : dp(1), 0, 0);

        item.addView(iconView, new LinearLayout.LayoutParams(active ? dp(24) : -1, active ? -1 : dp(28)));
        item.addView(labelView, new LinearLayout.LayoutParams(active ? -2 : -1, active ? -2 : dp(18)));
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
        startEventStream();
        accounts.clear();
        accounts.addAll(store.readAccounts());
        if (accounts.isEmpty()) {
            fetchBootstrapThenAccounts(true);
        } else {
            renderAccounts();
            checkNotifications();
            fetchBootstrapThenAccounts(false);
        }
    }

    private void fetchBootstrapThenAccounts(boolean render) {
        runAsync("同步账户...", () -> {
            api.get("/api/sync/bootstrap");
            JSONObject local = api.get("/api/mailboxes");
            JSONObject external = api.get("/imap/api/accounts");
            parseAccounts(local, external);
            store.replaceAccounts(accounts);
            return "ok";
        }, result -> {
            if (render) renderAccounts();
            checkNotifications();
        });
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
        setHeader("账户与分组", accounts.size() + " 个账号");
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        LinearLayout list = column(dp(10));
        scroll.addView(list);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));
        Map<String, List<Models.Account>> grouped = new HashMap<>();
        for (Models.Account account : accounts) {
            String group = account.group == null || account.group.isEmpty() ? "未分组" : account.group;
            grouped.computeIfAbsent(group, k -> new ArrayList<>()).add(account);
        }
        for (String group : grouped.keySet()) {
            TextView groupTitle = text(group, 15, MUTED, true);
            groupTitle.setPadding(dp(4), dp(10), dp(4), dp(4));
            list.addView(groupTitle);
            for (Models.Account account : grouped.get(group)) list.addView(accountRow(account));
        }
        if (accounts.isEmpty()) list.addView(empty("暂无账户，请先在服务端添加邮箱账号。"));
    }

    private View accountRow(Models.Account account) {
        LinearLayout row = panel(dp(14), dp(12));
        row.setOnClickListener(v -> {
            selectedAccount = account;
            loadFolders(account);
        });
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        TextView icon = text("✉", 17, PRIMARY, true);
        top.addView(icon, new LinearLayout.LayoutParams(dp(30), dp(30)));
        LinearLayout names = new LinearLayout(this);
        names.setOrientation(LinearLayout.VERTICAL);
        TextView name = text(account.label(), 16, TEXT, true);
        TextView meta = text(account.email, 12, MUTED, false);
        names.addView(name);
        names.addView(meta);
        top.addView(names, new LinearLayout.LayoutParams(0, -2, 1));
        if (account.unread > 0) {
            TextView badge = badge(String.valueOf(account.unread), ACCENT, Color.WHITE);
            top.addView(badge);
        }
        row.addView(top);
        TextView type = text(("local".equals(account.type) ? "本地邮箱" : "外部邮箱") + " · " + nonEmpty(account.group, "未分组"), 11, MUTED, false);
        type.setPadding(dp(30), dp(6), 0, 0);
        row.addView(type);
        return row;
    }

    private void renderMailHub() {
        currentScreen = "mailHub";
        selectedAccount = null;
        selectedFolder = null;
        selectedVirtualMode = "";
        selectedGroup = "";
        setHeader("邮件", "所有账号和分组");
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        LinearLayout list = column(dp(3));
        scroll.addView(list);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));
        list.addView(hubRow("全部账号", "所有邮件", totalUnread(), v -> openVirtualMailbox("global_all", "")));
        list.addView(hubRow("全部账号", "未读邮件", totalUnread(), v -> openVirtualMailbox("global_unread", "")));
        for (String group : allGroups()) {
            int unread = groupUnread(group);
            list.addView(hubRow(group, "分组所有邮件", unread, v -> openVirtualMailbox("group_all", group)));
            list.addView(hubRow(group, "分组未读邮件", unread, v -> openVirtualMailbox("group_unread", group)));
        }
        if (accounts.isEmpty()) list.addView(empty("暂无账户，请先在服务端添加邮箱账号。"));
    }

    private View hubRow(String titleText, String subText, int unread, View.OnClickListener listener) {
        LinearLayout row = panel(dp(14), dp(12));
        row.setOnClickListener(listener);
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        TextView titleView = text(titleText, 16, TEXT, true);
        TextView badgeView = unread > 0 ? badge(String.valueOf(unread), ACCENT, Color.WHITE) : null;
        top.addView(titleView, new LinearLayout.LayoutParams(0, -2, 1));
        if (badgeView != null) top.addView(badgeView);
        row.addView(top);
        row.addView(text(subText, 13, MUTED, false));
        return row;
    }

    private void openVirtualMailbox(String mode, String group) {
        selectedVirtualMode = mode;
        selectedGroup = group == null ? "" : group;
        selectedAccount = null;
        selectedFolder = folder("virtual", "", mode, virtualTitle(mode, selectedGroup), 0);
        currentPage = 1;
        hasMore = false;
        searchQuery = "";
        loadMails(false);
    }

    private void loadFolders(Models.Account account) {
        selectedAccount = account;
        selectedFolder = null;
        folders.clear();
        searchQuery = "";
        currentPage = 1;
        hasMore = false;
        List<Models.Folder> cachedFolders = store.readFolders(account.type, account.id);
        if (!cachedFolders.isEmpty()) {
            folders.addAll(cachedFolders);
            selectedFolder = pickDefaultFolder(account.id);
            renderFoldersOrMails();
            showCachedMailsIfAny();
        } else {
            mails.clear();
        }
        if ("local".equals(account.type)) {
            List<Models.Folder> fixed = localFolders(account);
            folders.clear();
            folders.addAll(fixed);
            store.replaceFolders(account, folders);
            if (selectedFolder == null) selectedFolder = pickDefaultFolder(account.id);
            renderFoldersOrMails();
            loadMails(false);
            return;
        }
        runAsync("加载文件夹...", () -> {
            JSONArray arr = asArray(api.get("/imap/api/accounts/" + encode(account.id) + "/folders"));
            folders.clear();
            folders.add(folder("external", account.id, "drafts", "草稿箱", 0));
            folders.add(folder("external", account.id, "outbox", "发送失败", 0));
            for (int i = 0; i < arr.length(); i++) {
                JSONObject item = arr.optJSONObject(i);
                String path = Json.anyStr(item, "path", "name");
                if (path.isEmpty()) continue;
                folders.add(folder("external", account.id, path, Json.anyStr(item, "name", "path"), 0));
            }
            store.replaceFolders(account, folders);
            return "ok";
        }, result -> {
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
        for (Models.Folder folder : folders) {
            if ("INBOX".equalsIgnoreCase(folder.path)) return folder;
        }
        for (Models.Folder folder : folders) {
            if (!"drafts".equals(folder.path) && !"outbox".equals(folder.path)) return folder;
        }
        return folders.isEmpty() ? folder("external", accountId, "INBOX", "INBOX", 0) : folders.get(0);
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
        if (selectedAccount != null) page.addView(folderChips(), new LinearLayout.LayoutParams(-1, dp(52)));
        page.addView(searchBar(), new LinearLayout.LayoutParams(-1, dp(48)));
        LinearLayout list = column(0);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(list);
        page.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        if (mails.isEmpty()) {
            list.addView(empty("暂无邮件"));
        } else {
            for (Models.Mail mail : mails) list.addView(mailRow(mail));
            if (hasMore) {
                TextView more = outlineButton("加载更多", v -> {
                    currentPage += 1;
                    loadMails(true);
                });
                list.addView(more);
            }
        }
    }

    private LinearLayout searchBar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setPadding(dp(16), dp(5), dp(16), dp(5));
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
        chips.setPadding(dp(16), dp(8), dp(16), dp(8));
        chips.setBackgroundColor(Color.WHITE);
        scroll.addView(chips);
        for (Models.Folder folder : folders) {
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
        if (currentPage <= 1) {
            if (showCachedMailsIfAny()) {
                silent = true;
            } else {
                mails.clear();
                hasMore = false;
                renderFoldersOrMails();
            }
        }
        runAsync(silent ? null : "加载邮件...", () -> {
            List<Models.Mail> next = new ArrayList<>();
            hasMore = false;
            if (selectedAccount == null && !selectedVirtualMode.isEmpty()) {
                next.addAll(loadVirtualMails());
            } else if ("drafts".equals(selectedFolder.path)) {
                JSONObject data = api.get("/api/drafts?account_type=" + encode(selectedAccount.type) + "&account_id=" + encode(selectedAccount.id));
                JSONArray arr = Json.array(data, "drafts");
                for (int i = 0; i < arr.length(); i++) {
                    Models.Mail mail = Models.Mail.fromDraft(arr.optJSONObject(i));
                    if (matchesSearch(mail)) next.add(mail);
                }
                hasMore = false;
            } else if ("outbox".equals(selectedFolder.path)) {
                JSONObject data = api.get("/api/outbox?account_type=" + encode(selectedAccount.type) + "&account_id=" + encode(selectedAccount.id));
                JSONArray arr = Json.array(data, "messages");
                for (int i = 0; i < arr.length(); i++) {
                    Models.Mail mail = Models.Mail.fromOutbox(arr.optJSONObject(i));
                    if (matchesSearch(mail)) next.add(mail);
                }
                hasMore = false;
            } else if ("local".equals(selectedAccount.type)) {
                JSONObject data;
                if (!searchQuery.isEmpty()) {
                    data = api.post("/api/inbox/search", new JSONObject()
                        .put("email", selectedAccount.email)
                        .put("query", searchQuery));
                    hasMore = false;
                } else {
                    int offset = Math.max(0, (currentPage - 1) * pageSize);
                    JSONObject body = new JSONObject()
                        .put("email", selectedAccount.email)
                        .put("offset", offset)
                        .put("limit", pageSize)
                        .put("unread_only", "unread".equals(selectedFolder.path));
                    data = "sent".equals(selectedFolder.path)
                        ? api.post("/api/sent/query", body)
                        : api.post("/api/inbox/query", body);
                    int total = data.optInt("total", 0);
                    hasMore = offset + pageSize < total;
                }
                JSONArray arr = Json.array(data, "messages");
                for (int i = 0; i < arr.length(); i++) {
                    String cacheFolder = "unread".equals(selectedFolder.path) ? "inbox" : selectedFolder.path;
                    next.add(Models.Mail.fromLocal(arr.optJSONObject(i), selectedAccount.id, cacheFolder));
                }
            } else {
                JSONObject data;
                if (!searchQuery.isEmpty()) {
                    data = api.get("/imap/api/accounts/" + encode(selectedAccount.id) + "/search?folder=" + encode(selectedFolder.path) + "&q=" + encode(searchQuery) + "&count=" + pageSize + "&offset=" + ((currentPage - 1) * pageSize));
                } else {
                    data = api.get("/imap/api/accounts/" + encode(selectedAccount.id) + "/mails?folder=" + encode(selectedFolder.path) + "&count=" + pageSize + "&page=" + currentPage + "&cacheOnly=1");
                }
                hasMore = data.optBoolean("hasMore", false) || currentPage * pageSize < data.optInt("total", 0);
                JSONArray arr = Json.array(data, "mails");
                for (int i = 0; i < arr.length(); i++) {
                    next.add(Models.Mail.fromExternal(arr.optJSONObject(i), selectedAccount.id, selectedFolder.path));
                }
            }
            store.upsertMails(next);
            if (currentPage <= 1) mails.clear();
            mails.addAll(next);
            return "ok";
        }, result -> renderFoldersOrMails());
    }

    private boolean showCachedMailsIfAny() {
        List<Models.Mail> cached = cachedMails();
        if (cached.isEmpty()) return false;
        mails.clear();
        mails.addAll(cached);
        hasMore = cached.size() >= pageSize;
        renderFoldersOrMails();
        return true;
    }

    private List<Models.Mail> cachedMails() {
        int offset = Math.max(0, (currentPage - 1) * pageSize);
        if (selectedAccount == null && !selectedVirtualMode.isEmpty()) {
            return store.readVirtualMails(scopedAccounts(), selectedVirtualMode.endsWith("_unread"), searchQuery, pageSize, offset);
        }
        if (selectedAccount == null || selectedFolder == null) return new ArrayList<>();
        boolean unreadOnly = "unread".equals(selectedFolder.path);
        return store.readMails(selectedAccount.type, selectedAccount.id, selectedFolder.path, searchQuery, pageSize, offset, unreadOnly);
    }

    private List<Models.Mail> loadVirtualMails() throws Exception {
        List<Models.Mail> merged = new ArrayList<>();
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
                Models.Mail mail = Models.Mail.fromExternal(item, String.valueOf(item.opt("accountId")), Json.anyStr(item, "folder", "folderName"));
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

    private List<Models.Account> scopedAccounts() {
        List<Models.Account> scoped = new ArrayList<>();
        for (Models.Account account : accounts) {
            if (selectedVirtualMode.startsWith("group_")) {
                String group = account.group == null || account.group.isEmpty() ? "未分组" : account.group;
                if (!group.equals(selectedGroup)) continue;
            }
            scoped.add(account);
        }
        return scoped;
    }

    private boolean matchesSearch(Models.Mail mail) {
        if (searchQuery == null || searchQuery.isEmpty()) return true;
        String q = searchQuery.toLowerCase();
        String blob = (nonEmpty(mail.sender, "") + " " + nonEmpty(mail.to, "") + " " + nonEmpty(mail.subject, "") + " " + nonEmpty(mail.preview, "")).toLowerCase();
        return blob.contains(q);
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
        fetchBootstrapThenAccounts(false);
        currentPage = 1;
        loadMails(false);
    }

    private void handleServerEvent(JSONObject event) {
        int seq = event.optInt("seq", 0);
        if (seq > 0) prefs.edit().putInt("sync_seq", seq).apply();
        String type = Json.str(event, "type");
        if (type.startsWith("mail.") || type.startsWith("draft.") || type.startsWith("outbox.") || type.startsWith("message.")) {
            fetchBootstrapThenAccounts(false);
            if ("list".equals(currentScreen) && (selectedAccount != null || !selectedVirtualMode.isEmpty())) {
                currentPage = 1;
                loadMails(true);
            }
        }
    }

    private void startEventStream() {
        if (token.isEmpty() || eventStreamRunning) return;
        eventStreamRunning = true;
        eventThread = new Thread(this::eventStreamLoop, "memail-mobile-events");
        eventThread.start();
    }

    private void stopEventStream() {
        eventStreamRunning = false;
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
                conn.setConnectTimeout(12000);
                conn.setReadTimeout(0);
                conn.setRequestProperty("Accept", "text/event-stream");
                conn.setRequestProperty("Authorization", "Bearer " + api.token());
                int code = conn.getResponseCode();
                if (code >= 400) throw new Exception("events HTTP " + code);
                readEventStream(conn.getInputStream());
            } catch (Exception ignored) {
                sleepQuietly(EVENT_RECONNECT_MS);
            } finally {
                if (conn != null) conn.disconnect();
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
        shell.setPadding(dp(16), dp(12), dp(14), 0);
        shell.setBackgroundColor(Color.WHITE);
        LinearLayout.LayoutParams shellLp = new LinearLayout.LayoutParams(-1, -2);
        shellLp.setMargins(0, 0, 0, 0);
        shell.setLayoutParams(shellLp);

        TextView avatar = avatarView(mail.sender);
        shell.addView(avatar, new LinearLayout.LayoutParams(dp(46), dp(46)));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.VERTICAL);
        row.setPadding(dp(12), 0, 0, 0);
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
        TextView sender = text((mail.favorite ? "★ " : "") + nonEmpty(mail.sender, "未知发件人"), 16, TEXT, !mail.seen);
        sender.setSingleLine(true);
        senderWrap.addView(sender, new LinearLayout.LayoutParams(0, -2, 1));
        TextView date = text(shortMailDate(mail.date), 12, MUTED, false);
        date.setGravity(Gravity.RIGHT);
        top.addView(senderWrap, new LinearLayout.LayoutParams(0, -2, 1));
        top.addView(date, new LinearLayout.LayoutParams(dp(78), -2));
        TextView subject = text(nonEmpty(mail.subject, "无主题"), 15, TEXT, !mail.seen);
        subject.setPadding(mail.seen ? 0 : dp(16), dp(4), 0, dp(3));
        subject.setMaxLines(1);
        TextView preview = text(cleanPreview(mail.preview), 13, Color.rgb(135, 143, 148), false);
        preview.setPadding(mail.seen ? 0 : dp(16), 0, 0, dp(12));
        preview.setMaxLines(2);
        row.addView(top);
        row.addView(subject);
        if (!mail.preview.isEmpty()) row.addView(preview);
        View divider = new View(this);
        divider.setBackgroundColor(Color.rgb(238, 241, 242));
        row.addView(divider, new LinearLayout.LayoutParams(-1, 1));
        shell.addView(row, new LinearLayout.LayoutParams(0, -2, 1));
        shell.setOnClickListener(v -> loadDetail(mail));
        return shell;
    }

    private void loadDetail(Models.Mail mail) {
        if ("draft".equals(mail.kind) || "outbox".equals(mail.kind)) {
            renderLocalMessageDetail(mail);
            return;
        }
        runAsync("打开邮件...", () -> {
            if ("external".equals(mail.accountType)) {
                return api.get("/imap/api/accounts/" + encode(mail.accountId) + "/mails/" + encode(mail.id) + "?folder=" + encode(mail.folder));
            }
            if ("sent".equals(mail.folder)) {
                return api.post("/api/sent/detail", new JSONObject().put("email", mail.accountId).put("message_id", mail.id));
            }
            return api.post("/api/inbox/detail", new JSONObject().put("email", mail.accountId).put("message_id", mail.id));
        }, data -> renderDetail(mail, data));
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

    private void renderDetail(Models.Mail mail, JSONObject data) {
        currentScreen = "detail";
        currentDetailMail = mail;
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
        String text = Json.str(source, "text");
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
        bodyCard.addView(web, new LinearLayout.LayoutParams(-1, dp(720)));
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
        HorizontalScrollView actionScroll = new HorizontalScrollView(this);
        actionScroll.setHorizontalScrollBarEnabled(false);
        actionScroll.setFillViewport(false);
        actionScroll.setPadding(0, 0, 0, 0);
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.setGravity(Gravity.CENTER_VERTICAL);
        actions.setPadding(dp(2), dp(2), dp(2), dp(2));
        actionScroll.addView(actions);
        if ("draft".equals(mail.kind)) {
            actions.addView(detailAction("✎", "继续编辑", v -> renderComposeEditor(mail.to, mail.subject, mail.text, mail.id)));
            actions.addView(detailAction("×", "删除草稿", v -> deleteDraft(mail)));
        } else if ("outbox".equals(mail.kind)) {
            actions.addView(detailAction("↻", "重试", v -> retryOutbox(mail)));
            actions.addView(detailAction("✎", "编辑再发", v -> renderComposeEditor(mail.to, mail.subject, mail.text, "")));
            actions.addView(detailAction("×", "删除记录", v -> deleteOutbox(mail)));
        } else if (!"sent".equals(mail.folder)) {
            actions.addView(detailAction(mail.seen ? "○" : "✓", mail.seen ? "标未读" : "标已读", v -> toggleSeen(mail)));
            actions.addView(detailAction(mail.favorite ? "★" : "☆", mail.favorite ? "取消星标" : "星标", v -> toggleFavorite(mail)));
        }
        if (!"draft".equals(mail.kind) && !"outbox".equals(mail.kind)) {
            actions.addView(detailAction("译", "翻译", v -> translateMail(mail, source)));
            actions.addView(detailAction("↩", "回复", v -> renderComposeFor(mail, "回复：" + mail.subject)));
            actions.addView(detailAction("↪", "转发", v -> renderComposeFor(mail, "转发：" + mail.subject)));
            if (!"sent".equals(mail.folder)) actions.addView(detailAction("×", "删除", v -> confirmDelete(mail)));
        }
        return actionScroll;
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

    private void translateMail(Models.Mail mail, JSONObject source) {
        String sourceHash = translationSourceHash(mail, source);
        LocalStore.TranslationCache cached = store.readTranslation(mail, sourceHash);
        if (cached != null) {
            showTranslation(mail, source, cached.translation, cached.format, true);
            return;
        }
        runAsync("翻译中...", () -> api.post("/api/ai/translate", new JSONObject()
            .put("subject", mail.subject)
            .put("text", Json.str(source, "text"))
            .put("html", Json.str(source, "html"))
            .put("account_type", mail.accountType)
            .put("account_id", mail.accountId)
            .put("folder", mail.folder)
            .put("message_id", mail.id)), result -> {
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
        box.addView(readingSection(cached ? "中文翻译 · 已缓存" : "中文翻译", "html".equals(format) ? translated : plainTextHtml(translated), dp(520)));

        String originalHtml = Json.str(source, "html");
        String originalText = Json.str(source, "text");
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
            mails.remove(mail);
            store.deleteMail(mail);
            toast("已删除");
            renderFoldersOrMails();
        });
    }

    private void deleteDraft(Models.Mail mail) {
        runAsync("删除草稿...", () -> api.delete("/api/drafts/" + mail.id), result -> {
            mails.remove(mail);
            store.deleteMail(mail);
            toast("草稿已删除");
            renderFoldersOrMails();
        });
    }

    private void retryOutbox(Models.Mail mail) {
        runAsync("重试发送...", () -> api.post("/api/outbox/" + mail.id + "/retry", new JSONObject()), result -> {
            mails.remove(mail);
            toast("重试发送成功");
            renderFoldersOrMails();
        });
    }

    private void deleteOutbox(Models.Mail mail) {
        runAsync("删除记录...", () -> api.delete("/api/outbox/" + mail.id), result -> {
            mails.remove(mail);
            store.deleteMail(mail);
            toast("发送失败记录已删除");
            renderFoldersOrMails();
        });
    }

    private void renderCompose() {
        selectedVirtualMode = "";
        selectedGroup = "";
        renderComposeFor(null, "");
    }

    private void renderComposeFor(Models.Mail source, String subject) {
        String toValue = source == null ? "" : cleanAddress(source.sender);
        String bodyValue = source == null ? "" : "\n\n---- 原邮件 ----\n" + nonEmpty(source.preview, "");
        renderComposeEditor(toValue, subject, bodyValue, "");
    }

    private void renderComposeEditor(String toValue, String subjectValue, String bodyValue, String draftId) {
        currentScreen = "compose";
        setHeader("写邮件", selectedAccount == null ? "选择发件账号" : selectedAccount.label());
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        LinearLayout box = column(dp(10));
        scroll.addView(box);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));
        EditText to = input("收件人，多个用逗号分隔", toValue, false);
        EditText sub = input("主题", subjectValue, false);
        EditText body = input("正文", bodyValue, false);
        body.setMinLines(8);
        body.setGravity(Gravity.TOP);
        box.addView(to);
        box.addView(sub);
        box.addView(body);
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        TextView save = outlineButton("保存草稿", v -> saveDraft(to.getText().toString(), sub.getText().toString(), body.getText().toString(), draftId));
        TextView send = primaryButton("发送");
        send.setOnClickListener(v -> sendMail(to.getText().toString(), sub.getText().toString(), body.getText().toString(), draftId));
        actions.addView(save, new LinearLayout.LayoutParams(0, dp(48), 1));
        actions.addView(send, new LinearLayout.LayoutParams(0, dp(48), 1));
        box.addView(actions);
    }

    private JSONObject composePayload(Models.Account sender, String to, String subject, String body) throws Exception {
        JSONObject payload = new JSONObject()
            .put("account_type", sender.type)
            .put("account_id", sender.id)
            .put("from_email", sender.email)
            .put("from_name", nonEmpty(sender.sendName, sender.name))
            .put("to", to)
            .put("subject", subject)
            .put("text", body)
            .put("html", "<div>" + escape(body).replace("\n", "<br>") + "</div>");
        if ("external".equals(sender.type)) payload.put("fromName", nonEmpty(sender.sendName, sender.name));
        return payload;
    }

    private void saveDraft(String to, String subject, String body, String draftId) {
        Models.Account account = selectedAccount;
        if (account == null && !accounts.isEmpty()) account = accounts.get(0);
        if (account == null) {
            toast("请先选择发件账号");
            return;
        }
        Models.Account sender = account;
        runAsync("保存草稿...", () -> {
            JSONObject payload = composePayload(sender, to, subject, body);
            if (draftId != null && !draftId.isEmpty()) payload.put("id", draftId);
            return api.post("/api/drafts", payload);
        }, result -> {
            toast("草稿已保存");
            if (selectedFolder != null && "drafts".equals(selectedFolder.path)) loadMails(false);
        });
    }

    private void sendMail(String to, String subject, String body, String draftId) {
        Models.Account account = selectedAccount;
        if (account == null && !accounts.isEmpty()) account = accounts.get(0);
        if (account == null) {
            toast("请先选择发件账号");
            return;
        }
        Models.Account sender = account;
        runAsync("发送中...", () -> {
            JSONObject payload = composePayload(sender, to, subject, body);
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
        setHeader("设置", server);
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        LinearLayout box = column(dp(12));
        scroll.addView(box);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));
        box.addView(card(text("移动端使用设备 Token 访问服务端 API。退出会删除本机 Token，不影响服务端其它设备。", 14, MUTED, false)));
        TextView refresh = primaryButton("刷新账户和邮件");
        refresh.setOnClickListener(v -> fetchBootstrapThenAccounts(false));
        box.addView(refresh);
        TextView notify = outlineButton(notifyEnabled ? "关闭新邮件通知" : "开启新邮件通知", v -> {
            notifyEnabled = !notifyEnabled;
            prefs.edit().putBoolean("notify", notifyEnabled).apply();
            renderSettings();
        });
        box.addView(notify);
        Set<String> enabledGroups = notificationGroups();
        box.addView(text("通知分组", 15, TEXT, true));
        box.addView(card(text(enabledGroups.isEmpty() ? "当前：所有分组都会通知" : "当前：" + enabledGroups, 13, MUTED, false)));
        for (String group : allGroups()) {
            boolean enabled = enabledGroups.isEmpty() || enabledGroups.contains(group);
            TextView groupButton = outlineButton((enabled ? "✓ " : "○ ") + group, v -> {
                Set<String> current = notificationGroups();
                if (current.isEmpty()) current.addAll(allGroups());
                if (current.contains(group)) current.remove(group);
                else current.add(group);
                if (current.size() == allGroups().size()) current.clear();
                saveNotificationGroups(current);
                renderSettings();
            });
            box.addView(groupButton);
        }
        TextView logout = outlineButton("退出本机 Token", v -> {
            prefs.edit().remove("token").apply();
            token = "";
            api.configure(server, "");
            stopEventStream();
            if (navBar != null) navBar.setVisibility(View.GONE);
            showLogin();
        });
        box.addView(logout);
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
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
            ? new Notification.Builder(this, CHANNEL_MAIL)
            : new Notification.Builder(this);
        Notification notification = builder
            .setSmallIcon(android.R.drawable.ic_dialog_email)
            .setContentTitle(title)
            .setContentText(text)
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
        if (loading != null) setLoading(true, loading);
        io.submit(() -> {
            try {
                Object result = task.run();
                JSONObject object = result instanceof JSONObject ? (JSONObject) result : new JSONObject().put("value", String.valueOf(result));
                runOnUiThread(() -> {
                    setLoading(false, "");
                    success.accept(object);
                });
            } catch (Exception e) {
                runOnUiThread(() -> {
                    setLoading(false, "");
                    toast(e.getMessage());
                });
            }
        });
    }

    private void setLoading(boolean loading, String message) {
        progress.setVisibility(loading ? View.VISIBLE : View.GONE);
        if (loading && message != null) subtitle.setText(message);
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

    private JSONArray asArray(JSONObject object) {
        JSONArray arr = object.optJSONArray("items");
        if (arr == null) arr = object.optJSONArray("folders");
        if (arr == null) arr = object.optJSONArray("data");
        return arr == null ? new JSONArray() : arr;
    }

    private static String encode(String value) {
        return URLEncoder.encode(value == null ? "" : value, StandardCharsets.UTF_8).replace("+", "%20");
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
        settings.setTextZoom(106);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        web.setBackgroundColor(Color.TRANSPARENT);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.setVerticalScrollBarEnabled(false);
        web.setHorizontalScrollBarEnabled(false);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                view.postDelayed(() -> view.evaluateJavascript(
                    "(function(){return Math.max(document.body.scrollHeight,document.documentElement.scrollHeight);})()",
                    value -> {
                        try {
                            int cssHeight = (int) Math.ceil(Double.parseDouble(value.replace("\"", "")));
                            int px = Math.max(dp(260), (int) (cssHeight * getResources().getDisplayMetrics().density) + dp(28));
                            ViewGroup.LayoutParams lp = view.getLayoutParams();
                            if (lp != null && Math.abs(lp.height - px) > dp(24)) {
                                lp.height = Math.min(px, dp(2400));
                                view.setLayoutParams(lp);
                            }
                        } catch (Exception ignored) {
                        }
                    }), 120);
            }
        });
        return web;
    }

    private static String wrapMailHtml(String body) {
        String safeBody = body == null ? "" : body;
        return "<!doctype html><html><head>"
            + "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, maximum-scale=3\">"
            + "<style>"
            + "html,body{margin:0;padding:0;background:transparent;color:#14252c;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:16px;line-height:1.68;overflow-x:hidden;}"
            + "body{box-sizing:border-box;padding:12px 18px 24px 18px;word-break:break-word;overflow-wrap:anywhere;}"
            + "p{margin:0 0 14px 0;}div{max-width:100%;}"
            + "img{max-width:100%!important;height:auto!important;}"
            + "table{max-width:100%!important;width:100%!important;border-collapse:collapse;table-layout:auto;display:block;overflow-x:auto;margin:12px 0;border-radius:10px;}"
            + "tbody,thead,tfoot,tr{max-width:100%;}"
            + "td,th{word-break:break-word;overflow-wrap:anywhere;white-space:normal!important;padding:8px 6px;border-color:#dfe8ea;}"
            + "a{color:#0b7285;word-break:break-word;}"
            + "pre,.memail-plain{white-space:pre-wrap;font:16px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.7;margin:0;}"
            + "*{max-width:100%;box-sizing:border-box;}"
            + "</style></head><body>" + safeBody + "</body></html>";
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

    private static String translationSourceHash(Models.Mail mail, JSONObject source) {
        String raw = nonEmpty(mail.subject, "")
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
