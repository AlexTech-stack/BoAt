package com.boat.companion.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.boat.companion.ui.theme.BoatBorder
import com.boat.companion.ui.theme.BoatElevated
import com.boat.companion.ui.theme.BoatMono
import com.boat.companion.ui.theme.BoatMuted
import com.boat.companion.ui.theme.BoatPanel
import com.boat.companion.ui.theme.BoatRed
import com.boat.companion.ui.theme.BoatRedTint
import com.boat.companion.ui.theme.PaneTitle

/**
 * Shared chrome, mirroring the web UIs so the phone reads as the same tool.
 *
 * Each screen previously drew its own headers, buttons and error rows, which is
 * how three tabs ended up looking like three apps.
 */

/** The web UIs' `.pane-header`: 36px, uppercase muted title, optional trailing content. */
@Composable
fun PaneHeader(
    title: String,
    modifier: Modifier = Modifier,
    trailing: @Composable RowScope.() -> Unit = {},
) {
    Column(modifier = modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(36.dp)
                .background(BoatPanel)
                .padding(horizontal = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = title.uppercase(),
                style = PaneTitle,
                color = BoatMuted,
                modifier = Modifier.weight(1f),
            )
            trailing()
        }
        HorizontalDivider(color = BoatBorder)
    }
}

/** A compact metric, e.g. "1 482 frames · 312/s". Monospace so digits do not jitter. */
@Composable
fun Metric(value: String, label: String, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier,
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(
            text = value,
            style = MaterialTheme.typography.bodyMedium,
            fontFamily = BoatMono,
            color = MaterialTheme.colorScheme.onBackground,
        )
        Text(text = label, style = MaterialTheme.typography.labelSmall, color = BoatMuted)
    }
}

/** Inline error strip, dismissible. Same tint the web UIs use for failures. */
@Composable
fun ErrorStrip(message: String, onDismiss: () -> Unit, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(6.dp))
            .background(BoatRedTint)
            .border(1.dp, BoatRed.copy(alpha = 0.4f), RoundedCornerShape(6.dp))
            .padding(start = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = message,
            style = MaterialTheme.typography.labelMedium,
            color = BoatRed,
            modifier = Modifier.weight(1f).padding(vertical = 8.dp),
        )
        TextButton(onClick = onDismiss) {
            Text("Dismiss", style = MaterialTheme.typography.labelMedium, color = BoatRed)
        }
    }
}

/** Primary action. Tinted fill rather than Material's solid brand colour. */
@Composable
fun BoatButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    tint: Color = MaterialTheme.colorScheme.primary,
    fill: Color = BoatElevated,
) {
    Button(
        onClick = onClick,
        enabled = enabled,
        shape = RoundedCornerShape(6.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(
            horizontal = 14.dp, vertical = 6.dp,
        ),
        colors = ButtonDefaults.buttonColors(
            containerColor = fill,
            contentColor = tint,
            disabledContainerColor = BoatElevated.copy(alpha = 0.4f),
            disabledContentColor = BoatMuted,
        ),
        modifier = modifier,
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge)
    }
}

/** Placeholder text for an empty pane. */
@Composable
fun EmptyHint(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = BoatMuted,
        modifier = modifier.padding(horizontal = 12.dp, vertical = 20.dp),
    )
}
