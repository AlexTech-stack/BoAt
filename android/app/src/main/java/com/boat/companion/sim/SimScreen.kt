package com.boat.companion.sim

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
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

@Composable
fun SimScreen(
    modifier: Modifier = Modifier,
    viewModel: SimViewModel = viewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    Column(modifier = modifier.fillMaxSize().padding(12.dp)) {
        if (state.busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
        }

        state.error?.let { message ->
            Card(modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp)) {
                Row(
                    modifier = Modifier.padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = message,
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(onClick = viewModel::dismissError) { Text("Dismiss") }
                }
            }
        }

        CurrentSimulation(state)

        FlowRow(
            modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Button(onClick = viewModel::start, enabled = !state.busy) { Text("Start") }
            Button(onClick = viewModel::pause, enabled = !state.busy) { Text("Pause") }
            Button(onClick = viewModel::step, enabled = !state.busy) { Text("Step") }
            OutlinedButton(onClick = viewModel::reset, enabled = !state.busy) { Text("Reset") }
            OutlinedButton(onClick = viewModel::stop, enabled = !state.busy) { Text("Stop") }
        }

        OutlinedTextField(
            value = state.stepTicks,
            onValueChange = viewModel::setStepTicks,
            label = { Text("Ticks per step") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier.fillMaxWidth(),
        )

        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = state.scenarioId,
                onValueChange = viewModel::setScenarioId,
                label = { Text("Scenario id") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            Button(onClick = viewModel::create, enabled = !state.busy) { Text("Create") }
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Simulations", style = MaterialTheme.typography.titleSmall)
            TextButton(onClick = viewModel::refresh) { Text("Refresh") }
        }
        HorizontalDivider()

        if (state.simulations.isEmpty()) {
            Text(
                text = "None. Create one from a scenario stored on the gateway.",
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(top = 12.dp),
            )
        } else {
            LazyColumn(modifier = Modifier.fillMaxSize()) {
                items(state.simulations, key = { it.simulationId }) { simulation ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .selectable(
                                selected = simulation.simulationId == state.selectedId,
                                onClick = { viewModel.select(simulation.simulationId) },
                            )
                            .padding(vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(
                            selected = simulation.simulationId == state.selectedId,
                            onClick = { viewModel.select(simulation.simulationId) },
                        )
                        Column {
                            Text(
                                text = simulation.simulationId,
                                fontFamily = FontFamily.Monospace,
                                style = MaterialTheme.typography.bodySmall,
                            )
                            Text(
                                text = "${simulation.scenarioId} · ${simulation.state.label()}",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun CurrentSimulation(state: SimUiState) {
    val simulation = state.current
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            if (simulation == null) {
                Text("No simulation selected", style = MaterialTheme.typography.bodyMedium)
            } else {
                Text(
                    text = simulation.state.label(),
                    style = MaterialTheme.typography.titleMedium,
                )
                Text(
                    text = "tick ${simulation.tick}",
                    fontFamily = FontFamily.Monospace,
                    style = MaterialTheme.typography.bodyMedium,
                )
                Text(
                    text = simulation.simulationId,
                    fontFamily = FontFamily.Monospace,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
