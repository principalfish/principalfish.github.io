#!/bin/sh
git checkout dev
git add .
git commit -am "made change"
git push
git config --global credential.helper "cache --timeout=3600"