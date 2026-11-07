cd ./maps_work/r11rev1
python -W ignore h/hr.py . after --fast > run_after.out 2>&1
echo AFTER_DONE >> run_after.out
cd ./maps_work/r9build/headsrc
python -W ignore ./maps_work/r11rev1/h/hr.py ./maps_work/r9build/headsrc ./maps_work/r11rev1/before --fast > ./maps_work/r11rev1/run_before.out 2>&1
echo HEAD_DONE >> ./maps_work/r11rev1/run_before.out
