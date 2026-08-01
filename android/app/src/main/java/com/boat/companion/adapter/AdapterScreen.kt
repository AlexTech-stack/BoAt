package com.boat.companion.adapter

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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import android.content.Context
import android.content.Intent
import java.io.File

@Composable
fun AdapterScreen(
    modifier: Modifier = Modifier,
    viewModel: AdapterViewModel = viewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val frames by viewModel.frames.collectAsStateWithLifecycle()
    val context = LocalContext.current

    Column(modifier = modifier.fillMaxSize().padding(12.dp)) {
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(12.dp)) {
                Text(
                    text = state.deviceName ?: "No adapter attached",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Text(
                    text = "${state.bitrate.bitsPerSecond / 1000} kbit" +
                        if (state.silent) " · listen-only" else " · normal",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(
                    modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("Listen-only", style = MaterialTheme.typography.labelMedium)
                        Switch(
                            checked = state.silent,
                            onCheckedChange = viewModel::setSilent,
                            enabled = !state.streaming,
                        )
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        TextButton(
                            onClick = viewModel::refreshAttachment,
                            enabled = !state.streaming,
                        ) { Text("Rescan") }
                        Button(onClick = viewModel::toggle) {
                            Text(if (state.streaming) "Stop" else "Start")
                        }
                    }
                }
            }
        }

        state.error?.let { message ->
            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
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

        Row(
            modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "${state.received} frames · ${state.framesPerSecond}/s",
                style = MaterialTheme.typography.labelLarge,
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Button(onClick = viewModel::toggleRecording, enabled = state.streaming) {
                    Text(if (state.recording) "Stop rec" else "Record")
                }
                TextButton(onClick = viewModel::clear) { Text("Clear") }
            }
        }

        if (state.recording || state.recordingName != null) {
            Text(
                text = buildString {
                    append(if (state.recording) "● recording " else "saved ")
                    append(state.recordingName ?: "")
                    append("  ${state.recordedFrames} frames · ")
                    append("${"%.1f".format(state.recordedBytes / 1024.0)} KB")
                },
                style = MaterialTheme.typography.labelSmall,
                color = if (state.recording) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(bottom = 4.dp),
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = state.bridgeIface,
                onValueChange = viewModel::setBridgeIface,
                label = { Text("Bridge to gateway iface") },
                singleLine = true,
                enabled = !state.bridging,
                modifier = Modifier.weight(1f),
            )
            Button(onClick = viewModel::toggleBridge, enabled = state.streaming) {
                Text(if (state.bridging) "Unbridge" else "Bridge")
            }
        }

        if (state.bridging || state.bridgedToGateway > 0) {
            Text(
                text = "↑ ${state.bridgedToGateway} to gateway · " +
                    "↓ ${state.bridgedToBus} to bus" +
                    if (state.bridgeDropped > 0) " · ${state.bridgeDropped} dropped" else "",
                style = MaterialTheme.typography.labelSmall,
                color = if (state.bridgeDropped > 0) MaterialTheme.colorScheme.error
                else MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(bottom = 4.dp),
            )
        }

        if (state.traces.isNotEmpty()) {
            TraceFiles(state.traces, onShare = { share(context, it) }, onDelete = viewModel::delete)
        }

        HorizontalDivider()

        if (frames.isEmpty()) {
            Text(
                text = if (state.streaming) "Waiting for bus traffic…"
                else "Start to read frames from the adapter",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = 24.dp),
            )
        } else {
            LazyColumn(modifier = Modifier.fillMaxSize()) {
                items(frames, key = { it.seq }) { row ->
                    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text(
                                text = row.identifier,
                                fontFamily = FontFamily.Monospace,
                                style = MaterialTheme.typography.bodyMedium,
                            )
                            Text(
                                text = "${row.busType} [${row.length}]",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
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
            }
        }
    }
}

@Composable
private fun TraceFiles(
    traces: List<File>,
    onShare: (File) -> Unit,
    onDelete: (File) -> Unit,
) {
    Column(modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp)) {
        // Only the newest few; the rest are reachable over adb or the share sheet.
        traces.take(3).forEach { file ->
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = file.name,
                        style = MaterialTheme.typography.labelMedium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        text = "%.1f KB".format(file.length() / 1024.0),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                TextButton(onClick = { onShare(file) }) { Text("Share") }
                TextButton(onClick = { onDelete(file) }) { Text("Delete") }
            }
        }
    }
}

/** Hands the file out through the share sheet via FileProvider. */
private fun share(context: Context, file: File) {
    val uri = FileProvider.getUriForFile(context, "${context.packageName}.traces", file)
    val intent = Intent(Intent.ACTION_SEND).apply {
        type = "application/octet-stream"
        putExtra(Intent.EXTRA_STREAM, uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    context.startActivity(Intent.createChooser(intent, "Share trace"))
}
