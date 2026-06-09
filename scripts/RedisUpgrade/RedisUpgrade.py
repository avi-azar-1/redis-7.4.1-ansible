import argparse
import time
import sys
import shutil
import re
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
EXTRA_BINARIES = ['redis-check-aof','redis-check-rdb','redis-benchmark']
CHECK_VERSION = '{} --version; echo $?'
IDLE_WINDOW = 60
IGNORED_COMMANDS = ('ping', 'info', 'client|list',
                    'replconf', 'unsubscribe', 'cluster|nodes', 'command')
APPLICATION_RECONNECT_TIME = 10
RELOAD_SERVICE = 'systemctl daemon-reload'
SERVICE_LOCATION = '/usr/lib/systemd/system/{}.service'
REDIS_CONF_PATH = '/etc/redis/{}.conf'
CLIENT_PAUSE_TIMEOUT_MS = 30000
REPL_SYNC_TIMEOUT_SEC = 25
ROLE_CHANGE_TIMEOUT_SEC = 30
REPL_LAG_WARN_BYTES = 1024 * 1024  # 1MB
MONITOR_DURATION_SEC = 5
MONITOR_IGNORED_COMMANDS = {'ping', 'replconf', 'publish', 'info'}


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
    parser.add_argument('--port',
                        help='single-redis mode: only process this port (dry run only)')
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


def _copyRedisFiles(srcDir, destDir):
    """Copy individual files and symlinks from srcDir to destDir, skipping subdirectories.
    Uses unlink-then-copy to avoid 'Text file busy' errors on running binaries."""
    destDir.mkdir(mode=0o755, parents=True, exist_ok=True)
    copied = []
    for item in srcDir.iterdir():
        dest = destDir / item.name
        if item.is_symlink():
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            dest.symlink_to(item.readlink())
            copied.append(item.name)
        elif item.is_file():
            if dest.exists():
                dest.unlink()
            shutil.copy2(item, dest)
            copied.append(item.name)
    return copied


def backupRedisBinaries(path, current_version, new_version):
    backupDir = Path(REDIS_SOFTWARE_DIR)
    newSoftwarePath = Path(path).resolve()
    runningSoftwarePath = Path(RUNNING_BINARIES_PATH)
    runningLibsPath = Path(RUNNING_LIBRARIES_PATH)

    # backup current redis binaries (required + extra, skip non-redis like redis-exporter)
    currentBinBackup = backupDir / current_version / BINARIES_DIR
    currentBinBackup.mkdir(mode=0o755, parents=True, exist_ok=True)
    for binary in REQUIRED_BINARIES + EXTRA_BINARIES:
        src = runningSoftwarePath / binary
        if src.exists():
            shutil.copy2(src, currentBinBackup / binary)
            print(f'  backed up {src} -> {currentBinBackup / binary}')

    # backup current redis libraries (only files matching new software's lib contents)
    currentLibBackup = backupDir / current_version / LIBRARIES_DIR
    currentLibBackup.mkdir(mode=0o755, parents=True, exist_ok=True)
    newLibDir = newSoftwarePath / LIBRARIES_DIR
    for item in newLibDir.iterdir():
        currentLib = runningLibsPath / item.name
        if currentLib.exists() or currentLib.is_symlink():
            dest = currentLibBackup / item.name
            if currentLib.is_symlink():
                if dest.exists() or dest.is_symlink():
                    dest.unlink()
                dest.symlink_to(currentLib.readlink())
            else:
                shutil.copy2(currentLib, dest)
    print(f'backed up current redis libraries to {currentLibBackup}')

    # save new software to new version folder
    newBackup = backupDir / new_version
    newBackup.mkdir(mode=0o755, parents=True, exist_ok=True)
    shutil.copytree(newSoftwarePath, newBackup, dirs_exist_ok=True)
    print(f'copied new redis software to {newBackup}')


def switchRedisBinaries(path):
    newSoftwarePath = Path(path).resolve()
    runningSoftwarePath = Path(RUNNING_BINARIES_PATH)
    runningLibsPath = Path(RUNNING_LIBRARIES_PATH)

    # copy only redis binaries from new software
    newBinDir = newSoftwarePath / BINARIES_DIR
    binFiles = _copyRedisFiles(newBinDir, runningSoftwarePath)
    print(f'switched binaries into {runningSoftwarePath}: {", ".join(binFiles)}')

    # copy only redis libraries from new software
    newLibDir = newSoftwarePath / LIBRARIES_DIR
    libFiles = _copyRedisFiles(newLibDir, runningLibsPath)
    print(f'switched libraries into {runningLibsPath}: {", ".join(libFiles)}')


