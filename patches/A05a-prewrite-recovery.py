# A05a recovery design and source transformation
# Apply only to exact A03d rgb.py SHA256 adc8eea228c175d672986815d793f67bc6a7cce28f7c6c0a387a6b9abefee6f4.
from pathlib import Path
import hashlib
import sys
p=Path(sys.argv[1])
raw=p.read_bytes()
assert hashlib.sha256(raw).hexdigest()=="adc8eea228c175d672986815d793f67bc6a7cce28f7c6c0a387a6b9abefee6f4"
new=raw.decode().replace('import time, contextlib,','import errno\nimport time, contextlib,',1)
anchor='class ConnectError(RuntimeError): pass\n'
new=new.replace(anchor,anchor+'''
# A05a: only transport failures before a lighting write are retryable.
@contextlib.contextmanager
def prewrite_transport(stage):
    try:
        yield
    except OSError as exc:
        transient = (errno.EAGAIN, errno.EINTR, errno.ETIMEDOUT,
                     errno.ENOTCONN, errno.ECONNRESET, errno.ECONNABORTED,
                     errno.ECONNREFUSED, errno.EHOSTDOWN, errno.EHOSTUNREACH,
                     errno.ENETDOWN, errno.ENETUNREACH, errno.EPIPE)
        if isinstance(exc, TimeoutError) or exc.errno in transient:
            raise ConnectError(f'{stage}: transporte BLE indisponível antes de 0x37: {exc}') from exc
        raise
''',1)
start=new.index('        matches=[(lo,hi)')
end=new.index('        def read():',start)
new=new[:start]+'        with prewrite_transport("GATT/identidade"):\n'+''.join('    '+line if line.strip() else line for line in new[start:end].splitlines(keepends=True))+new[end:]
new=new.replace("            current=read(); print('B7 inicial:',current.hex(' '),flush=True)","            with prewrite_transport('B7 inicial'):\n                current=read()\n            print('B7 inicial:',current.hex(' '),flush=True)",1)
compile(new,'rgb.py','exec')
# Write candidate only. Do not modify live installation.
out=Path(sys.argv[2])
with out.open('x',encoding='utf-8') as f: f.write(new)
