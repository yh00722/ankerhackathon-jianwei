package com.anker.esiotkit.sample.jianwei

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.anker.esiotkit.sample.view.BaseScreen

/** 老人端页面地址：笔记本上的网页，经 USB `adb reverse tcp:5173 tcp:5173` 映射到手机。 */
const val ELDER_URL = "http://localhost:5173/#/elder"

/** 两周趋势页（AI 小结 + 图表）；app=1 表示在 App 的「趋势」页签里打开，网页不显示「返回」。 */
const val TRENDS_URL = "http://localhost:5173/#/trends?app=1"

/** 分享给家人 / 康复师的设置页；在原生页面 [JianweiWebScreen] 里打开，返回用顶栏的箭头。 */
const val SHARE_URL = "http://localhost:5173/#/share?app=1"

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

/**
 * 「趋势」页签：老人自己看的两周趋势和 AI 小结（和老人端「看两周趋势」按钮打开的是同一个网页）。
 */
@Composable
fun JianweiTrendsTab() {
  LaunchedEffect(Unit) { JianweiBridge.start() } // 从这个页签进 App 时，设备桥也照常运行
  PlatformWebView(TRENDS_URL, Modifier.fillMaxSize())
}

/** 顶栏右上角的「分享」按钮（「见微知行」和「趋势」页签都有），打开分享给家人 / 康复师的设置页。 */
@Composable
fun JianweiShareAction(onClick: () -> Unit) {
  TextButton(onClick = onClick) {
    Icon(Icons.Default.Share, contentDescription = null, tint = Color(0xFF005D8E), modifier = Modifier.size(20.dp))
    Spacer(Modifier.width(4.dp))
    Text("分享", color = Color(0xFF005D8E), fontSize = 16.sp)
  }
}

/**
 * 原生页面里打开一个见微知行网页，顶栏带标题和返回箭头（例如「分享给家人 / 康复师」）。
 */
class JianweiWebScreen(private val pageTitle: String, private val url: String) : BaseScreen() {
  @Composable
  override fun dynamicTitle(): String = pageTitle

  @Composable
  override fun Content() {
    BaseColumn(paddingHorizontal = 0.dp) {
      PlatformWebView(url, Modifier.fillMaxSize())
    }
  }
}
