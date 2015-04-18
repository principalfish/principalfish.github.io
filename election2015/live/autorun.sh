#!/bin/sh
git checkout master
git add .
git config --global push.default simple
git commit -am full_election_update
git push
