package com.anker.esiotkit.sample.jianwei

import android.annotation.SuppressLint
import android.view.View
import android.view.ViewGroup
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView

/** 安卓 WebView：开启 JS / DOM Storage，保持亮屏；页面没连上（本机服务未启动）时 2 秒后自动重试。
 * 挂上 window.JianweiNative（系统语音、振动），老人端用它念训练口令和次数。
 * visible = false：网页保留但隐藏（不画、不接收点击）；重新显示时发 jw:show 事件，网页借此刷新数据。
 * 从界面上移除时销毁 WebView（以前没销毁，切一次页签就多留一个网页在后台运行）。 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
actual fun PlatformWebView(url: String, modifier: Modifier, visible: Boolean) {
  AndroidView(
    modifier = modifier,
    factory = { ctx ->
      WebView(ctx).apply {
        // 高度要「填满」：默认是随内容，网页内核会把页面的基准高度当成 0，100vh / 百分比高度都算成 0
        layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
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
    update = { view ->
      val v = if (visible) View.VISIBLE else View.INVISIBLE
      if (view.visibility != v) {
        view.visibility = v
        if (visible) view.evaluateJavascript("window.dispatchEvent(new Event('jw:show'))", null)
      }
    },
    onRelease = { view ->
      view.stopLoading()
      view.destroy()
    },
  )
}
