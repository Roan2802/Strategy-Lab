# Isolated shadow-route E2E: its own server on :8768 with a fresh temporary shadow store per browser run.
# The real shadow store (:8767) is only read, by the E2E's before/after counts.
export PYTHONIOENCODING=utf-8
cd C:/AI/Jesse/Jesse-Audit
P=C:/Python311/python.exe; S=evidence/scripts/ux_e2e.py; O=evidence/v102/ui/shadow_route; B=http://127.0.0.1:8768
mkdir -p $O
stop() { powershell -c "Get-NetTCPConnection -LocalPort 8768 -State Listen -ErrorAction SilentlyContinue | % { Stop-Process -Id \$_.OwningProcess -Force }"; sleep 2; }
run() { # $1 channel  $2 WxH  $3 log
  T=$(cygpath -m "$(mktemp -d /tmp/shadow-e2e-XXXX)"); stop
  STRATEGY_LAB_SHADOW_DB=$T/shadow.sqlite3 STRATEGY_LAB_PORT=8768 STRATEGY_LAB_AUTO_RELOAD=0 STRATEGY_LAB_TRICKLE=0 nohup $P -m strategy_lab.server > $O/server8768.log 2>&1 &
  for i in $(seq 1 40); do curl -sf $B/api/shadow/portfolios > /dev/null && break; sleep 1; done
  $P $S $O $B $1 $2 --shadow > $O/$3 2>&1
}
PYTHONPATH=. $P evidence/scripts/shadow_probe.py $O/probe.json > /dev/null
run chrome 1600x900 log-chrome-1600x900.txt
run msedge 1920x1080 log-edge-1920x1080.txt
stop