def rollbackRedisBinaries(version):
    """Rollback Redis binaries and libraries from /redis/software/<version>/."""
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

    # restore only backed-up redis binaries
    binDir = backupDir / BINARIES_DIR
    binFiles = _copyRedisFiles(binDir, runningSoftwarePath)
    print(f'restored binaries into {runningSoftwarePath}: {", ".join(binFiles)}')

    # restore only backed-up redis libraries
    libDir = backupDir / LIBRARIES_DIR
    libFiles = _copyRedisFiles(libDir, runningLibsPath)
    print(f'restored libraries into {runningLibsPath}: {", ".join(libFiles)}')


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


def checkReplicationLag(instance, port):
    """Check replication lag on a master port. Returns max lag in bytes across all slaves."""
    info = instance.getInfo(port)
    if info['role'] != 'master' or info['connected_slaves'] == 0:
        return 0

    master_offset = info['master_repl_offset']
    max_lag = 0
    for i in range(info['connected_slaves']):
        slave_key = f'slave{i}'
        if slave_key in info:
            slave_offset = int(info[slave_key]['offset'])
            lag = master_offset - slave_offset
            max_lag = max(max_lag, lag)
            state = info[slave_key]['state']
            print(f'  slave{i} ({info[slave_key]["ip"]}:{info[slave_key]["port"]}): '
                  f'lag={lag} bytes, state={state}')
    return max_lag


def waitForReplicationSync(instance, port, timeout_sec=REPL_SYNC_TIMEOUT_SEC):
    """Poll until all slaves have caught up to the master's replication offset."""
    start = time.time()
    while time.time() - start < timeout_sec:
        info = instance.getInfo(port)
        master_offset = info['master_repl_offset']
        all_synced = True
        for i in range(info['connected_slaves']):
            slave_key = f'slave{i}'
            if slave_key in info:
                if int(info[slave_key]['offset']) < master_offset:
                    all_synced = False
                    break
        if all_synced:
            return True
        time.sleep(0.1)
    return False


