package com.boat.companion.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * The palette the BoAt web UIs use (the `ui/` FastAPI services) — GitHub
 * Primer dark.
 *
 * Kept as the same literal values rather than approximated, so a phone sitting
 * next to a dashboard reads as the same tool rather than a lookalike.
 */
val BoatBg = Color(0xFF0D1117)       // page background
val BoatPanel = Color(0xFF161B22)    // panels, headers
val BoatElevated = Color(0xFF21262D) // raised surfaces, pressed states
val BoatBorder = Color(0xFF30363D)
val BoatText = Color(0xFFE6EDF3)
val BoatMuted = Color(0xFF8B949E)

val BoatBlue = Color(0xFF58A6FF)     // accent, logo, links
val BoatGreen = Color(0xFF3FB950)    // running / connected
val BoatYellow = Color(0xFFD29922)   // warning
val BoatRed = Color(0xFFF85149)      // error / disconnected
val BoatPurple = Color(0xFFD2A8FF)
val BoatOrange = Color(0xFFFFA657)

/** Badge fills: the web UIs pair a dark tint with a saturated border. */
val BoatGreenTint = Color(0xFF1F3A1F)
val BoatGreenBorder = Color(0xFF2EA043)
val BoatRedTint = Color(0xFF3D0B0B)
val BoatBlueTint = Color(0xFF1C3A5C)
