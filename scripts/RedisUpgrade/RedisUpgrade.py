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


class UpgradeError(Exception):
    """Raised when the upgrade process encounters a fatal error."""
    pass


class ValidationError(Exception):
    """Raised when pre-upgrade validation checks fail."""
    pass


class UserCancelledError(Exception):
    """Raised when the user cancels the operation."""
    pass


def processArgs():
    parser = argparse.ArgumentParser(
        description='upgrade redis instances on this server')
    parser.add_argument('--path', '-p',
                        help='path to new redis software (required for upgrade)')
    parser.add_argument('--dryrun', '--dry', '-d', action="store_true",
                        help='dry run only, run checks and master/slave switch without upgrading')
    parser.add_argument('--rollback', '-r',
                        help='rollback to a previously backed-up version (e.g. 7.4.6)')
    return parser.parse_args()


def checkDirectoryValid(path):
    path = Path(path)
    if not path.exists():
        raise ValidationError(
            f'software directory path \'{path}\' does not exist')
    if not (path / BINARIES_DIR).exists():
        raise ValidationError(
            f'software directory path \'{path}\' does not contain {BINARIES_DIR} directory')
    if not (path / LIBRARIES_DIR).exists():
        raise ValidationError(
            f'software directory path \'{path}\' does not contain {LIBRARIES_DIR} directory')


def checkBinariesValid(path):
    path = Path(path) / BINARIES_DIR

    for binary in REQUIRED_BINARIES:
        if not (path / binary).exists():
            raise ValidationError(
                f'software directory \'{path}\' does not contain required {binary} binary')

        fullBinaryPath = (path / binary).resolve()
        # check if redis binary even runs on the current os- if not return non zero value
        returnCode = int(common.runBash(
            CHECK_VERSION.format(fullBinaryPath)).splitlines()[1])
        if returnCode != 0:
            raise ValidationError(
                f'the server rhel version is too old for the given redis software. '
                f'You can see the error yourself by running: {CHECK_VERSION.format(fullBinaryPath)}')


def checkBinariesNewVersion(path):
    fullBinaryPath = (Path(path) / BINARIES_DIR / 'redis-server').resolve()
    version = common.runBash(CHECK_VERSION.format(
        fullBinaryPath)).splitlines()[0]
    # from output 'Redis server v=7.4.6 ...' -> extract second part of third word
    version = version.split()[2].split('=')[1]
    return version


def compareVersions(current_version, new_version):
    """Compare current and new versions. Raise if new version is not newer."""
    def version_tuple(v):
        return tuple(int(x) for x in v.split('.'))

    current = version_tuple(current_version)
    new = version_tuple(new_version)

    if new == current:
        raise ValidationError(
            f'new version ({new_version}) is the same as the current version ({current_version}). '
            f'Nothing to upgrade.')
    if new < current:
        print(f'WARNING: new version ({new_version}) is OLDER than current version ({current_version}).')
        print('This is a downgrade, not an upgrade.')
        confirmProceed()


def backupRedisBinaries(path, current_version, new_version):
    backupDir = Path(REDIS_SOFTWARE_DIR)
    newSoftwarePath = Path(path).resolve()
    runningSoftwarePath = Path(RUNNING_BINARIES_PATH)
    runningLibsPath = Path(RUNNING_LIBRARIES_PATH)

    # copy new software to new version folder
    (backupDir / new_version).mkdir(mode=0o755, parents=True, exist_ok=True)
    shutil.copytree(newSoftwarePath, backupDir /
                    new_version, dirs_exist_ok=True)
    print(f'copied new redis software to {backupDir / new_version}')

    # copy existing software to current version folder
    (backupDir / current_version /
     BINARIES_DIR).mkdir(mode=0o755, parents=True, exist_ok=True)
    shutil.copytree(runningSoftwarePath, backupDir /
                    current_version / BINARIES_DIR, dirs_exist_ok=True)
    (backupDir / current_version /
     LIBRARIES_DIR).mkdir(mode=0o755, parents=True, exist_ok=True)
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


