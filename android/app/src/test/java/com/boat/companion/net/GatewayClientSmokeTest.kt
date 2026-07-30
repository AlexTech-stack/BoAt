package com.boat.companion.net

import com.boat.companion.monitor.toRow
import com.boat.proto.v1.CanMetadata
import com.boat.proto.v1.Frame
import com.boat.proto.v1.FrameServiceGrpc
import com.boat.proto.v1.SendFrameRequest
import com.google.protobuf.ByteString
import io.grpc.Grpc
import io.grpc.InsecureChannelCredentials
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
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
 * Exercises [GatewayClient] against a real boat_gateway. GatewayClient touches no
 * Android APIs, so it runs as a plain JVM test.
 *
 * Skipped unless a gateway is already listening — this is a smoke test to run
 * beside a live gateway, not a CI gate:
 *
 *   sudo modprobe vcan && sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up
 *   BOAT_CAN_INTERFACES=vcan0 build/debug/src/gateway/grpc_gateway/boat_gateway
 */
class GatewayClientSmokeTest {

    private val host = System.getProperty("boat.gateway.host") ?: "127.0.0.1"
    private val port = (System.getProperty("boat.gateway.port") ?: "50051").toInt()
    private val iface = System.getProperty("boat.gateway.iface") ?: "vcan0"

    @Test
    fun `subscribed frame round-trips through the gateway`() = runBlocking {
        assumeTrue("no gateway on $host:$port", gatewayIsListening())

        GatewayClient(Endpoint(host = host, port = port)).use { client ->
            val received = withTimeout(10_000) {
                // Subscribe first: the gateway streams live traffic, so a frame sent
                // before the subscription exists is simply missed.
                val stream = client.subscribeFrames(ifaceFilter = iface)
                val collector = async { stream.first() }
                delay(500)
                sendCanFrame(canId = 0x123, payload = byteArrayOf(0xDE.toByte(), 0xAD.toByte()))
                collector.await()
            }

            assertEquals(iface, received.iface)
            assertEquals(0x123, received.can.canId)

            val row = received.toRow(seq = 0)
            assertEquals("0x123", row.identifier)
            assertEquals("DE AD", row.data)
            // The gateway tags everything it transmits itself, and a frame we sent
            // through SendFrame comes back as exactly that.
            assertTrue("expected the self-sent tag on our own frame", row.selfSent)
        }
    }

    private fun sendCanFrame(canId: Int, payload: ByteArray) {
        val channel = Grpc
            .newChannelBuilderForAddress(host, port, InsecureChannelCredentials.create())
            .build()
        try {
            val frame = Frame.newBuilder()
                .setBusType(Frame.BusType.CAN)
                .setIface(iface)
                .setPayload(ByteString.copyFrom(payload))
                .setCan(CanMetadata.newBuilder().setCanId(canId).setDlc(payload.size))
                .build()
            val response = FrameServiceGrpc.newBlockingStub(channel)
                .sendFrame(SendFrameRequest.newBuilder().setFrame(frame).build())
            assertTrue("gateway rejected the frame", response.accepted)
        } finally {
            channel.shutdownNow().awaitTermination(5, TimeUnit.SECONDS)
        }
    }

    private fun gatewayIsListening(): Boolean = runCatching {
        Socket().use { it.connect(InetSocketAddress(host, port), 500) }
    }.isSuccess
}
