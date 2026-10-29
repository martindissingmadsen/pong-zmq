#!/bin/sh
# Shape this container's outgoing traffic with tc netem, then run the command.
# DELAY_MS and JITTER_MS are one-way: every container shapes its own sending side, so a
# message between two shaped containers is delayed once and a round trip twice. LOSS_PCT
# drops that share of packets in each direction. With everything 0 nothing is changed.
set -e
if [ "${DELAY_MS:-0}" != 0 ] || [ "${LOSS_PCT:-0}" != 0 ]; then
    tc qdisc add dev eth0 root netem delay "${DELAY_MS:-0}ms" "${JITTER_MS:-0}ms" loss "${LOSS_PCT:-0}%"
    echo "netem on eth0: delay ${DELAY_MS:-0} ms +- ${JITTER_MS:-0} ms, loss ${LOSS_PCT:-0} %"
fi
exec "$@"
