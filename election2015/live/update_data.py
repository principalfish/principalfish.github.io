
def get_data(file):

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
        if incumbent == "independent" or incumbent == "speaker":
            incumbent = "other"

        seat_info["area"] = area

        seat_info["incumbent"] = incumbent

        candidates = itemlist[0].getElementsByTagName("Candidate")


        by_party = {}
        sum_others = 0
        sum_others_percentage = 0

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
                    if vote_percentage > 25:
                        by_party["other1"] = {"name" : name, "vote_total" : vote_total, "vote_percentage" : vote_percentage}

                    else:
                        sum_others += vote_total
                        sum_others_percentage += vote_percentage


        if not (sum_others <= 0 and sum_others_percentage <= 0):
            by_party["other2"] = {"name" : "Others", "vote_total" : sum_others, "vote_percentage" : sum_others_percentage}


        live_data[my_seat_name] = {"seat_info" : seat_info, "party_info" : by_party}
