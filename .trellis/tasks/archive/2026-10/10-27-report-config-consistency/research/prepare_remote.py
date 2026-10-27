"""Prepare ONLY MP-01's existing isolated test resources on 100.120.14.84."""
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
from urllib.parse import quote

root = Path('/tmp/stocks-mp01-config-consistency')
info = json.loads(subprocess.check_output(['docker', 'inspect', 'stocks-mp01-postgres']))[0]
container_env = dict(value.split('=', 1) for value in info['Config']['Env'] if '=' in value)
database = 'postgresql+psycopg://stocks:' + quote(container_env['POSTGRES_PASSWORD'], safe='') + '@127.0.0.1:15561/stocks'
values = dict(DATABASE_URL=database, TEST_DATABASE_URL=database,
              PYTHONPATH=str(root / 'backend/src'),
              S3_ENDPOINT_URL='http://127.0.0.1:18361',
              S3_ACCESS_KEY=secrets.token_hex(12), S3_SECRET_KEY=secrets.token_hex(24))
env_path = root / 'runtime.env'
env_path.write_text('\n'.join('export ' + k + '=' + shlex.quote(v) for k, v in values.items()) + '\n')
os.chmod(env_path, 0o600)
subprocess.run(['docker', 'run', '--rm', '-d', '--name', 'stocks-mp01-s3',
                '-v', str(root / 's3-data') + ':/data', '-p', '127.0.0.1:18361:8333',
                'chrislusf/seaweedfs', 'server', '-dir=/data', '-s3', '-s3.port=8333',
                '-volume.max=32', '-master.volumeSizeLimitMB=32'], check=True)
print('Task runtime environment saved privately; no credentials printed.')
