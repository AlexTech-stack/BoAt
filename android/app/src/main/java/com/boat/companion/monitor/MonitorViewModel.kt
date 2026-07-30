package com.boat.companion.monitor

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.boat.companion.net.Endpoint
import com.boat.companion.net.GatewayClient
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.buffer
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException

/** Frames retained for display. A busy bus would otherwise grow without bound. */
private const val HISTORY_LIMIT = 500

/** UI refresh period. Frames arrive far faster than a list can usefully repaint. */
private const val PUBLISH_INTERVAL_MS = 100L

sealed interface ConnectionState {
    data object Disconnected : ConnectionState
    data object Connecting : ConnectionState
    data object Streaming : ConnectionState
    data class Failed(val message: String) : ConnectionState
}

data class MonitorSettings(
    val host: String = "10.0.2.2",
    val port: String = "50051",
    val ifaceFilter: String = "",
    val hideSelfSent: Boolean = true,
)

data class MonitorStats(
    val received: Long = 0,
    val framesPerSecond: Int = 0,
)

class MonitorViewModel : ViewModel() {

    private val _settings = MutableStateFlow(MonitorSettings())
    val settings: StateFlow<MonitorSettings> = _settings.asStateFlow()

    private val _connection = MutableStateFlow<ConnectionState>(ConnectionState.Disconnected)
    val connection: StateFlow<ConnectionState> = _connection.asStateFlow()

    private val _frames = MutableStateFlow<List<FrameRow>>(emptyList())
    val frames: StateFlow<List<FrameRow>> = _frames.asStateFlow()

    private val _stats = MutableStateFlow(MonitorStats())
    val stats: StateFlow<MonitorStats> = _stats.asStateFlow()

    /** Guarded by [historyLock]; published to [_frames] on a timer, not per frame. */
    private val history = ArrayDeque<FrameRow>(HISTORY_LIMIT)
    private val historyLock = Any()

    private var client: GatewayClient? = null
    private var streamJob: Job? = null
    private var publishJob: Job? = null

    private var sequence = 0L
    private var received = 0L
    private var windowCount = 0
    private var windowStartedAt = 0L

    fun updateSettings(transform: (MonitorSettings) -> MonitorSettings) {
        _settings.value = transform(_settings.value)
    }

    fun toggle() {
        if (streamJob?.isActive == true) disconnect() else connect()
    }

    fun clear() {
        synchronized(historyLock) { history.clear() }
        _frames.value = emptyList()
        received = 0
        _stats.value = MonitorStats()
    }

    private fun connect() {
        val current = _settings.value
        val port = current.port.toIntOrNull()
        if (port == null || port !in 1..65535) {
            _connection.value = ConnectionState.Failed("Port must be 1–65535")
            return
        }
        if (current.host.isBlank()) {
            _connection.value = ConnectionState.Failed("Host is required")
            return
        }

        disconnect()
        _connection.value = ConnectionState.Connecting
        windowStartedAt = System.currentTimeMillis()
        windowCount = 0

        val gateway = GatewayClient(Endpoint(host = current.host.trim(), port = port))
        client = gateway

        publishJob = viewModelScope.launch { publishLoop() }
        streamJob = viewModelScope.launch {
            try {
                // Frames are decoded off the main thread; the flow is buffered so a
                // slow consumer cannot exert backpressure on the gateway's stream.
                withContext(Dispatchers.Default) {
                    gateway.subscribeFrames(ifaceFilter = current.ifaceFilter.trim())
                        .buffer()
                        .collect { frame ->
                            if (_connection.value !is ConnectionState.Streaming) {
                                _connection.value = ConnectionState.Streaming
                            }
                            record(frame.toRow(sequence++))
                        }
                }
                _connection.value = ConnectionState.Disconnected
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (error: Exception) {
                _connection.value =
                    ConnectionState.Failed(error.message ?: error::class.java.simpleName)
            }
        }
    }

    private fun disconnect() {
        streamJob?.cancel()
        streamJob = null
        publishJob?.cancel()
        publishJob = null
        client?.close()
        client = null
        if (_connection.value is ConnectionState.Streaming ||
            _connection.value is ConnectionState.Connecting
        ) {
            _connection.value = ConnectionState.Disconnected
        }
    }

    private fun record(row: FrameRow) {
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
                val rate = (windowCount * 1000L / elapsed).toInt()
                _stats.value = MonitorStats(received = received, framesPerSecond = rate)
                windowCount = 0
                windowStartedAt = now
            } else {
                _stats.value = _stats.value.copy(received = received)
            }
        }
    }

    override fun onCleared() {
        disconnect()
        super.onCleared()
    }
}
