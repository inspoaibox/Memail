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
import android.text.Html;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.Button;
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

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

public class MainActivity extends Activity {
    private static final int BG = Color.rgb(246, 250, 251);
    private static final int CARD = Color.WHITE;
    private static final int TEXT = Color.rgb(18, 38, 45);
    private static final int MUTED = Color.rgb(94, 112, 121);
    private static final int PRIMARY = Color.rgb(15, 118, 110);
    private static final int LINE = Color.rgb(213, 226, 229);
    private static final String PREFS = "memail_mobile";
    private static final String CHANNEL_MAIL = "memail_mail";

    private final ExecutorService io = Executors.newFixedThreadPool(4);
    private final ApiClient api = new ApiClient();
    private final List<Models.Account> accounts = new ArrayList<>();
    private final List<Models.Folder> folders = new ArrayList<>();
    private final List<Models.Mail> mails = new ArrayList<>();

    private SharedPreferences prefs;
    private LinearLayout root;
    private LinearLayout content;
    private TextView title;
    private TextView subtitle;
    private ProgressBar progress;
    private Models.Account selectedAccount;
    private Models.Folder selectedFolder;
    private String token = "";
    private String server = "";
    private boolean notifyEnabled = true;
    private int navIndex = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        if (Build.VERSION.SDK_INT >= 21) {
            getWindow().setStatusBarColor(BG);
            getWindow().setNavigationBarColor(BG);
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

    private void buildShell() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);
        setContentView(root);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(18), dp(14), dp(18), dp(10));
        root.addView(header, new LinearLayout.LayoutParams(-1, -2));

        ImageView logo = new ImageView(this);
        logo.setImageResource(getResources().getIdentifier("logo", "drawable", getPackageName()));
        header.addView(logo, new LinearLayout.LayoutParams(dp(34), dp(34)));

        LinearLayout titleBox = new LinearLayout(this);
        titleBox.setOrientation(LinearLayout.VERTICAL);
        titleBox.setPadding(dp(10), 0, 0, 0);
        header.addView(titleBox, new LinearLayout.LayoutParams(0, -2, 1));

        title = text("Memail", 22, TEXT, true);
        subtitle = text("移动邮箱工作台", 12, MUTED, false);
        titleBox.addView(title);
        titleBox.addView(subtitle);

        progress = new ProgressBar(this);
        progress.setVisibility(View.GONE);
        header.addView(progress, new LinearLayout.LayoutParams(dp(28), dp(28)));

        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(12), dp(2), dp(12), dp(2));
        root.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));

        root.addView(bottomNav(), new LinearLayout.LayoutParams(-1, dp(68)));
    }

    private LinearLayout bottomNav() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER);
        bar.setPadding(dp(10), dp(8), dp(10), dp(8));
        bar.setBackgroundColor(Color.WHITE);
        String[] labels = {"账户", "邮件", "写信", "设置"};
        for (int i = 0; i < labels.length; i++) {
            final int idx = i;
            Button btn = navButton(labels[i], idx == navIndex);
            btn.setOnClickListener(v -> {
                navIndex = idx;
                if (idx == 0) renderAccounts();
                if (idx == 1) renderFoldersOrMails();
                if (idx == 2) renderCompose();
                if (idx == 3) renderSettings();
                rebuildBottomNav();
            });
            bar.addView(btn, new LinearLayout.LayoutParams(0, -1, 1));
        }
        return bar;
    }

    private void rebuildBottomNav() {
        root.removeViewAt(root.getChildCount() - 1);
        root.addView(bottomNav(), new LinearLayout.LayoutParams(-1, dp(68)));
    }

    private Button navButton(String label, boolean active) {
        Button btn = new Button(this);
        btn.setAllCaps(false);
        btn.setText(label);
        btn.setTextSize(13);
        btn.setTextColor(active ? Color.WHITE : PRIMARY);
        btn.setBackground(bg(active ? PRIMARY : Color.TRANSPARENT, 18, active ? PRIMARY : LINE, active ? 0 : 1));
        return btn;
    }

    private void showLogin() {
        navIndex = 3;
        setHeader("连接服务端", "用管理员账号换取移动端 Token");
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        LinearLayout box = column(dp(18));
        scroll.addView(box);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));

        TextView intro = text("Android 客户端使用服务端 API，不套网页布局。登录只用于签发设备 Token，之后会保存在本机。", 14, MUTED, false);
        box.addView(card(intro));

        EditText serverInput = input("服务端地址", server, false);
        EditText userInput = input("管理员账号", prefs.getString("username", "admin"), false);
        EditText passInput = input("访问密码", "", true);
        EditText totpInput = input("2FA 验证码（启用时填写）", "", false);
        totpInput.setInputType(InputType.TYPE_CLASS_NUMBER);
        box.addView(serverInput);
        box.addView(userInput);
        box.addView(passInput);
        box.addView(totpInput);

        Button login = primaryButton("连接并进入");
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
        }, result -> loadHome()));
        box.addView(login);
    }

    private void loadHome() {
        navIndex = 0;
        requestNotificationPermission();
        fetchBootstrapThenAccounts(true);
    }

    private void fetchBootstrapThenAccounts(boolean render) {
        runAsync("同步账户...", () -> {
            api.get("/api/sync/bootstrap");
            JSONObject local = api.get("/api/mailboxes");
            JSONObject external = api.get("/imap/api/accounts");
            parseAccounts(local, external);
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
        account.group = Json.str(item, "group");
        JSONObject sync = Json.obj(item, "syncStatus");
        account.unread = sync.optInt("unseen", 0);
        accounts.add(account);
    }

    private void renderAccounts() {
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
        LinearLayout row = cardContainer();
        row.setOnClickListener(v -> {
            selectedAccount = account;
            loadFolders(account);
        });
        TextView name = text(account.label(), 16, TEXT, true);
        TextView meta = text(account.email + " · " + ("local".equals(account.type) ? "本地" : "外部") + unreadSuffix(account.unread), 12, MUTED, false);
        row.addView(name);
        row.addView(meta);
        return row;
    }

    private void loadFolders(Models.Account account) {
        selectedAccount = account;
        folders.clear();
        mails.clear();
        if ("local".equals(account.type)) {
            Models.Folder inbox = folder("local", account.id, "inbox", "收件箱", 0);
            Models.Folder unread = folder("local", account.id, "unread", "未读邮件", 0);
            Models.Folder sent = folder("local", account.id, "sent", "已发送", 0);
            folders.add(unread);
            folders.add(inbox);
            folders.add(sent);
            selectedFolder = inbox;
            renderFoldersOrMails();
            loadMails(false);
            return;
        }
        runAsync("加载文件夹...", () -> {
            JSONArray arr = asArray(api.get("/imap/api/accounts/" + encode(account.id) + "/folders"));
            folders.clear();
            for (int i = 0; i < arr.length(); i++) {
                JSONObject item = arr.optJSONObject(i);
                String path = Json.anyStr(item, "path", "name");
                if (path.isEmpty()) continue;
                folders.add(folder("external", account.id, path, Json.anyStr(item, "name", "path"), 0));
            }
            return "ok";
        }, result -> {
            selectedFolder = folders.isEmpty() ? folder("external", account.id, "INBOX", "INBOX", 0) : folders.get(0);
            renderFoldersOrMails();
            loadMails(false);
        });
    }

    private void renderFoldersOrMails() {
        if (selectedAccount == null) {
            renderAccounts();
            return;
        }
        setHeader(selectedAccount.label(), selectedFolder == null ? "选择文件夹" : selectedFolder.name);
        content.removeAllViews();
        LinearLayout page = column(0);
        content.addView(page, new LinearLayout.LayoutParams(-1, -1));
        page.addView(folderChips(), new LinearLayout.LayoutParams(-1, dp(54)));
        LinearLayout list = column(dp(8));
        ScrollView scroll = new ScrollView(this);
        scroll.addView(list);
        page.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        if (mails.isEmpty()) {
            list.addView(empty("暂无邮件"));
        } else {
            for (Models.Mail mail : mails) list.addView(mailRow(mail));
        }
    }

    private HorizontalScrollView folderChips() {
        HorizontalScrollView scroll = new HorizontalScrollView(this);
        scroll.setHorizontalScrollBarEnabled(false);
        LinearLayout chips = new LinearLayout(this);
        chips.setOrientation(LinearLayout.HORIZONTAL);
        chips.setPadding(0, dp(8), 0, dp(8));
        scroll.addView(chips);
        for (Models.Folder folder : folders) {
            Button chip = new Button(this);
            chip.setAllCaps(false);
            chip.setText(folder.name);
            chip.setTextColor(folder == selectedFolder ? Color.WHITE : PRIMARY);
            chip.setBackground(bg(folder == selectedFolder ? PRIMARY : Color.WHITE, 16, folder == selectedFolder ? PRIMARY : LINE, 1));
            chip.setOnClickListener(v -> {
                selectedFolder = folder;
                mails.clear();
                loadMails(false);
            });
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-2, -1);
            lp.setMargins(dp(2), 0, dp(8), 0);
            chips.addView(chip, lp);
        }
        return scroll;
    }

    private void loadMails(boolean silent) {
        if (selectedAccount == null || selectedFolder == null) return;
        runAsync(silent ? null : "加载邮件...", () -> {
            if ("local".equals(selectedAccount.type)) {
                JSONObject body = new JSONObject()
                    .put("email", selectedAccount.email)
                    .put("offset", 0)
                    .put("limit", 50)
                    .put("unread_only", "unread".equals(selectedFolder.path));
                JSONObject data = api.post("/api/inbox/query", body);
                JSONArray arr = Json.array(data, "messages");
                mails.clear();
                for (int i = 0; i < arr.length(); i++) {
                    mails.add(Models.Mail.fromLocal(arr.optJSONObject(i), selectedAccount.id, selectedFolder.path));
                }
            } else {
                String path = "/imap/api/accounts/" + encode(selectedAccount.id) + "/mails?folder=" + encode(selectedFolder.path) + "&count=50&cacheOnly=1";
                JSONObject data = api.get(path);
                JSONArray arr = Json.array(data, "mails");
                mails.clear();
                for (int i = 0; i < arr.length(); i++) {
                    mails.add(Models.Mail.fromExternal(arr.optJSONObject(i), selectedAccount.id, selectedFolder.path));
                }
            }
            return "ok";
        }, result -> renderFoldersOrMails());
    }

    private View mailRow(Models.Mail mail) {
        LinearLayout row = cardContainer();
        row.setPadding(dp(14), dp(12), dp(14), dp(12));
        if (!mail.seen) {
            row.setBackground(bg(Color.rgb(238, 249, 247), 16, Color.rgb(159, 214, 207), 1));
        }
        TextView sender = text(nonEmpty(mail.sender, "未知发件人"), 15, TEXT, !mail.seen);
        TextView subject = text(nonEmpty(mail.subject, "无主题"), 15, TEXT, !mail.seen);
        TextView preview = text(mail.preview, 12, MUTED, false);
        TextView date = text(mail.date, 11, MUTED, false);
        row.addView(sender);
        row.addView(subject);
        if (!mail.preview.isEmpty()) row.addView(preview);
        row.addView(date);
        row.setOnClickListener(v -> loadDetail(mail));
        return row;
    }

    private void loadDetail(Models.Mail mail) {
        runAsync("打开邮件...", () -> {
            if ("external".equals(mail.accountType)) {
                return api.get("/imap/api/accounts/" + encode(mail.accountId) + "/mails/" + encode(mail.id) + "?folder=" + encode(mail.folder));
            }
            return api.post("/api/inbox/detail", new JSONObject().put("email", mail.accountId).put("message_id", mail.id));
        }, data -> renderDetail(mail, data));
    }

    private void renderDetail(Models.Mail mail, JSONObject data) {
        setHeader(nonEmpty(mail.subject, "邮件详情"), mail.sender);
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        LinearLayout box = column(dp(10));
        scroll.addView(box);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));

        JSONObject detail = data.optJSONObject("detail");
        JSONObject source = detail == null ? data : detail;
        String html = Json.str(source, "html");
        String text = Json.str(source, "text");
        box.addView(text("发件人：" + nonEmpty(mail.sender, Json.anyStr(source, "from", "from_address")), 14, TEXT, true));
        box.addView(text("时间：" + nonEmpty(mail.date, Json.anyStr(source, "date", "createdAt", "created_at")), 12, MUTED, false));
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.addView(outlineButton("回复", v -> renderComposeFor(mail, "回复：" + mail.subject)));
        actions.addView(outlineButton("转发", v -> renderComposeFor(mail, "转发：" + mail.subject)));
        box.addView(actions);

        WebView web = new WebView(this);
        WebSettings settings = web.getSettings();
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setBuiltInZoomControls(false);
        String body = html.isEmpty() ? "<pre style='white-space:pre-wrap;font:15px sans-serif;line-height:1.6'>" + escape(text) + "</pre>" : html;
        web.loadDataWithBaseURL(api.baseUrl(), body, "text/html", "UTF-8", null);
        box.addView(web, new LinearLayout.LayoutParams(-1, dp(560)));
    }

    private void renderCompose() {
        renderComposeFor(null, "");
    }

    private void renderComposeFor(Models.Mail source, String subject) {
        setHeader("写邮件", selectedAccount == null ? "选择发件账号" : selectedAccount.label());
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        LinearLayout box = column(dp(10));
        scroll.addView(box);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));
        EditText to = input("收件人，多个用逗号分隔", "", false);
        EditText sub = input("主题", subject, false);
        EditText body = input("正文", "", false);
        body.setMinLines(8);
        body.setGravity(Gravity.TOP);
        box.addView(to);
        box.addView(sub);
        box.addView(body);
        Button send = primaryButton("发送");
        send.setOnClickListener(v -> sendMail(to.getText().toString(), sub.getText().toString(), body.getText().toString()));
        box.addView(send);
    }

    private void sendMail(String to, String subject, String body) {
        Models.Account account = selectedAccount;
        if (account == null && !accounts.isEmpty()) account = accounts.get(0);
        if (account == null) {
            toast("请先选择发件账号");
            return;
        }
        Models.Account sender = account;
        runAsync("发送中...", () -> {
            JSONObject payload = new JSONObject()
                .put("to", to)
                .put("subject", subject)
                .put("text", body)
                .put("html", "<div>" + escape(body).replace("\n", "<br>") + "</div>");
            if ("external".equals(sender.type)) {
                return api.post("/imap/api/accounts/" + encode(sender.id) + "/send", payload);
            }
            payload.put("from_email", sender.email);
            return api.post("/api/send", payload);
        }, result -> {
            toast("已发送");
            navIndex = 1;
            loadMails(false);
        });
    }

    private void renderSettings() {
        setHeader("设置", server);
        content.removeAllViews();
        ScrollView scroll = new ScrollView(this);
        LinearLayout box = column(dp(12));
        scroll.addView(box);
        content.addView(scroll, new LinearLayout.LayoutParams(-1, -1));
        box.addView(card(text("移动端使用设备 Token 访问服务端 API。退出会删除本机 Token，不影响服务端其它设备。", 14, MUTED, false)));
        Button refresh = primaryButton("刷新账户和邮件");
        refresh.setOnClickListener(v -> fetchBootstrapThenAccounts(false));
        box.addView(refresh);
        Button notify = outlineButton(notifyEnabled ? "关闭新邮件通知" : "开启新邮件通知", v -> {
            notifyEnabled = !notifyEnabled;
            prefs.edit().putBoolean("notify", notifyEnabled).apply();
            renderSettings();
        });
        box.addView(notify);
        Button logout = outlineButton("退出本机 Token", v -> {
            prefs.edit().remove("token").apply();
            token = "";
            api.configure(server, "");
            showLogin();
        });
        box.addView(logout);
    }

    private void checkNotifications() {
        if (!notifyEnabled) return;
        int total = 0;
        for (Models.Account account : accounts) total += Math.max(0, account.unread);
        int old = prefs.getInt("last_unread_total", 0);
        prefs.edit().putInt("last_unread_total", total).apply();
        if (total > old && old > 0) notifyMail("Memail 新邮件", "未读邮件增加到 " + total + " 封");
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
        subtitle.setText(sub == null ? "" : sub);
    }

    private EditText input(String hint, String value, boolean password) {
        EditText edit = new EditText(this);
        edit.setHint(hint);
        edit.setText(value == null ? "" : value);
        edit.setTextSize(15);
        edit.setSingleLine(!hint.equals("正文"));
        edit.setPadding(dp(14), dp(10), dp(14), dp(10));
        edit.setBackground(bg(Color.WHITE, 14, LINE, 1));
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

    private LinearLayout cardContainer() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        card.setBackground(bg(CARD, 18, LINE, 1));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.setMargins(0, dp(5), 0, dp(5));
        card.setLayoutParams(lp);
        return card;
    }

    private Button primaryButton(String text) {
        Button btn = new Button(this);
        btn.setAllCaps(false);
        btn.setText(text);
        btn.setTextColor(Color.WHITE);
        btn.setTextSize(15);
        btn.setBackground(bg(PRIMARY, 18, PRIMARY, 0));
        return btn;
    }

    private Button outlineButton(String text, View.OnClickListener listener) {
        Button btn = new Button(this);
        btn.setAllCaps(false);
        btn.setText(text);
        btn.setTextColor(PRIMARY);
        btn.setTextSize(13);
        btn.setBackground(bg(Color.WHITE, 16, LINE, 1));
        btn.setOnClickListener(listener);
        return btn;
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

    private static String escape(String value) {
        return value == null ? "" : Html.escapeHtml(value);
    }

    private static String nonEmpty(String value, String fallback) {
        return value == null || value.isEmpty() ? fallback : value;
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

    private GradientDrawable bg(int color, int radiusDp, int strokeColor, int strokeWidthDp) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(radiusDp));
        if (strokeWidthDp > 0) drawable.setStroke(dp(strokeWidthDp), strokeColor);
        return drawable;
    }
}
