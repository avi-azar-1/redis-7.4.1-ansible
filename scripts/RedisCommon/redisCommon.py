import subprocess
import redis
import time
from redis.cluster import RedisCluster as Cluster
import socket
from enum import Enum

FIND_REDIS_PROCESSES = "ps -ef | grep redis-server | grep -v grep | awk -F':' '{print $NF}' | sort | awk '{print $1}'"
GET_PASSWORD = 'cat /etc/redis/{}.conf | grep requirepass'
GET_CONF_MAXMEM = 'egrep -i "maxmemory [1-9]" /etc/redis/*.conf 2>/dev/null'
HOSTNAME = 'hostname'
IP = "hostname -I | awk '{print $1}' "
FREE_MEMORY = 'free --bytes'
STOP_SERVICE = 'systemctl stop {}'
START_SERVICE = 'systemctl start {}'
REDIS_SERVICE = 'redis_{}'
SENTINEL_PORT = 26379
POLL_WAIT_SEC = 0.1
POLL_PRINT_FREQUANCY = 100
noDns = False


def ip_to_hostname(ip):
    if noDns:
        return ip
    try:
        return socket.gethostbyaddr(ip)[0].split('.')[0]
    except:
        # dns resolution failure
        return ip


def runBash(command, multi=False):
    if not multi:
        return subprocess.run(command, shell=True, capture_output=True, text=True).stdout
    processes = [subprocess.Popen(cmd, shell=True, text=True)
                 for cmd in command]
    for p in processes:
        p.wait()
    return [p.stdout for p in processes]


def getLocalhost():
    return runBash(HOSTNAME).strip()


def getLocalIP():
    return runBash(IP).strip()


def getServerRam():
    server_ram = runBash(FREE_MEMORY).splitlines()
    server_ram = int(server_ram[1].split()[1])
    return server_ram


class RedisMode(Enum):
    MASTER_SLAVE = "Master-Slave"
    STANDALONE = "Standalone"
    CLUSTER = "Cluster"


