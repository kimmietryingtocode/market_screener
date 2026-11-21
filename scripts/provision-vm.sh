#!/usr/bin/env bash
# Prepare a fresh Ubuntu ARM VM to run the stack. Run it ON the VM:
#
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/provision-vm.sh | bash
#   # or: scp it over and run `bash provision-vm.sh`
#
# Idempotent: re-running it is safe.
#
# Two Oracle Cloud specifics are handled here, because each one produces a
# host that looks healthy while refusing every connection:
#
#   1. OCI's Ubuntu images ship an iptables INPUT chain that rejects
#      everything except SSH. Opening 80/443 in the VCN security list is
#      necessary but NOT sufficient — the packet still dies on the host.
#   2. Those rules are not persisted by default, so a reboot silently
#      reverts them unless iptables-persistent is installed.
set -euo pipefail

DATA_DIR="${DATA_DIR:-/srv/fintech}"

echo "==> System packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
	ca-certificates curl git rsync ufw

echo "==> Docker (official repo, so we get Compose v2 and a current engine)"
if ! command -v docker >/dev/null; then
	sudo install -m 0755 -d /etc/apt/keyrings
	curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
		| sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
	sudo chmod a+r /etc/apt/keyrings/docker.gpg
	echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
		| sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
	sudo apt-get update -qq
	sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
		docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker

echo "==> Opening 80/443 on the host firewall"
# Both layers. ufw is the friendly front end, but OCI's pre-seeded iptables
# REJECT rule sits ahead of ufw's chain on these images, so it is removed
# explicitly. Without this the VCN can be wide open and the port still
# refuses to connect.
sudo ufw allow 22/tcp  >/dev/null
sudo ufw allow 80/tcp  >/dev/null
sudo ufw allow 443/tcp >/dev/null
sudo ufw allow 443/udp >/dev/null
sudo ufw --force enable >/dev/null

if sudo iptables -C INPUT -j REJECT --reject-with icmp-host-prohibited 2>/dev/null; then
	sudo iptables -D INPUT -j REJECT --reject-with icmp-host-prohibited
	echo "    removed OCI's catch-all INPUT REJECT rule"
fi
for port in 80 443; do
	sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
		|| sudo iptables -I INPUT -p tcp --dport "$port" -j ACCEPT
done
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iptables-persistent
sudo netfilter-persistent save >/dev/null
echo "    rules saved so they survive a reboot"

echo "==> Swap"
# 12GB of RAM is plenty for the services, but the C++ engine build and the
# Python images are memory-spiky. 2GB of swap costs nothing and turns an
# OOM-killed build into a slow one.
if ! sudo swapon --show | grep -q /swapfile; then
	sudo fallocate -l 2G /swapfile
	sudo chmod 600 /swapfile
	sudo mkswap /swapfile >/dev/null
	sudo swapon /swapfile
	echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Data directory at $DATA_DIR"
sudo mkdir -p "$DATA_DIR"
sudo chown "$(id -u):$(id -g)" "$DATA_DIR"

echo
echo "==> Done. Remaining steps:"
echo "  1. Log out and back in (so docker group membership takes effect)."
echo "  2. Create $DATA_DIR/.env   (see .env.example; set SITE_ADDRESS to your hostname)."
echo "  3. From your laptop:  scripts/deploy.sh $USER@<this-host>"
echo
echo "  Reminder: OCI also needs an ingress rule for 80 and 443 in the"
echo "  subnet's security list. The host firewall above is only one half."
