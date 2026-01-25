cat << 'EOF'
  ____          _ _       _   _      _       
 |  _ \ ___  __| (_)___  | | | | ___| |_ __  
 | |_) / _ \/ _` | / __| | |_| |/ _ \ | '_ \ 
 |  _ <  __/ (_| | \__ \ |  _  |  __/ | |_) |
 |_| \_\___|\__,_|_|___/ |_| |_|\___|_| .__/ 
                                      |_|    
-- status
check status of redises on server (status -h for help)

-- sys <port>
connect to redis on port <port> (sys 1 -h for help)

-- addmemory -p <port> -a <addMb>
add <addMb> megabytes of memory to redis on port <port> (addmemory -h for help)

-- systemctl stop/start/status redis_<port>
stop/start/check_status for redis on port <port>

-- help
print this help message
EOF
