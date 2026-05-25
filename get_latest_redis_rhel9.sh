sudo yum clean all
#set repo
cat << 'EOF' > /etc/yum.repos.d/redis.repo
[Redis]
name=Redis
baseurl=http://packages.redis.io/rpm/rockylinux9
enabled=1
gpgcheck=1
priority=1
EOF
curl -fsSL https://packages.redis.io/gpg > /tmp/redis.key
sudo rpm --import /tmp/redis.key
#install redis
sudo yum install redis --disablerepo=appstream -y
#pack software to zip
version=$(redis-server --version | awk '{print $3}' | awk -F = '{print $2}')
mkdir redis-${version}
mkdir redis-${version}/bin
cp /usr/bin/redis* redis-${version}/bin
mkdir redis-${version}/lib
cp /usr/lib/redis/*.so redis-${version}/lib
tar -czf redis-${version}.tar.gz redis-${version}