def rollbackRedisBinaries(version):
    """Rollback to a previously backed-up version from /redis/software/<version>."""
    backupDir = Path(REDIS_SOFTWARE_DIR) / version
    if not backupDir.exists():
        raise ValidationError(
            f'rollback version directory \'{backupDir}\' does not exist')
    if not (backupDir / BINARIES_DIR).exists():
        raise ValidationError(
            f'rollback version directory \'{backupDir}\' does not contain {BINARIES_DIR} directory')
    if not (backupDir / LIBRARIES_DIR).exists():
        raise ValidationError(
            f'rollback version directory \'{backupDir}\' does not contain {LIBRARIES_DIR} directory')

    runningSoftwarePath = Path(RUNNING_BINARIES_PATH)
    runningLibsPath = Path(RUNNING_LIBRARIES_PATH)

    shutil.copytree(backupDir / BINARIES_DIR,
                    runningSoftwarePath, dirs_exist_ok=True)
    shutil.copytree(backupDir / LIBRARIES_DIR,
                    runningLibsPath, dirs_exist_ok=True)
    print(
        f'rolled back redis software from {backupDir} into {runningSoftwarePath} and {runningLibsPath}')


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
    standalones = []
    for port in instance.ports:
        if instance.isMaster(port):
            masters.append(port)
        elif not instance.isSlave(port):
            standalones.append(port)

    if len(standalones) > 0:
        print(f"the following ports have no connected slaves and cannot failover: "
              f"{' '.join(standalones)}")

    if len(masters) == 0:
        print("all redises on server are already slaves (or standalone), continuing")
    else:
        print("the following masters with active slaves will failover to other server:")
        print(' '.join(masters))
        confirmProceed()

        for port in masters:
            result = str(instance.failoverRedis(port))
            print(f'{instance.hostname}:{port} returned: {result}')
            if result not in ('ok', 'True', True):
                raise UpgradeError(
                    f'failover failed for {instance.hostname}:{port} — returned: {result}')

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
    if answer.strip().lower() not in ('y', 'yes'):
        raise UserCancelledError("user cancelled the operation")


def main():
    args = processArgs()
    dryRun = args.dryrun
    path = args.path
    rollback_version = args.rollback
    new_version = None
    print("\n-- redis upgrade tool --\n")

    # --- Rollback mode ---
    if rollback_version:
        print(f"-- rollback mode: restoring version {rollback_version} --\n")
        instance = common.RedisInstance(
            hostname=common.getLocalhost(), ip=common.getLocalIP(), isLocalhost=True)

        print("-- stopping all redises --")
        instance.stopRedisMulti(instance.ports)
        for port in instance.ports:
            print(f'{instance.hostname}:{port} stopped')

        print(f"\n-- rolling back to version {rollback_version} --")
        rollbackRedisBinaries(rollback_version)

        current_version = instance.version
        for port in instance.ports:
            renameRedisService(port, current_version, rollback_version)
            print(f'{instance.hostname}:{port} service renamed')

        print("\n-- starting redis back up --")
        instance.startRedisMulti(instance.ports)
        portsBackUp = instance.gatherPortsList()
        for port in instance.ports:
            if port in portsBackUp:
                print(f'{instance.hostname}:{port} started successfully')
            else:
                print(
                    f'ERROR: {instance.hostname}:{port} failed to start, please check')
        return

    # --- Upgrade / dry-run mode ---
    if not dryRun:
        if not path:
            raise ValidationError(
                '--path is required for upgrade mode (use --dryrun for dry run, or --rollback for rollback)')
        checkDirectoryValid(path)
        checkBinariesValid(path)
        new_version = checkBinariesNewVersion(path)

    instance = common.RedisInstance(
        hostname=common.getLocalhost(), ip=common.getLocalIP(), isLocalhost=True)
    current_version = instance.version

    if not dryRun:
        compareVersions(current_version, new_version)

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
        sys.exit(0)

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
    try:
        main()
    except ValidationError as e:
        print(f'Validation error: {e}')
        sys.exit(1)
    except UpgradeError as e:
        print(f'Upgrade error: {e}')
        sys.exit(1)
    except UserCancelledError:
        print('exiting...')
        sys.exit(0)
    except KeyboardInterrupt:
        print('\ninterrupted, exiting...')
        sys.exit(1)
