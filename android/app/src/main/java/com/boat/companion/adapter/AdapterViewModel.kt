package com.boat.companion.adapter

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.boat.companion.bridge.GatewayBridge
import com.boat.companion.monitor.FrameRow
import com.boat.companion.net.GatewayConnection
import com.boat.companion.trace.TraceRecorder
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
import java.io.File

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
    val recording: Boolean = false,
    val recordedFrames: Long = 0,
    val recordedBytes: Long = 0,
    val recordingName: String? = null,
    val traces: List<File> = emptyList(),
    val bridgeIface: String = "vcan0",
    val bridging: Boolean = false,
    val bridgedToGateway: Long = 0,
    val bridgedToBus: Long = 0,
    val bridgeDropped: Long = 0,
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

    /** Guarded by [recorderLock]: written from the USB read thread, closed from the UI. */
    private var recorder: TraceRecorder? = null
    private val recorderLock = Any()

    @Volatile
    private var bridge: GatewayBridge? = null
    private var bridgeJob: Job? = null

    private var sequence = 0L
    private var received = 0L
    private var windowCount = 0
    private var windowStartedAt = 0L

    init {
        refreshAttachment()
        refreshTraces()
    }

    fun refreshTraces() {
        _state.value = _state.value.copy(traces = TraceRecorder.list(getApplication()))
    }

    /**
     * Recording is independent of the on-screen list: the UI keeps a bounded
     * history, the file keeps everything.
     */
    fun toggleRecording() {
        if (_state.value.recording) stopRecording() else startRecording()
    }

    private fun startRecording() {
        TraceRecorder.start(getApplication(), _state.value.bitrate.bitsPerSecond)
            .onSuccess { started ->
                synchronized(recorderLock) { recorder = started }
                _state.value = _state.value.copy(
                    recording = true,
                    recordedFrames = 0,
                    recordedBytes = 0,
                    recordingName = started.file.name,
                    error = null,
                )
            }
            .onFailure { error ->
                _state.value = _state.value.copy(
                    error = "Could not start recording: ${error.message ?: error::class.java.simpleName}"
                )
            }
    }

    private fun stopRecording() {
        val finished = synchronized(recorderLock) {
            val current = recorder
            recorder = null
            current
        }
        runCatching { finished?.close() }
        _state.value = _state.value.copy(
            recording = false,
            recordedBytes = finished?.sizeBytes ?: 0,
        )
        refreshTraces()
    }

    fun delete(file: File) {
        runCatching { file.delete() }
        refreshTraces()
    }

    fun setBridgeIface(value: String) {
        _state.value = _state.value.copy(bridgeIface = value)
    }

    fun toggleBridge() {
        if (_state.value.bridging) stopBridge() else startBridge()
    }

    /**
     * Publishes the adapter's bus onto a gateway interface and transmits what
     * the gateway sends back.
     *
     * The target must be a vcan on the gateway. Pointing it at a physical
     * interface that shares this bus would feed every frame back to its source.
     */
    private fun startBridge() {
        val adapter = this.adapter
        if (adapter == null) {
            _state.value = _state.value.copy(error = "Start the adapter before bridging")
            return
        }
        val client = GatewayConnection.client.value
        if (client == null) {
            _state.value = _state.value.copy(error = "Not connected to a gateway")
            return
        }
        val iface = _state.value.bridgeIface.trim()
        if (iface.isEmpty()) {
            _state.value = _state.value.copy(error = "Bridge interface is required")
            return
        }

        val started = GatewayBridge(adapter, client, iface)
        bridge = started
        _state.value = _state.value.copy(bridging = true, error = null)

        bridgeJob = viewModelScope.launch {
            try {
                started.run()
                _state.value = _state.value.copy(bridging = false)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (error: Exception) {
                _state.value = _state.value.copy(
                    bridging = false,
                    // An unknown interface fails the whole call by design, so this
                    // is the likeliest message and worth showing verbatim.
                    error = "Bridge stopped: ${error.message ?: error::class.java.simpleName}",
                )
            } finally {
                bridge = null
            }
        }
    }

    private fun stopBridge() {
        bridgeJob?.cancel(); bridgeJob = null
        bridge?.close()
        bridge = null
        _state.value = _state.value.copy(bridging = false)
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
        stopBridge()
        readJob?.cancel(); readJob = null
        publishJob?.cancel(); publishJob = null
        runCatching { adapter?.close() }
        adapter = null
        _state.value = _state.value.copy(streaming = false)
    }

    private fun record(frame: SlcanFrame) {
        // Write to the trace before touching UI state: at bus saturation the
        // file is the artefact that matters, and it must not be starved by
        // display bookkeeping.
        synchronized(recorderLock) {
            recorder?.let { active ->
                runCatching { active.write(frame) }.onFailure {
                    _state.value = _state.value.copy(
                        error = "Recording stopped: ${it.message ?: "write failed"}",
                        recording = false,
                    )
                    runCatching { active.close() }
                    recorder = null
                }
            }
        }

        // Non-blocking: a stalled network must never back up the USB reader.
        bridge?.publish(frame)

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

            val active = synchronized(recorderLock) { recorder }
            val recordedFrames = active?.frameCount ?: _state.value.recordedFrames
            val recordedBytes = active?.sizeBytes ?: _state.value.recordedBytes
            bridge?.let {
                _state.value = _state.value.copy(
                    bridgedToGateway = it.framesToGateway,
                    bridgedToBus = it.framesToBus,
                    bridgeDropped = it.framesDropped,
                )
            }

            val now = System.currentTimeMillis()
            val elapsed = now - windowStartedAt
            if (elapsed >= 1000) {
                _state.value = _state.value.copy(
                    received = received,
                    framesPerSecond = (windowCount * 1000L / elapsed).toInt(),
                    recordedFrames = recordedFrames,
                    recordedBytes = recordedBytes,
                )
                windowCount = 0
                windowStartedAt = now
            } else {
                _state.value = _state.value.copy(
                    received = received,
                    recordedFrames = recordedFrames,
                    recordedBytes = recordedBytes,
                )
            }
        }
    }

    override fun onCleared() {
        // Close the file first so a partially written trace is still valid.
        if (_state.value.recording) stopRecording()
        stop()
        super.onCleared()
    }
}
