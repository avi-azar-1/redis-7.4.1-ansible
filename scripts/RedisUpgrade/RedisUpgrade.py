import argparse
import time
import sys
import shutil
from pathlib import Path
upperDirPath = Path(__file__).resolve().parent.parent
sys.path.append(str(upperDirPath))
import RedisCommon.redisCommon as common  # noqa: E402

BINARIES_DIR = 'bin'
LIBRARIES_DIR = 'lib'
REDIS_SOFTWARE_DIR = '/redis/software'
RUNNING_BINARIES_PATH = '/usr/local/sbin'
RUNNING_LIBRARIES_PATH = '/usr/local/lib'
REQUIRED_BINARIES = ['redis-cli', 'redis-sentinel', 'redis-server']
CHECK_VERSION = '{} --version; echo $?'
IDLE_WINDOW = 60
IGNORED_COMMANDS = ('ping', 'info', 'client|list',
                    'replconf', 'unsubscribe', 'cluster|nodes', 'command')
APPLICATION_RECONNECT_TIME = 10
RELOAD_SERVICE = 'systemctl daemon-reload'
SERVICE_LOCATION = '/usr/lib/systemd/system/{}.service'
SED_REPLACE = 'sed -i \'s/{}/{}/g\' {}'


def processArgs():
    parser = argparse.ArgumentParser(
        description='upgrade redis instances on this server')
    parser.add_argument('--path', '-p', required=True,
                        help='path to new redis software')
    parser.add_argument('--dryrun', '--dry', '-d', action="store_true",
                        help='dry run only, run checks and master/slave switch without upgrading')
    return parser.parse_args()


def checkDirectoryValid(path):
    path = Path(path)
    if not path.exists():
        print(f'error: software directory path \'{path}\' does not exist')
        exit(1)
    if not (path / BINARIES_DIR).exists():
        print(
            f'error: software directory path \'{path}\' does not contain {BINARIES_DIR} directory')
        exit(1)
    if not (path / LIBRARIES_DIR).exists():
        print(
            f'error: software directory path \'{path}\' does not contain {LIBRARIES_DIR} directory')
        exit(1)


def checkBinariesValid(path):
    path = Path(path) / BINARIES_DIR

    for binary in REQUIRED_BINARIES:
        if not (path / binary).exists():
            print(
                f'error: software directory \'{path}\' does not contain required {binary} binary')
            exit(1)

        fullBinaryPath = (path / binary).resolve()
        # check if redis binary even runs on the current os- if not return non zero value
        returnCode = int(common.runBash(
            CHECK_VERSION.format(fullBinaryPath)).splitlines()[1])
        if returnCode != 0:
            print(
                f'error: the server rhel version is too old for the given redis software')
            print('you can see the error yourself by running:')
            print(CHECK_VERSION.format(fullBinaryPath))
            exit(1)


def checkBinariesNewVersion(path):
    fullBinaryPath = (Path(path) / BINARIES_DIR / 'redis-server').resolve()
    version = common.runBash(CHECK_VERSION.format(
        fullBinaryPath)).splitlines()[0]
    # from output 'Redis server v=7.4.6 ...' -> extract second part of third word
    version = version.split()[2].split('=')[1]
    return version


def backupRedisBinaries(path, current_version, new_version):
    backupDir = Path(REDIS_SOFTWARE_DIR)
    newSoftwarePath = Path(path).resolve()
    runningSoftwarePath = Path(RUNNING_BINARIES_PATH)
    runningLibsPath = Path(RUNNING_LIBRARIES_PATH)

    # copy new software to new version folder
    (backupDir / new_version).mkdir(mode=755, parents=True, exist_ok=True)
    shutil.copytree(newSoftwarePath, backupDir /
                    new_version, dirs_exist_ok=True)
    print(f'copied new redis software to {backupDir / new_version}')

    # copy existing software to current version folder
    (backupDir / current_version /
     BINARIES_DIR).mkdir(mode=755, parents=True, exist_ok=True)
    shutil.copytree(runningSoftwarePath, backupDir /
                    current_version / BINARIES_DIR, dirs_exist_ok=True)
    (backupDir / current_version /
     LIBRARIES_DIR).mkdir(mode=755, parents=True, exist_ok=True)
    shutil.copytree(runningLibsPath, backupDir /
                    current_version / LIBRARIES_DIR, dirs_exist_ok=True)
    print(f'copied current redis software to {backupDir / current_version}')


def switchRedisBinaries(path):
    newSoftwarePath = Path(path).resolve()
    runningSoftwarePath = Path(RUNNING_BINARIES_PATH)
    runningLibsPath = Path(RUNNING_LIBRARIES_PATH)

    # copy new software to running folders
    shutil.copytree(newSoftwarePath / BINARIES_DIR,
                    runningSoftwarePath, dirs_exist_ok=True)
    shutil.copytree(newSoftwarePath / LIBRARIES_DIR,
                    runningLibsPath, dirs_exist_ok=True)
    print(
        f'switched new redis software into {runningSoftwarePath} and {runningLibsPath}')


