R=./maps_work/r11rev1
PY=python
cd .
start=$(date +%s)
$PY -m pytest -n 2 -q -p no:cacheprovider tests/test_graphics.py tests/test_graphics_16x9_2026_11_02.py tests/test_r3_graphics_2026_11_02.py tests/test_r4_graphics_2026_11_02.py tests/test_timeline_hierarchy_2026_11_03.py tests/test_brand_overhaul_2026_10_29.py tests/test_geo_polylabel_2026_11_03.py tests/test_globe_relief_2026_11_03.py tests/test_map_marker_2026_11_03.py tests/test_round6_review_2026_11_03.py tests/test_round7_review_2026_11_03.py tests/test_round8_review_2026_11_03.py tests/test_round9_review_2026_11_04.py tests/test_round10_review_2026_11_04.py tests/test_scale_bar_2026_11_03.py tests/test_timeline_ink_2026_11_03.py tests/test_timeline_v2_2026_11_02.py tests/test_round11_review_2026_11_04.py tests/test_round12_review_2026_11_04.py tests/test_round13_review_2026_11_04.py tests/test_round14_review_2026_11_04.py tests/test_round15_review_2026_11_05.py tests/test_geo_data_files_2026_11_03.py tests/test_review_once_map_labels_2026_10_29.py tests/test_safe_zones.py > $R/gfx.txt 2>&1
echo "ELAPSED $(( $(date +%s)-start ))" >> $R/gfx.txt
md5sum src/graphics.py src/geo.py > $R/md5_mid.txt
echo "ELAPSED $(( $(date +%s)-start ))" >> $R/gfx.txt; md5sum src/graphics.py src/geo.py > $R/md5_mid.txt
