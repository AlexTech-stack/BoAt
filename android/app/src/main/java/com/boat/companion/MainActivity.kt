package com.boat.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.unit.dp
import com.boat.companion.adapter.AdapterScreen
import com.boat.companion.monitor.MonitorScreen
import com.boat.companion.sim.SimScreen
import com.boat.companion.ui.ConnectionBar
import com.boat.companion.ui.theme.BoAtTheme
import com.boat.companion.ui.theme.BoatBg
import com.boat.companion.ui.theme.BoatBorder
import com.boat.companion.ui.theme.BoatMuted
import com.boat.companion.ui.theme.BoatPanel
import com.boat.companion.ui.theme.PaneTitle

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        // Dark-only, so the system bars are pinned to match rather than
        // following the device theme and leaving a pale strip above the header.
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(BoatPanel.toArgb()),
            navigationBarStyle = SystemBarStyle.dark(BoatBg.toArgb()),
        )
        super.onCreate(savedInstanceState)
        setContent {
            BoAtTheme {
                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    containerColor = BoatBg,
                ) { innerPadding ->
                    CompanionApp(modifier = Modifier.padding(innerPadding))
                }
            }
        }
    }
}

private data class Section(val title: String, val subtitle: String)

/** Subtitles follow the web UIs' header pattern ("gRPC Traffic Inspector"). */
private val SECTIONS = listOf(
    Section("Monitor", "Live Frame Monitor"),
    Section("Simulation", "Simulation Control"),
    Section("Adapter", "CAN Adapter"),
)

@Composable
private fun CompanionApp(modifier: Modifier = Modifier) {
    var selectedTab by remember { mutableIntStateOf(0) }

    Column(modifier = modifier.fillMaxSize()) {
        // One connection shared by every tab, so switching tabs never drops the
        // frame stream or opens a second channel to the same gateway.
        ConnectionBar(subtitle = SECTIONS[selectedTab].subtitle)

        TabRow(
            selectedTabIndex = selectedTab,
            containerColor = BoatPanel,
            contentColor = MaterialTheme.colorScheme.primary,
            indicator = { positions ->
                TabRowDefaults.SecondaryIndicator(
                    modifier = Modifier.tabIndicatorOffset(positions[selectedTab]),
                    height = 2.dp,
                    color = MaterialTheme.colorScheme.primary,
                )
            },
            divider = {},
        ) {
            SECTIONS.forEachIndexed { index, section ->
                Tab(
                    selected = index == selectedTab,
                    onClick = { selectedTab = index },
                    selectedContentColor = MaterialTheme.colorScheme.primary,
                    unselectedContentColor = BoatMuted,
                    text = { Text(section.title.uppercase(), style = PaneTitle) },
                )
            }
        }
        HorizontalDivider(color = BoatBorder)

        when (selectedTab) {
            0 -> MonitorScreen()
            1 -> SimScreen()
            // The adapter reads CAN directly over USB and needs no gateway, so
            // it stays usable while the connection above is down.
            else -> AdapterScreen()
        }
    }
}
