package com.boat.companion.adapter

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.boat.companion.monitor.FrameRow
import com.boat.companion.usb.SlcanAdapter
import com.boat.companion.usb.SlcanCodec
import com.boat.companion.usb.SlcanFrame
import com.boat.companion.usb.UsbAdapterHost
import com.boat.companion.usb.frames
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.buffer
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val HISTORY_LIMIT = 500
private const val PUBLISH_INTERVAL_MS = 100L

data class AdapterUiState(
    val deviceName: String? = null,
    val attached: Boolean = false,
    val streaming: Boolean = false,
    val bitrate: SlcanCodec.Bitrate = SlcanCodec.Bitrate.B500K,
    val silent: Boolean = false,
    val received: Long = 0,
    val framesPerSecond: Int = 0,
    val error: String? = null,
)

/**
 * Reads CAN frames straight off the USB adapter. No gateway, no network — this
 * is the standalone capture path.
 */
class AdapterViewModel(application: Application) : AndroidViewModel(application) {

    private val _state = MutableStateFlow(AdapterUiState())
    val state: StateFlow<AdapterUiState> = _state.asStateFlow()

    private val _frames = MutableStateFlow<List<FrameRow>>(emptyList())
    val frames: StateFlow<List<FrameRow>> = _frames.asStateFlow()

    private val history = ArrayDeque<FrameRow>(HISTORY_LIMIT)
    private val historyLock = Any()

    private var adapter: SlcanAdapter? = null
    private var readJob: Job? = null
    private var publishJob: Job? = null

    private var sequence = 0L
    private var received = 0L
    private var windowCount = 0
    private var windowStartedAt = 0L

    init {
        refreshAttachment()
    }

    fun refreshAttachment() {
        val device = UsbAdapterHost.findAdapter(getApplication())
        _state.value = _state.value.copy(
            attached = device != null,
            deviceName = device?.let { "${it.manufacturerName} ${it.productName}" }?.trim(),
        )
    }

    fun setBitrate(bitrate: SlcanCodec.Bitrate) {
        _state.value = _state.value.copy(bitrate = bitrate)
    }

    fun setSilent(silent: Boolean) {
        _state.value = _state.value.copy(silent = silent)
    }

    fun dismissError() {
        _state.value = _state.value.copy(error = null)
    }

    fun clear() {
        synchronized(historyLock) { history.clear() }
        _frames.value = emptyList()
        received = 0
        _state.value = _state.value.copy(received = 0, framesPerSecond = 0)
    }

    fun toggle() {
        if (_state.value.streaming) stop() else start()
    }

    private fun start() {
        val context = getApplication<Application>()
        val device = UsbAdapterHost.findAdapter(context)
        if (device == null) {
            _state.value = _state.value.copy(error = "No CAN adapter attached")
            return
        }

        viewModelScope.launch {
            if (!UsbAdapterHost.requestPermission(context, device)) {
                _state.value = _state.value.copy(error = "USB permission denied")
                return@launch
            }

            val opened = UsbAdapterHost.connect(context, device)
                .mapCatching { slcan ->
                    slcan.open(_state.value.bitrate, _state.value.silent).getOrThrow()
                    slcan
                }

            opened.onFailure { error ->
                _state.value = _state.value.copy(
                    error = error.message ?: error::class.java.simpleName,
                )
            }
            opened.onSuccess { slcan ->
                adapter = slcan
                windowStartedAt = System.currentTimeMillis()
                windowCount = 0
                _state.value = _state.value.copy(streaming = true, error = null, attached = true)

                publishJob = viewModelScope.launch { publishLoop() }
                readJob = viewModelScope.launch {
                    try {
                        withContext(Dispatchers.IO) {
                            slcan.frames().buffer().collect { record(it) }
                        }
                    } catch (cancellation: CancellationException) {
                        throw cancellation
                    } catch (error: Exception) {
                        _state.value = _state.value.copy(
                            error = error.message ?: error::class.java.simpleName,
                            streaming = false,
                        )
                    }
                }
            }
        }
    }

    private fun stop() {
        readJob?.cancel(); readJob = null
        publishJob?.cancel(); publishJob = null
        runCatching { adapter?.close() }
        adapter = null
        _state.value = _state.value.copy(streaming = false)
    }

    private fun record(frame: SlcanFrame) {
        val row = FrameRow(
            seq = sequence++,
            timestampNs = frame.timestampNanos,
            iface = "usb",
            busType = if (frame.fd) "CANFD" else "CAN",
            identifier = if (frame.extended) "0x%08X".format(frame.id)
            else "0x%03X".format(frame.id),
            length = frame.data.size,
            data = frame.data.joinToString(" ") { "%02X".format(it) },
            // Nothing here is an echo: these frames came off the wire, not from
            // a gateway that also transmitted them.
            selfSent = false,
        )
        synchronized(historyLock) {
            history.addFirst(row)
            while (history.size > HISTORY_LIMIT) history.removeLast()
        }
        received++
        windowCount++
    }

    private suspend fun publishLoop() {
        while (viewModelScope.isActive) {
            delay(PUBLISH_INTERVAL_MS)
            _frames.value = synchronized(historyLock) { history.toList() }

            val now = System.currentTimeMillis()
            val elapsed = now - windowStartedAt
            if (elapsed >= 1000) {
                _state.value = _state.value.copy(
                    received = received,
                    framesPerSecond = (windowCount * 1000L / elapsed).toInt(),
                )
                windowCount = 0
                windowStartedAt = now
            } else {
                _state.value = _state.value.copy(received = received)
            }
        }
    }

    override fun onCleared() {
        stop()
        super.onCleared()
    }
}
