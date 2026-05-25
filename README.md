# redis-8-ansible

generic install for redis 8 instance on rhel 8/9 servers, including software unpacking and creation of service and conf files

**HOW TO**

1. **downloads**:  
- download this repo  
- also download ansible rhel 8 install from:  
https://github.com/avi-azar-1/rhel8.8-ansible

  or for rhel 9 from:
  https://github.com/avi-azar-1/rhel9.6-ansible

- install centos8 for rhel 8 equivelent environment:
https://github.com/mishamosher/CentOS-WSL/releases/tag/8-stream-20230626

  or centos9 for rhel 9 equivelent environment:
https://github.com/mishamosher/CentOS-WSL/releases/tag/9-stream-20230626
- after the install copy and run inside the centos8 env:
```bash
./get_latest_redis.sh
```
  or for centos9/rhel9 run:
  ```bash
./get_latest_redis_rhel9.sh
```
the script will configure the server, download redis and package the software into redis-x.x.x.tar.gz

2. **install ansible**:  
look at instructions in ansible repo

3. **ready redis install**:  
unzip this repo in target server  
unzip redis software tarball in target server
```bash
tar -xzf redis-x.x.x.tar.gz
```
(replace with the version you created)

4. **edit playbook**:  
change target server and parameters inside redis_inventory.yaml

5. **run playbook**:  
from playbook folder:
```bash
ansible-playbook -i redis_inventory.yaml redis8.yaml
```
for local install (without ssh) run with '-c local' flag  
for remote ssh create id_rsa.pub in ansible server and copy to known_hosts un target server  

6. **replication, sentinels or cluster**:  
   **sentinels**: set up as normal redis then change to sentinel.conf file  
   **replication**: set replicaof during install than use redis_add_sentinel.sh  
   **cluster**: use cluster create:  
```bash
   ps -ef | grep redis | grep -v grep | grep -v export | awk '{print $9}' | tr '\n' ' '
```  
(get node list on each server, combine and run:)  
```bash
redis-cli --cluster create -a <password> --cluster-replicas 1 <redis_list>
```

comment: redis extras addes help,sys,status,addmemory,redisupgrade. if any of those dont work due to network without dns change to noDns=True inside /redis/software/RedisCommon/rediscommon.py 


