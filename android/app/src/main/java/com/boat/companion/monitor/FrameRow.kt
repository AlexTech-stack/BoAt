package com.boat.companion.monitor

import com.boat.proto.v1.Frame

/** BOAT_CAN_FLAG_SELF_SENT — sdk/cpp/include/boat/plugin.h. */
private const val CAN_FLAG_SELF_SENT = 0x08

private const val MAX_DISPLAYED_BYTES = 32

/**
 * A frame flattened for display. The proto [Frame] is not kept: the monitor holds
 * a bounded history and retaining parsed protos for all of it is pure overhead.
 */
data class FrameRow(
    val seq: Long,
    val timestampNs: Long,
    val iface: String,
    val busType: String,
    val identifier: String,
    val length: Int,
    val data: String,
    val selfSent: Boolean,
)

fun Frame.toRow(seq: Long): FrameRow = FrameRow(
    seq = seq,
    timestampNs = timestampNs,
    iface = iface.ifEmpty { "—" },
    busType = busTypeLabel(),
    identifier = identifier(),
    length = payload.size(),
    data = payloadHex(),
    selfSent = isSelfSent(),
)

/**
 * True when the gateway tagged this frame as one it transmitted itself. The
 * registry send path sets this on every locally-sent frame, so a bridge that both
 * injects into a bus and subscribes to it can drop its own echo.
 */
private fun Frame.isSelfSent(): Boolean = when {
    hasCan() -> can.flags and CAN_FLAG_SELF_SENT != 0
    // Ethernet cannot report this: core tags BOAT_ETH_FLAG_SELF_SENT in
    // ethernet_bus_registry, but EthMetadata has no flags field for it to
    // travel in, so a subscriber can never see it. CAN-only until that field
    // exists.
    else -> false
}

private fun Frame.busTypeLabel(): String = when (busType) {
    Frame.BusType.CAN -> "CAN"
    Frame.BusType.CANFD -> "CANFD"
    Frame.BusType.ETHERNET -> "ETH"
    Frame.BusType.TCP -> "TCP"
    Frame.BusType.PDU -> "PDU"
    else -> "?"
}

private fun Frame.identifier(): String = when {
    hasCan() -> {
        // 29-bit IDs exceed the 11-bit standard range and are shown at full width.
        val id = can.canId
        if (id > 0x7FF) "0x%08X".format(id) else "0x%03X".format(id)
    }
    hasEth() -> "0x%04X".format(eth.ethertype)
    hasPdu() -> "PDU %d".format(pdu.pduId)
    hasTcp() -> "%d→%d".format(tcp.srcPort, tcp.dstPort)
    else -> "—"
}

private fun Frame.payloadHex(): String {
    val bytes = payload.toByteArray()
    val shown = bytes.take(MAX_DISPLAYED_BYTES)
    val hex = shown.joinToString(" ") { "%02X".format(it) }
    return if (bytes.size > MAX_DISPLAYED_BYTES) "$hex …" else hex
}
