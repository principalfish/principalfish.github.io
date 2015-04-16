#!/bin/sh
git checkout master
git add .
the_time = date +%Y%m%d%H%M%S
git commit -am "%the_time"
git push
