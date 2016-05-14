#!/usr/bin/env bash
awk 'BEGIN { FS = "," } ; { printf "%.6f,%.6f,%d\n",$3,$4,$9 }' model.csv | grep -v "0.000000,0.000000,0" > dome_model.csv
