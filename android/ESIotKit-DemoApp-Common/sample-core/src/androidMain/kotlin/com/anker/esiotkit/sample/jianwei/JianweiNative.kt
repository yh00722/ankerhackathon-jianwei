package com.anker.esiotkit.sample.jianwei

import android.content.Context
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.speech.tts.TextToSpeech
import android.webkit.JavascriptInterface
import java.util.Locale

/**
 * 给老人端网页用的原生能力，网页里是 window.JianweiNative：
 * - speak(text)：用系统语音合成念口令和次数（安卓 WebView 不支持网页的 speechSynthesis）
 * - vibrate(ms)：每数一次振动一下
 * - ready()：语音是否可用（系统没有中文语音时返回 false，网页只显示文字）
 * 只念服务端算出来的口令和数字；不录音。
 */
class JianweiNative(context: Context) {
  private val app = context.applicationContext
  @Volatile private var ok = false
  private lateinit var tts: TextToSpeech

  init {
    tts = TextToSpeech(app) { status ->
      if (status == TextToSpeech.SUCCESS) {
        val r = tts.setLanguage(Locale.SIMPLIFIED_CHINESE)
        ok = r != TextToSpeech.LANG_MISSING_DATA && r != TextToSpeech.LANG_NOT_SUPPORTED
        tts.setSpeechRate(0.95f)
      }
    }
  }

  @JavascriptInterface
  fun ready(): Boolean = ok

  /** 打断正在念的，念新的（口令要跟得上动作）。 */
  @JavascriptInterface
  fun speak(text: String) {
    if (ok && text.isNotBlank()) tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jw")
  }

  @JavascriptInterface
  fun vibrate(ms: Int) {
    val v: Vibrator? = if (Build.VERSION.SDK_INT >= 31) {
      (app.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
    } else {
      @Suppress("DEPRECATION")
      app.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
    }
    val d = ms.coerceIn(10, 1000).toLong()
    if (Build.VERSION.SDK_INT >= 26) v?.vibrate(VibrationEffect.createOneShot(d, VibrationEffect.DEFAULT_AMPLITUDE))
    else @Suppress("DEPRECATION") v?.vibrate(d)
  }
}
