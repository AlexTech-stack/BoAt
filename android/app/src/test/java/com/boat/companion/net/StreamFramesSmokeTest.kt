package com.boat.companion.net

import com.boat.companion.monitor.toRow
import com.boat.proto.v1.CanMetadata
import com.boat.proto.v1.Frame
import com.boat.proto.v1.StreamFramesRequest
import com.google.protobuf.ByteString
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.net.InetSocketAddress
import java.net.Socket

/**
 * Covers the bidirectional bridge RPC and interface filtering against a live
 * gateway. Skipped when no gateway is listening — see [GatewayClientSmokeTest].
 *
 * The filter test needs a second interface:
 *   sudo ip link add vcan1 type vcan && sudo ip link set vcan1 up
 *   BOAT_CAN_INTERFACES=vcan0,vcan1 build/debug/src/gateway/grpc_gateway/boat_gateway
 */
class StreamFramesSmokeTest {

    private val host = System.getProperty("boat.gateway.host") ?: "127.0.0.1"
    private val port = (System.getProperty("boat.gateway.port") ?: "50051").toInt()
    private val primary = System.getProperty("boat.gateway.iface") ?: "vcan0"
    private val secondary = System.getProperty("boat.gateway.iface2") ?: "vcan1"

    @Test
    fun `frame injected over the bridge comes back on the same stream`() = runBlocking {
        assumeTrue("no gateway on $host:$port", gatewayIsListening())

        GatewayClient(Endpoint(host = host, port = port)).use { client ->
            val outgoing = MutableSharedFlow<StreamFramesRequest>(replay = 1)
            outgoing.emit(GatewayClient.subscribeMessage(ifaceFilter = primary))

            val received = withTimeout(10_000) {
                val incoming = async { client.streamFrames(outgoing).first() }
                delay(500)
                outgoing.emit(
                    GatewayClient.frameMessage(
                        canFrame(primary, canId = 0x321, payload = byteArrayOf(0x42))
                    )
                )
                incoming.await()
            }

            val row = received.toRow(seq = 0)
            assertEquals(primary, received.iface)
            assertEquals("0x321", row.identifier)
            assertEquals("42", row.data)
            assertTrue("bridge-injected frame should return tagged self-sent", row.selfSent)
        }
    }

    @Test
    fun `iface filter excludes traffic from other interfaces`() = runBlocking {
        assumeTrue("no gateway on $host:$port", gatewayIsListening())
        assumeTrue("needs a second interface", interfaceExists(secondary))

        GatewayClient(Endpoint(host = host, port = port)).use { client ->
            val outgoing = MutableSharedFlow<StreamFramesRequest>(replay = 1)
            // Subscribe to the secondary interface, then inject on the primary.
            outgoing.emit(GatewayClient.subscribeMessage(ifaceFilter = secondary))

            val leaked = withTimeoutOrNull(3_000) {
                val incoming = async { client.streamFrames(outgoing).first() }
                delay(500)
                outgoing.emit(
                    GatewayClient.frameMessage(
                        canFrame(primary, canId = 0x111, payload = byteArrayOf(0x01))
                    )
                )
                incoming.await()
            }

            assertNull("frame from $primary leaked to a $secondary subscription", leaked)
        }
    }

    private fun canFrame(iface: String, canId: Int, payload: ByteArray): Frame =
        Frame.newBuilder()
            .setBusType(Frame.BusType.CAN)
            .setIface(iface)
            .setPayload(ByteString.copyFrom(payload))
            .setCan(CanMetadata.newBuilder().setCanId(canId).setDlc(payload.size))
            .build()

    // NetworkInterface cannot see CAN interfaces — they carry no IP configuration —
    // so ask the kernel directly.
    private fun interfaceExists(name: String): Boolean =
        java.io.File("/sys/class/net/$name").exists()

    private fun gatewayIsListening(): Boolean = runCatching {
        Socket().use { it.connect(InetSocketAddress(host, port), 500) }
    }.isSuccess
}