def waitForRoleChange(instance, port, expected_role, timeout_sec=ROLE_CHANGE_TIMEOUT_SEC):
    """Poll until the instance's role for the given port matches expected_role."""
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            info = instance.getInfo(port)
            if info['role'] == expected_role:
                return True
        except Exception:
            pass  # connection may reset during failover
        time.sleep(0.5)
    return False


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
        # pre-failover replication lag check
        print("checking replication lag on masters...")
        for port in masters:
            print(f'{instance.hostname}:{port}:')
            lag = checkReplicationLag(instance, port)
            if lag > REPL_LAG_WARN_BYTES:
                print(f'WARNING: replication lag is {lag} bytes (>{REPL_LAG_WARN_BYTES})')
                print('high replication lag increases risk of data loss during failover')
                confirmProceed()

        print("\nthe following masters with active slaves will failover to other server:")
        print(' '.join(masters))
        confirmProceed()

        for port in masters:
            rds = instance.redisConnection(port)

            # pause client writes on master so no new data comes in
            print(f'{instance.hostname}:{port}: pausing client writes...')
            pause_start = time.time()
            try:
                rds.execute_command(f'CLIENT PAUSE {CLIENT_PAUSE_TIMEOUT_MS} WRITE')
            except Exception as e:
                print(f'WARNING: CLIENT PAUSE not supported ({e}), proceeding without pause')
                pause_start = None

            # wait for all replicas to catch up to the frozen master offset
            print(f'{instance.hostname}:{port}: waiting for replica sync...')
            synced = waitForReplicationSync(instance, port)
            if not synced:
                try:
                    rds.execute_command('CLIENT UNPAUSE')
                except Exception:
                    pass
                raise UpgradeError(
                    f'replica sync timed out for {instance.hostname}:{port} '
                    f'after {REPL_SYNC_TIMEOUT_SEC}s')
            print(f'{instance.hostname}:{port}: replicas fully synced')

            # trigger failover
            result = str(instance.failoverRedis(port))
            print(f'{instance.hostname}:{port}: failover returned: {result}')
            if result not in ('ok', 'True', True):
                try:
                    rds.execute_command('CLIENT UNPAUSE')
                except Exception:
                    pass
                raise UpgradeError(
                    f'failover failed for {instance.hostname}:{port} — returned: {result}')

            # verify role actually changed
            print(f'{instance.hostname}:{port}: waiting for role change to slave...')
            changed = waitForRoleChange(instance, port, 'slave')
            if changed:
                print(f'{instance.hostname}:{port}: confirmed role is now slave')
            else:
                print(f'WARNING: {instance.hostname}:{port}: role change not confirmed '
                      f'after {ROLE_CHANGE_TIMEOUT_SEC}s, proceeding anyway')

            # verify slave is connected to the new master
            try:
                info = instance.getInfo(port)
                link_status = info.get('master_link_status', 'unknown')
                master_host = info.get('master_host', 'unknown')
                master_port = info.get('master_port', 'unknown')
                if link_status == 'up':
                    print(f'{instance.hostname}:{port}: slave link to new master '
                          f'{master_host}:{master_port} is up')
                else:
                    print(f'WARNING: {instance.hostname}:{port}: slave link status is '
                          f'\'{link_status}\' (master: {master_host}:{master_port})')
            except Exception:
                pass

            # brief delay for sentinel +switch-master propagation
            # allows python sentinel clients to update their pool address
            # before we unpause and they receive ReadOnlyError
            time.sleep(2)

            # unpause clients on old master (now slave)
            # held writes will receive ReadOnlyError, prompting clients to reconnect
            # to the new master via sentinel-resolved address
            try:
                rds.execute_command('CLIENT UNPAUSE')
            except Exception:
                pass  # connection may have been reset during failover

            if pause_start:
                pause_duration = time.time() - pause_start
                print(f'{instance.hostname}:{port}: clients were paused for '
                      f'{pause_duration:.1f}s (writes held server-side)')

        print(f"waiting {APPLICATION_RECONNECT_TIME} seconds for client reconnection...")
        time.sleep(APPLICATION_RECONNECT_TIME)
        print("all failovers completed successfully")


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

def monitorReplicaTraffic(instance):
    """Run MONITOR on each slave for MONITOR_DURATION_SEC and warn about
    applicative (non-replication, non-sentinel) traffic. Runs checks in parallel."""
    import redis as redispy
    import concurrent.futures

    # collect sentinel IPs to exclude
    sentinel_ips = set()
    if instance.mode == common.RedisMode.MASTER_SLAVE:
        for s in instance.sentinels:
            try:
                sentinel_ips.add(common.runBash(f'getent hosts {s}').split()[0])
            except Exception:
                pass

    slave_ports = [port for port in instance.ports if instance.isSlave(port)]
    if not slave_ports:
        return

    print(f'monitoring traffic for {MONITOR_DURATION_SEC}s on {len(slave_ports)} slaves...')

    def _monitor_worker(port):
        # get master IP for this slave to exclude replication traffic
        info = instance.getInfo(port)
        master_ip = info.get('master_host', '')
        excluded_ips = sentinel_ips | {master_ip, '127.0.0.1', instance.ip}

        # dedicated connection with socket_timeout so listen() doesn't block forever
        passwrd = instance.getPassword(port)
        rds = redispy.Redis(host=instance.hostname, port=int(port),
                            password=passwrd, socket_timeout=1)
        applicative_traffic = {}  # ip -> set of commands

        try:
            with rds.monitor() as monitor:
                start = time.time()
                while time.time() - start < MONITOR_DURATION_SEC:
                    try:
                        for event in monitor.listen():
                            if time.time() - start >= MONITOR_DURATION_SEC:
                                break

                            command = event.get('command', '')
                            client_address = event.get('client_address', '')

                            if not client_address or not command:
                                continue

                            cmd_name = command.split()[0].lower() if command else ''
                            if cmd_name in MONITOR_IGNORED_COMMANDS:
                                continue

                            client_ip = client_address.split(':')[0]
                            if client_ip in excluded_ips:
                                continue

                            if client_ip not in applicative_traffic:
                                applicative_traffic[client_ip] = set()
                            applicative_traffic[client_ip].add(cmd_name)
                    except redispy.TimeoutError:
                        continue  # socket timed out, check elapsed time
        except Exception as e:
            return port, f'WARNING: could not run MONITOR on {port}: {e}', None
        finally:
            try:
                rds.close()
            except Exception:
                pass

        return port, None, applicative_traffic

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(slave_ports)) as executor:
        futures = {executor.submit(_monitor_worker, port): port for port in slave_ports}
        for future in concurrent.futures.as_completed(futures):
            port = futures[future]
            try:
                p, err, traffic = future.result()
                if err:
                    print(err)
                elif traffic:
                    print(f'WARNING: slave {instance.hostname}:{port} has applicative traffic:')
                    for ip, cmds in traffic.items():
                        host = common.ip_to_hostname(ip)
                        display = f'{host} ({ip})' if host != ip else ip
                        print(f'  {display}: {", ".join(sorted(cmds))}')
            except Exception as e:
                print(f'WARNING: exception monitoring {port}: {e}')


