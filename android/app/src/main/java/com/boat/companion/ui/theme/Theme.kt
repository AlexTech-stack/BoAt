package com.boat.companion.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * One fixed dark scheme, matching the BoAt web UIs.
 *
 * Deliberately not `isSystemInDarkTheme()` and not dynamic colour: this is an
 * instrument that shows CAN traffic on a bench, and the meaning of green,
 * amber and red here is the same as on the dashboards. Letting the wallpaper
 * recolour it, or flipping to a light scheme, would break that correspondence.
 */
private val BoatColorScheme = darkColorScheme(
    primary = BoatBlue,
    onPrimary = BoatBg,
    primaryContainer = BoatBlueTint,
    onPrimaryContainer = BoatBlue,

    secondary = BoatGreen,
    onSecondary = BoatBg,
    secondaryContainer = BoatGreenTint,
    onSecondaryContainer = BoatGreen,

    tertiary = BoatPurple,
    onTertiary = BoatBg,

    background = BoatBg,
    onBackground = BoatText,

    surface = BoatPanel,
    onSurface = BoatText,
    surfaceVariant = BoatElevated,
    onSurfaceVariant = BoatMuted,
    surfaceContainerHighest = BoatElevated,

    error = BoatRed,
    onError = BoatText,
    errorContainer = BoatRedTint,
    onErrorContainer = BoatRed,

    outline = BoatBorder,
    outlineVariant = BoatBorder,
    scrim = Color(0xCC000000),
)

@Composable
fun BoAtTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = BoatColorScheme,
        typography = BoatTypography,
        content = content,
    )
}
