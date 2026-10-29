#!/bin/sh
# The load generator: CPU_LOAD busy CPU workers and MEM_LOAD memory workers (MEM_MB each,
# writing and reading their memory all the time), on the same CPUs as the game. Idle when both are 0.
args=""
if [ "${CPU_LOAD:-0}" -gt 0 ]; then args="$args --cpu $CPU_LOAD"; fi
if [ "${MEM_LOAD:-0}" -gt 0 ]; then args="$args --vm $MEM_LOAD --vm-bytes ${MEM_MB:-256}M"; fi
if [ -z "$args" ]; then exec sleep infinity; fi
echo "load: stress-ng$args"
exec stress-ng $args --quiet
