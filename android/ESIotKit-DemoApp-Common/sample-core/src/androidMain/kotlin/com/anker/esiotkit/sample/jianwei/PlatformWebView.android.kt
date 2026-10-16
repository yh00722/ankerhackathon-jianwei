package com.anker.esiotkit.sample.jianwei

import android.annotation.SuppressLint
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView

/** 安卓 WebView：开启 JS / DOM Storage，保持亮屏；页面没连上（本机服务未启动）时 2 秒后自动重试。
 * 挂上 window.JianweiNative（系统语音、振动），老人端用它念训练口令和次数。 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
actual fun PlatformWebView(url: String, modifier: Modifier) {
  AndroidView(
    modifier = modifier,
    factory = { ctx ->
      WebView(ctx).apply {
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        keepScreenOn = true
        addJavascriptInterface(JianweiNative(ctx), "JianweiNative")
        webViewClient = object : WebViewClient() {
          override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame) view.postDelayed({ view.loadUrl(url) }, 2_000)
          }
        }
        loadUrl(url)
      }
    },
  )
}
