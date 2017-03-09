import os, json, math
from polls_analysis import regional_averages, previous_regional_totals, parties, regions

party_map = {
     'conservative': "Conservative",
     'labour': "Labour",
     'libdems': "Lib Dems",
     'ukip': "UKIP",
     'snp': "SNP",
     'plaidcymru': "Plaid Cymru",
     'green': "Green",
     'uu': "UUP",
     'sdlp': "SDLP",
     'dup': "DUP",
     'sinnfein': "Sinn Fein",
     'alliance': "Alliance",
     'other' : "Others"
}

seat_data = {}
current_dir = os.path.dirname(__file__)
parent_dir = os.path.split(current_dir)[0]
file_path = os.path.join(parent_dir, "2015election_600seat.json")
with open(file_path) as data_file:
    data = json.load(data_file)
    for seat in data:
        seat_data[seat] = data[seat]
    data_file.close()

for seat, data in seat_data.iteritems():

    other_total = 0
    for party in data["partyInfo"]:
        if party == "others" or party == "other":
            other_total += data["partyInfo"][party]["total"]

    seat_data[seat]["partyInfo"]["other"] = {"total" : other_total, "name" : "Others"}
    if "others" in data["partyInfo"]:
        del data["partyInfo"]["others"]

    seat_data[seat] = data

previous_regional_percentages = {}
for region, data in previous_regional_totals.iteritems():
    previous_regional_percentages[region] = {}
    for party in data:
        if party != "turnout":
            previous_regional_percentages[region][party] = data[party] / float(data["turnout"])


regional_relative_changes = {}

for region, data in previous_regional_percentages.iteritems():
    regional_relative_changes[region] = {}
    for party in data:
        if data[party] != 0:
            regional_relative_changes[region][party] = regional_averages[region][party] / previous_regional_percentages[region][party]

def get_new_data(seat, data):

    # if seat == "Berwick-upon-Tweed":
        region = data["seatInfo"]["region"]

        turnout = 0
        for party in data["partyInfo"]:
            turnout += data["partyInfo"][party]["total"]

        # for getting current, majority
        max = 0
        current_max = ""

        new_percentages = {}

        for party in data["partyInfo"]:
            percentage_vote = data["partyInfo"][party]["total"] / float(turnout)
            seat_relative = percentage_vote / previous_regional_percentages[region][party]
            #print party, percentage_vote, seat_relative

            new = 0

            if seat_relative != 0:
                distribute = regional_relative_changes[region][party] - 1
                seat_change = 1 + distribute / math.pow(seat_relative, 0.5)

                if seat_change < 0.15:
                    seat_change = 0.15

                new = seat_change * data["partyInfo"][party]["total"] / float(turnout)

                incumbencies = {"libdems" : 0.04, "ukip" : 0.03, "conservative" : 0.02, "labour" : 0.02, "green" : 0.03, "snp": 0.04, "plaidcymru" : 0.04}

                if party == data["seatInfo"]["current"]:
                    if party in incumbencies:
                        new += incumbencies[party]

                if new > max:
                    max = new
                    current_max = party

            new_percentages[party] = new

        sum = 0
        for party, total in new_percentages.iteritems():
            sum += total

        votes_array = []
        normaliser = 1 / sum
        for party, total in new_percentages.iteritems():
            new_percentages[party] *= normaliser
            votes = int(round(new_percentages[party] * turnout))
            votes_array.append(votes)
            data["partyInfo"][party] = {"name" : party_map[party], "total" : votes}


        data["seatInfo"]["current"] = current_max
        data["seatInfo"]["majority"] =  sorted(votes_array)[-1] - sorted(votes_array)[-2]


for seat, data in seat_data.iteritems():
    get_new_data(seat, data)


with open("../prediction_600seat.json", "w") as output:
    json.dump(seat_data, output)
