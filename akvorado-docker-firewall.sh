#!/bin/bash
# Restrict who can reach the Docker-published NetFlow port (udp/2055 -> akvorado-inlet).
# Docker publishes ports via its own iptables chains, which bypass ufw; the
# DOCKER-USER chain is the supported place to filter them (same approach as
# graylog-docker-firewall.sh for syslog).
set -euo pipefail

# Flow exporters allowed to send NetFlow (TAG headends, Loopback1). One per TAG.
ALLOWED=(10.228.224.17/32 10.228.224.18/32)   # YAU-MCO-OC4-TAG01, YAU-MCO-OC3-TAG01 (Loopback1)

PORT=2055
CHAIN=AKVORADO-FLOW
iptables -N "$CHAIN" 2>/dev/null || true
iptables -F "$CHAIN"
for src in "${ALLOWED[@]}"; do
  iptables -A "$CHAIN" -s "$src" -j RETURN
done
iptables -A "$CHAIN" -j DROP

rule=(-p udp -m conntrack --ctorigdstport "$PORT" --ctdir ORIGINAL -j "$CHAIN")
iptables -C DOCKER-USER "${rule[@]}" 2>/dev/null || iptables -I DOCKER-USER 1 "${rule[@]}"
echo "netflow firewall applied: udp/$PORT allow ${ALLOWED[*]}"
