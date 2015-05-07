#!/bin/sh
git checkout master
git add .
git config --global push.default simple
git commit -am the_real_thing
git push
