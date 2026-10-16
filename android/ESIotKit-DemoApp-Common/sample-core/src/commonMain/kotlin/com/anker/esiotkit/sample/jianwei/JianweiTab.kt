package com.anker.esiotkit.sample.jianwei

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** 老人端页面地址：笔记本上的网页，经 USB `adb reverse tcp:5173 tcp:5173` 映射到手机。 */
const val ELDER_URL = "http://localhost:5173/#/elder"

/** 两周趋势页（AI 小结 + 图表）；app=1 表示在 App 的「趋势」页签里打开，网页不显示「返回」。 */
const val TRENDS_URL = "http://localhost:5173/#/trends?app=1"

/** 分享给家人 / 康复师的设置页（App 底部「分享」页签） */
const val SHARE_URL = "http://localhost:5173/#/share?app=1"

/** WebView 只在安卓上有实现；iOS 端是占位。 */
@Composable
expect fun PlatformWebView(url: String, modifier: Modifier)

/**
 * 「首页」页签：老人端网页。手机和电脑断开时顶部出现一条提醒（连上时不显示，不占地方）。
 */
@Composable
fun JianweiTab() {
  LaunchedEffect(Unit) { JianweiBridge.start() }
  val s by JianweiBridge.state.collectAsState()
  Column(modifier = Modifier.fillMaxSize()) {
    if (!s.serverOk) {
      Text(
        text = "手机和电脑断开了，请检查 USB 线",
        color = Color.White,
        fontSize = 16.sp,
        modifier = Modifier.fillMaxWidth()
          .background(Color(0xFF8A4B00))
          .padding(horizontal = 16.dp, vertical = 8.dp),
      )
    }
    PlatformWebView(ELDER_URL, Modifier.fillMaxSize())
  }
}

/**
 * 「趋势」页签：老人自己看的两周趋势和 AI 小结（和老人端「看两周趋势」按钮打开的是同一个网页）。
 */
@Composable
fun JianweiTrendsTab() {
  LaunchedEffect(Unit) { JianweiBridge.start() } // 从这个页签进 App 时，设备桥也照常运行
  PlatformWebView(TRENDS_URL, Modifier.fillMaxSize())
}

/** 「分享」页签：分享给家人 / 康复师的设置。 */
@Composable
fun JianweiShareTab() {
  LaunchedEffect(Unit) { JianweiBridge.start() }
  PlatformWebView(SHARE_URL, Modifier.fillMaxSize())
}
