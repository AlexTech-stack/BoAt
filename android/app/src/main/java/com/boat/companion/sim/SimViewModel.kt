package com.boat.companion.sim

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.boat.companion.net.GatewayClient
import com.boat.companion.net.GatewayConnection
import com.boat.proto.v1.Simulation
import com.boat.proto.v1.SimulationState
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** How often the tick is polled while a simulation is RUNNING. */
private const val TICK_POLL_INTERVAL_MS = 500L

data class SimUiState(
    val simulations: List<Simulation> = emptyList(),
    val selectedId: String? = null,
    val current: Simulation? = null,
    val scenarioId: String = "",
    val stepTicks: String = "1",
    val busy: Boolean = false,
    val error: String? = null,
)

class SimViewModel : ViewModel() {

    private val _state = MutableStateFlow(SimUiState())
    val state: StateFlow<SimUiState> = _state.asStateFlow()

    private var watchJob: Job? = null

    init {
        viewModelScope.launch {
            GatewayConnection.client.collect { client ->
                watchJob?.cancel()
                watchJob = null
                if (client == null) {
                    _state.value = SimUiState()
                } else {
                    refresh()
                }
            }
        }
    }

    fun setScenarioId(value: String) {
        _state.value = _state.value.copy(scenarioId = value)
    }

    fun setStepTicks(value: String) {
        _state.value = _state.value.copy(stepTicks = value)
    }

    fun select(id: String) {
        _state.value = _state.value.copy(selectedId = id)
        watch(id)
    }

    fun refresh() = withClient { client ->
        val simulations = client.listSimulations()
        // Keep the current selection if it still exists, otherwise take the first.
        val selected = _state.value.selectedId
            ?.takeIf { id -> simulations.any { it.simulationId == id } }
            ?: simulations.firstOrNull()?.simulationId
        _state.value = _state.value.copy(simulations = simulations, selectedId = selected)
        selected?.let { watch(it) }
    }

    /** Requires a scenario already stored on the gateway; the id is server-assigned. */
    fun create() = withClient { client ->
        val scenario = _state.value.scenarioId.trim()
        if (scenario.isEmpty()) throw IllegalArgumentException("Scenario id is required")
        val created = client.createSimulation(scenario)
        // Select it outright: having just created a simulation, being told "No
        // simulation selected" and having to tap it in the list is nonsense.
        _state.value = _state.value.copy(
            selectedId = created.simulationId,
            current = created,
        )
        watch(created.simulationId)
        refreshInline(client)
    }

    fun start() = withSelected { client, id -> client.startSimulation(id) }
    fun pause() = withSelected { client, id -> client.pauseSimulation(id) }
    fun reset() = withSelected { client, id -> client.resetSimulation(id) }
    fun stop() = withSelected { client, id -> client.stopSimulation(id) }

    fun step() = withSelected { client, id ->
        val ticks = _state.value.stepTicks.toIntOrNull()
            ?: throw IllegalArgumentException("Ticks must be a number")
        if (ticks < 1) throw IllegalArgumentException("Ticks must be at least 1")
        client.stepSimulation(id, ticks)
    }

    fun dismissError() {
        _state.value = _state.value.copy(error = null)
    }

    /**
     * Live state for [id], from two sources.
     *
     * WatchSimulation only writes when the state machine transitions, so it alone
     * leaves the tick counter frozen for the whole time a simulation is running —
     * which is exactly when it is worth watching. The tick is therefore polled,
     * but only while RUNNING, so an idle or paused simulation costs nothing.
     */
    private fun watch(id: String) {
        watchJob?.cancel()
        val client = GatewayConnection.client.value ?: return
        watchJob = viewModelScope.launch {
            launch {
                try {
                    client.watchSimulation(id).collect { simulation ->
                        _state.value = _state.value.copy(current = simulation)
                    }
                } catch (cancellation: CancellationException) {
                    throw cancellation
                } catch (error: Exception) {
                    _state.value = _state.value.copy(error = describe(error))
                }
            }
            launch {
                while (isActive) {
                    delay(TICK_POLL_INTERVAL_MS)
                    if (_state.value.current?.state != SimulationState.SIMULATION_STATE_RUNNING) {
                        continue
                    }
                    // A dropped poll is not worth surfacing: the next one is 500ms
                    // away and the watch stream still reports real state changes.
                    runCatching { client.getSimulationState(id) }.getOrNull()?.let { latest ->
                        _state.value = _state.value.copy(current = latest)
                    }
                }
            }
        }
    }

    private fun withSelected(block: suspend (GatewayClient, String) -> Simulation) = withClient { client ->
        val id = _state.value.selectedId
            ?: throw IllegalStateException("No simulation selected")
        val updated = block(client, id)
        _state.value = _state.value.copy(current = updated)
        refreshInline(client)
    }

    private suspend fun refreshInline(client: GatewayClient) {
        _state.value = _state.value.copy(simulations = client.listSimulations())
    }

    private fun withClient(block: suspend (GatewayClient) -> Unit) {
        val client = GatewayConnection.client.value
        if (client == null) {
            _state.value = _state.value.copy(error = "Not connected")
            return
        }
        viewModelScope.launch {
            _state.value = _state.value.copy(busy = true, error = null)
            try {
                block(client)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (error: Exception) {
                _state.value = _state.value.copy(error = describe(error))
            } finally {
                _state.value = _state.value.copy(busy = false)
            }
        }
    }

    private fun describe(error: Exception): String =
        error.message?.takeIf { it.isNotBlank() } ?: error::class.java.simpleName
}

fun SimulationState.label(): String = when (this) {
    SimulationState.SIMULATION_STATE_IDLE -> "Idle"
    SimulationState.SIMULATION_STATE_RUNNING -> "Running"
    SimulationState.SIMULATION_STATE_PAUSED -> "Paused"
    SimulationState.SIMULATION_STATE_STOPPED -> "Stopped"
    SimulationState.SIMULATION_STATE_ERROR -> "Error"
    else -> "Unknown"
}
