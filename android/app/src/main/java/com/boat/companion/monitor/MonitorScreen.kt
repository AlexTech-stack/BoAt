package com.boat.companion.monitor

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

@Composable
fun MonitorScreen(
    modifier: Modifier = Modifier,
    viewModel: MonitorViewModel = viewModel(),
) {
    val settings by viewModel.settings.collectAsStateWithLifecycle()
    val connection by viewModel.connection.collectAsStateWithLifecycle()
    val stats by viewModel.stats.collectAsStateWithLifecycle()
    val frames by viewModel.frames.collectAsStateWithLifecycle()

    val streaming = connection is ConnectionState.Streaming ||
        connection is ConnectionState.Connecting

    Column(modifier = modifier.fillMaxSize().padding(12.dp)) {
        ConnectionCard(
            settings = settings,
            connection = connection,
            streaming = streaming,
            onSettingsChange = viewModel::updateSettings,
            onToggle = viewModel::toggle,
        )

        Row(
            modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "${stats.received} frames · ${stats.framesPerSecond}/s",
                style = MaterialTheme.typography.labelLarge,
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Hide echo", style = MaterialTheme.typography.labelMedium)
                Switch(
                    checked = settings.hideSelfSent,
                    onCheckedChange = { hide ->
                        viewModel.updateSettings { it.copy(hideSelfSent = hide) }
                    },
                )
                TextButton(onClick = viewModel::clear) { Text("Clear") }
            }
        }

        HorizontalDivider()

        val visible = if (settings.hideSelfSent) frames.filterNot { it.selfSent } else frames
        if (visible.isEmpty()) {
            Text(
                text = if (streaming) "Waiting for frames…" else "Not connected",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = 24.dp),
            )
        } else {
            LazyColumn(modifier = Modifier.fillMaxSize()) {
                items(visible, key = { it.seq }) { FrameEntry(it) }
            }
        }
    }
}

@Composable
private fun ConnectionCard(
    settings: MonitorSettings,
    connection: ConnectionState,
    streaming: Boolean,
    onSettingsChange: ((MonitorSettings) -> MonitorSettings) -> Unit,
    onToggle: () -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = settings.host,
                    onValueChange = { host -> onSettingsChange { it.copy(host = host) } },
                    label = { Text("Gateway host") },
                    singleLine = true,
                    enabled = !streaming,
                    modifier = Modifier.weight(2f),
                )
                OutlinedTextField(
                    value = settings.port,
                    onValueChange = { port -> onSettingsChange { it.copy(port = port) } },
                    label = { Text("Port") },
                    singleLine = true,
                    enabled = !streaming,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.weight(1f),
                )
            }
            OutlinedTextField(
                value = settings.ifaceFilter,
                onValueChange = { f -> onSettingsChange { it.copy(ifaceFilter = f) } },
                label = { Text("Interface filter (blank = all)") },
                singleLine = true,
                enabled = !streaming,
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            )
            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = connection.label(),
                    style = MaterialTheme.typography.bodySmall,
                    color = when (connection) {
                        is ConnectionState.Failed -> MaterialTheme.colorScheme.error
                        else -> MaterialTheme.colorScheme.onSurfaceVariant
                    },
                    modifier = Modifier.weight(1f),
                )
                Button(onClick = onToggle) {
                    Text(if (streaming) "Disconnect" else "Connect")
                }
            }
        }
    }
}

@Composable
private fun FrameEntry(row: FrameRow) {
    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                text = row.identifier,
                fontFamily = FontFamily.Monospace,
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                text = "${row.busType} ${row.iface}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = "[${row.length}]",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (row.selfSent) {
                Text(
                    text = "echo",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
        }
        Text(
            text = row.data,
            fontFamily = FontFamily.Monospace,
            style = MaterialTheme.typography.bodySmall,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

private fun ConnectionState.label(): String = when (this) {
    ConnectionState.Disconnected -> "Disconnected"
    ConnectionState.Connecting -> "Connecting…"
    ConnectionState.Streaming -> "Streaming"
    is ConnectionState.Failed -> "Failed: $message"
}
