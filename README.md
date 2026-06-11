# Guition ESP32-S3-4848S040 bootstrap

Commands used:

```bash
. /opt/esp-idf/export.sh
export PORT=/dev/ttyUSB0

idf.py create-project esp32s3_4848s040_bootstrap
idf.py set-target esp32s3
idf.py add-dependency "espressif/esp_lcd_st7701"
idf.py add-dependency "espressif/esp_lcd_panel_io_additions"
idf.py add-dependency "espressif/esp_lcd_touch_gt911"

idf.py build
idf.py -p $PORT flash monitor

idf.py save-defconfig

python -m esptool --port $PORT chip_id
python -m esptool --port $PORT erase_flash
```

Notes:

```bash
# Standard milestone loop
idf.py build && idf.py -p $PORT flash monitor

# Recovery erase before reflashing
python -m esptool --port $PORT erase_flash
idf.py -p $PORT flash monitor
```
