#!/bin/sh
git checkout dev
git add .

now=$(date +"%T")
echo "Current time : $now"

git commit -am now
git push
