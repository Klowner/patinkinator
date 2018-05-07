#!/bin/sh

if [[ "$1" == "" ]]; then
	echo "usage: $0 <input> <output>"
	exit 1
fi

infile=$1

# if no output is specified, create a destination based
# on the source path.
if [[ "$2" == "" ]]; then
	outfile="${infile%.*}__SCALE_.gif"
fi

scales="800 500 200"
palette=$(mktemp -t XXXXXXXXXX.png)
filters="fps=15,scale=_SCALE_:-1:flags=lanczos"

ffmpeg -v warning -i "$infile" -vf "${filters/_SCALE_/200},palettegen" -y $palette

for s in $scales; do
	filter="${filters/_SCALE_/$s}"
	ffmpeg -v warning -i "$infile" -i $palette -lavfi "$filter [x]; [x][1:v] paletteuse" -y "${outfile/_SCALE_/$s}"
done

rm $palette
