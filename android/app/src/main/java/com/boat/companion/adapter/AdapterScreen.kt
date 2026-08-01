package com.boat.companion.adapter

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.background
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
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
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
import com.boat.companion.ui.theme.BoatOrange
import com.boat.companion.ui.theme.BoatPurple
import com.boat.companion.ui.theme.BoatRed
import com.boat.companion.ui.theme.PaneTitle
import java.io.File

@Composable
fun AdapterScreen(
    modifier: Modifier = Modifier,
    viewModel: AdapterViewModel = viewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val frames by viewModel.frames.collectAsStateWithLifecycle()
    val context = LocalContext.current

    Column(modifier = modifier.fillMaxSize()) {
        DeviceHeader(state, viewModel)

        state.error?.let { message ->
            ErrorStrip(
                message = message,
                onDismiss = viewModel::dismissError,
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            )
        }

        BridgeRow(state, viewModel)

        if (state.traces.isNotEmpty()) {
            PaneHeader(title = "Traces")
            TraceFiles(
                traces = state.traces,
                onShare = { share(context, it) },
                onDelete = viewModel::delete,
            )
        }

        PaneHeader(title = "Frames") {
            Metric("${state.received}", "total")
            Metric("${state.framesPerSecond}", "f/s")
            TextButton(onClick = viewModel::clear) {
                Text("Clear", style = MaterialTheme.typography.labelMedium, color = BoatMuted)
            }
        }

        if (frames.isEmpty()) {
            EmptyHint(
                if (state.streaming) "Waiting for bus traffic…"
                else "Start the adapter to read frames"
            )
        } else {
            LazyColumn(modifier = Modifier.fillMaxSize()) {
                items(frames, key = { it.seq }) { row ->
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp, vertical = 5.dp),
                    ) {
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                text = row.identifier,
                                fontFamily = BoatMono,
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onBackground,
                            )
                            Text(
                                text = row.busType,
                                style = MaterialTheme.typography.labelSmall,
                                color = if (row.busType == "CANFD") BoatPurple else BoatMuted,
                            )
                            Text(
                                text = "${row.length}B",
                                style = MaterialTheme.typography.labelSmall,
                                color = BoatMuted,
                            )
                        }
                        Text(
                            text = row.data,
                            fontFamily = BoatMono,
                            style = MaterialTheme.typography.bodySmall,
                            color = BoatMuted,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    HorizontalDivider(color = BoatBorder.copy(alpha = 0.4f))
                }
            }
        }
    }
}

