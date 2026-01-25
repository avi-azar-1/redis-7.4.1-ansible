import argparse
from prettytable import PrettyTable
import sys
from pathlib import Path
upperDirPath = Path(__file__).resolve().parent.parent
sys.path.append(str(upperDirPath))
import RedisCommon.redisCommon as common  # noqa: E402

DEFAULT_MEMORY_ADDED_MB = 100
SERVER_DEFAULT_MAX_PERCENT = 80
SERVER_WARNING_MAX_PERCENT = 90
BYTES_TO_MB = 1024 * 1024
MIN_MAXMEMORY_MB = 50
BANNER = """
  ____          _ _          _       _     _   __  __                                 
 |  _ \ ___  __| (_)___     / \   __| | __| | |  \/  | ___ _ __ ___   ___  _ __ _   _ 
 | |_) / _ \/ _` | / __|   / _ \ / _` |/ _` | | |\/| |/ _ \ '_ ` _ \ / _ \| '__| | | |
 |  _ |  __/ (_| | \__ \  / ___ \ (_| | (_| | | |  | |  __/ | | | | | (_) | |  | |_| |
 |_| \_\___|\__,_|_|___/ /_/   \_\__,_|\__,_| |_|  |_|\___|_| |_| |_|\___/|_|   \__, |
                                                                                |___/ 
"""


def printOtherSiteMsg(redis_host):
    if redis_host[0] == 'n':
        return
    elif redis_host[4] == 'm' or redis_host[4] == 'M':
        redis__other_host = redis_host[:4] + "t" + redis_host[5:]
        print()
        print(
            f"notice: please run the same command to change memory on {redis__other_host} (metzuda)")
    elif redis_host[4] == 't' or redis_host[4] == 'T':
        redis__other_host = redis_host[:4] + "m" + redis_host[5:]
        print()
        print(
            f"notice: please run the same command to change memory on {redis__other_host} (mate)")
    else:
        print()
        print(
            "notice: production server not named according to naming standard mate/metzuda")


def printMemory(mem):
    mem = int(mem)
    if mem > 1024 or mem < -1024:
        return str(round(mem/1024, 2))+" GB"
    else:
        return str(mem)+" MB"


def processArgs():
    parser = argparse.ArgumentParser(description='increase redis memory')
    parser.add_argument('--port', '-p', required=True, help='redis port')
    parser.add_argument('--threshold', '-t', default=SERVER_DEFAULT_MAX_PERCENT,
                        help='max percent of server ram allocated (change with caution!!!)')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--addMb', '--add', '-a',
                       default=DEFAULT_MEMORY_ADDED_MB, help='Redis memory added (in mb)')
    group.add_argument('--exactMb', '--exact', '-e',
                       help='Redis exact new maxmemory (in mb)')
    return parser.parse_args()


def checkThreshold(threshold):
    if threshold >= 100:
        print("Error: threshold for server ram cannot be more then 100%")
        print()
        exit(1)
    if threshold >= SERVER_WARNING_MAX_PERCENT:
        print("WARNING: you are allocating for 90% or more of server ram!!!")
        print("make double sure server still has reasonable free ram for os or server will crash!")
        print()


def printHostNodeInfo(localInstance, updated_ports):
    all_hosts = []
    all_nodes = []
    for server in localInstance.connectedServers:
        all_hosts.append(server.hostname)
        for port in updated_ports:
            all_nodes.append((server.hostname, port))

    print(*(sorted(all_hosts)))
    nodePerServer = int(len(all_nodes)/len(all_hosts))
    if localInstance.mode == common.RedisMode.CLUSTER:
        print(
            f"{len(all_nodes)} nodes on {len(all_hosts)} servers: {nodePerServer} nodes per server")


def printMemoryInfo(port, currentMaxmemory, addedMemory, newMaxmemory, memoryAddable, nodePerServer):
    ptTable = PrettyTable()
    ptTable.field_names = ["port ", "current maxmemory", "memory added",
                           "new maxmemory", "memory remaining for allocation"]

    ptTable.add_row([
        port,
        printMemory(currentMaxmemory),
        printMemory(addedMemory),
        printMemory(newMaxmemory),
        printMemory(memoryAddable-(addedMemory*nodePerServer))
    ])
    print()
    print(ptTable)
    print()


def checkRequestedMemoryValid(mode, currentMaxmemory, addedMemory, nodePerServer, memoryAddable, server_memory_max_percent):
    if addedMemory*nodePerServer > memoryAddable:
        print(
            f"Error: Not enough memory, more than {server_memory_max_percent}% of RAM")
        print(f"you can only add: {printMemory(memoryAddable)}")
        if mode == common.RedisMode.CLUSTER:
            print(
                f"meaning: {printMemory(int(memoryAddable/nodePerServer))} per node")
        exit(1)

    if currentMaxmemory+addedMemory < MIN_MAXMEMORY_MB:
        print(f"Error: maxmemory should not be less than {MIN_MAXMEMORY_MB}mb")
        exit(1)


def addMemory(localInstance, updated_ports, newMaxmemory):
    for server in localInstance.connectedServers:
        for port in updated_ports:
            rds = server.redisConnection(port)
            rds.config_set('maxmemory', str(newMaxmemory)+'mb')
            rds.config_rewrite()
            print(
                f"new maxmemory set for {server.hostname}:{port} - {printMemory(newMaxmemory)}")


def main():
    print(BANNER)
    args = processArgs()

    server_memory_max_percent = int(args.threshold)
    checkThreshold(server_memory_max_percent)

    localInstance = common.RedisInstance(
        hostname=common.getLocalhost(), ip=common.getLocalIP(), isLocalhost=True)

    if args.port not in localInstance.ports:
        print(f"Error: port {args.port} does not exist on server")
        exit(1)

    match localInstance.mode:
        case common.RedisMode.CLUSTER:
            # assume all cluster servers use all ports on server and all ports are identical on all servers
            updated_ports = localInstance.ports
            print("production cluster, changing for servers:")

        case common.RedisMode.MASTER_SLAVE:
            # assume both master and slave use the same port
            updated_ports = [args.port]
            print("production master-slave, changing for servers:")

        case common.RedisMode.STANDALONE:
            updated_ports = [args.port]
            print("test enviroment, changing for single server:")

    printHostNodeInfo(localInstance, updated_ports)
    nodePerServer = len(updated_ports)

    currentMaxmemory = int(localInstance.getInfo(args.port)[
        'maxmemory']) // BYTES_TO_MB
    if args.exactMb:
        addedMemory = int(args.exactMb) - currentMaxmemory
    else:
        addedMemory = int(args.addMb)
    newMaxmemory = currentMaxmemory+addedMemory

    serverTotalAllocated = localInstance.getServerMemoryUsage() // BYTES_TO_MB
    serverTotalRam = common.getServerRam() // BYTES_TO_MB
    memoryAddable = serverTotalRam * \
        (server_memory_max_percent/100) - serverTotalAllocated

    printMemoryInfo(args.port, currentMaxmemory, addedMemory,
                    newMaxmemory, memoryAddable, nodePerServer)

    checkRequestedMemoryValid(localInstance.mode, currentMaxmemory,
                              addedMemory, nodePerServer, memoryAddable, server_memory_max_percent)

    answer = input("are you sure you want to continue? (y/n) ")
    if answer not in ('y', 'Y', 'YES', 'yes', 'Yes'):
        print("exiting...")
        exit(0)

    addMemory(localInstance, updated_ports, newMaxmemory)

    printOtherSiteMsg(localInstance.hostname)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"An error occurred: {e}")
