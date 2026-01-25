if [ -n "$1" ]; then port=$1; fi
if [ -f "/etc/redis/${port}.conf" ]; then
pass=$(grep requirepass /etc/redis/${port}.conf | grep -v '#' | awk '{print $2}' | tr -d '"'); fi
redis-cli -p $port -a $pass --no-auth-warning ${@:2}
 
