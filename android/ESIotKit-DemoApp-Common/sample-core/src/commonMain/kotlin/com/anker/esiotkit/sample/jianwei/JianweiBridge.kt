package com.anker.esiotkit.sample.jianwei

import com.anker.esiotkit.core.device.MegaDeviceManager
import com.anker.esiotkit.core.support.constants.ThingIdentifier
import com.anker.esiotkit.data_center.export.LocalEventRepository
import com.anker.esiotkit.domain_thing.api.ESIotDeviceManager
import com.anker.esiotkit.domain_thing.api.domain.PTZDirection
import com.anker.esiotkit.domain_thing.api.model.ThingResult
import com.anker.esiotkit.event.domain.repository.HistoryRecordSearchParams
import com.anker.esiotkit.sample.demo.net.createHttpClient
import com.anker.esiotkit.sample.support.propertyBoolean
import com.anker.esiotkit.sample.support.propertyInt
import com.anker.esiotkit.sample.support.propertyString
import com.anker.esiotkit.support.di.esInject
import com.anker.esiotkit.support.platform.currentTimeMillis
import com.anker.esiotkit.support.utils.DateUtil
import io.ktor.client.request.get
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.content.TextContent
import kotlin.coroutines.resume
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.put

/**
 * 见微知行 · 设备桥。
 *
 * 每 0.5 秒向本机分析服务（经 USB `adb reverse` 映射到手机的 localhost:8000）拉一次指令，
 * 用 App SDK 执行后回报确认（ACK）和耗时；每 5 秒上报一次设备状态（在线、电量、Wi-Fi 名称 / 信号 / IP、追踪、直播画质、预置位）。
 * 只读设备属性，不碰 Wi-Fi 密码。
 * 设备控制只走这里，网页端不直接碰设备。
 *
 * 不卡住（10/16 晚）：摄像头不响应时，一条指令能把轮询卡住几十秒，电脑那边就显示「手机和电脑断开」。所以
 * - 拉指令、执行指令、报状态各在自己的协程里，执行或查询卡住时轮询照常；
 * - 每个对本机服务的请求最多 6 秒（示例工程的客户端默认 120 秒，是给慢接口用的）；
 * - 每条设备指令最多等 20 秒，等不到就回报失败；状态里的预置位查询最多 2 秒；
 * - 在手机上排队超过 25 秒的指令不再执行（回报失败），免得摄像头恢复后一口气补做、乱转。
 */
object JianweiBridge {
  const val SERVER = "http://localhost:8000"
  private const val QUEUE_MAX_MS = 25_000L

  data class UiState(
    val serverOk: Boolean = false,
    val cameraSn: String? = null,
    val lastCommand: String? = null,
    val lastError: String? = null,
  )

  private val _state = MutableStateFlow(UiState())
  val state: StateFlow<UiState> = _state

  private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
  private val http by lazy { createHttpClient() }
  private var job: Job? = null
  private var worker: Job? = null
  private var reporter: Job? = null
  private var lastId = 0L
  private var cameraSn: String? = null
  private class Cmd(val id: Long, val type: String, val args: JsonObject, val at: Long)
  private val queue = Channel<Cmd>(Channel.UNLIMITED)

  fun start() {
    if (worker?.isActive != true) worker = scope.launch {
      for (c in queue) {
        runCatching {
          if (currentTimeMillis() - c.at > QUEUE_MAX_MS) ack(c.id, false, -1003, "排队超过 25 秒，没有执行", 0.0)
          else execute(c.id, c.type, c.args)
        }
      }
    }
    if (reporter?.isActive != true) reporter = scope.launch {
      while (isActive) {
        runCatching { postStatus() }
        delay(5_000)
      }
    }
    if (job?.isActive == true) return
    job = scope.launch { loop() }
  }

