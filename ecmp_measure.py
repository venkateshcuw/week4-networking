#!/usr/bin/env python3
"""ecmp_measure.py - Experiment 4 (CSC 6410): how evenly does ECMP spread flows over the
four core links that leave pod 0 of the k = 4 fat-tree built by fattree_ecmp.py?

>>> REWRITE THESE COMMENTS IN YOUR OWN WORDS BEFORE YOU SUBMIT (the assignment requires it). <<<

PURPOSE
  Read the transmit byte counter of each pod-0 core-facing port (a00-eth3 -> c00, a00-eth4 -> c01,
  a01-eth3 -> c10, a01-eth4 -> c11) before and after a traffic run, and print the bytes, the share
  of the total, an estimate of the number of flows, the busiest share and max/mean per link.

LOGIC
  Counters never reset, so every result is a difference: after - before.
  share_i   = bytes_i / sum(bytes)
  max/mean  = max(bytes) / (sum(bytes) / 4)            (1.00 = perfectly even, 4.00 = all on one link)
  flows_i  ~= bytes_i / (sum(bytes) / N)               (valid because every flow is fixed-rate UDP)

TWO WAYS TO USE IT
  A) By hand, from the Mininet CLI of the starter (good for the screenshots):
       mininet> sh python3 ecmp_measure.py before
       mininet> h300 iperf -s -u &
       mininet> h000 iperf -c 10.3.0.2 -u -b 1M -P 16 -t 20
       mininet> sh python3 ecmp_measure.py after --n 16 --label N16-run1
  B) Automatic: builds the fat-tree itself (imports the starter) and does N = 4, 8, 16, 32 x 3 runs
     plus the a00-c00 link-failure run, writing everything to ecmp_raw.txt and ecmp_results.csv:
       python3 ecmp_measure.py auto            (add --hash for the seeded OpenFlow 1.5 hash, --cli to stay in the CLI)

ASSUMPTIONS
  * Run as root in the lab container (or sudo -E in WSL). Mininet switch ports live in the root
    network namespace, so /sys/class/net/<port>/statistics/tx_bytes is readable from here.
  * Hosts use static ARP (the starter calls net.staticArp()) and there is no controller, so no ARP or
    LLDP should cross the core; any residual noise is measured by the 'noise' command below.

TESTING
  'noise' takes two readings 10 s apart with no traffic: the difference is the background bytes.
  'read' prints the raw counters and cross-checks them against ovs-ofctl dump-ports.

LIMITS
  Measures only pod 0's uplinks (the h000 -> h300 direction). Byte shares equal flow shares only for
  equal fixed-rate flows. Each run's split depends on the random source ports iperf gets.

FALLBACK
  If /sys/class/net is not readable, the counters are taken from 'ovs-ofctl -O OpenFlow13 dump-ports'.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

# (switch, OpenFlow port, interface name, core switch it leads to)
CORE_LINKS = [('a00', 3, 'a00-eth3', 'c00'), ('a00', 4, 'a00-eth4', 'c01'),
              ('a01', 3, 'a01-eth3', 'c10'), ('a01', 4, 'a01-eth4', 'c11')]
HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(HERE, 'ecmp_before.json')
RESULTS = os.path.join(HERE, 'ecmp_results.csv')
RAW = os.path.join(HERE, 'ecmp_raw.txt')


# ---------- reading counters ----------
def tx_bytes_sysfs(iface):
    with open(f'/sys/class/net/{iface}/statistics/tx_bytes') as f:
        return int(f.read())


def tx_bytes_ofctl(switch, port):
    out = subprocess.run(['ovs-ofctl', '-O', 'OpenFlow13', 'dump-ports', switch, str(port)],
                         capture_output=True, text=True).stdout
    m = re.search(r'tx pkts=\d+, bytes=(\d+)', out)
    if not m:
        raise RuntimeError(f'cannot parse dump-ports {switch} {port}:\n{out}')
    return int(m.group(1))


def read_counters():
    """Return {interface: tx_bytes} for the four core links; sysfs first, ovs-ofctl as fallback."""
    out = {}
    for sw, port, iface, _ in CORE_LINKS:
        try:
            out[iface] = tx_bytes_sysfs(iface)
        except OSError:
            out[iface] = tx_bytes_ofctl(sw, port)
    return out


# ---------- computing and printing ----------
def report(before, after, n=None, label='', log=None):
    diff = {iface: after[iface] - before[iface] for _, _, iface, _ in CORE_LINKS}
    total = sum(diff.values())
    lines = [f'=== {label or "run"}  N={n if n else "?"}  {datetime.now():%Y-%m-%d %H:%M:%S}',
             f'{"link":<10}{"to":<5}{"tx bytes":>14}{"share":>9}{"~flows":>9}']
    row = {'label': label, 'N': n or '', 'time': f'{datetime.now():%H:%M:%S}'}
    for _, _, iface, core in CORE_LINKS:
        share = diff[iface] / total if total else 0.0
        flows = diff[iface] / (total / n) if (total and n) else float('nan')
        lines.append(f'{iface:<10}{core:<5}{diff[iface]:>14,}{share:>9.1%}{flows:>9.1f}')
        row[f'{iface}_bytes'] = diff[iface]
        row[f'{iface}_share'] = round(share, 4)
    busiest = max(diff.values()) / total if total else 0.0
    maxmean = max(diff.values()) / (total / 4) if total else 0.0
    lines.append(f'{"total":<15}{total:>14,}   busiest share {busiest:.1%}   max/mean {maxmean:.2f}')
    row.update(total=total, busiest_share=round(busiest, 4), max_mean=round(maxmean, 3))
    text = '\n'.join(lines)
    print(text)
    if log:
        log.write(text + '\n\n')
    new = not os.path.exists(RESULTS)
    with open(RESULTS, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    return row


# ---------- mode A: by hand ----------
def cmd_before(_):
    c = read_counters()
    with open(SNAPSHOT, 'w') as f:
        json.dump(c, f)
    print('before:', c)


def cmd_after(a):
    with open(SNAPSHOT) as f:
        before = json.load(f)
    after = read_counters()
    with open(RAW, 'a') as log:
        log.write(f'before {before}\nafter  {after}\n')
        report(before, after, a.n, a.label, log)


def cmd_read(_):
    for sw, port, iface, core in CORE_LINKS:
        try:
            s = tx_bytes_sysfs(iface)
        except OSError:
            s = None
        print(f'{iface:<10}-> {core}   sysfs tx_bytes={s}   ovs-ofctl tx bytes={tx_bytes_ofctl(sw, port)}')


def cmd_noise(a):
    b = read_counters()
    time.sleep(a.seconds)
    c = read_counters()
    print(f'background bytes in {a.seconds} s with no traffic:',
          {k: c[k] - b[k] for k in c})


# ---------- mode B: automatic, via the Mininet API ----------
def cmd_auto(a):
    sys.path.insert(0, HERE)
    from functools import partial
    from mininet.net import Mininet
    from mininet.link import Link
    from mininet.cli import CLI
    from mininet.log import setLogLevel
    import fattree_ecmp as ft                     # the course starter, unchanged

    setLogLevel('warning')
    net = Mininet(topo=ft.FatTree(), controller=None, autoSetMacs=True, link=Link,
                  switch=partial(ft.SWITCH, failMode='secure', protocols='OpenFlow13,OpenFlow15'))
    net.start()
    net.staticArp()
    ft.install_rules(net, a.hash)
    h000, h300 = net.get('h000', 'h300')
    log = open(RAW, 'a')
    log.write(f'##### auto run {datetime.now()}  MN_SWITCH={ft.MN_SWITCH}  hash={a.hash}\n')
    log.write(net.get('a00').cmd('ovs-ofctl -O OpenFlow13 dump-groups a00') + '\n')
    print('pingall loss %:', net.pingAll(timeout=1))
    h300.cmd('iperf -s -u > /tmp/iperf_server.log 2>&1 &')
    time.sleep(1)

    def one_run(n, label):
        before = read_counters()
        out = h000.cmd(f'iperf -c 10.3.0.2 -u -b 1M -P {n} -t {a.t}')
        after = read_counters()
        log.write(f'--- {label}: iperf -c 10.3.0.2 -u -b 1M -P {n} -t {a.t}\n{out}\n'
                  f'before {before}\nafter  {after}\n')
        report(before, after, n, label, log)

    try:
        for n in a.ns:
            for r in range(1, a.runs + 1):
                one_run(n, f'N{n}-run{r}{"-hash" if a.hash else ""}')
        if not a.no_fail:
            net.configLinkStatus('a00', 'c00', 'down')
            for r in range(1, a.runs + 1):
                one_run(16, f'N16-a00c00down-run{r}')
            net.configLinkStatus('a00', 'c00', 'up')
        if a.cli:
            CLI(net)
    finally:
        h300.cmd('kill %iperf')
        log.close()
        net.stop()
    print(f'\nraw output -> {RAW}\ntable      -> {RESULTS}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('before', help='save a counter snapshot').set_defaults(f=cmd_before)
    p = sub.add_parser('after', help='read again, print bytes/share vs the snapshot')
    p.add_argument('--n', type=int, help='number of flows in this run')
    p.add_argument('--label', default='', help='name of the run, e.g. N16-run1')
    p.set_defaults(f=cmd_after)
    sub.add_parser('read', help='print raw counters (sysfs and ovs-ofctl)').set_defaults(f=cmd_read)
    p = sub.add_parser('noise', help='background bytes with no traffic')
    p.add_argument('--seconds', type=int, default=10)
    p.set_defaults(f=cmd_noise)
    p = sub.add_parser('auto', help='build the fat-tree and run everything')
    p.add_argument('--ns', type=int, nargs='+', default=[4, 8, 16, 32])
    p.add_argument('--runs', type=int, default=3)
    p.add_argument('--t', type=int, default=20, help='iperf seconds per run')
    p.add_argument('--hash', action='store_true', help='use the seeded OpenFlow 1.5 hash')
    p.add_argument('--no-fail', action='store_true', help='skip the link-failure runs')
    p.add_argument('--cli', action='store_true', help='open the Mininet CLI at the end')
    p.set_defaults(f=cmd_auto)
    a = ap.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()
