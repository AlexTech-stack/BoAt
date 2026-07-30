package com.boat.companion.monitor

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

@Composable
fun MonitorScreen(
    modifier: Modifier = Modifier,
    viewModel: MonitorViewModel = viewModel(),
) {
    val filters by viewModel.filters.collectAsStateWithLifecycle()
    val stream by viewModel.stream.collectAsStateWithLifecycle()
    val stats by viewModel.stats.collectAsStateWithLifecycle()
    val frames by viewModel.frames.collectAsStateWithLifecycle()

    Column(modifier = modifier.fillMaxSize().padding(12.dp)) {
        OutlinedTextField(
            value = filters.ifaceFilter,
            onValueChange = { f -> viewModel.updateFilters { it.copy(ifaceFilter = f) } },
            label = { Text("Interface filter (blank = all)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
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
                    checked = filters.hideSelfSent,
                    onCheckedChange = { hide ->
                        viewModel.updateFilters { it.copy(hideSelfSent = hide) }
                    },
                )
                TextButton(onClick = viewModel::clear) { Text("Clear") }
            }
        }

        (stream as? StreamState.Failed)?.let { failure ->
            Text(
                text = "Stream failed: ${failure.message}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(bottom = 8.dp),
            )
        }

        HorizontalDivider()

        val visible = if (filters.hideSelfSent) frames.filterNot { it.selfSent } else frames
        if (visible.isEmpty()) {
            Text(
                text = when (stream) {
                    StreamState.Streaming -> "Waiting for frames…"
                    else -> "Connect to a gateway to see traffic"
                },
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