  private suspend fun loop() {
    while (scope.isActive) {
      try {
        if (cameraSn == null) {
          val cfg = Json.parseToJsonElement(within(6_000) { http.get("$SERVER/api/bridge/config").bodyAsText() }).jsonObject
          cameraSn = cfg["camera_sn"]?.jsonPrimitive?.content
          _state.value = _state.value.copy(cameraSn = cameraSn)
        }
        val cmds = Json.parseToJsonElement(within(6_000) { http.get("$SERVER/api/bridge/commands?after=$lastId").bodyAsText() }).jsonArray
        for (c in cmds) {
          val o = c.jsonObject
          val id = o["id"]!!.jsonPrimitive.long
          lastId = maxOf(lastId, id)
          queue.trySend(Cmd(id, o["type"]!!.jsonPrimitive.content, o["args"] as? JsonObject ?: JsonObject(emptyMap()), currentTimeMillis()))
        }
        _state.value = _state.value.copy(serverOk = true, lastError = null)
      } catch (e: Throwable) {
        if (e is CancellationException) throw e
        _state.value = _state.value.copy(serverOk = false, lastError = e.message)
      }
      delay(500)
    }
  }

  private suspend fun execute(id: Long, type: String, args: JsonObject) {
    val sn = cameraSn ?: return ack(id, false, -1001, "camera sn unknown", 0.0)
    val t0 = currentTimeMillis()
    _state.value = _state.value.copy(lastCommand = "#$id $type")
    val live = esInject<ESIotDeviceManager>().liveStream(sn)
    val force = (args["force"] as? JsonPrimitive)?.booleanOrNull == true
    val result: ThingResult<*> = try {
      device(20_000) { when (type) {
        "GOTO_PRESET" -> live.jumpToPreset(args["index"]!!.jsonPrimitive.int, if (force) 1 else 0)
        "SAVE_PRESET" -> live.savePreset(args["index"]!!.jsonPrimitive.int, if (force) 1 else 0)
        "PTZ_STEP" -> live.controlPTZ(PTZDirection.valueOf(args["dir"]!!.jsonPrimitive.content))
        "SET_TRACKING" -> live.toggleAITracking(args["on"]!!.jsonPrimitive.boolean)
        // 直播画质（设备级设置，对网页取流也生效）：分析只需要 960 宽，720P 码率低、卡顿少
        "SET_RESOLUTION" -> esInject<ESIotDeviceManager>().video(sn).setResolution(args["value"]!!.jsonPrimitive.content)
        "QUERY_STATUS" -> postStatus().let { ThingResult.Success(Unit) }
        "QUERY_EVENTS" -> postEvents(sn).let { ThingResult.Success(Unit) }
        else -> ThingResult.Error(-1, "unknown command $type")
      } } ?: ThingResult.Error(-1002, "设备 20 秒没有响应")
    } catch (e: Throwable) {
      if (e is CancellationException) throw e
      ThingResult.Error(-9999, e.message ?: e.toString())
    }
    val ms = (currentTimeMillis() - t0).toDouble()
    when (result) {
      is ThingResult.Success -> ack(id, true, 0, null, ms)
      is ThingResult.Error -> ack(id, false, result.code, result.message, ms)
    }
  }

  private suspend fun ack(id: Long, ok: Boolean, code: Int, msg: String?, ms: Double) {
    for (attempt in 0 until 3) {  // 回报确认失败（比如 USB 抖了一下）就再试，别让电脑那边一直等
      val ok2 = runCatching { postAck(id, ok, code, msg, ms) }.isSuccess
      if (ok2) return
      delay(500)
    }
  }

  private suspend fun postAck(id: Long, ok: Boolean, code: Int, msg: String?, ms: Double) {
    post("/api/bridge/ack", buildJsonObject {
      put("id", id)
      put("ok", ok)
      put("code", code)
      if (msg != null) put("msg", msg)
      put("latency_ms", ms)
    }.toString())
  }

