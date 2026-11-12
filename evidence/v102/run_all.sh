export PYTHONIOENCODING=utf-8
cd C:/AI/Jesse/Jesse-Audit
P=C:/Python311/python.exe; S=evidence/scripts/ux_e2e.py; O=evidence/v102/ui; B=http://127.0.0.1:8767
$P $S $O $B chrome 1600x900 > $O/log-chrome-1600x900-full.txt 2>&1
$P $S $O $B msedge 1920x1080 > $O/log-edge-1920x1080-full.txt 2>&1
for th in dark light; do
 $P $S $O $B chrome 1280x720 --quick --theme=$th > $O/log-chrome-1280x720-$th.txt 2>&1
 $P $S $O $B chrome 2560x1440 --quick --theme=$th > $O/log-chrome-2560x1440-$th.txt 2>&1
 $P $S $O $B msedge 1920x1080 --quick --theme=$th > $O/log-edge-1920x1080-$th.txt 2>&1
done
bash evidence/v102/shadow_route.sh
echo ALLDONE > $O/done.txt
