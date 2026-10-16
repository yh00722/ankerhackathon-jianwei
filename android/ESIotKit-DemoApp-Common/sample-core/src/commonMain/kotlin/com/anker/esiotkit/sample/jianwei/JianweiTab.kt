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

/** WebView 只在安卓上有实现；iOS 端是占位。 */
@Composable
expect fun PlatformWebView(url: String, modifier: Modifier)

/**
 * 「见微知行」页签：顶部一条设备桥状态，下面是老人端网页。
 */
@Composable
fun JianweiTab() {
  LaunchedEffect(Unit) { JianweiBridge.start() }
  val s by JianweiBridge.state.collectAsState()
  Column(modifier = Modifier.fillMaxSize()) {
    Text(
      text = if (s.serverOk) "设备桥已连接本机 · ${s.cameraSn ?: "—"}${s.lastCommand?.let { " · $it" } ?: ""}"
      else "设备桥未连接本机服务（检查 USB 线和 adb reverse）",
      color = Color.White,
      fontSize = 12.sp,
      modifier = Modifier.fillMaxWidth()
        .background(if (s.serverOk) Color(0xFF2E7D5B) else Color(0xFFB7791F))
        .padding(horizontal = 12.dp, vertical = 4.dp),
    )
    PlatformWebView(ELDER_URL, Modifier.fillMaxSize())
  }
}