def printStaticInfo(current_version, new_version, mode, dryRun):
    # static info - version and redis mode
    print(f"the current version is: {current_version}")
    if not dryRun:
        print(f"the new version is:     {new_version}")
    else:
        print("dry run - no new version installed")

    print("\nrunning upgrade procedure for:")
    match mode:
        case common.RedisMode.CLUSTER:
            print("cluster server")
            print("upgrade will restart only cluster slave redises")
        case common.RedisMode.MASTER_SLAVE:
            print("master-slave server")
            print("upgrade will restart only slave redises")
        case common.RedisMode.STANDALONE:
            print("standalone server")
            print("upgrade will restart all active redises")


def getActiveConnections(instance, port):
    clist = instance.getClientList(port)
    activeConnection = []
    for connection in clist:
        if int(connection['idle']) < IDLE_WINDOW and connection['cmd'] not in IGNORED_COMMANDS:
            ip = connection['addr'].split(':')[0]
            if 'sentinel' in connection['name'] or 'M' in connection['flags']:
                continue
            activeConnection.append((connection['cmd'], ip))
    return activeConnection


def handleMasterFailovers(instance):
    masters = []
    noSlaves = False
    for port in instance.ports:
        if instance.isMaster(port):
            masters.append(port)
        elif not instance.isSlave(port):
            noSlaves = True
            break

    if noSlaves:
        print("no connected slaves found - cannot enslave redises, skipping")
    elif len(masters) == 0:
        print("all redises on server are already slaves, continuing")
    else:
        print("the following masters with active slaves will failover to other server:")
        print(' '.join(masters))
        confirmProceed()

        for port in masters:
            result = str(instance.failoverRedis(port))
            print(f'{instance.hostname}:{port} returned: {result}')
            if result not in ('ok', 'True', True):
                print("error during failover, exiting...")
                exit(1)

        print(f"waiting {APPLICATION_RECONNECT_TIME} seconds for reconnect...")
        time.sleep(APPLICATION_RECONNECT_TIME)


def slaveUsageInfo(instance):
    # assumes all redises on server have been unslaved beforehand
    unusedSlaves = []
    for port in instance.ports:
        if instance.isSlave(port):
            activeconns = getActiveConnections(instance, port)
            cmds = set(cmd for cmd, server in activeconns)
            if len(cmds) > 0:
                print(
                    f'WARNING: slave {instance.hostname}:{port} is used by application to run commands: {cmds}')
            else:
                unusedSlaves.append(port)
        # not slave - standalone/ cluster standalone scenario
        else:
            print("no slaves - nothing to check")
            return
    if len(unusedSlaves) > 0:
        print('the following slaves are unused and can be restarted safely:')
        print(' '.join(unusedSlaves))


def renameRedisService(port, current_version, new_version):
    service = common.REDIS_SERVICE.format(port)
    service_path = SERVICE_LOCATION.format(service)
    common.runBash(SED_REPLACE.format(
        current_version, new_version, service_path))
    common.runBash(RELOAD_SERVICE)


def confirmProceed():
    answer = input("\nare you sure you want to continue? (y/n) ")
    if answer not in ('y', 'Y', 'YES', 'yes', 'Yes'):
        print("exiting...")
        exit(0)


def main():
    args = processArgs()
    dryRun = args.dryrun
    path = args.path
    new_version = None
    print("\n-- redis upgrade tool --\n")

    if not dryRun:
        checkDirectoryValid(path)
        checkBinariesValid(path)
        new_version = checkBinariesNewVersion(path)

    instance = common.RedisInstance(
        hostname=common.getLocalhost(), ip=common.getLocalIP(), isLocalhost=True)
    current_version = instance.version

    printStaticInfo(current_version, new_version, instance.mode, dryRun)

    print("\n-- part 1: ensure all redises on server are slaves --")

    handleMasterFailovers(instance)

    print("\n-- part 2: check if slaves are used by applicative connections --")

    slaveUsageInfo(instance)
    confirmProceed()

    print("\n-- part 3: save all data to disk for fast recovery --")

    for port in instance.ports:
        instance.saveRDB(port)
        print(f'{instance.hostname}:{port} rdb saved to disk')

    if dryRun:
        print("\ndry run ends here, exiting...")
        exit(0)

    print("\n-- part 4: stop all redises and rename services to new version --")

    instance.stopRedisMulti(instance.ports)

    for port in instance.ports:
        renameRedisService(port, current_version, new_version)
        print(f'{instance.hostname}:{port} stopped, service renamed')

    print("\n-- part 5: create copy of redis old and new software in /redis/software --")

    backupRedisBinaries(path, current_version, new_version)

    print("\n-- part 6: switch new redis software instead of old software (upgrade step) --")

    switchRedisBinaries(path)

    print("\n-- part 7: start redis back up and check validity --")

    instance.startRedisMulti(instance.ports)
    portsBackUp = instance.gatherPortsList()
    for port in instance.ports:
        if port in portsBackUp:
            print(f'{instance.hostname}:{port} started successfully')
        else:
            print(
                f'ERROR: {instance.hostname}:{port} failed to start, please check')


if __name__ == '__main__':
    main()
