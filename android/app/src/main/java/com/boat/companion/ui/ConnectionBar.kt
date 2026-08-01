package com.boat.companion.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.keyframes
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.boat.companion.net.ConnectionState
import com.boat.companion.net.GatewayConnection
import com.boat.companion.ui.theme.BoatBorder
import com.boat.companion.ui.theme.BoatGreen
import com.boat.companion.ui.theme.BoatGreenBorder
import com.boat.companion.ui.theme.BoatGreenTint
import com.boat.companion.ui.theme.BoatMuted
import com.boat.companion.ui.theme.BoatPanel
import com.boat.companion.ui.theme.BoatRed
import com.boat.companion.ui.theme.BoatRedTint
import com.boat.companion.ui.theme.BoatYellow
import com.boat.companion.ui.theme.PaneTitle
import kotlinx.coroutines.launch

/**
 * The app header: product mark, connection status, and the connection controls
 * folded away behind them.
 *
 * The address and Connect button are needed once per session and then never
 * again, so they collapse; what stays on screen is the one fact that matters
 * continuously — whether there is a gateway. Modelled on the web UIs' 46px
 * header, which carries the same badge in the same place.
 */
@Composable
fun ConnectionBar(subtitle: String, modifier: Modifier = Modifier) {
    val settings by GatewayConnection.settings.collectAsStateWithLifecycle()
    val state by GatewayConnection.state.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()

    val connected = state is ConnectionState.Connected
    val busy = state is ConnectionState.Connecting

    // Opens itself when there is nothing connected, since that is the only time
    // the controls are wanted; collapses once a connection is established.
    var expanded by remember { mutableStateOf(!connected) }

    Column(modifier = modifier.fillMaxWidth().background(BoatPanel)) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(46.dp)
                .clickable { expanded = !expanded }
                .padding(horizontal = 16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                text = "BoAt",
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(
                text = subtitle,
                style = MaterialTheme.typography.labelMedium,
                color = BoatMuted,
                modifier = Modifier.weight(1f),
            )
            StatusBadge(state)
            Text(
                text = if (expanded) "▴" else "▾",
                style = MaterialTheme.typography.labelMedium,
                color = BoatMuted,
            )
        }

        AnimatedVisibility(
            visible = expanded,
            enter = expandVertically(),
            exit = shrinkVertically(),
        ) {
            Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(
                        value = settings.host,
                        onValueChange = { host ->
                            GatewayConnection.updateSettings { it.copy(host = host) }
                        },
                        label = { Text("Gateway host", style = MaterialTheme.typography.labelSmall) },
                        singleLine = true,
                        enabled = !connected && !busy,
                        textStyle = MaterialTheme.typography.bodyMedium,
                        colors = fieldColors(),
                        modifier = Modifier.weight(2f),
                    )
                    OutlinedTextField(
                        value = settings.port,
                        onValueChange = { port ->
                            GatewayConnection.updateSettings { it.copy(port = port) }
                        },
                        label = { Text("Port", style = MaterialTheme.typography.labelSmall) },
                        singleLine = true,
                        enabled = !connected && !busy,
                        textStyle = MaterialTheme.typography.bodyMedium,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        colors = fieldColors(),
                        modifier = Modifier.weight(1f),
                    )
                }
                Row(
                    modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = state.detail(),
                        style = MaterialTheme.typography.labelSmall,
                        color = if (state is ConnectionState.Failed) BoatRed else BoatMuted,
                        modifier = Modifier.weight(1f),
                    )
                    Button(
                        enabled = !busy,
                        onClick = {
                            if (connected) {
                                GatewayConnection.disconnect()
                            } else {
                                scope.launch {
                                    GatewayConnection.connect()
                                    // Get out of the way once there is a gateway.
                                    if (GatewayConnection.state.value is ConnectionState.Connected) {
                                        expanded = false
                                    }
                                }
                            }
                        },
                        shape = RoundedCornerShape(6.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = if (connected) BoatRedTint else BoatGreenTint,
                            contentColor = if (connected) BoatRed else BoatGreen,
                        ),
                    ) {
                        Text(
                            text = if (connected) "Disconnect" else "Connect",
                            style = MaterialTheme.typography.labelLarge,
                        )
                    }
                }
            }
        }
        HorizontalDivider(color = BoatBorder)
    }
}

/** The web UIs' `.gw-badge`: a dot plus a word, tinted by state. */
@Composable
private fun StatusBadge(state: ConnectionState) {
    val badge: Triple<String, Color, Color> = when (state) {
        ConnectionState.Connected -> Triple("connected", BoatGreen, BoatGreenTint)
        ConnectionState.Connecting -> Triple("connecting", BoatYellow, Color(0xFF3A2E0B))
        is ConnectionState.Failed -> Triple("failed", BoatRed, BoatRedTint)
        ConnectionState.Disconnected -> Triple("offline", BoatMuted, Color(0xFF1C2128))
    }
    val (label, colour, tint) = badge
    val border = if (state is ConnectionState.Connected) BoatGreenBorder else colour.copy(alpha = 0.5f)

    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(12.dp))
            .background(tint)
            .border(1.dp, border, RoundedCornerShape(12.dp))
            .padding(horizontal = 10.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        StatusDot(colour, pulsing = state is ConnectionState.Connected)
        Text(text = label, style = PaneTitle, color = colour)
    }
}

/** Pulses only while connected, mirroring `.status-dot.running` in the web UIs. */
@Composable
private fun StatusDot(colour: Color, pulsing: Boolean) {
    val alpha = if (pulsing) {
        val transition = rememberInfiniteTransition(label = "status-dot")
        val value by transition.animateFloat(
            initialValue = 1f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(
                animation = keyframes {
                    durationMillis = 2000
                    1f at 0
                    0.35f at 1000
                    1f at 2000
                }
            ),
            label = "status-dot-alpha",
        )
        value
    } else 1f

    Box(
        modifier = Modifier
            .size(7.dp)
            .alpha(alpha)
            .clip(RoundedCornerShape(50))
            .background(colour)
    )
}

@Composable
private fun fieldColors() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = MaterialTheme.colorScheme.primary,
    unfocusedBorderColor = BoatBorder,
    disabledBorderColor = BoatBorder,
    focusedTextColor = MaterialTheme.colorScheme.onBackground,
    unfocusedTextColor = MaterialTheme.colorScheme.onBackground,
    disabledTextColor = BoatMuted,
    focusedLabelColor = MaterialTheme.colorScheme.primary,
    unfocusedLabelColor = BoatMuted,
    disabledLabelColor = BoatMuted,
)

private fun ConnectionState.detail(): String = when (this) {
    ConnectionState.Disconnected -> "Not connected to a gateway"
    ConnectionState.Connecting -> "Connecting…"
    ConnectionState.Connected -> "Connected"
    is ConnectionState.Failed -> message
}