  /** 设备状态：物模型属性（在线、电量、充电、Wi-Fi、追踪）+ 预置位列表。 */
  private suspend fun postStatus() {
    val sn = cameraSn ?: return
    val dev = MegaDeviceManager.getDeviceBy(sn) ?: return
    val presets = when (val r = device(2_000) { esInject<ESIotDeviceManager>().liveStream(sn).queryPresetPositions() }) {
      is ThingResult.Success -> r.data
      else -> null
    }
    val track = dev.propertyString("motion_track_status")
    val res = dev.getProperty(ThingIdentifier.LIVE_STREAM_RESOLUTION)
    val body = buildJsonObject {
      put("sn", sn)
      put("online", dev.propertyBoolean("device_is_online"))
      put("battery", dev.propertyInt("battery_value", -1))
      put("charging", dev.propertyInt("charging_status", -1))
      put("wifi_level", dev.propertyInt("device_wifi_signal_strength_level", -1))
      put("wifi_dbm", dev.propertyInt("device_wifi_signal_strength", 0))
      dev.propertyString(ThingIdentifier.DEVICE_WIFI_NAME).takeIf { it.isNotEmpty() }?.let { put("wifi_name", it) }
      dev.propertyString(ThingIdentifier.IP_ADDRESS).takeIf { it.isNotEmpty() }?.let { put("ip", it) }
      put("tracking", track == "1" || track.equals("true", ignoreCase = true))
      res?.get()?.asString()?.let { put("resolution", it) }
      res?.td?.dataType?.specs?.enumList?.let { opts ->
        put("resolution_options", buildJsonArray {
          opts.forEach { o -> add(buildJsonObject { put("value", o.value); put("desc", o.desc) }) }
        })
      }
      if (presets != null) put("presets", buildJsonArray { presets.forEach { add(JsonPrimitive(it)) } })
      put("ts", currentTimeMillis())
    }
    post("/api/bridge/status", body.toString())
  }

  /** 今天的设备事件（App SDK 事件查询）：只转发时间和类型，不取缩略图。 */
  private suspend fun postEvents(sn: String) {
    val dev = MegaDeviceManager.getDeviceBy(sn) ?: return
    val repo = runCatching { esInject<LocalEventRepository>() }.getOrNull() ?: return
    val today = DateUtil.getToday()
    val params = HistoryRecordSearchParams(dev.parentSn, today, today).setDeviceSnList(listOf(sn)).setCount(20)
    val events = withTimeoutOrNull(15_000) {
      suspendCancellableCoroutine<JsonArray> { cont ->
        repo.getHistoryRecord(isRefresh = true, params = params) { res ->
          if (!cont.isActive) return@getHistoryRecord
          cont.resume(buildJsonArray {
            res.recordList.forEach { r ->
              add(buildJsonObject {
                put("id", r.record_id.toString())
                put("start_time", r.start_time.toString())
                put("type", r.trigger_type.toString())
              })
            }
          })
        }
      }
    } ?: JsonArray(emptyList())
    post("/api/bridge/events", events.toString())
  }

  private suspend fun post(path: String, json: String) {
    within(6_000) { http.post("$SERVER$path") { setBody(TextContent(json, ContentType.Application.Json)) } }
  }

  /** 等设备最多 ms 毫秒，等不到返回 null。SDK 的调用不一定响应取消，所以放进单独的协程里等：超时就不等了，那次调用自己在后台结束。 */
  private suspend fun <T> device(ms: Long, block: suspend () -> T): T? =
    withTimeoutOrNull(ms) { scope.async { block() }.await() }

  /** 带超时的本机请求。超时转成普通异常：withTimeout 抛的是取消异常，上面的 catch 会把它再抛出去，整个轮询就停了。 */
  private suspend fun <T> within(ms: Long, block: suspend CoroutineScope.() -> T): T =
    try {
      withTimeout(ms, block)
    } catch (e: TimeoutCancellationException) {
      throw IllegalStateException("本机服务 ${ms / 1000} 秒没有响应")
    }
}
