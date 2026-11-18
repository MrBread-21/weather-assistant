package com.mrbread.weather;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.webkit.GeolocationPermissions;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * App Android mỏng: mở giao diện thời tiết chạy trên server mrbread (qua Tailscale)
 * bằng một WebView. Nhờ vậy có icon riêng, mở phát vào thẳng, và lấy được GPS.
 *
 * Muốn đổi địa chỉ server thì sửa APP_URL rồi build lại.
 */
public class MainActivity extends Activity {

    // Địa chỉ app web trên server (link Tailscale). Đổi nếu tên máy hoặc tailnet khác.
    private static final String APP_URL = "https://mrbread.tail10f69f.ts.net/";

    private WebView web;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Xin quyền vị trí để trang web trong WebView lấy được GPS
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION}, 1);
        }

        web = new WebView(this);
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setGeolocationEnabled(true);

        web.setWebViewClient(new WebViewClient());  // giữ điều hướng bên trong app
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onGeolocationPermissionsShowPrompt(
                    String origin, GeolocationPermissions.Callback callback) {
                callback.invoke(origin, true, false);  // cho phép trang lấy vị trí
            }
        });

        if (savedInstanceState == null) {
            web.loadUrl(APP_URL);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    protected void onRestoreInstanceState(Bundle savedInstanceState) {
        super.onRestoreInstanceState(savedInstanceState);
        web.restoreState(savedInstanceState);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();   // nút back đi lại trong trang trước khi thoát app
        } else {
            super.onBackPressed();
        }
    }
}
