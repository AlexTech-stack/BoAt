package com.boat.companion.net

import com.boat.proto.v1.CreateSimulationRequest
import com.boat.proto.v1.Frame
import com.boat.proto.v1.FrameServiceGrpcKt
import com.boat.proto.v1.GetSimulationStateRequest
import com.boat.proto.v1.ListSimulationsRequest
import com.boat.proto.v1.PauseSimulationRequest
import com.boat.proto.v1.ResetSimulationRequest
import com.boat.proto.v1.Simulation
import com.boat.proto.v1.SimulationServiceGrpcKt
import com.boat.proto.v1.StartSimulationRequest
import com.boat.proto.v1.StepSimulationRequest
import com.boat.proto.v1.StopSimulationRequest
import com.boat.proto.v1.StreamFramesRequest
import com.boat.proto.v1.SubscribeFramesRequest
import io.grpc.ChannelCredentials
import io.grpc.Grpc
import io.grpc.InsecureChannelCredentials
import io.grpc.ManagedChannel
import io.grpc.TlsChannelCredentials
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import java.io.Closeable
import java.io.InputStream
import java.util.concurrent.TimeUnit

/**
 * How to reach a boat_gateway.
 *
 * The gateway listens on 0.0.0.0:50051 with InsecureServerCredentials, so [useTls]
 * is false until env-gated SslServerCredentials lands gateway-side. When it does,
 * supply the CA that signed the gateway certificate via [caCertificate]; gRPC
 * verifies the hostname, so a gateway reached by IP needs an IP SAN in its cert.
 */
data class Endpoint(
    val host: String,
    val port: Int = 50051,
    val useTls: Boolean = false,
    val caCertificate: (() -> InputStream)? = null,
)

/**
 * Owns one gRPC channel to a gateway. Not reusable after [close].
 */
class GatewayClient(endpoint: Endpoint) : Closeable {

    private val channel: ManagedChannel =
        Grpc.newChannelBuilderForAddress(endpoint.host, endpoint.port, endpoint.credentials())
            // Frames arrive continuously; without a keepalive a silent NAT timeout
            // looks identical to an idle bus.
            .keepAliveTime(30, TimeUnit.SECONDS)
            .keepAliveWithoutCalls(true)
            .build()

    private val frameService = FrameServiceGrpcKt.FrameServiceCoroutineStub(channel)
    private val simulationService =
        SimulationServiceGrpcKt.SimulationServiceCoroutineStub(channel)

    /**
     * Live frames from the gateway. Empty [busTypes] means all types, empty
     * [ifaceFilter] means all interfaces — matching SubscribeFramesRequest semantics.
     *
     * The stream has no deadline: it is meant to run until cancelled.
     */
    fun subscribeFrames(
        busTypes: List<Frame.BusType> = emptyList(),
        ifaceFilter: String = "",
    ): Flow<Frame> {
        val request = SubscribeFramesRequest.newBuilder()
            .addAllBusTypes(busTypes)
            .setIfaceFilter(ifaceFilter)
            .build()
        return frameService.subscribeFrames(request)
    }

    /**
     * Bidirectional bridge: [outgoing] carries frames into the gateway, the
     * returned flow carries subscribed frames back, both over one connection.
     *
     * Send [subscribeMessage] first to choose what comes back; a client that only
     * pushes can skip it. Frames the bridge injects return tagged as self-sent, so
     * filter on that to avoid re-transmitting your own traffic.
     */
    fun streamFrames(outgoing: Flow<StreamFramesRequest>): Flow<Frame> =
        frameService.streamFrames(outgoing)

    /* ── SimulationService ──────────────────────────────────────────────── */

    /**
     * Simulations the gateway knows about. Doubles as a connection probe: a gRPC
     * channel connects lazily, so nothing proves the gateway is reachable until
     * an actual RPC completes.
     */
    suspend fun listSimulations(): List<Simulation> =
        simulationService
            .listSimulations(ListSimulationsRequest.getDefaultInstance())
            .simulationsList

    /** Requires a scenario already stored on the gateway; the id is server-assigned. */
    suspend fun createSimulation(scenarioId: String): Simulation =
        simulationService
            .createSimulation(
                CreateSimulationRequest.newBuilder().setScenarioId(scenarioId).build()
            )
            .simulation

    suspend fun startSimulation(id: String): Simulation =
        simulationService
            .startSimulation(StartSimulationRequest.newBuilder().setSimulationId(id).build())
            .simulation

    suspend fun pauseSimulation(id: String): Simulation =
        simulationService
            .pauseSimulation(PauseSimulationRequest.newBuilder().setSimulationId(id).build())
            .simulation

    suspend fun stepSimulation(id: String, ticks: Int): Simulation =
        simulationService
            .stepSimulation(
                StepSimulationRequest.newBuilder().setSimulationId(id).setTicks(ticks).build()
            )
            .simulation

    suspend fun resetSimulation(id: String): Simulation =
        simulationService
            .resetSimulation(ResetSimulationRequest.newBuilder().setSimulationId(id).build())
            .simulation

    suspend fun stopSimulation(id: String): Simulation =
        simulationService
            .stopSimulation(StopSimulationRequest.newBuilder().setSimulationId(id).build())
            .simulation

    suspend fun getSimulationState(id: String): Simulation =
        simulationService
            .getSimulationState(
                GetSimulationStateRequest.newBuilder().setSimulationId(id).build()
            )
            .simulation

    /**
     * Simulation state changes; runs until cancelled.
     *
     * The gateway writes only on state-machine transitions, so this does NOT
     * report tick progress while a simulation runs — the tick has to be polled.
     */
    fun watchSimulation(id: String): Flow<Simulation> =
        simulationService
            .watchSimulation(
                GetSimulationStateRequest.newBuilder().setSimulationId(id).build()
            )
            .map { it.simulation }

    companion object {
        fun subscribeMessage(
            busTypes: List<Frame.BusType> = emptyList(),
            ifaceFilter: String = "",
        ): StreamFramesRequest = StreamFramesRequest.newBuilder()
            .setSubscribe(
                SubscribeFramesRequest.newBuilder()
                    .addAllBusTypes(busTypes)
                    .setIfaceFilter(ifaceFilter)
            )
            .build()

        fun frameMessage(frame: Frame): StreamFramesRequest =
            StreamFramesRequest.newBuilder().setFrame(frame).build()
    }

    override fun close() {
        channel.shutdownNow()
    }
}

private fun Endpoint.credentials(): ChannelCredentials =
    if (!useTls) {
        InsecureChannelCredentials.create()
    } else {
        val ca = caCertificate
        if (ca == null) {
            // Platform trust store: only correct for a gateway cert from a public CA.
            TlsChannelCredentials.create()
        } else {
            ca().use { TlsChannelCredentials.newBuilder().trustManager(it).build() }
        }
    }
