#!/bin/sh
git checkout master
git add .
git config --global push.default simple
git push
