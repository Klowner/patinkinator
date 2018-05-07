#!/bin/sh

if [[ "$1" == "" ]]; then
	echo "usage: $0 <input> <output>"
	exit 1
fi

infile=$1

# if no output is specified, create a destination based
# on the source path.
if [[ "$2" == "" ]]; then
	outfile="${infile%.*}.webm"
fi

# scales="-1 800 500 "
#filters=":flags=lanczos"

ffmpeg -v warning -i "$infile" -c:v libvpx-vp9 -crf 30 -b:v 2000k -an -y "${outfile}"
