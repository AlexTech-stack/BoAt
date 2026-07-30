package com.boat.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.boat.companion.monitor.MonitorScreen
import com.boat.companion.sim.SimScreen
import com.boat.companion.ui.ConnectionCard
import com.boat.companion.ui.theme.BoAtTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            BoAtTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    CompanionApp(modifier = Modifier.padding(innerPadding))
                }
            }
        }
    }
}

private val TABS = listOf("Monitor", "Simulation")

@Composable
private fun CompanionApp(modifier: Modifier = Modifier) {
    var selectedTab by remember { mutableIntStateOf(0) }

    Column(modifier = modifier.fillMaxSize()) {
        // One connection shared by every tab, so switching tabs never drops the
        // frame stream or opens a second channel to the same gateway.
        ConnectionCard(modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp))

        TabRow(selectedTabIndex = selectedTab) {
            TABS.forEachIndexed { index, title ->
                Tab(
                    selected = index == selectedTab,
                    onClick = { selectedTab = index },
                    text = { Text(title) },
                )
            }
        }

        when (selectedTab) {
            0 -> MonitorScreen()
            else -> SimScreen()
        }
    }
}
