package com.boat.companion.bridge

import com.boat.companion.net.GatewayClient
import com.boat.companion.usb.SlcanAdapter
import com.boat.companion.usb.SlcanFrame
import com.boat.proto.v1.CanMetadata
import com.boat.proto.v1.Frame
import com.boat.proto.v1.StreamFramesRequest
import com.google.protobuf.ByteString
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.receiveAsFlow
import java.util.concurrent.atomic.AtomicLong

/** BOAT_CAN_FLAG_SELF_SENT — sdk/cpp/include/boat/plugin.h. */
private const val CAN_FLAG_SELF_SENT = 0x08

/** CANFD_FDF — marks a frame for the gateway's CAN FD transmit path. */
private const val CANFD_FDF = 0x04

/**
 * A bus buffers rather than blocking: if the network stalls, dropping the
 * oldest queued frames is better than stalling the USB reader and losing
 * everything behind it.
 */
private const val OUTGOING_CAPACITY = 4096

/**
 * Bridges a phone-attached CAN adapter onto a gateway interface.
 *
 * Ingress and egress share one StreamFrames call: frames read from the adapter
 * are published to [ifaceName] on the gateway, and frames the gateway puts on
 * that interface are transmitted onto the physical bus.
 *
 * The gateway interface must be a **vcan**, not a physical one. Bridging onto a
 * real interface that shares a bus with the adapter would feed every frame
 * straight back to its source.
 *
 * Loop prevention cannot rely on BOAT_CAN_FLAG_SELF_SENT alone. That flag means
 * "this gateway transmitted it", not "you sent it": a frame from
 * `boat frame send` travels the same CanBusRegistry::SendFrame path and is
 * tagged identically to this bridge's own echo. Filtering on it would silently
 * drop every gateway-originated frame, which is exactly what it did before
 * [EchoSuppressor] was introduced. The flag is now only a cheap pre-filter; what
 * actually decides is whether this bridge published that frame moments earlier.
 */
class GatewayBridge(
    private val adapter: SlcanAdapter,
    private val client: GatewayClient,
    private val ifaceName: String,
) {

    private val outgoing = Channel<StreamFramesRequest>(
        capacity = OUTGOING_CAPACITY,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )

    private val _toGateway = AtomicLong(0)
    private val _toBus = AtomicLong(0)
    private val _dropped = AtomicLong(0)

    val framesToGateway: Long get() = _toGateway.get()
    val framesToBus: Long get() = _toBus.get()
    val framesDropped: Long get() = _dropped.get()

    /**
     * Runs until cancelled or the stream fails.
     *
     * The subscription is queued before the call starts so it is the first
     * message the gateway sees, which is what configures the return direction.
     */
    suspend fun run() {
        outgoing.trySend(GatewayClient.subscribeMessage(ifaceFilter = ifaceName))
        client.streamFrames(outgoing.receiveAsFlow()).collect { frame ->
            if (!frame.hasCan()) return@collect
            val slcan = frame.toSlcan()
            // SELF_SENT is not enough on its own: the gateway tags everything it
            // transmits, so a frame from `boat frame send` is tagged exactly like
            // this bridge's own echo. Only a frame we actually published is an
            // echo, so that is what gets suppressed.
            if (frame.can.flags and CAN_FLAG_SELF_SENT != 0 && echoes.consume(slcan)) {
                return@collect
            }
            adapter.send(slcan)
            _toBus.incrementAndGet()
        }
    }

    /** Queues a frame read from the adapter. Non-blocking by design. */
    fun publish(frame: SlcanFrame) {
        val message = GatewayClient.frameMessage(frame.toProto(ifaceName))
        if (outgoing.trySend(message).isSuccess) {
            echoes.remember(frame)
            _toGateway.incrementAndGet()
        } else {
            _dropped.incrementAndGet()
        }
    }

    private val echoes = EchoSuppressor()

    fun close() {
        outgoing.close()
    }
}

/**
 * Remembers frames this bridge published so their echoes can be recognised.
 *
 * Needed because nothing in the protocol identifies who originated a frame: the
 * gateway tags everything it transmits with SELF_SENT, so a bridge cannot
 * distinguish its own echo from genuine gateway-originated traffic. Matching on
 * content within a short window is the standard way out.
 *
 * The residual ambiguity is inherent: if the gateway deliberately sends a frame
 * identical to one this bridge published moments earlier, it is swallowed. The
 * window is kept short to bound that, and an origin identifier in the protocol
 * would remove the guesswork entirely.
 *
 * Keyed by a 64-bit content hash rather than the bytes themselves — at bus
 * saturation this runs thousands of times a second, and a hash collision costs
 * one suppressed frame, not correctness of the stream.
 */
internal class EchoSuppressor(
    private val windowNanos: Long = 1_000_000_000L,
    private val capacity: Int = 8192,
) {
    private val pending = HashMap<Long, ArrayDeque<Long>>()
    private var count = 0

    @Synchronized
    fun remember(frame: SlcanFrame) {
        val now = System.nanoTime()
        prune(now)
        if (count >= capacity) return
        pending.getOrPut(frame.fingerprint()) { ArrayDeque() }.addLast(now)
        count++
    }

    /** True when [frame] matches an outstanding publication, which it consumes. */
    @Synchronized
    fun consume(frame: SlcanFrame): Boolean {
        val now = System.nanoTime()
        prune(now)
        val queue = pending[frame.fingerprint()] ?: return false
        val stamp = queue.removeFirstOrNull() ?: return false
        count--
        if (queue.isEmpty()) pending.remove(frame.fingerprint())
        return now - stamp <= windowNanos
    }

    private fun prune(now: Long) {
        if (count == 0) return
        val iterator = pending.entries.iterator()
        while (iterator.hasNext()) {
            val entry = iterator.next()
            while (true) {
                val head = entry.value.firstOrNull() ?: break
                if (now - head <= windowNanos) break
                entry.value.removeFirst()
                count--
            }
            if (entry.value.isEmpty()) iterator.remove()
        }
    }
}

/** FNV-1a over identifier, FD flag and payload. */
internal fun SlcanFrame.fingerprint(): Long {
    var hash = -0x340d631b7bdddcdbL
    fun mix(byte: Int) {
        hash = hash xor (byte.toLong() and 0xFF)
        hash *= 0x100000001b3L
    }
    for (shift in 0..3) mix(id ushr (shift * 8))
    mix(if (fd) 1 else 0)
    for (byte in data) mix(byte.toInt())
    return hash
}

/**
 * The gateway derives extended-ness from the identifier value rather than a
 * flag — SocketCanDriver sets CAN_EFF_FLAG when can_id > 0x7FF — so the bare
 * identifier is what travels. An extended frame with an identifier below 0x800
 * cannot be represented and will reach the bus as a standard frame.
 */
private fun SlcanFrame.toProto(iface: String): Frame {
    val metadata = CanMetadata.newBuilder()
        .setCanId(id)
        .setDlc(data.size)
        .setFlags(if (fd) CANFD_FDF else 0)

    return Frame.newBuilder()
        .setBusType(if (fd) Frame.BusType.CANFD else Frame.BusType.CAN)
        .setIface(iface)
        .setTimestampNs(timestampNanos)
        .setPayload(ByteString.copyFrom(data))
        .setCan(metadata)
        .build()
}

private fun Frame.toSlcan(): SlcanFrame = SlcanFrame(
    id = can.canId,
    data = payload.toByteArray(),
    // Mirrors the gateway's own convention, so a frame survives the round trip.
    extended = can.canId > 0x7FF,
    fd = busType == Frame.BusType.CANFD,
)
