"""Validate resolved Compose config only; run on the MP-01 remote directory."""
import json
import os
from pathlib import Path
import secrets
import subprocess

root = Path('/tmp/stocks-mp01-config-consistency')
assert Path.cwd() == root
paths = {key + '_CONFIG_PATH': '/app/config/mp01-' + key.lower() + '.yaml'
         for key in ('TASKS', 'SOURCES', 'MODELS', 'TOOLS')}
env_file = root / '.env'
assert not env_file.exists()
env_file.write_text('\n'.join(key + '=' + value for key, value in paths.items()) + '\n')
env_file.chmod(0o600)
try:
    resolved = json.loads(subprocess.check_output(
        ['docker', 'compose', '--project-name', 'stocks-mp01-check', 'config', '--format', 'json'],
        env={**os.environ, 'POSTGRES_PASSWORD': secrets.token_hex(24)}, text=True))
    api, worker = (resolved['services'][name] for name in ('api', 'worker'))
    assert worker['command'] == ['python', '-m', 'stocks.worker']
    for service in (api, worker):
        assert all(service['environment'][key] == value for key, value in paths.items())
        assert any(volume['target'] == '/app/config' and volume['read_only']
                   for volume in service['volumes'])
    print(json.dumps({'status': 'passed', 'worker_command': worker['command'],
                      'shared_configuration_paths': paths,
                      'shared_read_only_config_mount': True}))
finally:
    env_file.unlink()
