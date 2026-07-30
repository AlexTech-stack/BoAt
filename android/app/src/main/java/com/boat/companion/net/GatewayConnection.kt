package com.boat.companion.net

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withTimeout

sealed interface ConnectionState {
    data object Disconnected : ConnectionState
    data object Connecting : ConnectionState
    data object Connected : ConnectionState
    data class Failed(val message: String) : ConnectionState
}

data class ConnectionSettings(
    // The emulator's alias for the host machine; a phone needs the real address.
    val host: String = "10.0.2.2",
    val port: String = "50051",
)

/**
 * The app's single gateway connection, shared by every screen.
 *
 * A process-wide object rather than a ViewModel: the monitor and the simulation
 * controls must talk over the same channel, and duplicating channels per screen
 * would mean two TCP connections and two sets of keepalives to one gateway.
 */
object GatewayConnection {

    private val _settings = MutableStateFlow(ConnectionSettings())
    val settings: StateFlow<ConnectionSettings> = _settings.asStateFlow()

    private val _state = MutableStateFlow<ConnectionState>(ConnectionState.Disconnected)
    val state: StateFlow<ConnectionState> = _state.asStateFlow()

    /** Non-null only while connected. Screens observe this to start their work. */
    private val _client = MutableStateFlow<GatewayClient?>(null)
    val client: StateFlow<GatewayClient?> = _client.asStateFlow()

    fun updateSettings(transform: (ConnectionSettings) -> ConnectionSettings) {
        _settings.value = transform(_settings.value)
    }

    /**
     * Opens a channel and proves it works before reporting success.
     *
     * A gRPC channel connects lazily, so construction alone says nothing about
     * reachability — without a probe the UI would claim "connected" against an
     * address that is simply wrong. ListSimulations is cheap and always present.
     */
    suspend fun connect() {
        disconnect()

        val current = _settings.value
        val port = current.port.toIntOrNull()
        if (port == null || port !in 1..65535) {
            _state.value = ConnectionState.Failed("Port must be 1–65535")
            return
        }
        if (current.host.isBlank()) {
            _state.value = ConnectionState.Failed("Host is required")
            return
        }

        _state.value = ConnectionState.Connecting
        val candidate = GatewayClient(Endpoint(host = current.host.trim(), port = port))
        try {
            withTimeout(5_000) { candidate.listSimulations() }
            _client.value = candidate
            _state.value = ConnectionState.Connected
        } catch (cancellation: CancellationException) {
            candidate.close()
            throw cancellation
        } catch (error: Exception) {
            candidate.close()
            _state.value = ConnectionState.Failed(describe(error))
        }
    }

    fun disconnect() {
        _client.value?.close()
        _client.value = null
        if (_state.value !is ConnectionState.Failed) {
            _state.value = ConnectionState.Disconnected
        }
    }

    private fun describe(error: Exception): String =
        error.message?.takeIf { it.isNotBlank() } ?: error::class.java.simpleName
}
