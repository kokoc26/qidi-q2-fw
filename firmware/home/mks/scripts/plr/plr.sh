#!/bin/bash 
 
CONFIG_FILE="/home/mks/printer_data/config/saved_variables.cfg" 

BED_TEMP="" 
GCODE_LINES="" 
CHAMBER_TEMP="" 
EXTRUDER_TEMP="" 
 
BED_TEMP=$(awk -F " = " '/bed_temp/ {gsub(/'\''/, "", $2); print $2}' $CONFIG_FILE) 
GCODE_LINES=$(awk -F " = " '/gcode_lines/ {gsub(/'\''/, "", $2); print $2}' $CONFIG_FILE) 
CHAMBER_TEMP=$(awk -F " = " '/hot_temp/ {gsub(/'\''/, "", $2); print $2}' $CONFIG_FILE) 
EXTRUDER_TEMP=$(awk -F " = " '/print_temp/ {gsub(/'\''/, "", $2); print $2}' $CONFIG_FILE) 
 
echo "" 
echo "运行断电续打恢复" 
echo "GCODE_LINES: $GCODE_LINES" 
echo "EXTRUDER_TEMP: $EXTRUDER_TEMP" 
echo "BED_TEMP: $BED_TEMP" 
echo "CHAMBER_TEMP: $CHAMBER_TEMP" 

rm -f "/home/mks/printer_data/.temp/plr.gcode" 
GCODE_PATH=$(find /home/mks/printer_data/.temp/ -maxdepth 1 -name "*.gcode" -print -quit)

if [ -z "$GCODE_PATH" ]; then
    cat "错误：在/home/mks/printer_data/.temp/目录下未找到gcode文件"
    exit 1
fi

echo "找到gcode文件: $GCODE_PATH"

GCODE_FILENAME=$(basename "$GCODE_PATH")
echo "文件名: $GCODE_FILENAME"

# if grep -q "gcode_filename" "$CONFIG_FILE"; then
#     sed -i "s/gcode_filename = .*/gcode_filename = '$GCODE_FILENAME'/" "$CONFIG_FILE"
# else
#     echo "gcode_filename = '$GCODE_FILENAME'" >> "$CONFIG_FILE"
# fi

TEMP_FILE=$(mktemp)

num_lines=$(($GCODE_LINES - 1))
head -n $num_lines "$GCODE_PATH" > "$TEMP_FILE.header"
tail -n +$GCODE_LINES "$GCODE_PATH" > "$TEMP_FILE.footer"

z_position=$(cat "$TEMP_FILE.header" | sed -n '/;Z:/s/.*;Z:\([0-9.]*\).*/\1/p' | tail -n 1)
if [ -z "$z_position" ]; then
    z_position=$(cat "$TEMP_FILE.header" | sed -n '/; Z_HEIGHT: /s/.*;\x20Z_HEIGHT:\x20\([0-9.]*\).*/\1/p' | tail -n 1)
fi
echo "z_position: $z_position"

isInFile=$(grep -c "thumbnail end" "$GCODE_PATH")

{
    # if [ $isInFile -ne 0 ]; then
    #     sed -n '/thumbnail begin/,/thumbnail end/p' "$GCODE_PATH"
    #     echo ";"
    #     echo ""
    # fi
    
    echo "SET_KINEMATIC_POSITION Z=$z_position"

    grep "EXCLUDE_OBJECT_DEFINE" "$TEMP_FILE.header"
    
    echo "M109 S$EXTRUDER_TEMP"
    echo "M140 S$BED_TEMP"
    echo "M104 S$EXTRUDER_TEMP"
    
    echo "G91"
    echo "G1 Z5 F600"
    echo "G90"
    echo "G28 X Y"
    echo "G28 X"
    
    echo "CLEAR_NOZZLE_PLR hotend=$EXTRUDER_TEMP"
    
    echo "M190 S$BED_TEMP"
    echo "M191 S$CHAMBER_TEMP"
    
    grep "M106 S" "$TEMP_FILE.header" | tail -n 1
    grep "M106 P2 S" "$TEMP_FILE.header" | tail -n 1
    grep "M106 P3 S" "$TEMP_FILE.header" | tail -n 1
    
    echo "G90"
    echo "G1 Z$z_position"
    
    grep -E "M83|M82" "$TEMP_FILE.header" | tail -n 1
    
    echo "ENABLE_ALL_SENSOR"
    
    echo "G1 F6000"
    
    cat "$TEMP_FILE.footer"
} > "$TEMP_FILE"


mv "$TEMP_FILE" "/home/mks/printer_data/.temp/plr.gcode"
chmod 644 "/home/mks/printer_data/.temp/plr.gcode"

rm -f "$TEMP_FILE.header" "$TEMP_FILE.footer"

echo "断电续打文件处理完成: $GCODE_PATH"