/** Device identity, bus configuration, and the start/record controls. */
@Composable
private fun DeviceHeader(state: AdapterUiState, viewModel: AdapterViewModel) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface)
            .padding(horizontal = 12.dp, vertical = 10.dp),
    ) {
        Text(
            text = state.deviceName ?: "No adapter attached",
            style = MaterialTheme.typography.bodyMedium,
            color = if (state.attached) MaterialTheme.colorScheme.onBackground else BoatMuted,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        Row(
            modifier = Modifier.padding(top = 2.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "${state.bitrate.bitsPerSecond / 1000}k",
                fontFamily = BoatMono,
                style = MaterialTheme.typography.labelMedium,
                color = BoatGreen,
            )
            // Tapping cycles classic -> 1M -> 2M -> 5M; it must match the bus.
            TextButton(onClick = viewModel::cycleDataBitrate, enabled = !state.streaming) {
                Text(
                    text = state.dataBitrate
                        ?.let { "FD ${it.bitsPerSecond / 1_000_000}M" } ?: "classic",
                    style = MaterialTheme.typography.labelMedium,
                    color = if (state.dataBitrate != null) BoatPurple else BoatMuted,
                )
            }
            Text("listen-only", style = MaterialTheme.typography.labelSmall, color = BoatMuted)
            Switch(
                checked = state.silent,
                onCheckedChange = viewModel::setSilent,
                enabled = !state.streaming,
                colors = SwitchDefaults.colors(
                    checkedThumbColor = BoatBlue,
                    checkedTrackColor = BoatBlue.copy(alpha = 0.3f),
                    uncheckedThumbColor = BoatMuted,
                    uncheckedTrackColor = BoatBorder,
                    uncheckedBorderColor = BoatBorder,
                ),
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            BoatButton(
                text = if (state.streaming) "Stop" else "Start",
                onClick = viewModel::toggle,
                tint = if (state.streaming) BoatRed else BoatGreen,
            )
            BoatButton(
                text = if (state.recording) "Stop rec" else "Record",
                onClick = viewModel::toggleRecording,
                enabled = state.streaming,
                tint = if (state.recording) BoatRed else BoatBlue,
            )
            TextButton(onClick = viewModel::refreshAttachment, enabled = !state.streaming) {
                Text("Rescan", style = MaterialTheme.typography.labelMedium, color = BoatMuted)
            }
        }
        if (state.recording || state.recordingName != null) {
            Text(
                text = (if (state.recording) "● " else "saved ") + (state.recordingName ?: "") +
                    "  ${state.recordedFrames} frames · " +
                    "%.1f KB".format(state.recordedBytes / 1024.0),
                style = MaterialTheme.typography.labelSmall,
                color = if (state.recording) BoatRed else BoatMuted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(top = 6.dp),
            )
        }
    }
    HorizontalDivider(color = BoatBorder)
}

@Composable
private fun BridgeRow(state: AdapterUiState, viewModel: AdapterViewModel) {
    Column(modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = state.bridgeIface,
                onValueChange = viewModel::setBridgeIface,
                label = { Text("Bridge to iface", style = MaterialTheme.typography.labelSmall) },
                singleLine = true,
                enabled = !state.bridging,
                textStyle = MaterialTheme.typography.bodyMedium,
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = BoatBlue,
                    unfocusedBorderColor = BoatBorder,
                    focusedLabelColor = BoatBlue,
                    unfocusedLabelColor = BoatMuted,
                ),
                modifier = Modifier.weight(1f),
            )
            BoatButton(
                text = if (state.bridging) "Unbridge" else "Bridge",
                onClick = viewModel::toggleBridge,
                enabled = state.streaming,
                tint = if (state.bridging) BoatOrange else BoatBlue,
            )
        }
        if (state.bridging || state.bridgedToGateway > 0) {
            Row(
                modifier = Modifier.padding(top = 6.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Metric("↑ ${state.bridgedToGateway}", "to gateway")
                Metric("↓ ${state.bridgedToBus}", "to bus")
                if (state.bridgeDropped > 0) {
                    Text(
                        text = "${state.bridgeDropped} dropped",
                        style = MaterialTheme.typography.labelSmall,
                        color = BoatRed,
                    )
                }
            }
        }
    }
}

@Composable
private fun TraceFiles(traces: List<File>, onShare: (File) -> Unit, onDelete: (File) -> Unit) {
    Column(modifier = Modifier.fillMaxWidth()) {
        // Newest few; the rest are reachable over adb or the share sheet.
        traces.take(3).forEach { file ->
            Row(
                modifier = Modifier.fillMaxWidth().padding(start = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f).padding(vertical = 6.dp)) {
                    Text(
                        text = file.name,
                        fontFamily = BoatMono,
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onBackground,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        text = "%.1f KB".format(file.length() / 1024.0),
                        style = PaneTitle,
                        color = BoatMuted,
                    )
                }
                TextButton(onClick = { onShare(file) }) {
                    Text("Share", style = MaterialTheme.typography.labelMedium, color = BoatBlue)
                }
                TextButton(onClick = { onDelete(file) }) {
                    Text("Delete", style = MaterialTheme.typography.labelMedium, color = BoatMuted)
                }
            }
            HorizontalDivider(color = BoatBorder.copy(alpha = 0.4f))
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
