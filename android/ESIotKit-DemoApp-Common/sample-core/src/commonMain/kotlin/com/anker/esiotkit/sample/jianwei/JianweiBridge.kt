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
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
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
 * 用 App SDK 执行后回报确认（ACK）和耗时；每 5 秒上报一次设备状态（在线、电量、Wi-Fi、追踪、直播画质、预置位）。
 * 设备控制只走这里，网页端不直接碰设备。
 */
object JianweiBridge {
  const val SERVER = "http://localhost:8000"

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
  private var lastId = 0L
  private var cameraSn: String? = null

  fun start() {
    if (job?.isActive == true) return
    job = scope.launch { loop() }
  }

  private suspend fun loop() {
    var lastStatus = 0L
    while (scope.isActive) {
      try {
        if (cameraSn == null) {
          val cfg = Json.parseToJsonElement(http.get("$SERVER/api/bridge/config").bodyAsText()).jsonObject
          cameraSn = cfg["camera_sn"]?.jsonPrimitive?.content
          _state.value = _state.value.copy(cameraSn = cameraSn)
        }
        val cmds = Json.parseToJsonElement(http.get("$SERVER/api/bridge/commands?after=$lastId").bodyAsText()).jsonArray
        for (c in cmds) {
          val o = c.jsonObject
          val id = o["id"]!!.jsonPrimitive.long
          lastId = maxOf(lastId, id)
          execute(id, o["type"]!!.jsonPrimitive.content, o["args"] as? JsonObject ?: JsonObject(emptyMap()))
        }
        _state.value = _state.value.copy(serverOk = true, lastError = null)
        val now = currentTimeMillis()
        if (now - lastStatus > 5_000) {
          lastStatus = now
          postStatus()
        }
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
      when (type) {
        "GOTO_PRESET" -> live.jumpToPreset(args["index"]!!.jsonPrimitive.int, if (force) 1 else 0)
        "SAVE_PRESET" -> live.savePreset(args["index"]!!.jsonPrimitive.int, if (force) 1 else 0)
        "PTZ_STEP" -> live.controlPTZ(PTZDirection.valueOf(args["dir"]!!.jsonPrimitive.content))
        "SET_TRACKING" -> live.toggleAITracking(args["on"]!!.jsonPrimitive.boolean)
        // 直播画质（设备级设置，对网页取流也生效）：分析只需要 960 宽，720P 码率低、卡顿少
        "SET_RESOLUTION" -> esInject<ESIotDeviceManager>().video(sn).setResolution(args["value"]!!.jsonPrimitive.content)
        "QUERY_STATUS" -> postStatus().let { ThingResult.Success(Unit) }
        "QUERY_EVENTS" -> postEvents(sn).let { ThingResult.Success(Unit) }
        else -> ThingResult.Error(-1, "unknown command $type")
      }
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
    val presets = when (val r = esInject<ESIotDeviceManager>().liveStream(sn).queryPresetPositions()) {
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
    http.post("$SERVER$path") { setBody(TextContent(json, ContentType.Application.Json)) }
  }
}
