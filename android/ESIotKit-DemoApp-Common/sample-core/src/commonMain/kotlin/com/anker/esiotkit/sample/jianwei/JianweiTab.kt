package com.anker.esiotkit.sample.jianwei

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
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

/** WebView 只在安卓上有实现；iOS 端是占位。visible = false 时网页保留、只是不显示（也不接收点击）。 */
@Composable
expect fun PlatformWebView(url: String, modifier: Modifier, visible: Boolean)

/** 首页 / 趋势 / 分享三个页签的编号，和 MegaMainScreen 里 when (selectedItem) 的编号一致 */
const val TAB_HOME = 2
const val TAB_TRENDS = 3
const val TAB_SHARE = 5

/**
 * 首页 / 趋势 / 分享三个网页一直保留，切换页签时只是显示 / 隐藏，不再新建网页、重新加载。
 * （以前每切一次都新建一个网页，旧的也没有销毁，在后台照常收实时数据，越切越卡、越占内存。）
 * 首页顶部：手机和电脑断开时出现一条提醒，连上时不显示。
 */
@Composable
fun JianweiTabs(selected: Int) {
  LaunchedEffect(Unit) { JianweiBridge.start() }
  val s by JianweiBridge.state.collectAsState()
  Box(modifier = Modifier.fillMaxSize()) {
    Column(modifier = Modifier.fillMaxSize()) {
      if (selected == TAB_HOME && !s.serverOk) {
        Text(
          text = "手机和电脑断开了，请检查 USB 线",
          color = Color.White,
          fontSize = 16.sp,
          modifier = Modifier.fillMaxWidth()
            .background(Color(0xFF8A4B00))
            .padding(horizontal = 16.dp, vertical = 8.dp),
        )
      }
      PlatformWebView(ELDER_URL, Modifier.fillMaxSize(), visible = selected == TAB_HOME)
    }
    PlatformWebView(TRENDS_URL, Modifier.fillMaxSize(), visible = selected == TAB_TRENDS)
    PlatformWebView(SHARE_URL, Modifier.fillMaxSize(), visible = selected == TAB_SHARE)
  }
}
