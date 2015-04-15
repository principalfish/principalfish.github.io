from xml.dom import minidom
import os
import csv
import json

seat_dict = open("seat_map.json").read()
seat_map = json.loads(seat_dict)

seat_name_dict = open("seat_name_map.json").read()
seat_name_map = json.loads(seat_name_dict)

old = open("old_data.json").read()
old_data = json.loads(old)

live_data = {}

party_map = {
    "Conservative": "conservative",
    "Labour" : "labour",
    "Liberal Democrat" : "libdems",
    "UK Independence Party" : "ukip",
    "Scottish National Party": "snp",
    "Green" : "green",
    "Plaid Cymru" : "plaidcymru",
    "Alliance" : "alliance",
    "Ulster Unionist Party" : "uu",
    "Democratic Unionist Party" :  "dup",
    "Sinn Fein" : "sinnfein",
    "Social Democratic and Labour Party" : "sdlp"
}

winners_map = {
    "C" : "conservative",
    "Lab" : "labour",
    "Lab Co-op" :  "labour",
    "SDLP" : "sdlp",
    "Alliance" : "alliance",
    "UUP" : "uu",
    "LD" : "libdems",
    "SNP" : "snp",
    "Speaker" : "other",
    "UKIP" : "ukip",
    "PC" : "plaidcymru",
    "SF" : "sinnfein",
    "DUP" : "dup",
    "Green" : "green",
    "BNP" : "other",
    "Ind" : "other"
}

path = "testdata/test-general-election-2015-tory-hung-20150227/results/"
files = os.listdir(path)

for file in files:
    if "result" in file:
        file_dir = path + file
        xmldoc = minidom.parse(file_dir)

        declaration = xmldoc.getElementsByTagName('FirstPastThePostResult')

        declaration_time = (declaration[0].attributes['declarationTime'].value)

        itemlist = xmldoc.getElementsByTagName('Constituency')


        seat_name = itemlist[0].attributes['name'].value
        seat_id = itemlist[0].attributes['number'].value

        my_seat_name = seat_name_map[seat_name]
        my_seat_id = seat_map[seat_id]



        seat_info = {}

        seat_info["id"] = my_seat_id
        seat_info["declared_at"] = declaration_time
        seat_info["electorate"] =  (itemlist[0].attributes['electorate'].value)
        seat_info["turnout"] = (itemlist[0].attributes['turnout'].value)
        seat_info["percentage_turnout"] = (itemlist[0].attributes['percentageTurnout'].value)
        seat_info["change"] = (itemlist[0].attributes['gainOrHold'].value)
        seat_info["majority_total"] =  (itemlist[0].attributes['majority'].value)
        seat_info["majority_percentage"] = (itemlist[0].attributes['percentageMajority'].value)

        winning_party = (itemlist[0].attributes['winningParty'].value)
        if winning_party in winners_map.keys():
            seat_info["winning_party"] = winners_map[winning_party]

        else:
            seat_info["winning_party"] = "other"

        area = old_data[str(my_seat_id)]["area"]
        incumbent = old_data[str(my_seat_id)]["incumbent"]
        seat_info["area"] = area
        seat_info["incumbent"] = incumbent

        candidates = itemlist[0].getElementsByTagName("Candidate")


        by_party = {}

        for candidate in candidates:

            name = candidate.attributes['firstName'].value + " " + candidate.attributes['surname'].value

            results = candidate.getElementsByTagName("Party")

            for result in results:

                vote_total = float(result.attributes["votes"].value)
                vote_percentage = float(result.attributes["percentageShare"].value)
                party = result.attributes["name"].value

                if party in party_map.keys():
                    by_party[party_map[party]] = {"name" : name, "vote_total" : vote_total, "vote_percentage" : vote_percentage}

                else:
                    if vote_percentage > 15:
                        by_party["other"] = {"name" : name, "vote_total" : vote_total, "vote_percentage" : vote_percentage}



        live_data[my_seat_name] = {"seat_info" : seat_info, "party_info" : by_party}


# berwick = live_data["50"]
#
# seatinfo = berwick["seat_info"]
# print "Berwick-upon-Tweed"
# for key, val in seatinfo.iteritems():
#     print key, val
#
# partyinfo = berwick["party_info"]
# for key, val in partyinfo.iteritems():
#     print key,  val
#




with open("info.json", "w") as output:
    json.dump(live_data, output)
