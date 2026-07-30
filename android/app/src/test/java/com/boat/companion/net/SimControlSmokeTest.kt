package com.boat.companion.net

import com.boat.proto.v1.CreateScenarioRequest
import com.boat.proto.v1.Scenario
import com.boat.proto.v1.ScenarioServiceGrpc
import com.boat.proto.v1.SimulationState
import io.grpc.Grpc
import io.grpc.InsecureChannelCredentials
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.TimeUnit

/**
 * Drives a simulation through its lifecycle against a live gateway. Skipped when
 * no gateway is listening — see [GatewayClientSmokeTest].
 */
class SimControlSmokeTest {

    private val host = System.getProperty("boat.gateway.host") ?: "127.0.0.1"
    private val port = (System.getProperty("boat.gateway.port") ?: "50051").toInt()

    // Matches what ScenarioLoader::LoadFromJson accepts (see test_seed.cpp).
    private val scenarioJson = """
        {"id":"android-smoke","name":"Android smoke","version":"1.0",
         "duration_ticks":1000,"seed":1234,"plugins":[],"signals":[],"faults":[]}
    """.trimIndent()

    @Test
    fun `simulation runs through start pause step stop`() = runBlocking {
        assumeTrue("no gateway on $host:$port", gatewayIsListening())

        val scenarioId = "android-smoke-${System.currentTimeMillis()}"
        storeScenario(scenarioId)

        GatewayClient(Endpoint(host = host, port = port)).use { client ->
            withTimeout(20_000) {
                val created = client.createSimulation(scenarioId)
                val id = created.simulationId
                assertTrue("gateway returned no simulation id", id.isNotEmpty())

                assertTrue(
                    "created simulation should be listed",
                    client.listSimulations().any { it.simulationId == id },
                )

                assertEquals(
                    SimulationState.SIMULATION_STATE_RUNNING,
                    client.startSimulation(id).state,
                )
                assertEquals(
                    SimulationState.SIMULATION_STATE_PAUSED,
                    client.pauseSimulation(id).state,
                )

                // Stepping from paused advances the tick counter without resuming.
                val before = client.stepSimulation(id, ticks = 1)
                val after = client.stepSimulation(id, ticks = 5)
                assertTrue(
                    "tick should advance across steps (${before.tick} -> ${after.tick})",
                    after.tick > before.tick,
                )

                assertEquals(
                    SimulationState.SIMULATION_STATE_STOPPED,
                    client.stopSimulation(id).state,
                )
            }
        }
    }

    /** CreateSimulation needs the scenario already in the gateway's config store. */
    private fun storeScenario(scenarioId: String) {
        val channel = Grpc
            .newChannelBuilderForAddress(host, port, InsecureChannelCredentials.create())
            .build()
        try {
            ScenarioServiceGrpc.newBlockingStub(channel).createScenario(
                CreateScenarioRequest.newBuilder()
                    .setScenario(
                        Scenario.newBuilder()
                            .setScenarioId(scenarioId)
                            .setName("Android smoke")
                            .setContent(scenarioJson)
                    )
                    .build()
            )
        } finally {
            channel.shutdownNow().awaitTermination(5, TimeUnit.SECONDS)
        }
    }

    private fun gatewayIsListening(): Boolean = runCatching {
        Socket().use { it.connect(InetSocketAddress(host, port), 500) }
    }.isSuccess
}
