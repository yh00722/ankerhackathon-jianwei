package com.anker.esiotkit.sample.jianwei

import androidx.compose.foundation.layout.Box
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/** 本次只做安卓端；iOS 占位，保证公共代码能编译。 */
@Composable
actual fun PlatformWebView(url: String, modifier: Modifier, visible: Boolean) {
  if (visible) Box(modifier) { Text("请在安卓 App 中使用：$url") }
}
