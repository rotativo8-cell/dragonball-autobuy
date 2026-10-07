#!/usr/bin/env python3
"""Comprobación local de Ubuntu; stdlib, sin reinicios ni cambios en contenedores."""
import argparse
import fcntl
import json
import os
import smtplib
import socket
import ssl
import subprocess
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

LOG_DIR = Path('/var/log/comprobar-servidor')
STATE_DIR = Path('/var/lib/comprobar-servidor')
PROJECT = Path('/opt/dragonball-autobuy')
RECIPIENT = 'rotativo8@gmail.com'
MOUNTS = {
    '/mnt/synology': '//192.168.0.30/homes/cristian/docker',
    '/mnt/restic': '//192.168.0.30/home',
}


def command(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          check=True).stdout.strip()


def prune_logs(directory=LOG_DIR, current=None):
    cutoff = (time.time() if current is None else current) - 5 * 86400
    for file in directory.glob('revision-*.log'):
        if file.is_file() and file.stat().st_mtime <= cutoff:
            file.unlink()


def checks():
    results = []
    for path, source in MOUNTS.items():
        try:
            command(['ls', path])
            mounted = command(['findmnt', '-rn', '-M', path, '-t', 'cifs', '-o', 'SOURCE'])
            ok = mounted == source
        except (subprocess.SubprocessError, OSError):
            ok = False
        results.append((ok, f'NAS {path}: ' + ('accesible y montaje correcto' if ok
                                             else 'no accesible o montaje incorrecto')))
    try:
        command(['systemctl', 'is-active', '--quiet', 'docker'])
        command(['docker', 'info'])
        ids = command(['docker', 'ps', '-aq']).split()
        containers = json.loads(command(['docker', 'inspect', *ids])) if ids else []
        results.append((True, 'Docker: activo y responde'))
        if not ids:
            results.append((False, 'Docker: no hay contenedores para comprobar'))
        for container in containers:
            labels = container.get('Config', {}).get('Labels') or {}
            # Las pruebas compose run son temporales; no son servicios esperados.
            if labels.get('com.docker.compose.oneoff', '').lower() == 'true':
                continue
            name = container['Name'].lstrip('/')
            state = container['State']
            running = state.get('Status') == 'running'
            health = state.get('Health', {}).get('Status')
            ok = running and health in (None, 'healthy')
            detail = ('en ejecución; saludable' if health == 'healthy' else
                      'en ejecución; sin comprobación de salud' if running and health is None else
                      f"estado {state.get('Status', 'desconocido')}; salud {health or 'no definida'}")
            results.append((ok, f'Contenedor {name}: {detail}'))
    except (subprocess.SubprocessError, OSError, ValueError, KeyError):
        results.append((False, 'Docker: no se pudo completar la consulta'))
    return results


def report(results):
    failures = sum(not ok for ok, _ in results)
    stamp = datetime.now(ZoneInfo('Europe/Madrid')).strftime('%d/%m/%Y %H:%M:%S %Z')
    lines = [f'SERVIDOR: {socket.gethostname()}', f'FECHA: {stamp}',
             f'RESULTADO: {"TODO CORRECTO" if failures == 0 else str(failures) + " FALLOS"}', '']
    lines.extend(('OK — ' if ok else 'FALLO — ') + detail for ok, detail in results)
    lines.extend(['', 'Esta revisión no reinicia servicios.',
                  'Sin comprobación de salud significa que solo se verificó la ejecución.'])
    return '\n'.join(lines), failures


def send_mail(body, failures):
    # Compose resuelve .env; nunca ejecutar .env como código de shell ni imprimirlo.
    data = json.loads(command(['docker', 'compose', '--project-directory', str(PROJECT),
                               'config', '--format', 'json']))
    env = data['services']['monitor']['environment']
    host = env.get('SMTP_HOST') or 'smtp.gmail.com'
    port = int(env.get('SMTP_PORT') or '587')
    user, password = env.get('SMTP_USER'), env.get('SMTP_PASSWORD')
    if not user or not password:
        raise ValueError('Configuración SMTP incompleta')
    message = EmailMessage()
    message['From'] = user
    message['To'] = RECIPIENT
    message['Subject'] = ('Ubuntu tras arranque: ' +
                          ('TODO CORRECTO' if failures == 0 else f'{failures} FALLOS'))
    message.set_content(body)
    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(user, password)
        if smtp.send_message(message):
            raise RuntimeError('Correo rechazado')


def notify_boot(body, failures, state_dir=STATE_DIR, boot_id=None, sender=send_mail):
    boot_id = boot_id or Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    marker = state_dir / 'boot-mail-id'
    if marker.exists() and marker.read_text().strip() == boot_id:
        return 'Correo: ya se intentó el resumen de este arranque; no se repite.'
    # Reservar antes de enviar para evitar duplicados tras un resultado incierto.
    temp = marker.with_suffix('.tmp')
    temp.write_text(boot_id)
    temp.replace(marker)
    try:
        sender(body, failures)
        return 'Correo: resumen de arranque enviado.'
    except Exception:
        return 'FALLO — Correo: envío fallido; revisar SMTP/conectividad. Sin reintento automático.'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--boot-mail', action='store_true')
    parser.add_argument('--prune-only', action='store_true')
    args = parser.parse_args()
    for directory in (LOG_DIR, STATE_DIR):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE_DIR / 'check.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        prune_logs()
        if args.prune_only:
            return 0
        body, failures = report(checks())
        if args.boot_mail:
            mail_status = notify_boot(body, failures)
            body += '\n\n' + mail_status
            if mail_status.startswith('FALLO'):
                failures += 1
        file = LOG_DIR / f'revision-{time.time_ns()}.log'
        file.write_text(body + '\n')
        file.chmod(0o600)
        print(body)
        return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
