#!/bin/sh
# The first thing a sandboxed session runs, as root with NET_ADMIN, SETUID,
# SETGID and SETPCAP and nothing else. See watchtower/sandbox.py.
#
# 1. Shut this computer and its network out. In order:
#      - DNS to the resolvers in resolv.conf, port 53 only;
#      - reject this computer: every address it has (BOX_SELF) and the box's
#        own gateway, which is this computer seen from inside;
#      - accept what the session was allowed (BOX_ALLOW: `net` or `net,port`),
#        such as a controller it has to ssh to;
#      - reject every other private, link-local, carrier-grade (Tailscale),
#        benchmark, multicast and reserved range.
#    The rest of the internet stays reachable, because the session needs the
#    API. This computer is rejected before anything is accepted, so allowing
#    the subnet it is on still does not let the box reach it.
# 2. Give BOX_UID a passwd entry (ssh will not run without one).
# 3. Drop to BOX_UID:BOX_GID with no capabilities, an empty bounding set and
#    no-new-privileges, so nothing inside can undo step 1.
#
# Any failure stops here: a box that could not shut the network out does not
# start at all, rather than starting without it.
set -eu

die() { echo "sandbox: $*" >&2; exit 1; }

[ -n "${BOX_UID:-}" ] && [ -n "${BOX_GID:-}" ] || die "no uid to run as"
[ "$BOX_UID" != 0 ] || die "refusing to run the session as root"
command -v iptables >/dev/null || die "iptables is not in this image"
command -v setpriv >/dev/null || die "setpriv is not in this image"

V6=no
if grep -qv ' lo$' /proc/net/if_inet6 2>/dev/null; then
  command -v ip6tables >/dev/null || die "the box has IPv6 but no ip6tables"
  V6=yes
fi
# One rule, in whichever family the address is.
rule() {  # rule <address> <iptables arguments...>
  addr=$1; shift
  case "$addr" in
    # No IPv6 in the box means no IPv6 route out of it: nothing to add.
    *:*) if [ "$V6" = yes ]; then ip6tables "$@"; fi ;;
    *) iptables "$@" ;;
  esac
}

iptables -F OUTPUT || die "cannot set the firewall (no NET_ADMIN?)"
iptables -P OUTPUT ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
if [ "$V6" = yes ]; then
  ip6tables -F OUTPUT || die "cannot set the IPv6 firewall"
  ip6tables -A OUTPUT -o lo -j ACCEPT
  ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
fi

for ns in $(awk '/^nameserver/ {print $2}' /etc/resolv.conf); do
  rule "$ns" -A OUTPUT -d "$ns" -p udp --dport 53 -j ACCEPT
  rule "$ns" -A OUTPUT -d "$ns" -p tcp --dport 53 -j ACCEPT
done

# This computer, first and whatever is allowed after.
gw=$(awk '$2 == "00000000" { print $3; exit }' /proc/net/route 2>/dev/null || true)
if [ -n "$gw" ]; then
  gw=$(printf '%d.%d.%d.%d' "0x$(echo "$gw" | cut -c7-8)" "0x$(echo "$gw" | cut -c5-6)" \
                            "0x$(echo "$gw" | cut -c3-4)" "0x$(echo "$gw" | cut -c1-2)")
fi
for addr in $gw ${BOX_SELF:-}; do
  rule "$addr" -A OUTPUT -d "$addr" -j REJECT || die "cannot shut out $addr"
done

# What this session was allowed on the local network.
for entry in ${BOX_ALLOW:-}; do
  net=${entry%%,*}
  port=
  [ "$net" = "$entry" ] || port=${entry#*,}
  if [ -n "$port" ]; then
    rule "$net" -A OUTPUT -d "$net" -p tcp --dport "$port" -j ACCEPT || die "cannot allow $entry"
    rule "$net" -A OUTPUT -d "$net" -p udp --dport "$port" -j ACCEPT || die "cannot allow $entry"
  else
    rule "$net" -A OUTPUT -d "$net" -j ACCEPT || die "cannot allow $entry"
  fi
done

# Everything else that is not the internet.
for net in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 \
           172.16.0.0/12 192.0.0.0/24 192.168.0.0/16 198.18.0.0/15 \
           224.0.0.0/4 240.0.0.0/4; do
  iptables -A OUTPUT -d "$net" -j REJECT
done
if [ "$V6" = yes ]; then
  for net in ::1/128 fc00::/7 fe80::/10 ff00::/8 ::ffff:0:0/96 64:ff9b::/96; do
    ip6tables -A OUTPUT -d "$net" -j REJECT
  done
fi

# ssh looks itself up by uid and gives up when there is no entry.
if ! awk -F: -v u="$BOX_UID" '$3 == u { found = 1 } END { exit !found }' /etc/passwd; then
  echo "claude:x:$BOX_UID:$BOX_GID:Claude:${HOME:-/home/claude}:/bin/sh" >> /etc/passwd
fi
if ! awk -F: -v g="$BOX_GID" '$3 == g { found = 1 } END { exit !found }' /etc/group; then
  echo "claude:x:$BOX_GID:" >> /etc/group
fi

exec setpriv --reuid="$BOX_UID" --regid="$BOX_GID" --clear-groups \
  --inh-caps=-all --bounding-set=-all --no-new-privs -- "$@"
