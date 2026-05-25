#!/usr/bin/env python
# A new and improved status script for Python 3.11
# SSH changed to use Redis python client
import difflib
from operator import itemgetter
import argparse
import colors
from prettytable import PrettyTable
from concurrent.futures import ThreadPoolExecutor
import sys
from pathlib import Path
upperDirPath = Path(__file__).resolve().parent.parent
sys.path.append(str(upperDirPath))
import RedisCommon.redisCommon as common  # noqa: E402


ptTable = PrettyTable()
ptTable.field_names = ["Server", "Port", "Memory Used", "Memory Percent",
                       "Mem Peak Percent", "keys", "Role", "Clients", "ops per Sec"]
instance_list = []
run_list = []

SORT_SIMILARITY = 0.3  # see difflib cutoff parameter
BYTES_TO_GB = 1024 * 1024 * 1024
CURRENT_VERSION = '8.6.3'


def printLogo():
    print(colors.BANNER)


def processArgs():
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--ports', nargs='+',
                        help='Specify ports to check')
    parser.add_argument('-l', '--local', action="store_true",
                        help='show only redises on local server')
    parser.add_argument('-s', '--sort', nargs='+', help='sort by colomn\s')
    return parser.parse_args()


def addInstanceInfoToTable(instance):
    matchingColorMemory = colors.getMatchingColor(
        instance['memory_percent'], 75, 90)
    ptTable.add_row([
        colors.colorString(instance['server'], colors.PINK),
        instance['port'],
        colors.colorString("%.2f" % (instance['memory_used']/BYTES_TO_GB), matchingColorMemory) +
        '/' + "%.2f" % (instance['maxmemory']/BYTES_TO_GB) + " GB",
        colors.colorString(
            "%.1f" % instance['memory_percent'] + '%', matchingColorMemory),
        colors.colorString("%.1f" %
                           (instance['mem_peak_percent'])+'%', colors.CYAN),
        colors.colorString("{:,}".format(instance['keys']), colors.PINK),
        colors.colorString(
            instance['role'], colors.getRoleColor(instance['role'])),
        "{:,}".format(instance['clients']),
        colors.colorString("{:,}".format(instance['ops_per_sec']), colors.PINK)
    ])


def sortInstanceList(sort_fields):
    possible_fields = ptTable.field_names
    corrected_sort_fields = []
    for field in sort_fields:
        if field not in possible_fields:
            field_new = difflib.get_close_matches(
                field, possible_fields, n=1, cutoff=SORT_SIMILARITY)
            if field_new == []:
                continue
            print(f"{field} => {field_new[0]}")
            field = field_new[0]

        corrected_sort_fields.append(field.lower().replace(" ", "_"))
    if len(corrected_sort_fields) != 0:
        instance_list.sort(key=itemgetter(*corrected_sort_fields))


def runStatus():
    printLogo()
    args = processArgs()
    input_ports = args.ports
    local_only = args.local
    sort_fields = args.sort
    localInstance = common.RedisInstance(
        hostname=common.getLocalhost(), ip=common.getLocalIP(), isLocalhost=True)
    serverMemoryUsage = "%.1f" % (
        localInstance.getServerMemoryUsage()/BYTES_TO_GB)
    serverRam = "%.1f" % (common.getServerRam()/BYTES_TO_GB)
    serverRamPercent = "%.1f" % (
        localInstance.getServerMemoryUsage()*100/common.getServerRam())
    version = localInstance.getVersion()
    matchingColorVersion = colors.GREEN if version == CURRENT_VERSION else colors.RED
    print('Redis Mode: ' + localInstance.mode.value)
    print('Redis Version: ' + colors.colorString(version, matchingColorVersion))
    print(
        f'allocated memory: {serverMemoryUsage}gb out of {serverRam}gb ({serverRamPercent}%)')
    for server in localInstance.connectedServers:
        if local_only and server.hostname != localInstance.hostname:
            continue
        for port in server.ports:
            if not input_ports or port in input_ports:
                run_list.append((server, port))
    # run all info commands in parallel
    with ThreadPoolExecutor() as executor:
        result = list(executor.map(
            lambda a: a[0].gatherInstanceStatusInfo(a[1]), run_list))
        instance_list.extend(result)
    if sort_fields:
        sortInstanceList(sort_fields)
    for instance in instance_list:
        addInstanceInfoToTable(instance)
    print(ptTable)


try:
    runStatus()
except Exception as e:
    print(colors.colorString(f"An error occurred: {e}", colors.FAIL))
