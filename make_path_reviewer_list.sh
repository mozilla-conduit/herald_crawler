#!/bin/sh
# example:
#
#     ./make_path_reviewer_list.sh '| [].reviewers[]' < herald_rules.json

jp "rules[?[conditions[].type=='differential-affected-files']].{name: name, re: conditions[?type=='differential-affected-files'].value[], reviewers: actions[?type=='add-reviewers'].reviewers[].target[], repositories: conditions[?type=='repository'].value[]} $*"
