package com.boat.companion.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.boat.companion.net.ConnectionState
import com.boat.companion.net.GatewayConnection
import kotlinx.coroutines.launch

@Composable
fun ConnectionCard(modifier: Modifier = Modifier) {
    val settings by GatewayConnection.settings.collectAsStateWithLifecycle()
    val state by GatewayConnection.state.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()

    val connected = state is ConnectionState.Connected
    val busy = state is ConnectionState.Connecting

    Card(modifier = modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = settings.host,
                    onValueChange = { host ->
                        GatewayConnection.updateSettings { it.copy(host = host) }
                    },
                    label = { Text("Gateway host") },
                    singleLine = true,
                    enabled = !connected && !busy,
                    modifier = Modifier.weight(2f),
                )
                OutlinedTextField(
                    value = settings.port,
                    onValueChange = { port ->
                        GatewayConnection.updateSettings { it.copy(port = port) }
                    },
                    label = { Text("Port") },
                    singleLine = true,
                    enabled = !connected && !busy,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.weight(1f),
                )
            }
            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = state.label(),
                    style = MaterialTheme.typography.bodySmall,
                    color = when (state) {
                        is ConnectionState.Failed -> MaterialTheme.colorScheme.error
                        else -> MaterialTheme.colorScheme.onSurfaceVariant
                    },
                    modifier = Modifier.weight(1f),
                )
                Button(
                    enabled = !busy,
                    onClick = {
                        if (connected) {
                            GatewayConnection.disconnect()
                        } else {
                            scope.launch { GatewayConnection.connect() }
                        }
                    },
                ) {
                    Text(if (connected) "Disconnect" else "Connect")
                }
            }
        }
    }
}

private fun ConnectionState.label(): String = when (this) {
    ConnectionState.Disconnected -> "Disconnected"
    ConnectionState.Connecting -> "Connecting…"
    ConnectionState.Connected -> "Connected"
    is ConnectionState.Failed -> "Failed: $message"
}