def disableRedisGears(port):
    """Comment out any loadmodule line for redisgears in the Redis conf file."""
    conf_path = Path(REDIS_CONF_PATH.format(port))
    if not conf_path.exists():
        print(f'WARNING: conf file {conf_path} not found for port {port}, skipping gears check')
        return

    lines = conf_path.read_text().splitlines(True)
    modified = False
    new_lines = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.lower().startswith('loadmodule') and 'redisgears' in stripped.lower():
            if not stripped.startswith('#'):
                new_lines.append('# ' + line if not line.startswith(' ') else '#' + line)
                modified = True
                continue
        new_lines.append(line)

    if modified:
        conf_path.write_text(''.join(new_lines))
        print(f'{port}: commented out redisgears loadmodule in {conf_path}')
    else:
        print(f'{port}: no active redisgears loadmodule found in {conf_path}')


def renameRedisService(port, current_version, new_version):
    service = common.REDIS_SERVICE.format(port)
    service_path = Path(SERVICE_LOCATION.format(service))

    if not service_path.exists():
        print(f"WARNING: service file {service_path} does not exist, skipping rename")
        return

    content = service_path.read_text()

    # Replace explicit occurrences of current_version with new_version
    content = content.replace(current_version, new_version)

    # Ensure Description is updated correctly regardless of previous value
    content = re.sub(r'^Description=Redis.*$', f'Description=Redis {new_version}', content, flags=re.MULTILINE)

    service_path.write_text(content)

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

    # single-redis mode: restrict to one port (dry run only)
    single_port = args.port
    if single_port:
        if not dryRun:
            raise ValidationError('--port can only be used with --dryrun')
        if single_port not in instance.ports:
            raise ValidationError(
                f'port {single_port} not found on this server '
                f'(available: {", ".join(instance.ports)})')
        print(f'single-redis mode: only processing port {single_port}')
        instance.ports = [single_port]

    if not dryRun:
        compareVersions(current_version, new_version)

    printStaticInfo(current_version, new_version, instance.mode, dryRun)

    print("\n-- part 1: ensure all redises on server are slaves --")

    handleMasterFailovers(instance)

    print("\n-- part 2: check if slaves are used by applicative connections --")

    slaveUsageInfo(instance)

    print("\n-- part 2b: monitor replica traffic for applicative commands --")

    monitorReplicaTraffic(instance)
    confirmProceed()

    print("\n-- part 3: save all data to disk for fast recovery --")

    for port in instance.ports:
        instance.saveRDB(port)
        print(f'{instance.hostname}:{port} rdb saved to disk')

    if dryRun:
        print("\ndry run ends here, exiting...")
        sys.exit(0)

    print("\n-- part 4: disable deprecated redisgears module in conf files --")

    for port in instance.ports:
        disableRedisGears(port)

    print("\n-- part 5: stop all redises and rename services to new version --")

    instance.stopRedisMulti(instance.ports)

    for port in instance.ports:
        renameRedisService(port, current_version, new_version)
        print(f'{instance.hostname}:{port} stopped, service renamed')

    print("\n-- part 6: create copy of redis old and new software in /redis/software --")

    backupRedisBinaries(path, current_version, new_version)

    print("\n-- part 7: switch new redis software instead of old software (upgrade step) --")

    switchRedisBinaries(path)

    print("\n-- part 8: start redis back up and check validity --")

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
