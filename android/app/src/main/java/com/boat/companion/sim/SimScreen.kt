package com.boat.companion.sim

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.boat.proto.v1.SimulationState
import com.boat.companion.ui.BoatButton
import com.boat.companion.ui.EmptyHint
import com.boat.companion.ui.ErrorStrip
import com.boat.companion.ui.Metric
import com.boat.companion.ui.PaneHeader
import com.boat.companion.ui.theme.BoatBlue
import com.boat.companion.ui.theme.BoatBorder
import com.boat.companion.ui.theme.BoatGreen
import com.boat.companion.ui.theme.BoatMono
import com.boat.companion.ui.theme.BoatMuted
import com.boat.companion.ui.theme.BoatRed
import com.boat.companion.ui.theme.BoatYellow
import com.boat.companion.ui.theme.PaneTitle

@Composable
fun SimScreen(
    modifier: Modifier = Modifier,
    viewModel: SimViewModel = viewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    Column(modifier = modifier.fillMaxSize()) {
        if (state.busy) {
            LinearProgressIndicator(
                modifier = Modifier.fillMaxWidth(),
                color = BoatBlue,
                trackColor = BoatBorder,
            )
        }

        state.error?.let { message ->
            ErrorStrip(
                message = message,
                onDismiss = viewModel::dismissError,
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            )
        }

        CurrentSimulation(state)

        FlowRow(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            BoatButton("Start", viewModel::start, enabled = !state.busy, tint = BoatGreen)
            BoatButton("Pause", viewModel::pause, enabled = !state.busy, tint = BoatYellow)
            BoatButton("Step", viewModel::step, enabled = !state.busy)
            BoatButton("Reset", viewModel::reset, enabled = !state.busy, tint = BoatMuted)
            BoatButton("Stop", viewModel::stop, enabled = !state.busy, tint = BoatRed)
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = state.stepTicks,
                onValueChange = viewModel::setStepTicks,
                label = { Text("Ticks / step", style = MaterialTheme.typography.labelSmall) },
                singleLine = true,
                textStyle = MaterialTheme.typography.bodyMedium,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                colors = simFieldColors(),
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = state.scenarioId,
                onValueChange = viewModel::setScenarioId,
                label = { Text("Scenario id", style = MaterialTheme.typography.labelSmall) },
                singleLine = true,
                textStyle = MaterialTheme.typography.bodyMedium,
                colors = simFieldColors(),
                modifier = Modifier.weight(2f),
            )
            BoatButton("Create", viewModel::create, enabled = !state.busy)
        }

        PaneHeader(title = "Simulations", modifier = Modifier.padding(top = 12.dp)) {
            TextButton(onClick = viewModel::refresh) {
                Text("Refresh", style = MaterialTheme.typography.labelMedium, color = BoatMuted)
            }
        }

        if (state.simulations.isEmpty()) {
            EmptyHint("None. Create one from a scenario stored on the gateway.")
        } else {
            LazyColumn(modifier = Modifier.fillMaxSize()) {
                items(state.simulations, key = { it.simulationId }) { simulation ->
                    val selected = simulation.simulationId == state.selectedId
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .selectable(
                                selected = selected,
                                onClick = { viewModel.select(simulation.simulationId) },
                            )
                            .background(
                                if (selected) BoatBlue.copy(alpha = 0.08f)
                                else androidx.compose.ui.graphics.Color.Transparent
                            )
                            .padding(horizontal = 12.dp, vertical = 7.dp),
                    ) {
                        Text(
                            text = simulation.simulationId,
                            fontFamily = BoatMono,
                            style = MaterialTheme.typography.bodySmall,
                            color = if (selected) BoatBlue else MaterialTheme.colorScheme.onBackground,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(
                                text = simulation.scenarioId,
                                style = MaterialTheme.typography.labelSmall,
                                color = BoatMuted,
                            )
                            Text(
                                text = simulation.state.label(),
                                style = MaterialTheme.typography.labelSmall,
                                color = simulation.state.colour(),
                            )
                        }
                    }
                    HorizontalDivider(color = BoatBorder.copy(alpha = 0.4f))
                }
            }
        }
    }
}

/** The selected simulation's live state — the one thing worth watching while it runs. */
@Composable
private fun CurrentSimulation(state: SimUiState) {
    val simulation = state.current
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface)
            .padding(horizontal = 12.dp, vertical = 10.dp),
    ) {
        if (simulation == null) {
            Text(
                text = "No simulation selected",
                style = MaterialTheme.typography.bodyMedium,
                color = BoatMuted,
            )
            return@Column
        }
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = simulation.state.label().uppercase(),
                style = PaneTitle,
                color = simulation.state.colour(),
            )
            Metric("${simulation.tick}", "tick")
        }
        Text(
            text = simulation.simulationId,
            fontFamily = BoatMono,
            style = MaterialTheme.typography.labelSmall,
            color = BoatMuted,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
    HorizontalDivider(color = BoatBorder)
}

private fun SimulationState.colour() = when (this) {
    SimulationState.SIMULATION_STATE_RUNNING -> BoatGreen
    SimulationState.SIMULATION_STATE_PAUSED -> BoatYellow
    SimulationState.SIMULATION_STATE_ERROR -> BoatRed
    else -> BoatMuted
}

@Composable
private fun simFieldColors() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = BoatBlue,
    unfocusedBorderColor = BoatBorder,
    focusedLabelColor = BoatBlue,
    unfocusedLabelColor = BoatMuted,
)
