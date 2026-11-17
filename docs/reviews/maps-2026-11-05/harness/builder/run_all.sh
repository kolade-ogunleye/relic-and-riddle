S=./maps_work
cd $S/r11build
OUT=${1:-after}
JOBS="bonifacio dover manama singapore miraflores thursday nagurskoye qaanaaq esperanza concordia heard noronha galapagos chatham wrangel clipperton wake cocos fakaofo nauru monaco kaliningrad nakhchivan cabinda busingen goldentri treriks saotome northpole gibraltar route_iceland route_indo tl_alhambra tl_colosseum tl_stonehenge lesotho vatican liecht gambia akrotiri male tristan ascension stkilda diego hongkong zanzibar longyear nemo baarle tl_angkor tl_hagia tl_machu tl_notredame"
for j in $JOBS; do timeout 900 $S/venv/bin/python -W ignore h/hr11.py . $OUT $j --fast; done
echo ALL_DONE
