R=./maps_work/r11rev1
PY=python
cd .
start=$(date +%s)
$PY -m pytest -n 2 -q -p no:cacheprovider tests/ > $R/full.txt 2>&1
echo "ELAPSED $(( $(date +%s)-start ))" >> $R/full.txt
md5sum src/graphics.py src/geo.py > $R/md5_end.txt
