# A05d: exact A05b service.py transformation. Candidate output only.
from pathlib import Path
import sys, hashlib
raw=Path(sys.argv[1]).read_bytes()
assert hashlib.sha256(raw).hexdigest()=="9c3acfc4695afc7d6f40d0a1edb4ddbf53272e2f8ab5ef4d261bd723a85e278a"
s=raw.decode()
pairs=[
("    def publish(self):","    def monitoring(self):\n        return self.enabled or self.policy!='normal'\n    def publish(self):"),
("if not self.enabled or self.blocked or self.retry_mode or self.reconnect_target is None: return","if not self.monitoring() or self.blocked or self.retry_mode or self.reconnect_target is None: return\n        if not self.enabled and pending(): return"),
("idle=self.enabled and self.policy!='normal' and not self.active","idle=self.policy!='normal' and not self.active"),
("if not self.enabled and not pending() and not policy_pending(): self.clear_retry(); return","if not self.monitoring() and not pending() and not policy_pending(): self.clear_retry(); return"),
("            self.reconnect_target=None\n            self.enabled=","            if pending(): self.reconnect_target=None\n            self.enabled="),
("            if not self.enabled: self.active={}; self.owner=None","            if not self.enabled:\n                self.owner=None\n                if not self.monitoring(): self.active={}"),
("            self.enabled=False; app.save_json(PREF,{'enabled':False}); self.blocked=False;","            self.enabled=False; app.save_json(PREF,{'enabled':False}); self.policy='normal'; app.save_json(POLICY,{'mode':'normal'}); self.blocked=False;"),
("self.enabled and self.policy=='games'","self.policy=='games'"),
("self.enabled and self.policy=='games_gui'","self.policy=='games_gui'"),
("e.enabled or pending() or policy_pending()","e.monitoring() or pending() or policy_pending()"),
("wake.drain() and e.enabled","wake.drain() and e.monitoring()"),
]
for a,b in pairs:
    assert a in s,a
    s=s.replace(a,b)
compile(s,'service.py','exec')
assert hashlib.sha256(s.encode()).hexdigest()=="b2fd16cd11ba662f8e94e11f057a10feca54ce5d12d7da16d50e0223cb7799a4"
with Path(sys.argv[2]).open('x',encoding='utf-8') as f: f.write(s)
