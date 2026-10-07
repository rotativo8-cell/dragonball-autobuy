#!/bin/bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
    echo "Ejecuta: sudo bash ops/install_server_check.sh"
    exit 1
fi
cd /opt/dragonball-autobuy
command -v python3 >/dev/null
command -v docker >/dev/null
command -v findmnt >/dev/null
python3 -m py_compile ops/server_check.py
install -d -m 700 /var/log/comprobar-servidor /var/lib/comprobar-servidor
install -m 755 ops/server_check.py /usr/local/sbin/comprobar-servidor

systemctl stop comprobar-servidor.timer comprobar-servidor.service || true

cat > /etc/systemd/system/comprobar-servidor.service <<'SERVICE'
[Unit]
Description=Revision horaria de NAS, Docker y contenedores
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/comprobar-servidor
TimeoutStartSec=5min
StandardOutput=null
SERVICE

cat > /etc/systemd/system/comprobar-servidor.timer <<'TIMER'
[Unit]
Description=Revision del servidor cada hora

[Timer]
OnBootSec=1h
OnUnitInactiveSec=1h
Unit=comprobar-servidor.service

[Install]
WantedBy=timers.target
TIMER

cat > /etc/systemd/system/comprobar-servidor-arranque.service <<'SERVICE'
[Unit]
Description=Revision y correo del servidor tras el arranque
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/comprobar-servidor --boot-mail
TimeoutStartSec=5min
StandardOutput=null
SERVICE

cat > /etc/systemd/system/comprobar-servidor-arranque.timer <<'TIMER'
[Unit]
Description=Enviar revision dos minutos despues de arrancar

[Timer]
OnBootSec=2min
Unit=comprobar-servidor-arranque.service

[Install]
WantedBy=timers.target
TIMER

cat > /etc/systemd/system/comprobar-servidor-limpieza.service <<'SERVICE'
[Unit]
Description=Borrar registros propios de mas de cinco dias

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/comprobar-servidor --prune-only
TimeoutStartSec=1min
StandardOutput=null
SERVICE

cat > /etc/systemd/system/comprobar-servidor-limpieza.timer <<'TIMER'
[Unit]
Description=Aplicar retencion de cinco dias a los registros

[Timer]
OnBootSec=1min
OnUnitInactiveSec=1min
AccuracySec=1s
Unit=comprobar-servidor-limpieza.service

[Install]
WantedBy=timers.target
TIMER

systemctl daemon-reload
systemctl enable comprobar-servidor.timer comprobar-servidor-arranque.timer comprobar-servidor-limpieza.timer
systemctl restart comprobar-servidor.timer comprobar-servidor-arranque.timer comprobar-servidor-limpieza.timer
# Validar ahora sin enviar otro correo.
 /usr/local/sbin/comprobar-servidor || true
systemctl list-timers 'comprobar-servidor*' --no-pager
echo "Instalado. Resultados: sudo cat /var/log/comprobar-servidor/revision-*.log"
