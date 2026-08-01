package com.boat.companion.monitor

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.boat.companion.ui.EmptyHint
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

@Composable
fun MonitorScreen(
    modifier: Modifier = Modifier,
    viewModel: MonitorViewModel = viewModel(),
) {
    val filters by viewModel.filters.collectAsStateWithLifecycle()
    val stream by viewModel.stream.collectAsStateWithLifecycle()
    val stats by viewModel.stats.collectAsStateWithLifecycle()
    val frames by viewModel.frames.collectAsStateWithLifecycle()

    Column(modifier = modifier.fillMaxSize()) {
        // A filter row, not a form: one compact field and one switch, sized so
        // the frame list keeps the screen.
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = filters.ifaceFilter,
                onValueChange = { f -> viewModel.updateFilters { it.copy(ifaceFilter = f) } },
                placeholder = {
                    Text(
                        "interface — blank for all",
                        style = MaterialTheme.typography.bodySmall,
                        color = BoatMuted,
                    )
                },
                singleLine = true,
                textStyle = MaterialTheme.typography.bodyMedium,
                shape = RoundedCornerShape(6.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = BoatBlue,
                    unfocusedBorderColor = BoatBorder,
                ),
                modifier = Modifier.weight(1f).heightIn(min = 46.dp),
            )
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text("echo", style = MaterialTheme.typography.labelMedium, color = BoatMuted)
                Switch(
                    checked = !filters.hideSelfSent,
                    onCheckedChange = { show ->
                        viewModel.updateFilters { it.copy(hideSelfSent = !show) }
                    },
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = BoatBlue,
                        checkedTrackColor = BoatBlue.copy(alpha = 0.3f),
                        uncheckedThumbColor = BoatMuted,
                        uncheckedTrackColor = BoatBorder,
                        uncheckedBorderColor = BoatBorder,
                    ),
                )
            }
        }

        (stream as? StreamState.Failed)?.let { failure ->
            Text(
                text = failure.message,
                style = MaterialTheme.typography.labelSmall,
                color = BoatRed,
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
            )
        }

        val visible = if (filters.hideSelfSent) frames.filterNot { it.selfSent } else frames

        PaneHeader(title = "Frames") {
            Metric("${stats.received}", "total")
            Metric("${stats.framesPerSecond}", "f/s")
            TextButton(onClick = viewModel::clear) {
                Text("Clear", style = MaterialTheme.typography.labelMedium, color = BoatMuted)
            }
        }

        if (visible.isEmpty()) {
            EmptyHint(
                when (stream) {
                    StreamState.Streaming -> "Waiting for bus traffic…"
                    else -> "Connect to a gateway to see traffic"
                }
            )
        } else {
            LazyColumn(modifier = Modifier.fillMaxSize()) {
                items(visible, key = { it.seq }) { FrameRowItem(it) }
            }
        }
    }
}

/**
 * One frame per row: identifier and payload in monospace so columns line up
 * while scrolling, everything else muted so the data carries the eye.
 */
@Composable
private fun FrameRowItem(row: FrameRow) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(if (row.selfSent) BoatBlue.copy(alpha = 0.04f) else androidx.compose.ui.graphics.Color.Transparent)
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
                text = row.iface,
                style = MaterialTheme.typography.labelSmall,
                color = BoatGreen,
            )
            Text(
                text = "${row.length}B",
                style = MaterialTheme.typography.labelSmall,
                color = BoatMuted,
                modifier = Modifier.weight(1f),
            )
            if (row.selfSent) {
                Text("echo", style = MaterialTheme.typography.labelSmall, color = BoatOrange)
            }
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
