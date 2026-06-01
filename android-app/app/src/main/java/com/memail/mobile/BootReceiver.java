package com.memail.mobile;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (context == null || intent == null) return;
        String action = intent.getAction();
        if (!Intent.ACTION_BOOT_COMPLETED.equals(action) && !Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) return;
        SharedPreferences prefs = context.getSharedPreferences(MobileSyncEngine.PREFS, Context.MODE_PRIVATE);
        String token = prefs.getString("token", "");
        if (token == null || token.isEmpty()) return;
        BackgroundSyncService.schedule(context);
        RealtimeSyncService.start(context);
    }
}
