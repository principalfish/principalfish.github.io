import time
import datetime
import subprocess
import os
import json
import moment
import operator
from collections import OrderedDict

seat_dict = open("seat_map.json").read()
seat_map = json.loads(seat_dict)

old = open("old_data.json").read()
old_data = json.loads(old)

live_data = {}
new_data = {}


for_spreadsheet = []

parties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green", "uu", "sdlp", "dup", "sinnfein", "alliance", "other", "others"]

party_map = {
    "labour" : "Labour",
    "conservative" : "Conservative",
    "libdems" : "Lib Dem",
    "ukip" : "UKIP",
    "snp": "SNP",
    "green" : "Green",
    "plaidcymru" : "Plaid Cymru",
    "uu" : "Ulster Unionists",
    "dup" : "DUP",
    "sinnfein" : "Sinn Fein",
    "sdlp" : "SDLP",
    "alliance": "Alliance",
    "other" : "Other",
    "others" : "Other",
    "speaker" : "Speaker",
    "independent" : "Independent",
    "respect" : "Respect",
    "-" : "-",
    "" : ""
}

seat_list = []

def get_data(file):

    jsondata = open(path + file).read()
    data = json.loads(jsondata)

    seat_name = file[0:-5]

    turnout = data["seat_info"]["turnout"]
    electorate = data["seat_info"]["electorate"]
    percentage_turnout = data["seat_info"]["percentage_turnout"]
    declared_at = data["seat_info"]["declared_at"]
    declared_at_simple = data["seat_info"]["declared_at_simple"]

    by_party = {}

    max_party = ["" , 0]

    for party in parties:
        if party in data["party_info"].keys():
            info = data["party_info"][party]
            name = info["name"]
            vote_total = info["vote_total"]
            if vote_total > max_party[1]:
                max_party[0] = party
                max_party[1] = vote_total

            vote_percentage = 100 * vote_total / float(turnout)

            if old_data[seat_name][party] == None:
                old_votes = 0
            else:
                old_votes = old_data[seat_name][party]

            old_percentage = 100 * old_votes / float(old_data[seat_name]["turnout"])

            if party == "other" or party == "others":
                change_in_percentage = ""
            else:
                change_in_percentage = round(vote_percentage - old_percentage, 2)


            by_party[party] = {"name" : name,
                                "total" : vote_total,
                                "percent" : round(vote_percentage, 2),
                                "change": change_in_percentage,
                                "old" : old_votes
                                }

    by_seat = {}
    by_seat["area"] = old_data[seat_name]["area"]
    by_seat["winning_party"] = max_party[0]
    by_seat["incumbent"] = old_data[seat_name]["incumbent"]

    if max_party[0] != old_data[seat_name]["incumbent"]:
        change = "gain"
    else:
        change = "hold"

    old_turnout = 0
    for party in by_party:
        old_turnout += by_party[party]["old"]

    by_seat["old_turnout"] = old_turnout

    by_seat["change"] = change
    by_seat["electorate"] = electorate
    by_seat["turnout"] = turnout
    by_seat["percentage_turnout"] = percentage_turnout

    all_the_votes = []
    for candidate in by_party:
        all_the_votes.append(by_party[candidate]["total"])


    all_the_votes.remove(max(all_the_votes))
    majority_total = max_party[1] - max(all_the_votes)
    majority_percentage = 100 * majority_total / float(turnout)
    by_seat["maj"] = majority_total
    by_seat["maj_percent"] = round(majority_percentage, 2)

    by_seat["timestamp"] = declared_at
    by_seat["declared"] = declared_at_simple

    live_data[seat_name] = {"seat_info" : by_seat, "party_info" : by_party}

    if seat_name not in seat_list:
        seat_list.append(seat_name)

    now = time.time()
    if now - declared_at < 420:
        new_data[seat_name] = {"seat_info" : by_seat, "party_info" : by_party}


seats_declared = 0

while(True):

    print "********************"
    current_time = datetime.datetime.now()
    current_time = str(current_time)[0:16]
    print current_time

    delay = 30

    print "Refresh Rate: ", delay, "seconds"
    print "\n"

    path = "data/seats/"
    files = os.listdir(path)

    live_data = {}
    new_data = {}

    total_seats = 0
    for file in files:
        if file[0:-5] not in seat_list:
            print file[0:-5]
        total_seats +=1
        get_data(file)

    print "\n"

    print "seats declared so far", total_seats

    print "********************"
    if seats_declared == total_seats:
        print "no change"

    else:
        print "change"
        print "dumping to json"
        with open("info.json", "w") as output:
            json.dump(live_data, output)
        with open("new_info.json", "w") as out:
            json.dump(new_data, out)
        print "updating git"
        #subprocess.call("autorun.sh", shell = True)

        ## for spreadsheet update

        print "updating spreadsheet json"

        for seat in old_data:
            ss_change = "-"
            ss_actual = "-"
            ss_turnout = "-"

            if seat in live_data:
                ss_turnout = live_data[seat]["seat_info"]["percentage_turnout"]
                ss_actual = live_data[seat]["seat_info"]["winning_party"]
                if live_data[seat]["seat_info"]["change"] == "gain":
                    ss_change = party_map[live_data[seat]["seat_info"]["winning_party"]] +  " gain from " + party_map[old_data[seat]["incumbent"]]

            old_votes_for_spreadsheet = {}

            for party in parties:
                ss_incumbent = old_data[seat]["incumbent"]
                if party != ss_incumbent:
                    old_votes_for_spreadsheet[party] =  old_data[seat][party]

            ss_second_large = max(old_votes_for_spreadsheet.iteritems(), key=operator.itemgetter(1))[0]

            incumbent = old_data[seat]["incumbent"]

            if incumbent == "speaker" or incumbent == "respect" or incumbent == "independent":
                incumbent = "other"


            for_spreadsheet.append({
                "id" : old_data[seat]["off_id"],
                "name" : seat,
                "incumbent" : party_map[old_data[seat]["incumbent"]],
                "vote" : round(100 * old_data[seat][incumbent] / float(old_data[seat]["turnout"]), 2),
                "majority" : old_data[seat]["majority_percent_2010"],
                "closest_party" : party_map[ss_second_large],
                "expected" : "-",
                "actual" : party_map[ss_actual],
                "turnout" : ss_turnout,
                "change" : ss_change
                })

            ss_change = "-"
            ss_actual = "-"
            ss_turnout = "-"


        with open("spreadsheet_info.json", "w") as out_file:
            json.dump(for_spreadsheet, out_file)

    print "\n" * 3
    seats_declared = total_seats

    for i in range(delay):
        if (delay - i) % 10 == 0:
            print "time to next update", delay - i, "seconds"
        time.sleep(1)