class RedisInstance:
    def __init__(self, hostname, ip, isLocalhost=False):
        self.isLocalhost = isLocalhost
        self.hostname = hostname
        self.ip = ip
        self.ports = self.gatherPortsList()
        if len(self.ports) == 0:
            raise ValueError("Error: no redises on server")
        self.version = self.getVersion()
        self.mode = self.determineRedisMode()
        if self.isLocalhost:
            self.connectedServers = self.gatherConnectedServers()
            if self.mode == RedisMode.MASTER_SLAVE:
                self.sentinels = self.getSentinelNodes()

    def redisConnection(self, port):
        try:
            passwrd = self.getPassword(port)
            return redis.Redis(host=self.hostname, port=port, password=passwrd)
        except redis.RedisError:
            print(f"Error: could not connect to redis {self.hostname}:{port}")
            exit(1)

    def redisClusterConnection(self, port):
        try:
            passwrd = self.getPassword(port)
            return Cluster(host=self.hostname, port=port, password=passwrd)
        except redis.RedisError:
            print(f"Error: could not connect to redis {self.hostname}:{port}")
            exit(1)

    def redisSentinelConnection(self):
        try:
            return redis.Redis(host=self.sentinels[0], port=SENTINEL_PORT)
        except redis.RedisError:
            print(
                f"Error: could not connect to redis sentinel {self.sentinels[0]}:{SENTINEL_PORT}")
            exit(1)

    def getSentinelMasterName(self, port):
        info = self.getInfo(port)
        masterIP = self.ip if info['role'] == 'master' else info['master_host']
        snt = self.redisSentinelConnection()
        masters = snt.execute_command('sentinel masters')
        for master in masters.values():
            if str(master['port']) == port and master['ip'] == masterIP:
                return master['name']

    def failoverRedis(self, port):
        if self.mode == RedisMode.MASTER_SLAVE:
            masterName = self.getSentinelMasterName(port)
            snt = self.redisSentinelConnection()
            return snt.execute_command(f'sentinel failover {masterName}')
        elif self.mode == RedisMode.CLUSTER:
            slaveHost, slavePort = self.getClusterSlaveForNode(port)
            for server in self.connectedServers:
                if server.hostname == slaveHost:
                    rds = server.redisConnection(slavePort)
                    return rds.execute_command('cluster failover')

    def getClusterNodes(self):
        # assumes there is only one cluster on server
        port = self.ports[0]
        nodes = list(
            set([conn.host for conn in self.redisClusterConnection(port).get_nodes()]))
        nodes = sorted([(ip_to_hostname(ip), ip) for ip in nodes])

        return [RedisInstance(hostname=node[0], ip=node[1]) for node in nodes]

    def getMasterSlaveNodes(self):
        # assumes all ports use the same two master-slave servers
        info = self.getInfo(self.ports[0])
        host = info['slave0']['ip'] if info['role'] == 'master' else info['master_host']
        return [self, RedisInstance(ip_to_hostname(host), host)]

    def getClusterSlaveForNode(self, port):
        # assumes this function is called on a cluster node with a replica
        info = self.getInfo(port)
        slavePort = info['slave0']['port']
        slaveHost = ip_to_hostname(info['slave0']['ip'])
        return slaveHost, slavePort

    def getSentinelNodes(self):
        # assumes all ports use the same three master-slave servers
        clientList = self.getClientList(self.ports[0])
        sentinelServers = []
        for client in clientList:
            if 'sentinel' in client['name']:
                server = ip_to_hostname(client['addr'].split(':')[0])
                if server not in sentinelServers:
                    sentinelServers.append(server)
        sentinelServers.sort()
        return sentinelServers

    def gatherConnectedServers(self):
        if self.mode == RedisMode.STANDALONE:
            return [RedisInstance(self.hostname, self.ip)]
        elif self.mode == RedisMode.MASTER_SLAVE:
            return self.getMasterSlaveNodes()
        elif self.mode == RedisMode.CLUSTER:
            return self.getClusterNodes()

    def determineRedisMode(self):
        info = self.getInfo(self.ports[0])
        if info['cluster_enabled'] == 1:
            return RedisMode.CLUSTER
        elif info['role'] == 'slave':
            return RedisMode.MASTER_SLAVE
        elif info['connected_slaves'] == 0:
            return RedisMode.STANDALONE
        else:
            return RedisMode.MASTER_SLAVE

    def getPassword(self, port):
        return runBash(GET_PASSWORD.format(port)).strip().split()[-1].replace('"', '')

    def getInfo(self, port):
        return self.redisConnection(port).info()

    def getVersion(self):
        port = self.ports[0]
        return self.getInfo(port)['redis_version']

    def getClientList(self, port):
        return self.redisConnection(port).client_list()

    def gatherPortsList(self):
        return list(filter(None, runBash(FIND_REDIS_PROCESSES).split('\n')))

    def getServerMemoryUsage(self):
        total_memory_used_bytes = 0
        byte_modifiers = {'g': 10**9, 'gb': 1024**3, 'm': 10**6,
                          'mb': 1024**2, 'k': 10**3, 'kb': 1024, 'b': 1, '': 1}
        conf_maxmem = runBash(GET_CONF_MAXMEM).splitlines()
        if not conf_maxmem or conf_maxmem == '':
            return 0
        for line in conf_maxmem:
            line = line.split(' ')[1].lower()
            num = int("".join(i for i in line if i.isdigit()))
            unit = "".join(i for i in line if i.isalpha())
            total_memory_used_bytes += num*byte_modifiers[unit]
        return total_memory_used_bytes

    def gatherInstanceStatusInfo(self, port):
        info = self.getInfo(port)
        instance = {}
        instance['server'] = self.hostname
        instance['port'] = port
        instance['memory_used'] = int(info['used_memory'])
        instance['maxmemory'] = int(info['maxmemory'])
        instance['memory_percent'] = (
            100*instance['memory_used']/instance['maxmemory'])
        instance['used_memory_peak'] = int(info['used_memory_peak'])
        instance['mem_peak_percent'] = (
            100*instance['used_memory_peak']/instance['maxmemory'])
        instance['role'] = info['role']
        instance['clients'] = int(info['connected_clients'])
        instance['ops_per_sec'] = int(info['instantaneous_ops_per_sec'])
        try:
            instance['keys'] = int(info['db0']['keys'])
        except:
            instance['keys'] = 0

        return instance

    def isMaster(self, port):
        info = self.getInfo(port)
        return (info['role'] == 'master' and info['connected_slaves'] > 0)

    def isSlave(self, port):
        info = self.getInfo(port)
        return (info['role'] == 'slave')

    def saveRDB(self, port):
        rds = self.redisConnection(port)
        rds.bgsave()
        pollCount = 0
        while True:
            if rds.info()['rdb_bgsave_in_progress'] == 0:
                break
            time.sleep(POLL_WAIT_SEC)
            pollCount += 1
            if pollCount == POLL_PRINT_FREQUANCY:
                print(port, rds.info()['current_fork_perc'], '%')
                pollCount = 0

    def stopRedis(self, port):
        service = REDIS_SERVICE.format(port)
        runBash(STOP_SERVICE.format(service))

    def startRedis(self, port):
        service = REDIS_SERVICE.format(port)
        runBash(START_SERVICE.format(service))

    def startRedisMulti(self, ports):
        cmds = []
        for port in ports:
            service = REDIS_SERVICE.format(port)
            cmds.append(START_SERVICE.format(service))
        runBash(cmds, multi=True)

    def stopRedisMulti(self, ports):
        cmds = []
        for port in ports:
            service = REDIS_SERVICE.format(port)
            cmds.append(STOP_SERVICE.format(service))
        runBash(cmds, multi=True)
