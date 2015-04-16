#!/bin/sh
git checkout master
git add .
git config --global push.default simple
git commit -am test_election_update
git push
