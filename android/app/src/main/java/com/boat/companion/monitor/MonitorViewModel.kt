package com.boat.companion.monitor

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.boat.companion.net.GatewayClient
import com.boat.companion.net.GatewayConnection
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

/** Frames retained for display. A busy bus would otherwise grow without bound. */
private const val HISTORY_LIMIT = 500

/** UI refresh period. Frames arrive far faster than a list can usefully repaint. */
private const val PUBLISH_INTERVAL_MS = 100L

sealed interface StreamState {
    data object Idle : StreamState
    data object Streaming : StreamState
    data class Failed(val message: String) : StreamState
}

data class MonitorFilters(
    val ifaceFilter: String = "",
    val hideSelfSent: Boolean = true,
)

data class MonitorStats(
    val received: Long = 0,
    val framesPerSecond: Int = 0,
)

class MonitorViewModel : ViewModel() {

    private val _filters = MutableStateFlow(MonitorFilters())
    val filters: StateFlow<MonitorFilters> = _filters.asStateFlow()

    private val _stream = MutableStateFlow<StreamState>(StreamState.Idle)
    val stream: StateFlow<StreamState> = _stream.asStateFlow()

    private val _frames = MutableStateFlow<List<FrameRow>>(emptyList())
    val frames: StateFlow<List<FrameRow>> = _frames.asStateFlow()

    private val _stats = MutableStateFlow(MonitorStats())
    val stats: StateFlow<MonitorStats> = _stats.asStateFlow()

    /** Guarded by [historyLock]; published to [_frames] on a timer, not per frame. */
    private val history = ArrayDeque<FrameRow>(HISTORY_LIMIT)
    private val historyLock = Any()

    private var streamJob: Job? = null
    private var publishJob: Job? = null

    private var sequence = 0L
    private var received = 0L
    private var windowCount = 0
    private var windowStartedAt = 0L

    init {
        // The connection is owned elsewhere; follow it rather than duplicating it.
        viewModelScope.launch {
            GatewayConnection.client.collect { client ->
                stopStreaming()
                if (client != null) startStreaming(client)
            }
        }
    }

    fun updateFilters(transform: (MonitorFilters) -> MonitorFilters) {
        val previous = _filters.value
        val updated = transform(previous)
        _filters.value = updated
        // The interface filter is applied server-side, so changing it means
        // renegotiating the subscription.
        if (updated.ifaceFilter != previous.ifaceFilter) {
            GatewayConnection.client.value?.let { client ->
                stopStreaming()
                startStreaming(client)
            }
        }
    }

    fun clear() {
        synchronized(historyLock) { history.clear() }
        _frames.value = emptyList()
        received = 0
        _stats.value = MonitorStats()
    }

    private fun startStreaming(client: GatewayClient) {
        windowStartedAt = System.currentTimeMillis()
        windowCount = 0

        publishJob = viewModelScope.launch { publishLoop() }
        streamJob = viewModelScope.launch {
            try {
                // Frames are decoded off the main thread; the flow is buffered so a
                // slow consumer cannot exert backpressure on the gateway's stream.
                withContext(Dispatchers.Default) {
                    client.subscribeFrames(ifaceFilter = _filters.value.ifaceFilter.trim())
                        .buffer()
                        .collect { frame ->
                            if (_stream.value !is StreamState.Streaming) {
                                _stream.value = StreamState.Streaming
                            }
                            record(frame.toRow(sequence++))
                        }
                }
                _stream.value = StreamState.Idle
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (error: Exception) {
                _stream.value =
                    StreamState.Failed(error.message ?: error::class.java.simpleName)
            }
        }
    }

    private fun stopStreaming() {
        streamJob?.cancel()
        streamJob = null
        publishJob?.cancel()
        publishJob = null
        if (_stream.value is StreamState.Streaming) _stream.value = StreamState.Idle
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
        stopStreaming()
        super.onCleared()
    }
}
